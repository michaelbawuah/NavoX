"""Best-effort dispatch; durable database outboxes are recovered by the worker."""

from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from navox.core.settings import Settings
from navox.subscriptions.activities import SubscriptionWork
from navox.workflows.subscriptions import (
    CancelSubscriptionWorkflow,
    SubscriptionReconciliationWorkflow,
)


async def dispatch_subscription_reconciliation(*, settings: Settings) -> str:
    workflow_id = "navox-subscription-reconciliation-v1"
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            SubscriptionReconciliationWorkflow.run,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        pass
    return workflow_id


async def dispatch_cancellation(
    *,
    attempt_id: UUID,
    obligation_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    settings: Settings,
) -> str:
    workflow_id = f"subscription-cancel:{workspace_id}:{user_id}:{attempt_id}"
    client = await Client.connect(settings.temporal_target)
    try:
        await client.start_workflow(
            CancelSubscriptionWorkflow.run,
            SubscriptionWork(str(workspace_id), str(user_id), str(obligation_id), str(attempt_id)),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        await client.get_workflow_handle(workflow_id).signal(CancelSubscriptionWorkflow.wake)
    return workflow_id
