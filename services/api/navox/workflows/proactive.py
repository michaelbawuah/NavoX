from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from navox.proactive.activities import (
        CommitmentTimingState,
        ProactiveCommitmentInput,
        ProactiveWorkspaceInput,
        WorkflowStatusInput,
        commitment_timing_state_activity,
        daily_briefing_delay_activity,
        evaluate_proactive_workspace_activity,
        mark_proactive_workflow_status_activity,
        prepare_meeting_activity,
        record_scheduled_briefing_activity,
    )


async def mark_status(
    *,
    entity_type: str,
    entity_id: str,
    workflow_type: str,
    status: str,
) -> str:
    return await workflow.execute_activity(
        mark_proactive_workflow_status_activity,
        WorkflowStatusInput(
            entity_type=entity_type,
            entity_id=entity_id,
            workflow_type=workflow_type,
            status=status,
        ),
        start_to_close_timeout=timedelta(seconds=20),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


async def commitment_state(
    payload: ProactiveCommitmentInput,
) -> CommitmentTimingState:
    return await workflow.execute_activity(
        commitment_timing_state_activity,
        payload,
        start_to_close_timeout=timedelta(seconds=20),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@workflow.defn
class CommitmentLifecycleWorkflow:
    @workflow.run
    async def run(self, payload: ProactiveCommitmentInput) -> str:
        for _ in range(24 * 30):
            state = await commitment_state(payload)
            if state.state != "active":
                return await mark_status(
                    entity_type="commitment",
                    entity_id=payload.commitment_id,
                    workflow_type="commitment_lifecycle",
                    status="completed",
                )
            await workflow.execute_activity(
                evaluate_proactive_workspace_activity,
                ProactiveWorkspaceInput(
                    user_id=payload.user_id,
                    workspace_id=payload.workspace_id,
                    timezone=payload.timezone,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            await workflow.sleep(timedelta(hours=1))
        workflow.continue_as_new(payload)
        return "continued"


@workflow.defn
class FollowUpWorkflow:
    @workflow.run
    async def run(self, payload: ProactiveCommitmentInput) -> str:
        for _ in range(4 * 30):
            state = await commitment_state(payload)
            if state.state != "active" or state.status != "waiting":
                return await mark_status(
                    entity_type="commitment",
                    entity_id=payload.commitment_id,
                    workflow_type="follow_up",
                    status="completed",
                )
            await workflow.execute_activity(
                evaluate_proactive_workspace_activity,
                ProactiveWorkspaceInput(
                    user_id=payload.user_id,
                    workspace_id=payload.workspace_id,
                    timezone=payload.timezone,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            await workflow.sleep(timedelta(hours=6))
        workflow.continue_as_new(payload)
        return "continued"


@workflow.defn
class MeetingPreparationWorkflow:
    @workflow.run
    async def run(self, payload: ProactiveCommitmentInput) -> str:
        while True:
            state = await commitment_state(payload)
            if (
                state.state != "active"
                or state.commitment_type != "meeting"
                or state.due_at is None
            ):
                return await mark_status(
                    entity_type="commitment",
                    entity_id=payload.commitment_id,
                    workflow_type="meeting_preparation",
                    status="completed",
                )

            due_at = datetime.fromisoformat(state.due_at)
            now = workflow.now()
            if due_at <= now:
                return await mark_status(
                    entity_type="commitment",
                    entity_id=payload.commitment_id,
                    workflow_type="meeting_preparation",
                    status="missed",
                )
            prepare_at = due_at - timedelta(hours=1)
            if now >= prepare_at:
                result = await workflow.execute_activity(
                    prepare_meeting_activity,
                    payload,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                await mark_status(
                    entity_type="commitment",
                    entity_id=payload.commitment_id,
                    workflow_type="meeting_preparation",
                    status=result,
                )
                return result
            await workflow.sleep(min(prepare_at - now, timedelta(hours=6)))


@workflow.defn
class DailyBriefingWorkflow:
    @workflow.run
    async def run(self, payload: ProactiveWorkspaceInput) -> str:
        for _ in range(30):
            delay_seconds = await workflow.execute_activity(
                daily_briefing_delay_activity,
                payload,
                start_to_close_timeout=timedelta(seconds=20),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            await workflow.sleep(timedelta(seconds=delay_seconds))
            await workflow.execute_activity(
                record_scheduled_briefing_activity,
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        workflow.continue_as_new(payload)
        return "continued"
