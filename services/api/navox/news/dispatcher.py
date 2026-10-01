import asyncio

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from navox.core.settings import Settings
from navox.news.jobs import NewsConversationWork
from navox.workflows.news import NewsConversationRefreshWorkflow


async def dispatch_news_conversation(settings: Settings, payload: NewsConversationWork) -> None:
    async with asyncio.timeout(10):
        client = await Client.connect(settings.temporal_target)
        try:
            await client.start_workflow(
                NewsConversationRefreshWorkflow.run,
                payload,
                id=f"news-conversation:{payload.turn_id}",
                task_queue=settings.temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
