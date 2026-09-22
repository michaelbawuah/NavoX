from dataclasses import dataclass

from navox.agent.context import AgentContext
from navox.agent.contracts import get_action_contract

MAX_PLAN_STEPS = 8
MAX_REPLANS = 2
PLANNER_VERSION = "deterministic-v1"


@dataclass(frozen=True)
class PlannedStep:
    action_type: str
    description: str
    input_payload: dict[str, object]


@dataclass(frozen=True)
class PlanDraft:
    goal: str
    steps: tuple[PlannedStep, ...]


class PlanningError(ValueError):
    pass


class DeterministicPlanner:
    """Bounded planner for the pre-model Milestone 5 execution boundary."""

    version = PLANNER_VERSION

    def plan(self, context: AgentContext, goal: str) -> PlanDraft:
        normalized_goal = " ".join(goal.split()).strip()
        if not normalized_goal:
            raise PlanningError("Plan goal cannot be empty")
        if len(normalized_goal) > 512:
            raise PlanningError("Plan goal is too long")

        commitment = context.commitment
        preparation_mode = {
            "meeting": "meeting_brief",
            "renewal": "renewal_checklist",
            "follow_up": "follow_up_options",
            "promise": "promise_follow_through",
            "deadline": "deadline_checklist",
            "task": "execution_checklist",
        }.get(commitment.type, "execution_checklist")

        steps = (
            PlannedStep(
                action_type="navox.commitment.inspect",
                description="Inspect the saved commitment and current lifecycle state.",
                input_payload={
                    "commitment_id": str(commitment.id),
                    "context_hash": context.content_hash(),
                },
            ),
            PlannedStep(
                action_type="navox.context.prepare",
                description="Prepare a bounded context brief from saved NavoX state.",
                input_payload={
                    "commitment_id": str(commitment.id),
                    "context_hash": context.content_hash(),
                },
            ),
            PlannedStep(
                action_type="navox.next_steps.prepare",
                description="Prepare safe next-step options without external side effects.",
                input_payload={
                    "commitment_id": str(commitment.id),
                    "context_hash": context.content_hash(),
                    "mode": preparation_mode,
                },
            ),
        )

        if len(steps) > MAX_PLAN_STEPS:
            raise PlanningError("Plan exceeds the hard step limit")
        for step in steps:
            if get_action_contract(step.action_type) is None:
                raise PlanningError(f"Unknown action contract: {step.action_type}")

        return PlanDraft(goal=normalized_goal, steps=steps)
