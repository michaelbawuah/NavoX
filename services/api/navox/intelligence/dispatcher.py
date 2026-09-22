from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from navox.core.settings import Settings
from navox.intelligence.jobs import SourceWork, WorkspaceWork
from navox.workflows.intelligence import FeedbackLearningWorkflow, ProcessSourceEventWorkflow


async def dispatch_source(payload: SourceWork, *, settings: Settings, request_id: str) -> str:
    workflow_id = f"intelligence:{payload.connection_id}:{payload.source}:{request_id}"
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            ProcessSourceEventWorkflow.run,
            payload,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id


async def dispatch_feedback(payload: WorkspaceWork, *, settings: Settings, request_id: str) -> str:
    workflow_id = f"intelligence-feedback:{payload.workspace_id}:{request_id}"
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            FeedbackLearningWorkflow.run,
            payload,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id
