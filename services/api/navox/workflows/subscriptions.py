"""Durable subscription orchestration, carrying identifiers rather than private evidence."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError, is_cancelled_exception
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from navox.subscriptions.activities import (
        SubscriptionState,
        SubscriptionWork,
        cancellation_state_activity,
        execute_cancellation_activity,
        pending_subscriptions_activity,
        reevaluate_subscription_activity,
        verify_cancellation_activity,
    )


async def _evaluate(payload: SubscriptionWork) -> SubscriptionState:
    return await workflow.execute_activity(
        reevaluate_subscription_activity,
        payload,
        start_to_close_timeout=timedelta(minutes=2),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


async def _start_lifecycle(payload: SubscriptionWork, name: str) -> None:
    try:
        await workflow.start_child_workflow(
            name,
            payload,
            id=f"subscription:{name}:{payload.workspace_id}:{payload.user_id}:{payload.obligation_id}:{payload.revision}",
            parent_close_policy=ParentClosePolicy.ABANDON,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
    except WorkflowAlreadyStartedError:
        pass


@workflow.defn
class DiscoverRecurringObligationWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        # Authorized source ingestion atomically persisted evidence and its outbox.
        # This workflow publishes the resulting fact and installs lifecycle timers.
        return await workflow.execute_child_workflow(
            ReevaluateRecurringObligationWorkflow.run,
            payload,
            id=f"subscription-discovery:{workflow.uuid4()}",
        )


@workflow.defn
class ReevaluateRecurringObligationWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        state = await _evaluate(payload)
        if state.status == "active":
            current = SubscriptionWork(
                payload.workspace_id,
                payload.user_id,
                payload.obligation_id,
                revision=state.revision,
            )
            await _start_lifecycle(current, "RenewalLifecycleWorkflow")
            await _start_lifecycle(current, "TrialLifecycleWorkflow")
        return state.status


async def _lifecycle(payload: SubscriptionWork) -> str:
    for _ in range(48):
        state = await _evaluate(payload)
        if state.status != "active":
            return state.status
        await workflow.sleep(timedelta(seconds=max(1, state.delay_seconds)))
    workflow.continue_as_new(payload)
    return "continued"


@workflow.defn
class RenewalLifecycleWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        return await _lifecycle(payload)


@workflow.defn
class TrialLifecycleWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        return await _lifecycle(payload)


@workflow.defn
class PriceChangeWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        return (await _evaluate(payload)).status


async def _cancellation_state(payload: SubscriptionWork) -> SubscriptionState:
    return await workflow.execute_activity(
        cancellation_state_activity,
        payload,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@workflow.defn
class CancelSubscriptionWorkflow:
    def __init__(self) -> None:
        self._wake = False

    @workflow.signal
    def wake(self) -> None:
        # Signals only expedite a fresh database check; they grant no authority.
        self._wake = True

    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        for _ in range(120):
            state = await _cancellation_state(payload)
            if state.status == "awaiting_confirmation":
                self._wake = False
                try:
                    await workflow.wait_condition(lambda: self._wake, timeout=timedelta(seconds=30))
                except TimeoutError:
                    pass
                continue
            if state.status == "confirmed":
                try:
                    await workflow.execute_activity(
                        execute_cancellation_activity,
                        payload,
                        start_to_close_timeout=timedelta(minutes=2),
                        # A failed reply cannot authorize another provider write.
                        # The database IN_PROGRESS fence directs recovery to reads.
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                except ActivityError as error:
                    if is_cancelled_exception(error):
                        raise
                    state = await _cancellation_state(payload)
                    if state.status == "confirmed":
                        raise
                return await workflow.execute_child_workflow(
                    VerifyCancellationWorkflow.run,
                    payload,
                    id=f"subscription-verify:{payload.attempt_id}:{workflow.uuid4()}",
                )
            if state.status == "verification_pending":
                return await workflow.execute_child_workflow(
                    VerifyCancellationWorkflow.run,
                    payload,
                    id=f"subscription-verify:{payload.attempt_id}:{workflow.uuid4()}",
                )
            return state.status
        workflow.continue_as_new(payload)
        return "continued"


@workflow.defn
class VerifyCancellationWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        for _ in range(8):
            state = await _cancellation_state(payload)
            if state.status != "verification_pending":
                return state.status
            if state.delay_seconds:
                await workflow.sleep(timedelta(seconds=state.delay_seconds))
            try:
                await workflow.execute_activity(
                    verify_cancellation_activity,
                    payload,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                # A durable read budget was reserved before I/O. Recheck state
                # and its deadline instead of hammering a temporarily unavailable provider.
                workflow.logger.warning("Cancellation verification deferred")
        return "review_required"


@workflow.defn
class CancellationContradictionWorkflow:
    @workflow.run
    async def run(self, payload: SubscriptionWork) -> str:
        await _evaluate(payload)
        return await workflow.execute_child_workflow(
            VerifyCancellationWorkflow.run,
            payload,
            id=f"subscription-contradiction:{payload.attempt_id}:{workflow.uuid4()}",
        )


async def _recover(payload: SubscriptionWork) -> None:
    try:
        if payload.attempt_id is not None:
            # Completed verification workflows can be restarted after a new
            # contradiction. Durable attempt counters prevent replaying old reads.
            await workflow.start_child_workflow(
                CancelSubscriptionWorkflow.run,
                payload,
                id=f"subscription-cancel:{payload.workspace_id}:{payload.user_id}:{payload.attempt_id}",
                parent_close_policy=ParentClosePolicy.ABANDON,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            )
        else:
            await workflow.execute_child_workflow(
                ReevaluateRecurringObligationWorkflow.run,
                payload,
                id=f"subscription-reconcile:{workflow.uuid4()}",
            )
    except WorkflowAlreadyStartedError:
        pass
    except Exception as error:
        if is_cancelled_exception(error):
            raise
        workflow.logger.warning("Subscription recovery deferred")


@workflow.defn
class SubscriptionReconciliationWorkflow:
    @workflow.run
    async def run(self) -> None:
        for _ in range(100):
            try:
                pending = await workflow.execute_activity(
                    pending_subscriptions_activity,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                semaphore = asyncio.Semaphore(4)

                async def recover_one(
                    payload: SubscriptionWork, limiter: asyncio.Semaphore = semaphore
                ) -> None:
                    async with limiter:
                        await _recover(payload)

                await asyncio.gather(*(recover_one(payload) for payload in pending))
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Subscription reconciliation unavailable")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
