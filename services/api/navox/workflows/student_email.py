"""One-minute private email receipt cleanup; never reads mail or calls a model."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.student_email.activities import cleanup_email_reviews_activity


@workflow.defn
class StudentEmailCleanupWorkflow:
    @workflow.run
    async def run(self) -> str:
        for _ in range(480):
            try:
                await workflow.execute_activity(
                    cleanup_email_reviews_activity,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Email review cleanup deferred")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
        return "continued"
