"""Identifier-only durable check and recovery activities."""

from dataclasses import dataclass
from uuid import UUID

from temporalio import activity

from navox.core.settings import get_settings
from navox.db.session import get_session_factory
from navox.thread_watches.service import check_watch, pending_watches


@dataclass(frozen=True)
class ThreadWatchWork:
    watch_id: str
    revision: int


@activity.defn
async def check_thread_watch_activity(payload: ThreadWatchWork) -> str:
    async with get_session_factory()() as database:
        return await check_watch(
            database,
            watch_id=UUID(payload.watch_id),
            revision=payload.revision,
            settings=get_settings(),
        )


@activity.defn
async def pending_thread_watches_activity() -> list[ThreadWatchWork]:
    async with get_session_factory()() as database:
        return [
            ThreadWatchWork(str(row.id), row.revision) for row in await pending_watches(database)
        ]
