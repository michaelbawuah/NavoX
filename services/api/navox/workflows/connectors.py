import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.connectors.activities import (
        connector_disconnect_activity,
        connector_health_activity,
        connector_reconciliation_activity,
        connector_subscription_activity,
        connector_subscription_reconciliation_activity,
        connector_sync_activity,
    )
    from navox.connectors.jobs import (
        ConnectorDisconnectWork,
        ConnectorHealthWork,
        ConnectorSyncWork,
    )


async def _sync(payload: ConnectorSyncWork) -> int:
    return await workflow.execute_activity(
        connector_sync_activity,
        payload,
        start_to_close_timeout=timedelta(hours=2),
        heartbeat_timeout=(
            timedelta(seconds=60) if workflow.patched("connector-sync-heartbeats-v1") else None
        ),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=10),
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=5,
        ),
    )


@workflow.defn
class ConnectorInitialSyncWorkflow:
    @workflow.run
    async def run(self, payload: ConnectorSyncWork) -> int:
        return await _sync(payload)


@workflow.defn
class ConnectorIncrementalSyncWorkflow:
    @workflow.run
    async def run(self, payload: ConnectorSyncWork) -> int:
        return await _sync(payload)


@workflow.defn
class ConnectorHealthWorkflow:
    @workflow.run
    async def run(self, payload: ConnectorHealthWork) -> str:
        return await workflow.execute_activity(
            connector_health_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn
class ConnectorSubscriptionWorkflow:
    @workflow.run
    async def run(self, payload: ConnectorHealthWork) -> str:
        return await workflow.execute_activity(
            connector_subscription_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn
class ConnectorDisconnectWorkflow:
    @workflow.run
    async def run(self, payload: ConnectorDisconnectWork) -> str:
        return await workflow.execute_activity(
            connector_disconnect_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


async def _run_one(payload: ConnectorSyncWork, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        try:
            await workflow.execute_child_workflow(
                ConnectorIncrementalSyncWorkflow.run,
                payload,
                id=(
                    f"connector-reconcile:{payload.connection_id}:{workflow.uuid4()}"
                    if workflow.patched("connector-recovery-reconcile-v1")
                    else f"connector-reconcile:{payload.connection_id}:{payload.request_id}"
                ),
            )
        except Exception as error:
            if is_cancelled_exception(error):
                raise
            workflow.logger.warning("Connector sync deferred to next reconciliation")


async def _run_subscription(payload: ConnectorHealthWork, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        try:
            await workflow.execute_child_workflow(
                ConnectorSubscriptionWorkflow.run,
                payload,
                id=f"connector-subscription:{payload.connection_id}:{workflow.uuid4()}",
            )
        except Exception as error:
            if is_cancelled_exception(error):
                raise
            workflow.logger.warning("Connector subscription deferred to next reconciliation")


@workflow.defn
class ConnectorReconciliationWorkflow:
    @workflow.run
    async def run(self) -> None:
        for _ in range(100):
            try:
                pending = await workflow.execute_activity(
                    connector_reconciliation_activity,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                semaphore = asyncio.Semaphore(4)
                await asyncio.gather(*(_run_one(item, semaphore) for item in pending))
                if workflow.patched("connector-subscriptions-v1"):
                    subscriptions = await workflow.execute_activity(
                        connector_subscription_reconciliation_activity,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    await asyncio.gather(
                        *(_run_subscription(item, semaphore) for item in subscriptions)
                    )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Connector reconciliation temporarily unavailable")
            await workflow.sleep(timedelta(minutes=5))
        workflow.continue_as_new()
