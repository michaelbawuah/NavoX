"""Optional memory worker. Default-off; no native-provider or unpriced fallback."""

import asyncio
import signal

from navox.core.settings import get_settings
from navox.db.session import get_database_engine, get_session_factory
from navox.memory.learning import sweep_learning_jobs
from navox.memory.learning_runtime import process_one


async def main() -> None:
    settings, factory, stop = get_settings(), get_session_factory(), asyncio.Event()
    loop = asyncio.get_running_loop()
    for item in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(item, stop.set)
    try:
        while not stop.is_set():
            try:
                await sweep_learning_jobs(factory)
                completed = await process_one(factory, settings)
            except Exception:
                completed = False  # Never print private worker/provider exception bodies.
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.1 if completed else 5)
            except TimeoutError:
                pass
    finally:
        await get_database_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
