from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from navox.agent.activities import (
        execute_plan_step_activity,
        finalize_plan_activity,
    )


@dataclass(frozen=True)
class HandleCommitmentInput:
    plan_id: str
    step_ids: list[str]


@workflow.defn
class HandleCommitmentWorkflow:
    """Durably execute an already-persisted bounded plan."""

    @workflow.run
    async def run(self, payload: HandleCommitmentInput) -> str:
        if len(payload.step_ids) > 8:
            raise ValueError("Plan exceeds the hard eight-step execution limit")

        retry_policy = RetryPolicy(maximum_attempts=3)
        for step_id in payload.step_ids:
            result = await workflow.execute_activity(
                execute_plan_step_activity,
                step_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
            if result != "completed":
                break

        return await workflow.execute_activity(
            finalize_plan_activity,
            payload.plan_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry_policy,
        )
