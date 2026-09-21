import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from navox.core.settings import get_settings
from navox.workflows.foundation import FoundationHeartbeatWorkflow


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_target)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[FoundationHeartbeatWorkflow],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

