import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.agent.contracts import ActionContract, get_action_contract
from navox.agent.policy import ActionPolicy
from navox.db.models import (
    Action,
    Commitment,
    Connection,
    Plan,
    PlanStep,
    User,
    WorkflowRef,
)


def payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


async def granted_permissions(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID
) -> set[str]:
    connections = list(
        await database.scalars(
            select(Connection).where(
                Connection.user_id == user_id,
                Connection.workspace_id == workspace_id,
                Connection.status == "active",
            )
        )
    )
    permissions: set[str] = set()
    for connection in connections:
        permissions.update(connection.granted_scopes)
    return permissions


def _new_action(
    *,
    plan: Plan,
    step: PlanStep,
    contract: ActionContract | None,
) -> Action:
    risk_level = contract.risk_level if contract is not None else "R5"
    provider = contract.provider if contract is not None else "unknown"
    return Action(
        user_id=plan.user_id,
        workspace_id=plan.workspace_id,
        plan_step_id=step.id,
        commitment_id=plan.commitment_id,
        provider=provider,
        action_type=step.action_type,
        risk_level=risk_level,
        requires_approval=risk_level not in {"R0", "R1"},
        status="pending",
        payload=step.input_payload,
        payload_hash=payload_hash(step.input_payload),
        idempotency_key=f"plan-step:{step.id}",
        result={},
    )


async def _block(
    database: AsyncSession,
    *,
    plan: Plan,
    step: PlanStep,
    action: Action,
    reason: str,
) -> str:
    now = datetime.now(UTC)
    action.status = "blocked"
    action.policy_reason = reason
    action.executed_at = now
    step.status = "blocked"
    step.completed_at = now
    plan.status = "blocked"
    plan.error_code = reason[:64]
    plan.completed_at = now
    add_audit_event(
        database,
        user_id=plan.user_id,
        workspace_id=plan.workspace_id,
        event_type="agent.action.blocked",
        entity_type="action",
        entity_id=action.id,
        metadata={
            "plan_id": str(plan.id),
            "plan_step_id": str(step.id),
            "action_type": step.action_type,
            "reason": reason,
        },
    )
    await database.commit()
    return "blocked"


async def _execute_internal(
    database: AsyncSession,
    *,
    plan: Plan,
    step: PlanStep,
) -> dict[str, object]:
    context = cast(dict[str, object], plan.context_snapshot)
    commitment_context = cast(dict[str, object], context.get("commitment", {}))
    expected_context_hash = step.input_payload.get("context_hash")
    if expected_context_hash != plan.context_hash:
        raise ValueError("context_hash_mismatch")

    if step.action_type == "navox.commitment.inspect":
        if plan.commitment_id is None:
            raise ValueError("commitment_missing")
        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == plan.commitment_id,
                Commitment.user_id == plan.user_id,
                Commitment.workspace_id == plan.workspace_id,
            )
        )
        if commitment is None:
            raise ValueError("commitment_missing")
        return {
            "commitment_id": str(commitment.id),
            "type": commitment.commitment_type,
            "status": commitment.status,
            "priority": commitment.priority,
            "due_at": commitment.due_at.isoformat() if commitment.due_at is not None else None,
            "verified_from": "postgresql",
        }

    if step.action_type == "navox.context.prepare":
        sources = cast(list[object], context.get("sources", []))
        relations = cast(list[object], context.get("relations", []))
        capabilities = cast(list[object], context.get("capabilities", []))
        return {
            "goal": plan.goal,
            "commitment": {
                "id": commitment_context.get("id"),
                "type": commitment_context.get("type"),
                "title": commitment_context.get("title"),
                "status": commitment_context.get("status"),
                "due_at": commitment_context.get("due_at"),
            },
            "source_count": len(sources),
            "relation_count": len(relations),
            "capability_count": len(capabilities),
            "context_hash": plan.context_hash,
        }

    if step.action_type == "navox.next_steps.prepare":
        mode = step.input_payload.get("mode")
        status = str(commitment_context.get("status", "unknown"))
        due_at = commitment_context.get("due_at")
        suggestions: list[str] = []
        if status == "waiting":
            suggestions.append("Check whether the expected response or dependency has arrived.")
        if mode == "meeting_brief":
            suggestions.extend(
                [
                    "Review the saved meeting objective and outstanding commitments.",
                    "Prepare questions, decisions, and follow-ups before the meeting.",
                ]
            )
        elif mode == "renewal_checklist":
            suggestions.extend(
                [
                    "Review the renewal date and confirm whether the service is still needed.",
                    "Prepare a renewal-or-cancel decision before any external change.",
                ]
            )
        elif mode == "follow_up_options":
            suggestions.extend(
                [
                    "Review the latest saved context before following up.",
                    "Prepare a concise follow-up; sending remains a separate approved action.",
                ]
            )
        elif mode == "promise_follow_through":
            suggestions.append("Prepare the promised deliverable or a status update.")
        elif mode == "deadline_checklist":
            suggestions.append("Break the deadline into the smallest verifiable remaining steps.")
        else:
            suggestions.append("Identify the next verifiable step that advances this commitment.")
        if due_at is not None:
            suggestions.append("Re-check the saved due time before acting.")
        return {
            "mode": mode,
            "suggestions": suggestions[:5],
            "external_actions_executed": False,
        }

    raise ValueError("executor_unavailable")


async def execute_plan_step(database: AsyncSession, step_id: UUID) -> str:
    step = await database.scalar(select(PlanStep).where(PlanStep.id == step_id))
    if step is None:
        return "failed"
    plan = await database.scalar(select(Plan).where(Plan.id == step.plan_id))
    if plan is None:
        return "failed"

    if step.status == "completed":
        return "completed"
    if plan.status in {"completed", "blocked", "failed"}:
        return plan.status

    contract = get_action_contract(step.action_type)
    action = await database.scalar(select(Action).where(Action.plan_step_id == step.id))
    if action is None:
        action = _new_action(plan=plan, step=step, contract=contract)
        database.add(action)
        await database.flush()

    current_payload_hash = payload_hash(step.input_payload)
    if action.payload_hash != current_payload_hash:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason="payload_hash_mismatch",
        )
    if contract is None:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason="unknown_action_contract",
        )
    if step.risk_level != contract.risk_level or action.risk_level != contract.risk_level:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason="risk_contract_mismatch",
        )

    user = await database.scalar(select(User).where(User.id == plan.user_id))
    if user is None:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason="user_missing",
        )
    permissions = await granted_permissions(
        database, user_id=plan.user_id, workspace_id=plan.workspace_id
    )
    decision = ActionPolicy().evaluate(
        contract,
        granted_permissions=permissions,
        agent_paused=user.agent_paused,
    )
    action.requires_approval = decision.requires_approval
    action.policy_reason = decision.reason
    if not decision.allowed:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason=decision.reason,
        )
    if decision.requires_approval:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason="approval_required_milestone_6",
        )

    now = datetime.now(UTC)
    plan.status = "running"
    plan.error_code = None
    step.status = "running"
    step.started_at = step.started_at or now
    action.status = "running"
    action.started_at = action.started_at or now
    await database.flush()

    try:
        result = await _execute_internal(database, plan=plan, step=step)
    except ValueError as error:
        return await _block(
            database,
            plan=plan,
            step=step,
            action=action,
            reason=str(error),
        )

    finished = datetime.now(UTC)
    step.output_payload = result
    step.status = "completed"
    step.completed_at = finished
    action.result = result
    action.status = "completed"
    action.executed_at = finished
    add_audit_event(
        database,
        user_id=plan.user_id,
        workspace_id=plan.workspace_id,
        event_type="agent.action.completed",
        entity_type="action",
        entity_id=action.id,
        metadata={
            "plan_id": str(plan.id),
            "plan_step_id": str(step.id),
            "action_type": step.action_type,
            "risk_level": contract.risk_level,
            "verification_method": contract.verification_method,
        },
    )
    await database.commit()
    return "completed"


async def finalize_plan(database: AsyncSession, plan_id: UUID) -> str:
    plan = await database.scalar(select(Plan).where(Plan.id == plan_id))
    if plan is None:
        return "failed"
    steps = list(
        await database.scalars(
            select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.sequence_number)
        )
    )
    if not steps:
        status = "failed"
        error_code = "no_plan_steps"
    elif all(step.status == "completed" for step in steps):
        status = "completed"
        error_code = None
    elif any(step.status == "blocked" for step in steps):
        status = "blocked"
        error_code = plan.error_code or "step_blocked"
    elif any(step.status == "failed" for step in steps):
        status = "failed"
        error_code = plan.error_code or "step_failed"
    else:
        status = "running"
        error_code = None

    previous = plan.status
    plan.status = status
    plan.error_code = error_code
    if status in {"completed", "blocked", "failed"}:
        plan.completed_at = plan.completed_at or datetime.now(UTC)

    workflow_ref = await database.scalar(
        select(WorkflowRef).where(
            WorkflowRef.entity_type == "plan",
            WorkflowRef.entity_id == plan.id,
            WorkflowRef.workflow_type == "handle_commitment",
        )
    )
    if workflow_ref is not None:
        workflow_ref.status = status

    if previous != status and status in {"completed", "blocked", "failed"}:
        add_audit_event(
            database,
            user_id=plan.user_id,
            workspace_id=plan.workspace_id,
            event_type=f"agent.plan.{status}",
            entity_type="plan",
            entity_id=plan.id,
            metadata={"step_count": len(steps), "error_code": error_code},
        )
    await database.commit()
    return status
