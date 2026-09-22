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
from navox.workflows.approved_action import ApprovedActionWorkflow
from navox.workflows.foundation import FoundationHeartbeatWorkflow
from navox.workflows.handle_commitment import HandleCommitmentWorkflow


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
        ],
        activities=[
            execute_plan_step_activity,
            finalize_plan_activity,
            action_authorization_state_activity,
            execute_approved_action_activity,
            mark_execution_uncertain_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
