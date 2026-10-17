"""Best-effort immediate start; worker reconciliation recovers the saved opt-in."""

from uuid import UUID, uuid4

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from navox.core.settings import Settings
from navox.thread_watches.activities import ThreadWatchWork
from navox.thread_watches.workflows import (
    ThreadWatchCheckWorkflow,
    ThreadWatchReconciliationWorkflow,
)

RECONCILIATION_WORKFLOW_ID = "navox-thread-watch-reconciliation-v1"


async def dispatch_thread_watch_reconciliation(*, settings: Settings) -> str:
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            ThreadWatchReconciliationWorkflow.run,
            id=RECONCILIATION_WORKFLOW_ID,
            task_queue=settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        pass
    return RECONCILIATION_WORKFLOW_ID


async def dispatch_thread_watch(*, watch_id: UUID, revision: int, settings: Settings) -> str:
    client = await Client.connect(settings.temporal_target)
    workflow_id = f"thread-watch:{watch_id}:{revision}:{uuid4()}"
    await client.start_workflow(
        ThreadWatchCheckWorkflow.run,
        ThreadWatchWork(str(watch_id), revision),
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )
    return workflow_id
