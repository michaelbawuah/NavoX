"""Dedicated durable Vox cleanup process, independent of call admission switches.

Run ``python -m navox.vox.worker_reconcile`` in an API image with database and
qualified LiveKit Cloud configuration. Disabling new calls must not stop this
process while any cleanup ledger entry remains outstanding.
"""

import asyncio
import signal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.core.settings import get_settings
from navox.db.session import get_database_engine, get_session_factory
from navox.memory.history import sweep_expired_history
from navox.vox.provider import LiveKitCloudProvider, cloud_config
from navox.vox.reconcile import MediaCleaner, reconcile_one


async def run_cleanup(
    factory: async_sessionmaker[AsyncSession], cleaner: MediaCleaner, stop: asyncio.Event
) -> None:
    """Bounded one-at-a-time cleanup; database/provider failures never lose claims."""
    next_retention_sweep = 0.0
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        if loop.time() >= next_retention_sweep:
            try:
                await sweep_expired_history(factory)
            except Exception:
                # Retry independently of Cloud cleanup. Never log private data.
                pass
            next_retention_sweep = loop.time() + 60
        try:
            completed = await reconcile_one(factory, cleaner)
        except Exception:
            # A failed DB operation rolls back on session close. A provider call
            # has its own bounded timeout; never print private exception bodies.
            completed = False
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.05 if completed else 0.5)
        except TimeoutError:
            pass


async def main() -> None:
    settings = get_settings()
    provider = LiveKitCloudProvider(cloud_config(settings, require_release=False))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for item in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(item, stop.set)
    try:
        await run_cleanup(get_session_factory(), provider, stop)
    finally:
        await get_database_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
