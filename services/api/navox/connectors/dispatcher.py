from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from navox.connectors.jobs import (
    ConnectorDisconnectWork,
    ConnectorHealthWork,
    ConnectorSyncWork,
)
from navox.core.settings import Settings
from navox.workflows.connectors import (
    ConnectorDisconnectWorkflow,
    ConnectorHealthWorkflow,
    ConnectorIncrementalSyncWorkflow,
    ConnectorInitialSyncWorkflow,
)


async def dispatch_connector_sync(
    payload: ConnectorSyncWork,
    *,
    settings: Settings,
    initial: bool = False,
) -> str:
    prefix = "connector-initial" if initial else "connector-sync"
    workflow_id = f"{prefix}:{payload.connection_id}:{payload.request_id}"
    client = await Client.connect(settings.temporal_target)
    workflow = ConnectorInitialSyncWorkflow if initial else ConnectorIncrementalSyncWorkflow
    try:
        await client.start_workflow(
            workflow.run,
            payload,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id


async def dispatch_connector_health(
    payload: ConnectorHealthWork,
    *,
    settings: Settings,
) -> str:
    workflow_id = f"connector-health:{payload.connection_id}"
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            ConnectorHealthWorkflow.run,
            payload,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id


async def dispatch_connector_disconnect(
    payload: ConnectorDisconnectWork,
    *,
    settings: Settings,
    request_id: str,
) -> str:
    workflow_id = f"connector-disconnect:{payload.connection_id}:{request_id}"
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            ConnectorDisconnectWorkflow.run,
            payload,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id
