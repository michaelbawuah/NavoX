from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from navox.approvals.activities import (
        action_authorization_state_activity,
        execute_approved_action_activity,
        mark_execution_uncertain_activity,
    )


@dataclass(frozen=True)
class ApprovedActionInput:
    action_id: str


@workflow.defn
class ApprovedActionWorkflow:
    """Durably wait for an exact-action decision, then execute at most once."""

    def __init__(self) -> None:
        self._decision_signal: str | None = None

    @workflow.signal
    async def approval_decision(self, decision: str) -> None:
        self._decision_signal = decision

    @workflow.run
    async def run(self, payload: ApprovedActionInput) -> str:
        state_retry = RetryPolicy(maximum_attempts=3)
        safe_pre_execution_failures = 0

        while True:
            state = await workflow.execute_activity(
                action_authorization_state_activity,
                payload.action_id,
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=state_retry,
            )
            if state == "approved":
                try:
                    return await workflow.execute_activity(
                        execute_approved_action_activity,
                        payload.action_id,
                        start_to_close_timeout=timedelta(seconds=45),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                except Exception:
                    # Re-check persisted state before deciding whether another
                    # invocation is safe. If the action crossed into executing,
                    # never retry the provider request.
                    post_failure_state = await workflow.execute_activity(
                        action_authorization_state_activity,
                        payload.action_id,
                        start_to_close_timeout=timedelta(seconds=20),
                        retry_policy=state_retry,
                    )
                    if post_failure_state == "approved":
                        safe_pre_execution_failures += 1
                        if safe_pre_execution_failures < 3:
                            continue
                        return "failed_before_execution"
                    if post_failure_state == "executing":
                        return await workflow.execute_activity(
                            mark_execution_uncertain_activity,
                            payload.action_id,
                            start_to_close_timeout=timedelta(seconds=20),
                            retry_policy=state_retry,
                        )
                    return post_failure_state

            if state == "executing":
                return await workflow.execute_activity(
                    mark_execution_uncertain_activity,
                    payload.action_id,
                    start_to_close_timeout=timedelta(seconds=20),
                    retry_policy=state_retry,
                )
            if state in {
                "completed",
                "uncertain",
                "rejected",
                "expired",
                "blocked",
                "failed",
                "missing",
                "missing_approval",
            }:
                return state

            self._decision_signal = None
            try:
                await workflow.wait_condition(
                    lambda: self._decision_signal is not None,
                    timeout=timedelta(seconds=30),
                )
            except TimeoutError:
                pass
