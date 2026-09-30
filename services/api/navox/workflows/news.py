"""News polling runs independently of AI and never dispatches an external action."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.news.activities import (
        ingest_news_source_activity,
        news_clustering_activity,
        news_conversation_activity,
        news_exact_index_activity,
        news_intelligence_activity,
        news_sources_activity,
        prepare_news_conversation_activity,
    )
    from navox.news.jobs import NewsConversationWork, NewsSourceWork, NewsWorkResult


@workflow.defn
class NewsConversationRefreshWorkflow:
    @workflow.run
    async def run(self, payload: NewsConversationWork) -> None:
        if workflow.patched("news-conversation-refresh-v1"):
            await refresh_stale_sources(payload)
        await workflow.execute_activity(
            news_conversation_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@workflow.defn
class NewsSourceIngestionWorkflow:
    @workflow.run
    async def run(self, payload: NewsSourceWork) -> NewsWorkResult:
        result = await workflow.execute_activity(
            ingest_news_source_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        needs_recovery = False
        # Only a payload that explicitly deferred work needs the clustering
        # activity: the flag is recorded in history with the payload, so a
        # pre-patch payload never defers and never reaches this branch.
        if (
            result.status == "COMPLETED"
            and result.deferred
            and workflow.patched("news-semantic-clustering-v1")
        ):
            # Bounded semantic clustering runs before intelligence and always
            # ends with exact indexing, so no freshly ingested item is stranded.
            try:
                clustered = await workflow.execute_activity(
                    news_clustering_activity,
                    payload,
                    start_to_close_timeout=timedelta(minutes=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                needs_recovery = bool(clustered.deferred)
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("News semantic clustering remains unavailable")
                needs_recovery = True
        if needs_recovery and result.deferred and workflow.patched("news-exact-recovery-v1"):
            # A separate, non-paid activity guarantees exact indexing whenever
            # this payload deferred work and the paid path did not finish it.
            try:
                await workflow.execute_activity(
                    news_exact_index_activity,
                    payload,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("News exact recovery deferred to reconciliation")
        if result.status == "COMPLETED" and workflow.patched("news-intelligence-pipeline-v1"):
            try:
                await workflow.execute_activity(
                    news_intelligence_activity,
                    payload,
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("News intelligence remains unavailable")
        return result


async def refresh_stale_sources(payload: NewsConversationWork) -> None:
    """Refresh the stale sources this turn already identified; the answer never waits on it."""
    try:
        sources = await workflow.execute_activity(
            prepare_news_conversation_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
    except ActivityError as error:
        if is_cancelled_exception(error):
            raise
        workflow.logger.warning("News conversation refresh deferred")
        return
    await reconcile_page(sources, deferred="News conversation refresh deferred")


async def reconcile_page(
    sources: list[NewsSourceWork],
    *,
    deferred: str = "News source reconciliation deferred",
) -> None:
    semaphore = asyncio.Semaphore(4)

    async def process(payload: NewsSourceWork) -> None:
        async with semaphore:
            try:
                await workflow.execute_child_workflow(
                    NewsSourceIngestionWorkflow.run,
                    payload,
                    id=f"news-source:{payload.source_id}:{payload.request_id}",
                )
            except Exception as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning(deferred)

    await asyncio.gather(*(process(payload) for payload in sources))


@workflow.defn
class NewsSourceReconciliationWorkflow:
    @workflow.run
    async def run(self, after: str | None = None) -> None:
        for _ in range(50):
            try:
                page = await workflow.execute_activity(
                    news_sources_activity,
                    after,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                await reconcile_page(page.sources)
                after = page.after
                if after is None:
                    await workflow.sleep(timedelta(minutes=1))
            except ActivityError as error:
                if is_cancelled_exception(error):
                    raise
                workflow.logger.warning("News reconciliation temporarily unavailable")
                await workflow.sleep(timedelta(minutes=1))
        workflow.continue_as_new(after)
