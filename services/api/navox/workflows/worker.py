import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from navox.agent.activities import execute_plan_step_activity, finalize_plan_activity
from navox.approvals.activities import (
    action_authorization_state_activity,
    execute_approved_action_activity,
    mark_execution_uncertain_activity,
)
from navox.core.settings import get_settings
from navox.proactive.activities import (
    commitment_timing_state_activity,
    daily_briefing_delay_activity,
    evaluate_proactive_workspace_activity,
    mark_proactive_workflow_status_activity,
    prepare_meeting_activity,
    record_scheduled_briefing_activity,
)
from navox.workflows.approved_action import ApprovedActionWorkflow
from navox.workflows.foundation import FoundationHeartbeatWorkflow
from navox.workflows.handle_commitment import HandleCommitmentWorkflow
from navox.workflows.proactive import (
    CommitmentLifecycleWorkflow,
    DailyBriefingWorkflow,
    FollowUpWorkflow,
    MeetingPreparationWorkflow,
)


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_target)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[
            FoundationHeartbeatWorkflow,
            HandleCommitmentWorkflow,
            ApprovedActionWorkflow,
            CommitmentLifecycleWorkflow,
            FollowUpWorkflow,
            MeetingPreparationWorkflow,
            DailyBriefingWorkflow,
        ],
        activities=[
            execute_plan_step_activity,
            finalize_plan_activity,
            action_authorization_state_activity,
            execute_approved_action_activity,
            mark_execution_uncertain_activity,
            evaluate_proactive_workspace_activity,
            commitment_timing_state_activity,
            mark_proactive_workflow_status_activity,
            prepare_meeting_activity,
            daily_briefing_delay_activity,
            record_scheduled_briefing_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
