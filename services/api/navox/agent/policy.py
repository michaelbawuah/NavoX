from dataclasses import dataclass

from navox.agent.contracts import ActionContract

MILESTONE_5_AUTOMATIC_RISKS = {"R0", "R1"}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    requires_approval: bool


class ActionPolicy:
    """Deterministic policy boundary. Model/planner output cannot set risk."""

    def evaluate(
        self,
        contract: ActionContract,
        *,
        granted_permissions: set[str],
        agent_paused: bool,
    ) -> PolicyDecision:
        if agent_paused:
            return PolicyDecision(False, "agent_paused", False)

        missing = sorted(set(contract.required_permissions) - granted_permissions)
        if missing:
            return PolicyDecision(
                False,
                "missing_permissions:" + ",".join(missing),
                contract.risk_level not in MILESTONE_5_AUTOMATIC_RISKS,
            )

        if contract.risk_level not in MILESTONE_5_AUTOMATIC_RISKS:
            return PolicyDecision(True, "approval_required", True)

        if not contract.executable_in_milestone5:
            return PolicyDecision(False, "executor_unavailable", False)

        return PolicyDecision(True, "automatic_safe_action", False)
