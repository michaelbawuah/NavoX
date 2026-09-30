"""Bounded, identifier-only knowledge workflows. No provider or DB work in replay."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.knowledge.activities import (
        knowledge_delete_activity,
        knowledge_embedding_activity,
        knowledge_pending_embeddings_activity,
        knowledge_reindex_activity,
        knowledge_resource_activity,
        knowledge_source_page_activity,
    )
    from navox.knowledge.jobs import KnowledgeEmbeddingWork, KnowledgeResourceWork


async def _resource(payload: KnowledgeResourceWork) -> str:
    return await workflow.execute_activity(
        knowledge_resource_activity,
        payload,
        start_to_close_timeout=timedelta(minutes=2),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@workflow.defn
class KnowledgeResourceIngestWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeResourceWork) -> str:
        return await _resource(payload)


@workflow.defn
class KnowledgeResourceUpdateWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeResourceWork) -> str:
        return await _resource(payload)


@workflow.defn
class KnowledgeResourceDeleteWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeResourceWork) -> str:
        return await workflow.execute_activity(
            knowledge_delete_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn
class KnowledgeEmbeddingWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeEmbeddingWork) -> str:
        return await workflow.execute_activity(
            knowledge_embedding_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn
class KnowledgeEntityResolutionWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeResourceWork) -> str:
        # Exact source identities only; resource activity refreshes graph too.
        return await _resource(payload)


@workflow.defn
class KnowledgePermissionRefreshWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeResourceWork) -> str:
        return await _resource(payload)


@workflow.defn
class KnowledgeReindexWorkflow:
    @workflow.run
    async def run(self, payload: KnowledgeResourceWork) -> str:
        return await workflow.execute_activity(
            knowledge_reindex_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn
class KnowledgeReconciliationWorkflow:
    @workflow.run
    async def run(self, after: str | None = None) -> None:
        # Cursor survives history rollover; a large workspace cannot starve later IDs.
        for _ in range(100):
            page = await workflow.execute_activity(
                knowledge_source_page_activity,
                after,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            for payload in page.resources:
                try:
                    state = await _resource(payload)
                    if state == "INDEXED":
                        embeddings = await workflow.execute_activity(
                            knowledge_pending_embeddings_activity,
                            payload,
                            start_to_close_timeout=timedelta(minutes=1),
                            retry_policy=RetryPolicy(maximum_attempts=3),
                        )
                        for embedding in embeddings:
                            result = await workflow.execute_activity(
                                knowledge_embedding_activity,
                                embedding,
                                start_to_close_timeout=timedelta(minutes=2),
                                retry_policy=RetryPolicy(maximum_attempts=3),
                            )
                            if result in {"UNAVAILABLE", "QUOTA_EXCEEDED"}:
                                break
                except ActivityError as error:
                    if is_cancelled_exception(error):
                        raise
                    workflow.logger.warning("Knowledge projection deferred to reconciliation")
            after = page.after
            if after is None:
                await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new(after)
