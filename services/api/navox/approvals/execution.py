from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.agent.contracts import get_action_contract
from navox.agent.hashing import action_security_hash
from navox.approvals.schemas import StoredGmailSendPayload
from navox.approvals.service import ApprovalService, latest_approval, plan_for_action
from navox.core.settings import Settings
from navox.db.models import (
    Action,
    Approval,
    Commitment,
    Connection,
    User,
    WorkflowRef,
    WorkspaceMembership,
)
from navox.providers.google_gmail import (
    GmailGateway,
    GmailProviderError,
    GmailSendPayload,
    GoogleGmailGateway,
)
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
APPROVAL_TTL = timedelta(minutes=15)


async def _workflow_ref(database: AsyncSession, action_id: UUID) -> WorkflowRef | None:
    return cast(
        WorkflowRef | None,
        await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == "action",
                WorkflowRef.entity_id == action_id,
                WorkflowRef.workflow_type == "approved_action",
            )
        ),
    )


async def action_authorization_state(database: AsyncSession, action_id: UUID) -> str:
    action = await database.scalar(select(Action).where(Action.id == action_id))
    if action is None:
        return "missing"
    approval = await latest_approval(database, action.id)
    if approval is None:
        return "missing_approval"
    if await ApprovalService().expire_if_needed(database, action, approval):
        workflow_ref = await _workflow_ref(database, action.id)
        if workflow_ref is not None:
            workflow_ref.status = "expired"
            await database.commit()
        return "expired"

    state = action.status
    if state == "awaiting_approval":
        return approval.status
    if state == "approved":
        return "approved"
    if state == "executing":
        return "executing"
    if state in {
        "completed",
        "uncertain",
        "rejected",
        "expired",
        "blocked",
        "failed",
    }:
        workflow_ref = await _workflow_ref(database, action.id)
        desired = "manual_review" if state == "uncertain" else state
        if workflow_ref is not None and workflow_ref.status != desired:
            workflow_ref.status = desired
            await database.commit()
        return state
    return state


async def _invalidate_locked_for_pause(
    database: AsyncSession,
    *,
    action: Action,
    approval: Approval,
) -> None:
    now = datetime.now(UTC)
    approval.status = "superseded"
    approval.superseded_at = now
    replacement = Approval(
        action_id=action.id,
        user_id=action.user_id,
        workspace_id=action.workspace_id,
        version=approval.version + 1,
        action_payload_hash=action.payload_hash,
        status="pending",
        expires_at=now + APPROVAL_TTL,
    )
    database.add(replacement)
    action.status = "awaiting_approval"
    plan, step = await plan_for_action(database, action)
    plan.status = "awaiting_approval"
    plan.error_code = "approval_invalidated_by_pause"
    step.status = "awaiting_approval"
    add_audit_event(
        database,
        user_id=action.user_id,
        workspace_id=action.workspace_id,
        event_type="approval.action.invalidated_by_pause",
        entity_type="action",
        entity_id=action.id,
        metadata={
            "superseded_version": approval.version,
            "replacement_version": replacement.version,
        },
    )


async def _mark_blocked(
    database: AsyncSession,
    *,
    action: Action,
    reason: str,
) -> str:
    now = datetime.now(UTC)
    action.status = "blocked"
    action.policy_reason = reason
    plan, step = await plan_for_action(database, action)
    plan.status = "blocked"
    plan.error_code = reason[:64]
    plan.completed_at = now
    step.status = "blocked"
    step.completed_at = now
    workflow_ref = await _workflow_ref(database, action.id)
    if workflow_ref is not None:
        workflow_ref.status = "blocked"
    add_audit_event(
        database,
        user_id=action.user_id,
        workspace_id=action.workspace_id,
        event_type="action.execution.blocked",
        entity_type="action",
        entity_id=action.id,
        metadata={"reason": reason, "payload_hash": action.payload_hash},
    )
    await database.commit()
    return "blocked"


async def mark_execution_uncertain(
    database: AsyncSession,
    *,
    action_id: UUID,
    reason: str,
) -> str:
    action = await database.scalar(select(Action).where(Action.id == action_id))
    if action is None:
        return "missing"
    if action.status == "completed":
        return "completed"
    if action.status != "executing":
        return action.status

    approval = await latest_approval(database, action.id)
    now = datetime.now(UTC)
    action.status = "uncertain"
    action.policy_reason = reason
    action.executed_at = now
    plan, step = await plan_for_action(database, action)
    plan.status = "manual_review"
    plan.error_code = reason[:64]
    step.status = "uncertain"
    step.completed_at = now
    workflow_ref = await _workflow_ref(database, action.id)
    if workflow_ref is not None:
        workflow_ref.status = "manual_review"
    add_audit_event(
        database,
        user_id=action.user_id,
        workspace_id=action.workspace_id,
        event_type="action.execution.uncertain",
        entity_type="action",
        entity_id=action.id,
        metadata={
            "reason": reason,
            "approval_version": approval.version if approval is not None else None,
            "payload_hash": action.payload_hash,
        },
    )
    await database.commit()
    return "uncertain"


async def execute_approved_gmail_send(
    database: AsyncSession,
    *,
    action_id: UUID,
    settings: Settings,
    gateway: GmailGateway | None = None,
) -> str:
    action = await database.scalar(select(Action).where(Action.id == action_id))
    if action is None:
        return "missing"
    if action.status == "completed":
        return "completed"
    if action.status == "executing":
        return "executing"
    if action.status in {"uncertain", "rejected", "expired", "blocked", "failed"}:
        return action.status
    if action.action_type != "gmail.send" or action.provider != "google":
        return await _mark_blocked(
            database,
            action=action,
            reason="unsupported_approved_action",
        )

    contract = get_action_contract(action.action_type)
    if contract is None or contract.risk_level != "R3":
        return await _mark_blocked(
            database,
            action=action,
            reason="risk_contract_mismatch",
        )

    approval = await latest_approval(database, action.id)
    if approval is None:
        return await _mark_blocked(database, action=action, reason="approval_missing")
    if await ApprovalService().expire_if_needed(database, action, approval):
        return "expired"
    if approval.status != "approved":
        return approval.status

    current_hash = action_security_hash(
        provider=action.provider,
        action_type=action.action_type,
        payload=action.payload,
    )
    if current_hash != action.payload_hash or approval.action_payload_hash != action.payload_hash:
        return await _mark_blocked(
            database,
            action=action,
            reason="approval_payload_hash_mismatch",
        )

    try:
        payload = StoredGmailSendPayload.model_validate(action.payload)
    except ValueError:
        return await _mark_blocked(
            database,
            action=action,
            reason="invalid_action_payload",
        )

    connection = await database.scalar(
        select(Connection).where(
            Connection.id == payload.connection_id,
            Connection.user_id == action.user_id,
            Connection.workspace_id == action.workspace_id,
            Connection.provider == "google",
            Connection.status == "active",
        )
    )
    if connection is None:
        return await _mark_blocked(
            database,
            action=action,
            reason="google_connection_unavailable",
        )
    if GMAIL_SEND_SCOPE not in connection.granted_scopes:
        return await _mark_blocked(
            database,
            action=action,
            reason="gmail_send_scope_missing",
        )
    if connection.external_email != str(payload.sender).casefold():
        return await _mark_blocked(
            database,
            action=action,
            reason="sender_connection_mismatch",
        )

    prepared_payload_hash = action.payload_hash

    # Token refresh is side-effect free with respect to email sending and occurs
    # before consuming the one-time approval.
    try:
        access_token = await access_token_for_connection(
            database,
            connection=connection,
            settings=settings,
        )
    except GoogleAccessTokenError:
        connection.status = "needs_reauthorization"
        connection.last_error = "refresh_failed"
        return await _mark_blocked(
            database,
            action=action,
            reason="google_reauthorization_required",
        )
    await database.commit()

    # Serialize the approval-consumption boundary. Only one concurrent caller can
    # move this action from approved -> executing.
    async with database.begin():
        # Owner first, then action and draft: matches editing and source deletion.
        await database.scalar(select(User).where(User.id == action.user_id).with_for_update())
        locked_action = await database.scalar(
            select(Action)
            .where(Action.id == action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_action is None:
            return "missing"
        if locked_action.status == "completed":
            return "completed"
        if locked_action.status == "executing":
            return "executing"
        if locked_action.status != "approved":
            return locked_action.status

        locked_approval = await database.scalar(
            select(Approval)
            .where(Approval.action_id == locked_action.id)
            .order_by(Approval.version.desc())
            .limit(1)
            .with_for_update()
        )
        if locked_approval is None:
            return "missing_approval"
        if locked_approval.status != "approved" or locked_approval.consumed_at is not None:
            return locked_approval.status

        # Refresh can narrow the grant, and a disconnect may have won the race
        # while the token request was in flight. Recheck before consuming approval.
        current_connection = await database.scalar(
            select(Connection)
            .where(Connection.id == payload.connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            current_connection is None
            or current_connection.status != "active"
            or current_connection.provider != "google"
            or (current_connection.workspace_id, current_connection.user_id)
            != (locked_action.workspace_id, locked_action.user_id)
            or GMAIL_SEND_SCOPE not in current_connection.granted_scopes
            or current_connection.external_email != str(payload.sender).casefold()
            or await database.get(
                WorkspaceMembership,
                (locked_action.workspace_id, locked_action.user_id),
                populate_existing=True,
            )
            is None
        ):
            locked_action.status = "blocked"
            locked_action.policy_reason = "gmail_send_authority_changed"
            return "blocked"

        locked_hash = action_security_hash(
            provider=locked_action.provider,
            action_type=locked_action.action_type,
            payload=locked_action.payload,
        )
        if (
            locked_action.payload_hash != prepared_payload_hash
            or locked_hash != locked_action.payload_hash
            or locked_approval.action_payload_hash != locked_action.payload_hash
        ):
            locked_action.status = "blocked"
            locked_action.policy_reason = "approval_payload_hash_mismatch"
            return "blocked"

        from navox.communication.service import valid_draft_binding

        if not await valid_draft_binding(database, locked_action, approved=True):
            locked_action.status = "blocked"
            locked_action.policy_reason = "draft_version_changed"
            return "blocked"

        user = await database.scalar(
            select(User).where(User.id == locked_action.user_id).with_for_update()
        )
        if user is None:
            locked_action.status = "blocked"
            locked_action.policy_reason = "user_missing"
            return "blocked"
        if user.agent_paused:
            await _invalidate_locked_for_pause(
                database,
                action=locked_action,
                approval=locked_approval,
            )
            return "awaiting_approval"

        now = datetime.now(UTC)
        locked_approval.consumed_at = now
        locked_approval.status = "consuming"
        locked_action.status = "executing"
        locked_action.started_at = locked_action.started_at or now
        plan, step = await plan_for_action(database, locked_action)
        plan.status = "executing"
        plan.error_code = None
        step.status = "executing"
        step.started_at = step.started_at or now
        add_audit_event(
            database,
            user_id=locked_action.user_id,
            workspace_id=locked_action.workspace_id,
            event_type="action.execution.started",
            entity_type="action",
            entity_id=locked_action.id,
            metadata={
                "approval_version": locked_approval.version,
                "payload_hash": locked_action.payload_hash,
                "risk_level": locked_action.risk_level,
            },
        )

    # Re-read after the transaction so ORM state reflects the committed execution
    # marker before the provider request.
    action = await database.scalar(select(Action).where(Action.id == action_id))
    approval = await latest_approval(database, action_id)
    if action is None or approval is None:
        return "missing"

    gmail = gateway or GoogleGmailGateway(timeout_seconds=float(contract.timeout_seconds))
    try:
        receipt = await gmail.send(
            access_token=access_token,
            payload=GmailSendPayload(
                sender=str(payload.sender),
                to=str(payload.to),
                subject=payload.subject,
                body_text=payload.body_text,
                reply=payload.reply,
            ),
            idempotency_key=action.idempotency_key,
        )
        if payload.reply is not None and receipt.thread_id != payload.reply.thread_id:
            raise GmailProviderError("Gmail did not confirm the approved reply thread")
    except GmailProviderError:
        return await mark_execution_uncertain(
            database,
            action_id=action.id,
            reason="gmail_send_outcome_uncertain",
        )

    finished = datetime.now(UTC)
    action.status = "completed"
    action.executed_at = finished
    action.verified_at = finished
    action.policy_reason = "approved_and_provider_verified"
    action.result = {
        "message_id": receipt.message_id,
        "thread_id": receipt.thread_id,
        "verification": contract.verification_method,
    }
    if payload.draft_id is not None:
        from navox.db.communications import CommunicationDraft

        draft = await database.get(CommunicationDraft, payload.draft_id)
        if draft is not None:
            draft.status = "sent"
    approval.status = "consumed"
    plan, step = await plan_for_action(database, action)
    step.status = "completed"
    step.output_payload = action.result
    step.completed_at = finished
    plan.status = "completed"
    plan.completed_at = finished

    commitment = None
    if action.commitment_id is not None:
        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == action.commitment_id,
                Commitment.user_id == action.user_id,
                Commitment.workspace_id == action.workspace_id,
            )
        )
    if commitment is not None:
        if payload.post_send_state == "waiting":
            commitment.status = "waiting"
            commitment.completed_at = None
        elif payload.post_send_state == "completed":
            commitment.status = "completed"
            commitment.completed_at = finished

    workflow_ref = await _workflow_ref(database, action.id)
    if workflow_ref is not None:
        workflow_ref.status = "completed"

    add_audit_event(
        database,
        user_id=action.user_id,
        workspace_id=action.workspace_id,
        event_type="action.execution.verified",
        entity_type="action",
        entity_id=action.id,
        metadata={
            "provider": "google",
            "action_type": "gmail.send",
            "message_id": receipt.message_id,
            "approval_version": approval.version,
            "payload_hash": action.payload_hash,
            "verification_method": contract.verification_method,
        },
    )
    await database.commit()
    return "completed"
