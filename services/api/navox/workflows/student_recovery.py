"""One-minute ciphertext cleanup, independent of feature/AI/provider flags."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.student_recovery.activities import purge_expired_recovery_previews_activity


@workflow.defn
class StudentRecoveryCleanupWorkflow:
    @workflow.run
    async def run(self) -> str:
        for _ in range(480):
            try:
                await workflow.execute_activity(
                    purge_expired_recovery_previews_activity,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Recovery receipt cleanup deferred")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
        return "continued"
