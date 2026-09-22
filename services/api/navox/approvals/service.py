from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.agent.hashing import action_security_hash, canonical_hash
from navox.approvals.schemas import EditGmailSendRequest, PrepareGmailSendRequest
from navox.db.models import (
    Action,
    Approval,
    Commitment,
    Connection,
    Plan,
    PlanStep,
    User,
)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
APPROVAL_TTL = timedelta(minutes=15)
PREPARABLE_COMMITMENT_STATUSES = {"confirmed", "waiting", "attention"}


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ApprovalServiceError(ValueError):
    pass


class ApprovalNotFoundError(ApprovalServiceError):
    pass


class ApprovalConflictError(ApprovalServiceError):
    pass


class ApprovalPermissionError(ApprovalServiceError):
    pass


class ApprovalPausedError(ApprovalServiceError):
    pass


def gmail_payload(
    *,
    connection: Connection,
    to: str,
    subject: str,
    body_text: str,
    post_send_state: str,
) -> dict[str, object]:
    return {
        "connection_id": str(connection.id),
        "sender": connection.external_email,
        "to": to,
        "subject": subject,
        "body_text": body_text,
        "post_send_state": post_send_state,
    }


def ensure_same_prepare_request(action: Action, request: PrepareGmailSendRequest) -> None:
    expected = {
        "connection_id": str(request.connection_id),
        "to": str(request.to),
        "subject": request.subject,
        "body_text": request.body_text,
        "post_send_state": request.post_send_state,
    }
    actual = {
        "connection_id": action.payload.get("connection_id"),
        "to": action.payload.get("to"),
        "subject": action.payload.get("subject"),
        "body_text": action.payload.get("body_text"),
        "post_send_state": action.payload.get("post_send_state"),
    }
    if actual != expected:
        raise ApprovalConflictError(
            "That request ID is already bound to a different Gmail send payload"
        )


async def scoped_action(
    database: AsyncSession,
    *,
    action_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
) -> Action:
    action = await database.scalar(
        select(Action).where(
            Action.id == action_id,
            Action.user_id == user_id,
            Action.workspace_id == workspace_id,
        )
    )
    if action is None:
        raise ApprovalNotFoundError("Action not found")
    return action


async def latest_approval(database: AsyncSession, action_id: UUID) -> Approval | None:
    return await database.scalar(
        select(Approval)
        .where(Approval.action_id == action_id)
        .order_by(Approval.version.desc())
        .limit(1)
    )


async def plan_for_action(database: AsyncSession, action: Action) -> tuple[Plan, PlanStep]:
    step = await database.scalar(select(PlanStep).where(PlanStep.id == action.plan_step_id))
    if step is None:
        raise ApprovalNotFoundError("Plan step not found")
    plan = await database.scalar(select(Plan).where(Plan.id == step.plan_id))
    if plan is None:
        raise ApprovalNotFoundError("Plan not found")
    return plan, step


class ApprovalService:
    async def prepare_gmail_send(
        self,
        database: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: UUID,
        commitment_id: UUID,
        request: PrepareGmailSendRequest,
    ) -> tuple[Action, Approval, bool]:
        existing_plan = await database.scalar(
            select(Plan).where(
                Plan.user_id == user_id,
                Plan.workspace_id == workspace_id,
                Plan.request_id == request.request_id,
            )
        )
        if existing_plan is not None:
            if existing_plan.commitment_id != commitment_id:
                raise ApprovalConflictError(
                    "That request ID is already bound to another commitment"
                )
            existing_step = await database.scalar(
                select(PlanStep).where(PlanStep.plan_id == existing_plan.id)
            )
            if existing_step is None:
                raise ApprovalConflictError("Existing approval plan is incomplete")
            existing_action = await database.scalar(
                select(Action).where(Action.plan_step_id == existing_step.id)
            )
            if existing_action is None:
                raise ApprovalConflictError("Existing approval action is incomplete")
            existing_approval = await latest_approval(database, existing_action.id)
            if existing_approval is None:
                raise ApprovalConflictError("Existing approval record is incomplete")
            ensure_same_prepare_request(existing_action, request)
            return existing_action, existing_approval, False

        user = await database.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise ApprovalNotFoundError("User not found")
        if user.agent_paused:
            raise ApprovalPausedError("NavoX agent execution is paused")

        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == commitment_id,
                Commitment.user_id == user_id,
                Commitment.workspace_id == workspace_id,
            )
        )
        if commitment is None:
            raise ApprovalNotFoundError("Commitment not found")
        if commitment.status not in PREPARABLE_COMMITMENT_STATUSES:
            raise ApprovalConflictError(
                "Only active confirmed commitments can prepare external actions"
            )

        connection = await database.scalar(
            select(Connection).where(
                Connection.id == request.connection_id,
                Connection.user_id == user_id,
                Connection.workspace_id == workspace_id,
                Connection.provider == "google",
                Connection.status == "active",
            )
        )
        if connection is None:
            raise ApprovalNotFoundError("Google connection not found")
        if GMAIL_SEND_SCOPE not in connection.granted_scopes:
            raise ApprovalPermissionError("Gmail send permission is required")
        if not connection.external_email:
            raise ApprovalConflictError("Google connection is missing a verified sender")

        payload = gmail_payload(
            connection=connection,
            to=str(request.to),
            subject=request.subject,
            body_text=request.body_text,
            post_send_state=request.post_send_state,
        )
        security_hash = action_security_hash(
            provider="google",
            action_type="gmail.send",
            payload=payload,
        )
        context_snapshot = {
            "commitment": {
                "id": str(commitment.id),
                "type": commitment.commitment_type,
                "title": commitment.title,
                "status": commitment.status,
            },
            "connection": {
                "id": str(connection.id),
                "provider": "google",
                "sender": connection.external_email,
            },
        }
        plan = Plan(
            user_id=user_id,
            workspace_id=workspace_id,
            objective_id=commitment.objective_id,
            commitment_id=commitment.id,
            request_id=request.request_id,
            goal=f"Send approved email for {commitment.title}",
            status="awaiting_approval",
            planner_version="approval-v1",
            context_snapshot=context_snapshot,
            context_hash=canonical_hash(context_snapshot),
            max_steps=1,
            replan_count=0,
        )
        database.add(plan)
        await database.flush()

        step = PlanStep(
            plan_id=plan.id,
            sequence_number=1,
            action_type="gmail.send",
            description="Send the exact approved email through Gmail.",
            status="awaiting_approval",
            risk_level="R3",
            input_payload=payload,
            output_payload={},
        )
        database.add(step)
        await database.flush()

        action = Action(
            user_id=user_id,
            workspace_id=workspace_id,
            plan_step_id=step.id,
            commitment_id=commitment.id,
            provider="google",
            action_type="gmail.send",
            risk_level="R3",
            requires_approval=True,
            status="awaiting_approval",
            payload=payload,
            payload_hash=security_hash,
            idempotency_key=f"gmail-send:{request.request_id}",
            result={},
            policy_reason="approval_required",
        )
        database.add(action)
        await database.flush()

        approval = Approval(
            action_id=action.id,
            user_id=user_id,
            workspace_id=workspace_id,
            version=1,
            action_payload_hash=security_hash,
            status="pending",
            expires_at=datetime.now(UTC) + APPROVAL_TTL,
        )
        database.add(approval)
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="approval.action.prepared",
            entity_type="action",
            entity_id=action.id,
            metadata={
                "commitment_id": str(commitment.id),
                "action_type": action.action_type,
                "risk_level": action.risk_level,
                "payload_hash": security_hash,
                "approval_version": 1,
            },
            actor_type="user",
            actor_id=str(user_id),
        )
        try:
            await database.commit()
        except IntegrityError:
            await database.rollback()
            existing_plan = await database.scalar(
                select(Plan).where(
                    Plan.user_id == user_id,
                    Plan.workspace_id == workspace_id,
                    Plan.request_id == request.request_id,
                )
            )
            if existing_plan is None:
                raise
            existing_step = await database.scalar(
                select(PlanStep).where(PlanStep.plan_id == existing_plan.id)
            )
            if existing_step is None:
                raise
            existing_action = await database.scalar(
                select(Action).where(Action.plan_step_id == existing_step.id)
            )
            if existing_action is None:
                raise
            existing_approval = await latest_approval(database, existing_action.id)
            if existing_approval is None:
                raise
            ensure_same_prepare_request(existing_action, request)
            return existing_action, existing_approval, False

        return action, approval, True

    async def approve(
        self,
        database: AsyncSession,
        *,
        action_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        request_id: UUID,
    ) -> tuple[Action, Approval]:
        action = await scoped_action(
            database,
            action_id=action_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        user = await database.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise ApprovalNotFoundError("User not found")
        if user.agent_paused:
            raise ApprovalPausedError("NavoX agent execution is paused")

        decision_reuse = await database.scalar(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.decision_request_id == request_id,
            )
        )
        if decision_reuse is not None:
            if decision_reuse.action_id != action.id:
                raise ApprovalConflictError(
                    "That decision request ID is already bound to another action"
                )
            if decision_reuse.status not in {"approved", "consuming", "consumed"}:
                raise ApprovalConflictError(
                    "That decision request ID was already used for a different decision"
                )
            return action, decision_reuse

        approval = await latest_approval(database, action.id)
        if approval is None:
            raise ApprovalNotFoundError("Approval not found")
        now = datetime.now(UTC)
        if approval.status == "approved":
            return action, approval
        if approval.status != "pending":
            raise ApprovalConflictError(f"Approval is {approval.status}")
        if aware(approval.expires_at) <= now:
            await self._expire(database, action, approval)
            raise ApprovalConflictError("Approval expired")
        if approval.action_payload_hash != action.payload_hash:
            raise ApprovalConflictError("Action changed since approval was prepared")

        approval.status = "approved"
        approval.approved_at = now
        approval.decision_request_id = request_id
        action.status = "approved"
        plan, step = await plan_for_action(database, action)
        plan.status = "approved"
        step.status = "approved"
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="approval.action.approved",
            entity_type="action",
            entity_id=action.id,
            metadata={
                "payload_hash": action.payload_hash,
                "approval_version": approval.version,
            },
            actor_type="user",
            actor_id=str(user_id),
        )
        await database.commit()
        return action, approval

    async def reject(
        self,
        database: AsyncSession,
        *,
        action_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        request_id: UUID,
    ) -> tuple[Action, Approval]:
        action = await scoped_action(
            database,
            action_id=action_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        decision_reuse = await database.scalar(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.decision_request_id == request_id,
            )
        )
        if decision_reuse is not None:
            if decision_reuse.action_id != action.id:
                raise ApprovalConflictError(
                    "That decision request ID is already bound to another action"
                )
            if decision_reuse.status != "rejected":
                raise ApprovalConflictError(
                    "That decision request ID was already used for a different decision"
                )
            return action, decision_reuse

        approval = await latest_approval(database, action.id)
        if approval is None:
            raise ApprovalNotFoundError("Approval not found")
        if approval.status == "rejected":
            return action, approval
        if approval.status != "pending":
            raise ApprovalConflictError(f"Approval is {approval.status}")

        now = datetime.now(UTC)
        approval.status = "rejected"
        approval.rejected_at = now
        approval.decision_request_id = request_id
        action.status = "rejected"
        plan, step = await plan_for_action(database, action)
        plan.status = "rejected"
        plan.completed_at = now
        step.status = "rejected"
        step.completed_at = now
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="approval.action.rejected",
            entity_type="action",
            entity_id=action.id,
            metadata={"approval_version": approval.version},
            actor_type="user",
            actor_id=str(user_id),
        )
        await database.commit()
        return action, approval

    async def edit_gmail_send(
        self,
        database: AsyncSession,
        *,
        action_id: UUID,
        user_id: UUID,
        workspace_id: UUID,
        request: EditGmailSendRequest,
    ) -> tuple[Action, Approval]:
        action = await scoped_action(
            database,
            action_id=action_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        if action.action_type != "gmail.send":
            raise ApprovalConflictError("Only Gmail send actions can be edited here")
        approval = await latest_approval(database, action.id)
        if approval is None:
            raise ApprovalNotFoundError("Approval not found")
        if action.status != "awaiting_approval" or approval.status != "pending":
            raise ApprovalConflictError("Only pending actions can be edited")

        connection_id = action.payload.get("connection_id")
        if not isinstance(connection_id, str):
            raise ApprovalConflictError("Action connection is invalid")
        connection = await database.scalar(
            select(Connection).where(
                Connection.id == UUID(connection_id),
                Connection.user_id == user_id,
                Connection.workspace_id == workspace_id,
                Connection.provider == "google",
            )
        )
        if connection is None:
            raise ApprovalNotFoundError("Google connection not found")

        payload = gmail_payload(
            connection=connection,
            to=str(request.to),
            subject=request.subject,
            body_text=request.body_text,
            post_send_state=request.post_send_state,
        )
        new_hash = action_security_hash(
            provider=action.provider,
            action_type=action.action_type,
            payload=payload,
        )
        now = datetime.now(UTC)
        approval.status = "superseded"
        approval.superseded_at = now

        new_approval = Approval(
            action_id=action.id,
            user_id=user_id,
            workspace_id=workspace_id,
            version=approval.version + 1,
            action_payload_hash=new_hash,
            status="pending",
            expires_at=now + APPROVAL_TTL,
        )
        database.add(new_approval)
        action.payload = payload
        action.payload_hash = new_hash
        action.policy_reason = "approval_required"
        plan, step = await plan_for_action(database, action)
        step.input_payload = payload
        step.status = "awaiting_approval"
        plan.status = "awaiting_approval"
        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="approval.action.edited",
            entity_type="action",
            entity_id=action.id,
            metadata={
                "previous_version": approval.version,
                "approval_version": new_approval.version,
                "payload_hash": new_hash,
            },
            actor_type="user",
            actor_id=str(user_id),
        )
        await database.commit()
        return action, new_approval

    async def expire_if_needed(
        self,
        database: AsyncSession,
        action: Action,
        approval: Approval,
    ) -> bool:
        if approval.status == "pending" and aware(approval.expires_at) <= datetime.now(UTC):
            await self._expire(database, action, approval)
            return True
        return False

    async def _expire(
        self,
        database: AsyncSession,
        action: Action,
        approval: Approval,
    ) -> None:
        now = datetime.now(UTC)
        approval.status = "expired"
        action.status = "expired"
        plan, step = await plan_for_action(database, action)
        plan.status = "expired"
        plan.completed_at = now
        step.status = "expired"
        step.completed_at = now
        add_audit_event(
            database,
            user_id=action.user_id,
            workspace_id=action.workspace_id,
            event_type="approval.action.expired",
            entity_type="action",
            entity_id=action.id,
            metadata={"approval_version": approval.version},
        )
        await database.commit()
