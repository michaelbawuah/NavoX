import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

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
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(seconds=60),
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
        start_to_close_timeout=timedelta(minutes=5),
        heartbeat_timeout=timedelta(seconds=60),
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


async def _refresh_workspace(workspace: WorkspaceWork) -> None:
    try:
        await workflow.execute_child_workflow(
            ReevaluateCommitmentWorkflow.run,
            workspace,
            id=f"intelligence-state:{workflow.uuid4()}",
        )
    except Exception as error:
        if is_cancelled_exception(error):
            raise
        workflow.logger.warning("Workspace refresh deferred")


async def _process_pending(payload: SourceWork) -> None:
    try:
        await workflow.execute_child_workflow(
            ProcessSourceEventWorkflow.run,
            payload,
            id=f"intelligence-reconcile:{workflow.uuid4()}",
        )
    except Exception as error:
        if is_cancelled_exception(error):
            raise
        workflow.logger.warning("Intelligence batch deferred to next reconciliation")


async def _pending_sources() -> list[SourceWork]:
    return await workflow.execute_activity(
        pending_intelligence_activity,
        start_to_close_timeout=timedelta(minutes=10),
        heartbeat_timeout=timedelta(seconds=60),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


async def _reconcile_batch(workspaces: list[WorkspaceWork], pending: list[SourceWork]) -> None:
    # A slow account may consume one slot, but must not block every other account.
    semaphore = asyncio.Semaphore(4)

    async def refresh_one(workspace: WorkspaceWork) -> None:
        async with semaphore:
            await _refresh_workspace(workspace)

    async def process_one(payload: SourceWork) -> None:
        async with semaphore:
            await _process_pending(payload)

    await asyncio.gather(
        *(refresh_one(workspace) for workspace in workspaces),
        *(process_one(payload) for payload in pending),
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
                if workflow.patched("intelligence-reconciliation-concurrency-v1"):
                    await _reconcile_batch(workspaces, await _pending_sources())
                else:
                    # Preserve command order while replaying pre-upgrade histories.
                    for workspace in workspaces:
                        await _refresh_workspace(workspace)
                    for payload in await _pending_sources():
                        await _process_pending(payload)
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("Intelligence reconciliation temporarily unavailable")
            await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new()
