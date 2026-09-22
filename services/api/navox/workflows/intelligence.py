from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from navox.intelligence.activities import (
        intelligence_workspaces_activity,
        pending_intelligence_activity,
        process_source_activity,
        refresh_intelligence_activity,
    )
    from navox.intelligence.jobs import SourceWork, WorkspaceWork


@workflow.defn
class ProcessSourceEventWorkflow:
    @workflow.run
    async def run(self, payload: SourceWork) -> int:
        count = await workflow.execute_activity(
            process_source_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=10), maximum_attempts=3),
        )
        await workflow.execute_child_workflow(
            TodayRefreshWorkflow.run,
            WorkspaceWork(payload.user_id, payload.workspace_id),
            id=f"intelligence-today:{workflow.uuid4()}",
        )
        return count


async def refresh(payload: WorkspaceWork) -> int:
    return await workflow.execute_activity(
        refresh_intelligence_activity,
        payload,
        start_to_close_timeout=timedelta(seconds=60),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@workflow.defn
class ReevaluateCommitmentWorkflow:
    @workflow.run
    async def run(self, payload: WorkspaceWork) -> int:
        return await workflow.execute_child_workflow(
            AttentionEvaluationWorkflow.run,
            payload,
            id=f"intelligence-attention:{workflow.uuid4()}",
        )


@workflow.defn
class AttentionEvaluationWorkflow:
    @workflow.run
    async def run(self, payload: WorkspaceWork) -> int:
        return await refresh(payload)


@workflow.defn
class TodayRefreshWorkflow:
    @workflow.run
    async def run(self, payload: WorkspaceWork) -> int:
        return await refresh(payload)


@workflow.defn
class FeedbackLearningWorkflow:
    @workflow.run
    async def run(self, payload: WorkspaceWork) -> int:
        # Preferences are updated transactionally by idempotent feedback; rerank only.
        return await workflow.execute_child_workflow(
            AttentionEvaluationWorkflow.run,
            payload,
            id=f"intelligence-feedback-attention:{workflow.uuid4()}",
        )


@workflow.defn
class IntelligenceReconciliationWorkflow:
    @workflow.run
    async def run(self) -> None:
        for _ in range(100):
            try:
                workspaces = await workflow.execute_activity(
                    intelligence_workspaces_activity,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                for workspace in workspaces:
                    try:
                        await workflow.execute_child_workflow(
                            ReevaluateCommitmentWorkflow.run,
                            workspace,
                            id=f"intelligence-state:{workflow.uuid4()}",
                        )
                    except Exception:
                        workflow.logger.warning("Workspace refresh deferred")
                pending = await workflow.execute_activity(
                    pending_intelligence_activity,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                for payload in pending:
                    try:
                        await workflow.execute_child_workflow(
                            ProcessSourceEventWorkflow.run,
                            payload,
                            id=f"intelligence-reconcile:{workflow.uuid4()}",
                        )
                    except Exception:
                        workflow.logger.warning(
                            "Intelligence batch deferred to next reconciliation"
                        )
            except ActivityError:
                workflow.logger.warning("Intelligence reconciliation temporarily unavailable")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
