from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.routines.activities import (
        RoutineTickInput,
        reconcile_routines_activity,
        routine_failure_activity,
        routine_tick_activity,
    )


@workflow.defn
class PersonalRoutineWorkflow:
    @workflow.run
    async def run(self, payload: RoutineTickInput) -> str:
        for _ in range(480):
            try:
                result = await workflow.execute_activity(
                    routine_tick_activity,
                    payload,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError:
                return await workflow.execute_activity(
                    routine_failure_activity,
                    payload,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            if result.state == "terminal":
                return "stopped"
            await workflow.sleep(timedelta(seconds=result.delay_seconds))
        workflow.continue_as_new(payload)
        return "continued"


@workflow.defn
class PersonalRoutineReconciliationWorkflow:
    @workflow.run
    async def run(self) -> str:
        for _ in range(480):
            try:
                await workflow.execute_activity(
                    reconcile_routines_activity,
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Routine recovery deferred to next reconciliation")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
        return "continued"
