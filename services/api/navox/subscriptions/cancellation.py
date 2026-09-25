"""Exact R4 cancellation approvals and durable at-most-once dispatch.

Provider mechanics live behind SPEC-003. A successful write is never proof of
cancellation, and a lost response never permits a second provider write.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.agent.contracts import get_action_contract
from navox.agent.hashing import action_security_hash, canonical_hash
from navox.agent.policy import ActionPolicy
from navox.agent.provenance import record_plan_sources
from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.db.models import (
    Action,
    Approval,
    CancellationAttempt,
    CancellationEvidence,
    Connection,
    ConnectorConnection,
    Merchant,
    Plan,
    PlanStep,
    RecurringObligation,
    RecurringObligationEvidence,
    User,
    WorkspaceMembership,
)
from navox.subscriptions.cancellation_contracts import (
    CancellationPreview,
    CancellationSubmission,
    CancellationTarget,
    CancelSubscriptionCapability,
)
from navox.subscriptions.service import get_subscription, queue_event

EXECUTABLE = {"PROVIDER_API", "CONNECTED_PROVIDER_ACTION", "BROWSER_ASSISTED"}
IN_FLIGHT = {"IN_PROGRESS", "AWAITING_USER", "SUBMITTED", "VERIFICATION_PENDING"}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fingerprint(preview: CancellationPreview) -> str:
    return canonical_hash(preview.model_dump(mode="json", exclude={"inspected_at", "expires_at"}))


def _security_hash(preview: CancellationPreview) -> str:
    return action_security_hash(
        provider="connector",
        action_type="subscription.cancel",
        payload=preview.model_dump(mode="json"),
    )


async def target_for_obligation(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
) -> CancellationTarget:
    obligation = await get_subscription(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        obligation_id=obligation_id,
    )
    merchant = await database.get(Merchant, obligation.merchant_id)
    if merchant is None or (merchant.workspace_id, merchant.user_id) != (workspace_id, user_id):
        raise HTTPException(409, "Subscription merchant ownership is inconsistent")
    return CancellationTarget(
        obligation_id=obligation.id,
        workspace_id=workspace_id,
        user_id=user_id,
        connection_id=obligation.cancellation_connection_id,
        external_resource_id=obligation.cancellation_external_resource_id,
        merchant_id=merchant.id,
        merchant_domain=merchant.website_domain,
        name=obligation.name,
        plan_name=obligation.plan_name,
        billing_amount=obligation.billing_amount,
        billing_currency=obligation.billing_currency,
        billing_interval=obligation.billing_interval,
        billing_interval_count=obligation.interval_count,
        next_renewal_at=_utc(obligation.next_renewal_at) if obligation.next_renewal_at else None,
        auto_renew=obligation.auto_renew,
        revision=str(obligation.revision),
    )


async def _authority(database: AsyncSession, target: CancellationTarget, *, execute: bool) -> None:
    """Same connector -> member -> user -> legacy lock order as SPEC-003."""
    native = None
    if target.connection_id is not None:
        native = await database.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.legacy_connection_id == target.connection_id,
                ConnectorConnection.workspace_id == target.workspace_id,
                ConnectorConnection.user_id == target.user_id,
            )
        )
    if native is not None:
        try:
            native = await owned_connector(
                database,
                connection_id=native.id,
                workspace_id=target.workspace_id,
                user_id=target.user_id,
                require_active=execute,
                lock_authority=True,
            )
        except ConnectorAccessDenied as error:
            raise HTTPException(403, "Cancellation connection is unavailable") from error
    membership = await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == target.workspace_id,
            WorkspaceMembership.user_id == target.user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = await database.scalar(
        select(User)
        .where(User.id == target.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or membership is None:
        raise HTTPException(404, "Subscription not found")
    if execute and user.agent_paused:
        raise HTTPException(409, "NavoX agent execution is paused")
    if execute:
        if native is None or target.connection_id is None or not target.external_resource_id:
            raise HTTPException(409, "Cancellation needs an authorized provider target")
        connection = await database.scalar(
            select(Connection)
            .where(
                Connection.id == target.connection_id,
                Connection.user_id == target.user_id,
                Connection.workspace_id == target.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if connection is None or connection.status != "active":
            raise HTTPException(403, "Cancellation connection is unavailable")
        contract = get_action_contract("subscription.cancel")
        if contract is None or contract.risk_level != "R4":
            raise HTTPException(409, "Cancellation risk contract is unavailable")
        grants = set(native.authorized_capabilities) & set(native.provider_capabilities)
        decision = ActionPolicy().evaluate(
            contract,
            granted_permissions=grants,
            agent_paused=user.agent_paused,
        )
        if not decision.allowed or not decision.requires_approval:
            raise HTTPException(403, "Cancellation capability is not authorized")


async def get_cancellation(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    attempt_id: UUID,
) -> CancellationAttempt:
    attempt = await database.scalar(
        select(CancellationAttempt)
        .where(
            CancellationAttempt.id == attempt_id,
            CancellationAttempt.workspace_id == workspace_id,
            CancellationAttempt.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise HTTPException(404, "Cancellation not found")
    await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=attempt.obligation_id
    )
    return attempt


async def _locked(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    attempt_id: UUID,
    execute: bool,
) -> tuple[CancellationAttempt, RecurringObligation]:
    attempt = await get_cancellation(
        database, workspace_id=workspace_id, user_id=user_id, attempt_id=attempt_id
    )
    preview = CancellationPreview.model_validate(attempt.preview)
    if (preview.target.workspace_id, preview.target.user_id, preview.target.obligation_id) != (
        workspace_id,
        user_id,
        attempt.obligation_id,
    ):
        raise HTTPException(409, "Cancellation target changed")
    await _authority(database, preview.target, execute=execute)
    obligation = await get_subscription(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        obligation_id=attempt.obligation_id,
        lock=True,
    )
    locked_attempt = await database.scalar(
        select(CancellationAttempt)
        .where(
            CancellationAttempt.id == attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_attempt is None:
        raise HTTPException(404, "Cancellation not found")
    return locked_attempt, obligation


async def _records(
    database: AsyncSession, attempt: CancellationAttempt
) -> tuple[Action, Approval, Plan, PlanStep]:
    action = (
        await database.get(Action, attempt.action_id, with_for_update=True)
        if attempt.action_id
        else None
    )
    approval = (
        await database.get(Approval, attempt.approval_id, with_for_update=True)
        if attempt.approval_id
        else None
    )
    if action is None or approval is None or approval.action_id != action.id:
        raise HTTPException(409, "Cancellation approval is unavailable")
    step = await database.get(PlanStep, action.plan_step_id, with_for_update=True)
    plan = await database.get(Plan, step.plan_id, with_for_update=True) if step else None
    if (
        plan is None
        or step is None
        or any(
            (record.workspace_id, record.user_id) != (attempt.workspace_id, attempt.user_id)
            for record in (action, approval, plan)
        )
    ):
        raise HTTPException(409, "Cancellation approval ownership is inconsistent")
    if (
        action.action_type != "subscription.cancel"
        or action.provider != "connector"
        or action.risk_level != "R4"
        or not action.requires_approval
    ):
        raise HTTPException(409, "Cancellation risk contract changed")
    current = action_security_hash(
        provider=action.provider, action_type=action.action_type, payload=action.payload
    )
    if (
        current != action.payload_hash
        or current != approval.action_payload_hash
        or current != attempt.payload_hash
        or current != _security_hash(CancellationPreview.model_validate(attempt.preview))
    ):
        raise HTTPException(409, "Cancellation approval payload changed")
    return action, approval, plan, step


async def prepare_cancellation(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
    request_id: UUID,
    capability: CancelSubscriptionCapability | None,
) -> CancellationAttempt:
    target = await target_for_obligation(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id
    )
    now = datetime.now(UTC)
    preview = (
        await capability.inspect(target)
        if capability
        else CancellationPreview(
            target=target,
            method="UNSUPPORTED",
            provider_revision="unavailable",
            provider_state="unknown",
            expected_effect="UNKNOWN: no authorized cancellation mechanism is available.",
            warnings=[
                "Cancel through your provider account. NavoX has not cancelled this subscription."
            ],
            inspected_at=now,
            expires_at=now + timedelta(minutes=10),
        )
    )
    if (
        preview.target != target
        or _utc(preview.expires_at) <= now
        or _utc(preview.inspected_at) < now - timedelta(minutes=2)
        or _utc(preview.inspected_at) > now + timedelta(seconds=30)
    ):
        raise HTTPException(409, "Cancellation preview is stale or targets another subscription")
    executable = capability is not None and preview.method in EXECUTABLE
    await _authority(database, target, execute=executable)
    obligation = await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id, lock=True
    )
    if (
        await target_for_obligation(
            database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id
        )
        != target
    ):
        raise HTTPException(409, "Subscription changed while preparing cancellation")
    existing: CancellationAttempt | None = await database.scalar(
        select(CancellationAttempt).where(
            CancellationAttempt.workspace_id == workspace_id,
            CancellationAttempt.user_id == user_id,
            CancellationAttempt.request_id == request_id,
        )
    )
    if existing is not None:
        if existing.obligation_id != obligation_id:
            raise HTTPException(409, "Request ID is already bound to another subscription")
        return existing
    existing = await database.scalar(
        select(CancellationAttempt)
        .where(
            CancellationAttempt.workspace_id == workspace_id,
            CancellationAttempt.user_id == user_id,
            CancellationAttempt.obligation_id == obligation_id,
            CancellationAttempt.status.not_in(["FAILED", "ABORTED"]),
        )
        .order_by(CancellationAttempt.requested_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    if obligation.status in {"CANCELLED", "EXPIRED"}:
        raise HTTPException(409, "Subscription is already cancelled or expired")
    attempt = CancellationAttempt(
        id=uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        obligation_id=obligation_id,
        method=preview.method,
        status="AWAITING_CONFIRMATION" if executable else "AWAITING_USER",
        request_id=request_id,
        payload_hash=_security_hash(preview),
        preview=preview.model_dump(mode="json"),
        provider_state_fingerprint=_fingerprint(preview),
        obligation_revision=obligation.revision,
        verification_status="NOT_VERIFIED",
        expires_at=min(_utc(preview.expires_at), now + timedelta(minutes=10)),
        attempt_metadata={"original_status": obligation.status},
    )
    database.add(attempt)
    if executable:
        sources = set(
            await database.scalars(
                select(RecurringObligationEvidence.connection_id).where(
                    RecurringObligationEvidence.obligation_id == obligation_id,
                    RecurringObligationEvidence.connection_id.is_not(None),
                )
            )
        )
        source_ids = {identifier for identifier in sources if identifier is not None}
        if target.connection_id:
            source_ids.add(target.connection_id)
        plan = Plan(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=uuid4(),
            goal=f"Cancel subscription: {obligation.name}",
            status="awaiting_approval",
            planner_version="subscription-cancellation-v1",
            context_snapshot=attempt.preview,
            context_hash=canonical_hash(attempt.preview),
            max_steps=1,
        )
        database.add(plan)
        await database.flush()
        await record_plan_sources(database, plan, set(), explicit_connections=source_ids)
        if not plan.source_attributed:
            raise HTTPException(409, "Subscription sources require review")
        step = PlanStep(
            id=uuid4(),
            plan_id=plan.id,
            sequence_number=1,
            action_type="subscription.cancel",
            description="Execute the exact confirmed cancellation.",
            status="awaiting_approval",
            risk_level="R4",
            input_payload=attempt.preview,
        )
        database.add(step)
        await database.flush()
        action = Action(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            plan_step_id=step.id,
            provider="connector",
            action_type="subscription.cancel",
            risk_level="R4",
            requires_approval=True,
            status="awaiting_approval",
            payload=attempt.preview,
            payload_hash=attempt.payload_hash,
            idempotency_key=f"subscription-cancel:{attempt.id}",
            policy_reason="approval_required",
        )
        database.add(action)
        await database.flush()
        approval = Approval(
            id=uuid4(),
            action_id=action.id,
            user_id=user_id,
            workspace_id=workspace_id,
            action_payload_hash=attempt.payload_hash,
            status="pending",
            expires_at=attempt.expires_at,
        )
        database.add(approval)
        await database.flush()
        attempt.action_id, attempt.approval_id = action.id, approval.id
    await queue_event(
        database,
        obligation,
        "cancellation.requested",
        f"cancel:{attempt.id}:requested",
        {"attempt_id": str(attempt.id)},
    )
    await database.commit()
    return attempt


async def confirm_cancellation(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    attempt_id: UUID,
    request_id: UUID,
    preview_hash: str,
) -> CancellationAttempt:
    attempt, obligation = await _locked(
        database, workspace_id=workspace_id, user_id=user_id, attempt_id=attempt_id, execute=True
    )
    action, approval, plan, step = await _records(database, attempt)
    now = datetime.now(UTC)
    if (
        attempt.status != "AWAITING_CONFIRMATION"
        or approval.status != "pending"
        or approval.consumed_at is not None
    ):
        raise HTTPException(409, "Cancellation confirmation was already used or is unavailable")
    if preview_hash != attempt.payload_hash or obligation.revision != attempt.obligation_revision:
        raise HTTPException(409, "Cancellation preview changed; prepare a new confirmation")
    if _utc(approval.expires_at) <= now:
        raise HTTPException(409, "Cancellation confirmation expired")
    reused = await database.scalar(
        select(Approval.id).where(
            Approval.workspace_id == workspace_id,
            Approval.decision_request_id == request_id,
        )
    )
    if reused is not None:
        raise HTTPException(409, "Confirmation request was already used")
    approval.status, approval.approved_at, approval.decision_request_id = (
        "approved",
        now,
        request_id,
    )
    action.status = "approved"
    plan.status = step.status = "queued"
    attempt.confirmed_at = now
    attempt.status = "REQUESTED"
    obligation.status = "CANCELLATION_REQUESTED"
    await queue_event(
        database,
        obligation,
        "cancellation.confirmed",
        f"cancel:{attempt.id}:confirmed",
        {"attempt_id": str(attempt.id)},
    )
    add_audit_event(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        event_type="cancellation.confirmed",
        entity_type="action",
        entity_id=action.id,
        metadata={"payload_hash": attempt.payload_hash, "risk_level": "R4"},
        actor_type="user",
        actor_id=str(user_id),
    )
    await database.commit()
    return attempt


async def abort_cancellation(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    attempt_id: UUID,
) -> CancellationAttempt:
    attempt, obligation = await _locked(
        database, workspace_id=workspace_id, user_id=user_id, attempt_id=attempt_id, execute=False
    )
    if (
        attempt.status not in {"AWAITING_CONFIRMATION", "REQUESTED", "AWAITING_USER"}
        or attempt.submitted_at is not None
    ):
        raise HTTPException(409, "Submitted cancellation cannot be undone here")
    if attempt.action_id:
        action, approval, plan, step = await _records(database, attempt)
        if approval.consumed_at is not None:
            raise HTTPException(409, "Cancellation dispatch has already started")
        action.status = approval.status = plan.status = step.status = "rejected"
        approval.rejected_at = datetime.now(UTC)
    attempt.status = "ABORTED"
    if obligation.status == "CANCELLATION_REQUESTED":
        obligation.status = str(attempt.attempt_metadata.get("original_status", "UNKNOWN"))
    await database.commit()
    return attempt


async def execute_cancellation(
    database: AsyncSession,
    *,
    attempt_id: UUID,
    capability: CancelSubscriptionCapability,
) -> CancellationAttempt:
    attempt = await database.get(CancellationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Cancellation not found")
    if attempt.status != "REQUESTED" or attempt.confirmed_at is None:
        return attempt
    preview = CancellationPreview.model_validate(attempt.preview)
    fresh = await capability.inspect(preview.target)
    attempt, obligation = await _locked(
        database,
        workspace_id=attempt.workspace_id,
        user_id=attempt.user_id,
        attempt_id=attempt_id,
        execute=True,
    )
    if attempt.status != "REQUESTED":
        return attempt
    action, approval, plan, step = await _records(database, attempt)
    now = datetime.now(UTC)
    current_target = await target_for_obligation(
        database,
        workspace_id=attempt.workspace_id,
        user_id=attempt.user_id,
        obligation_id=obligation.id,
    )
    if (
        approval.status != "approved"
        or approval.consumed_at is not None
        or action.status != "approved"
    ):
        raise HTTPException(409, "Cancellation has no unused exact approval")
    if (
        _utc(approval.expires_at) <= now
        or current_target != preview.target
        or _fingerprint(fresh) != attempt.provider_state_fingerprint
        or _utc(fresh.expires_at) <= now
    ):
        approval.status = "expired"
        action.status = plan.status = step.status = "blocked"
        attempt.status, attempt.failure_code = "FAILED", "preview_changed_or_expired"
        obligation.status = str(attempt.attempt_metadata.get("original_status", "UNKNOWN"))
        await database.commit()
        return attempt
    approval.status, approval.consumed_at = "consuming", now
    action.status = plan.status = step.status = "executing"
    action.started_at = step.started_at = now
    attempt.status = "IN_PROGRESS"
    await database.commit()  # Durable fence BEFORE the provider write.
    try:
        submission = await capability.execute(preview, action.idempotency_key)
    except Exception:
        # A timeout/disconnect may happen after provider acceptance. Never retry.
        submission = CancellationSubmission(status="UNCERTAIN")
    attempt.attempt_metadata = {
        **attempt.attempt_metadata,
        "submission": submission.model_dump(mode="json"),
    }
    attempt.submitted_at = datetime.now(UTC)
    attempt.status = {
        "SUBMITTED": "SUBMITTED",
        "UNCERTAIN": "VERIFICATION_PENDING",
        "AWAITING_USER": "AWAITING_USER",
        "FAILED": "FAILED",
    }[submission.status]
    attempt.verification_status = "VERIFICATION_PENDING"
    action.status = "failed" if submission.status == "FAILED" else "uncertain"
    action.executed_at = attempt.submitted_at
    action.result = {"submission_status": submission.status}
    approval.status = "consumed"
    plan.status = step.status = "manual_review" if submission.status != "FAILED" else "failed"
    obligation.status = "CANCEL_PENDING"
    event = "cancellation.failed" if submission.status == "FAILED" else "cancellation.submitted"
    await queue_event(
        database,
        obligation,
        event,
        f"cancel:{attempt.id}:submitted",
        {"attempt_id": str(attempt.id)},
    )
    await database.commit()
    return attempt


async def verify_cancellation(
    database: AsyncSession,
    *,
    attempt_id: UUID,
    capability: CancelSubscriptionCapability,
) -> CancellationAttempt:
    attempt = await database.get(CancellationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "Cancellation not found")
    if attempt.confirmed_at is None or attempt.status not in IN_FLIGHT | {"VERIFIED_CANCELLED"}:
        return attempt
    preview = CancellationPreview.model_validate(attempt.preview)
    submission = CancellationSubmission.model_validate(
        attempt.attempt_metadata.get("submission", {"status": "UNCERTAIN"})
    )
    verification = await capability.verify(preview, submission)
    attempt, obligation = await _locked(
        database,
        workspace_id=attempt.workspace_id,
        user_id=attempt.user_id,
        attempt_id=attempt_id,
        execute=False,
    )
    action, approval, plan, step = await _records(database, attempt)
    if approval.consumed_at is None or attempt.status not in IN_FLIGHT | {"VERIFIED_CANCELLED"}:
        raise HTTPException(409, "Cancellation was never dispatched")
    now = datetime.now(UTC)
    observed = _utc(verification.observed_at)
    independent = (
        verification.source_type in {"provider_state", "provider_page"}
        and verification.external_resource_id == preview.target.external_resource_id
        and bool(verification.provider_revision)
        and bool(verification.evidence)
        and observed >= _utc(attempt.submitted_at or approval.consumed_at)
        and observed <= now + timedelta(seconds=30)
        and observed >= now - timedelta(minutes=5)
    )
    verified = (
        independent
        and verification.status == "VERIFIED_CANCELLED"
        and verification.evidence.get("state") == "cancelled"
        and verification.evidence.get("auto_renew") is False
    )
    status = verification.status if independent else "VERIFICATION_PENDING"
    if status == "VERIFIED_CANCELLED" and not verified:
        status = "VERIFICATION_PENDING"
    if attempt.status == "VERIFIED_CANCELLED" and status == "VERIFIED_ACTIVE":
        status = "CONTRADICTED"
    database.add(
        CancellationEvidence(
            workspace_id=attempt.workspace_id,
            user_id=attempt.user_id,
            cancellation_attempt_id=attempt.id,
            evidence_type="PROVIDER_STATE" if independent else "UNVERIFIED_SIGNAL",
            connection_id=preview.target.connection_id,
            external_resource_id=verification.external_resource_id,
            verification_status=status,
            confidence=Decimal("1.000") if independent else Decimal("0.000"),
            evidence_metadata=verification.model_dump(mode="json"),
            observed_at=observed,
        )
    )
    attempt.verification_status = status
    obligation.obligation_metadata = {
        **obligation.obligation_metadata,
        "verification_status": status,
    }
    if verified:
        attempt.status, attempt.verified_at = "VERIFIED_CANCELLED", now
        obligation.status, obligation.auto_renew, obligation.last_verified_at = (
            "CANCELLED",
            False,
            now,
        )
        obligation.obligation_metadata = {
            **obligation.obligation_metadata,
            "verification_status": "VERIFIED_CANCELLED",
        }
        action.status, action.verified_at = "completed", now
        plan.status = step.status = "completed"
        plan.completed_at = step.completed_at = now
        event = "cancellation.verified"
    else:
        attempt.status = "VERIFICATION_PENDING"
        if status == "CONTRADICTED":
            obligation.status = "UNKNOWN"
            obligation.obligation_metadata = {
                **obligation.obligation_metadata,
                "verification_status": status,
            }
        event = (
            "cancellation.contradicted"
            if status == "CONTRADICTED"
            else "cancellation.verification_pending"
        )
    await queue_event(
        database,
        obligation,
        event,
        f"cancel:{attempt.id}:{status}:{verification.provider_revision}",
        {"attempt_id": str(attempt.id)},
    )
    await database.commit()
    return attempt


async def lock_cancellation_for_verification(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    attempt_id: UUID,
) -> CancellationAttempt:
    attempt, _ = await _locked(
        database, workspace_id=workspace_id, user_id=user_id, attempt_id=attempt_id, execute=False
    )
    return attempt
