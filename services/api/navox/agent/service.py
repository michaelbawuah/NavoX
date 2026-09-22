from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.agent.context import ContextBuilder
from navox.agent.contracts import get_action_contract
from navox.agent.planner import DeterministicPlanner, MAX_PLAN_STEPS
from navox.db.models import Commitment, Plan, PlanStep, User

HANDLEABLE_STATUSES = {"confirmed", "waiting", "attention"}


class AgentPlanError(ValueError):
    pass


class AgentPlanConflictError(AgentPlanError):
    pass


class AgentPlanNotFoundError(AgentPlanError):
    pass


class AgentPausedError(AgentPlanError):
    pass


class BoundedAgentService:
    def __init__(
        self,
        *,
        context_builder: ContextBuilder | None = None,
        planner: DeterministicPlanner | None = None,
    ) -> None:
        self.context_builder = context_builder or ContextBuilder()
        self.planner = planner or DeterministicPlanner()

    async def create_plan(
        self,
        database: AsyncSession,
        *,
        user_id: UUID,
        workspace_id: UUID,
        commitment_id: UUID,
        request_id: UUID,
        goal: str | None,
    ) -> tuple[Plan, list[PlanStep], bool]:
        existing = await database.scalar(
            select(Plan).where(
                Plan.workspace_id == workspace_id,
                Plan.user_id == user_id,
                Plan.request_id == request_id,
            )
        )
        if existing is not None:
            if existing.commitment_id != commitment_id:
                raise AgentPlanConflictError(
                    "That request ID is already bound to another commitment"
                )
            steps = list(
                await database.scalars(
                    select(PlanStep)
                    .where(PlanStep.plan_id == existing.id)
                    .order_by(PlanStep.sequence_number)
                )
            )
            return existing, steps, False

        user = await database.scalar(
            select(User).where(User.id == user_id)
        )
        if user is None:
            raise AgentPlanNotFoundError("User not found")
        if user.agent_paused:
            raise AgentPausedError("NavoX agent execution is paused")

        commitment = await database.scalar(
            select(Commitment).where(
                Commitment.id == commitment_id,
                Commitment.user_id == user_id,
                Commitment.workspace_id == workspace_id,
            )
        )
        if commitment is None:
            raise AgentPlanNotFoundError("Commitment not found")
        if commitment.status not in HANDLEABLE_STATUSES:
            raise AgentPlanConflictError(
                "Only confirmed, waiting, or attention commitments can be handled"
            )

        context = await self.context_builder.build(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            commitment_id=commitment_id,
        )
        resolved_goal = goal or f"Handle {commitment.title}"
        draft = self.planner.plan(context, resolved_goal)
        if len(draft.steps) > MAX_PLAN_STEPS:
            raise AgentPlanError("Plan exceeds the hard step limit")

        plan = Plan(
            user_id=user_id,
            workspace_id=workspace_id,
            objective_id=commitment.objective_id,
            commitment_id=commitment.id,
            request_id=request_id,
            goal=draft.goal,
            status="queued",
            planner_version=self.planner.version,
            context_snapshot=context.canonical_payload(),
            context_hash=context.content_hash(),
            max_steps=MAX_PLAN_STEPS,
            replan_count=0,
        )
        database.add(plan)
        await database.flush()

        steps: list[PlanStep] = []
        for sequence, planned in enumerate(draft.steps, start=1):
            contract = get_action_contract(planned.action_type)
            if contract is None:
                raise AgentPlanError(f"Unknown action contract: {planned.action_type}")
            step = PlanStep(
                plan_id=plan.id,
                sequence_number=sequence,
                action_type=planned.action_type,
                description=planned.description,
                status="pending",
                risk_level=contract.risk_level,
                input_payload=planned.input_payload,
                output_payload={},
            )
            database.add(step)
            steps.append(step)

        add_audit_event(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            event_type="agent.plan.created",
            entity_type="plan",
            entity_id=plan.id,
            metadata={
                "commitment_id": str(commitment.id),
                "planner_version": self.planner.version,
                "step_count": len(steps),
                "context_hash": plan.context_hash,
            },
            actor_type="user",
            actor_id=str(user_id),
        )
        try:
            await database.commit()
        except IntegrityError:
            await database.rollback()
            existing = await database.scalar(
                select(Plan).where(
                    Plan.workspace_id == workspace_id,
                    Plan.user_id == user_id,
                    Plan.request_id == request_id,
                )
            )
            if existing is None or existing.commitment_id != commitment_id:
                raise
            existing_steps = list(
                await database.scalars(
                    select(PlanStep)
                    .where(PlanStep.plan_id == existing.id)
                    .order_by(PlanStep.sequence_number)
                )
            )
            return existing, existing_steps, False

        return plan, steps, True
