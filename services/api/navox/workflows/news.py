"""News polling runs independently of AI and never dispatches an external action."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from navox.news.activities import (
        ingest_news_source_activity,
        news_conversation_activity,
        news_sources_activity,
    )
    from navox.news.jobs import NewsConversationWork, NewsSourceWork, NewsWorkResult


@workflow.defn
class NewsConversationRefreshWorkflow:
    @workflow.run
    async def run(self, payload: NewsConversationWork) -> None:
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
        return await workflow.execute_activity(
            ingest_news_source_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


async def reconcile_page(sources: list[NewsSourceWork]) -> None:
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
                workflow.logger.warning("News source reconciliation deferred")

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
