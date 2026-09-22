from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.agent.contracts import get_action_contract
from navox.agent.hashing import action_security_hash
from navox.approvals.schemas import StoredGmailSendPayload
from navox.approvals.service import ApprovalService, latest_approval, plan_for_action
from navox.core.settings import Settings
from navox.db.models import Action, Approval, Commitment, Connection, User, WorkflowRef
from navox.providers.google_gmail import (
    GmailGateway,
    GmailProviderError,
    GmailSendPayload,
    GoogleGmailGateway,
)
from navox.providers.google_oauth import GoogleAccessTokenError, access_token_for_connection

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


async def action_authorization_state(database: AsyncSession, action_id: UUID) -> str:
    action = await database.scalar(select(Action).where(Action.id == action_id))
    if action is None:
        return "missing"
    approval = await latest_approval(database, action.id)
    if approval is None:
        return "missing_approval"
    if await ApprovalService().expire_if_needed(database, action, approval):
        return "expired"
    if action.status in {
        "completed",
        "uncertain",
        "rejected",
        "expired",
        "failed",
    }:
        return action.status
    return approval.status


async def _invalidate_for_pause(
    database: AsyncSession,
    *,
    action: Action,
    approval: Approval,
) -> str:
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
        expires_at=now + timedelta(minutes=15),
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
    await database.commit()
    return "awaiting_approval"


async def _mark_uncertain(
    database: AsyncSession,
    *,
    action: Action,
    approval: Approval,
    reason: str,
) -> str:
    now = datetime.now(UTC)
    action.status = "uncertain"
    action.policy_reason = reason
    action.executed_at = now
    plan, step = await plan_for_action(database, action)
    plan.status = "manual_review"
    plan.error_code = reason[:64]
    step.status = "uncertain"
    step.completed_at = now
    workflow_ref = await database.scalar(
        select(WorkflowRef).where(
            WorkflowRef.entity_type == "action",
            WorkflowRef.entity_id == action.id,
            WorkflowRef.workflow_type == "approved_action",
        )
    )
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
            "approval_version": approval.version,
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
    if action.action_type != "gmail.send" or action.provider != "google":
        return "failed"

    contract = get_action_contract(action.action_type)
    if contract is None or contract.risk_level != "R3":
        return "failed"

    approval = await latest_approval(database, action.id)
    if approval is None:
        return "missing_approval"
    if await ApprovalService().expire_if_needed(database, action, approval):
        return "expired"
    if approval.status != "approved":
        return approval.status
    if approval.consumed_at is not None:
        return "uncertain"

    current_hash = action_security_hash(
        provider=action.provider,
        action_type=action.action_type,
        payload=action.payload,
    )
    if current_hash != action.payload_hash or approval.action_payload_hash != action.payload_hash:
        action.status = "blocked"
        action.policy_reason = "approval_payload_hash_mismatch"
        await database.commit()
        return "blocked"

    user = await database.scalar(select(User).where(User.id == action.user_id))
    if user is None:
        return "failed"
    if user.agent_paused:
        return await _invalidate_for_pause(database, action=action, approval=approval)

    try:
        payload = StoredGmailSendPayload.model_validate(action.payload)
    except ValueError:
        action.status = "blocked"
        action.policy_reason = "invalid_action_payload"
        await database.commit()
        return "blocked"

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
        action.status = "blocked"
        action.policy_reason = "google_connection_unavailable"
        await database.commit()
        return "blocked"
    if GMAIL_SEND_SCOPE not in connection.granted_scopes:
        action.status = "blocked"
        action.policy_reason = "gmail_send_scope_missing"
        await database.commit()
        return "blocked"
    if connection.external_email != str(payload.sender).casefold():
        action.status = "blocked"
        action.policy_reason = "sender_connection_mismatch"
        await database.commit()
        return "blocked"

    try:
        access_token = await access_token_for_connection(
            database,
            connection=connection,
            settings=settings,
        )
    except GoogleAccessTokenError:
        connection.status = "needs_reauthorization"
        connection.last_error = "refresh_failed"
        action.status = "blocked"
        action.policy_reason = "google_reauthorization_required"
        await database.commit()
        return "blocked"

    now = datetime.now(UTC)
    approval.consumed_at = now
    approval.status = "consuming"
    action.status = "executing"
    action.started_at = action.started_at or now
    plan, step = await plan_for_action(database, action)
    plan.status = "executing"
    plan.error_code = None
    step.status = "executing"
    step.started_at = step.started_at or now
    add_audit_event(
        database,
        user_id=action.user_id,
        workspace_id=action.workspace_id,
        event_type="action.execution.started",
        entity_type="action",
        entity_id=action.id,
        metadata={
            "approval_version": approval.version,
            "payload_hash": action.payload_hash,
            "risk_level": action.risk_level,
        },
    )
    await database.commit()

    gmail = gateway or GoogleGmailGateway(timeout_seconds=float(contract.timeout_seconds))
    try:
        receipt = await gmail.send(
            access_token=access_token,
            payload=GmailSendPayload(
                to=str(payload.to),
                subject=payload.subject,
                body_text=payload.body_text,
            ),
            idempotency_key=action.idempotency_key,
        )
    except GmailProviderError:
        return await _mark_uncertain(
            database,
            action=action,
            approval=approval,
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
    approval.status = "consumed"
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

    workflow_ref = await database.scalar(
        select(WorkflowRef).where(
            WorkflowRef.entity_type == "action",
            WorkflowRef.entity_id == action.id,
            WorkflowRef.workflow_type == "approved_action",
        )
    )
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
