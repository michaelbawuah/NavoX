"""Durable metadata checks, independent of AI availability and grants."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from navox.thread_watches.activities import (
        ThreadWatchWork,
        check_thread_watch_activity,
        pending_thread_watches_activity,
    )


@workflow.defn
class ThreadWatchCheckWorkflow:
    @workflow.run
    async def run(self, payload: ThreadWatchWork) -> str:
        return await workflow.execute_activity(
            check_thread_watch_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )


async def _run_one(item: ThreadWatchWork, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        try:
            await workflow.execute_child_workflow(
                ThreadWatchCheckWorkflow.run,
                item,
                id=f"thread-watch:{item.watch_id}:{item.revision}:{workflow.uuid4()}",
                parent_close_policy=ParentClosePolicy.ABANDON,
            )
        except Exception as error:
            if is_cancelled_exception(error):
                raise
            workflow.logger.warning("Thread watch deferred to next reconciliation")


@workflow.defn
class ThreadWatchReconciliationWorkflow:
    @workflow.run
    async def run(self) -> None:
        # DB next_check_at is the durable outbox. No account is scanned without opt-in.
        for _ in range(500):
            try:
                pending = await workflow.execute_activity(
                    pending_thread_watches_activity,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                semaphore = asyncio.Semaphore(4)
                await asyncio.gather(*(_run_one(item, semaphore) for item in pending))
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Thread watch checks deferred to next reconciliation")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
