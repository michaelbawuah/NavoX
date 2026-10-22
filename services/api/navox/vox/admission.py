"""Cookie-authorized provisioning; external work always has a durable cleanup intent."""

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.db.vox import VoxCall
from navox.db.vox_media import VoxMediaConnection
from navox.vox import media, service
from navox.vox import provision as attempts
from navox.vox.media import ConnectionIntent
from navox.vox.provider import LiveKitCloudProvider, MediaUnavailable

# A disconnected HTTP client must not cancel the trusted completion/fencing step.
# Tasks are retained until completion; crashes are recovered by the durable outbox.
_PROVISIONS: set[asyncio.Task[ConnectionIntent]] = set()
_CLEANUPS: set[asyncio.Task[None]] = set()
_ACTIVE: dict[UUID, tuple[asyncio.Task[ConnectionIntent], asyncio.Event]] = {}


async def _retire(
    factory: async_sessionmaker[AsyncSession],
    intent: ConnectionIntent,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
) -> None:
    try:
        async with factory() as database:
            await media.revoke_connection(
                database, user_id, workspace_id, session_hash, intent.connection_id
            )
    except service.VoxError:
        # The trusted finalizer below remains authoritative after logout/erasure.
        pass
    async with factory() as database:
        try:
            await media.finalize_provision(database, intent)
        except service.VoxError:
            pass


async def provision(
    factory: async_sessionmaker[AsyncSession],
    provider: LiveKitCloudProvider,
    intent: ConnectionIntent,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
) -> ConnectionIntent:
    existing = _ACTIVE.get(intent.connection_id)
    abandoned = existing[1] if existing is not None else asyncio.Event()

    async def complete() -> ConnectionIntent:
        async with factory() as database:
            attempt_id = await attempts.begin_attempt(database, intent)
        try:
            if attempt_id is not None:
                try:
                    await provider.provision(intent)
                except BaseException:
                    async with factory() as database:
                        await attempts.finish_attempt(
                            database, intent.connection_id, attempt_id, acknowledged=False
                        )
                    raise
                async with factory() as database:
                    await attempts.finish_attempt(
                        database, intent.connection_id, attempt_id, acknowledged=True
                    )
            async with factory() as database:
                confirmed = await media.finalize_provision(database, intent)
            if abandoned.is_set():
                raise MediaUnavailable("The Vox connection has ended")
            return confirmed
        except BaseException:
            # Provider timeouts have uncertain external outcomes. Retirement is
            # required even if no browser token was returned or owned rows vanished.
            await _retire(factory, intent, user_id, workspace_id, session_hash)
            raise

    if existing is not None:
        # Exact request retries in this API process share the same external work.
        # The worker's database claim additionally fences jobs across API replicas.
        task = existing[0]
    else:
        task = asyncio.create_task(complete())
        _PROVISIONS.add(task)
        _ACTIVE[intent.connection_id] = task, abandoned

        def finished(done: asyncio.Task[ConnectionIntent]) -> None:
            _PROVISIONS.discard(done)
            if _ACTIVE.get(intent.connection_id, (None,))[0] is done:
                _ACTIVE.pop(intent.connection_id, None)
            if not done.cancelled():
                # Consume background exceptions without logging tokens/provider bodies.
                done.exception()

        task.add_done_callback(finished)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        abandoned.set()
        # If completion already returned, explicitly retire that late success too.
        if task.done() and not task.cancelled() and task.exception() is None:
            cleanup = asyncio.create_task(
                _retire(factory, intent, user_id, workspace_id, session_hash)
            )
            _CLEANUPS.add(cleanup)

            def cleaned(done: asyncio.Task[None]) -> None:
                _CLEANUPS.discard(done)
                if not done.cancelled():
                    done.exception()

            cleanup.add_done_callback(cleaned)
        raise


async def bound_connection(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    client_call_id: UUID,
    connection_id: UUID,
    generation: int,
) -> None:
    """Reject a swapped URL/generation before confirming or closing a connection.

    This lookup grants no authority. The media operation independently locks and
    rereads the current User, web session, ownership and expiry boundary.
    """
    match = await database.scalar(
        select(VoxMediaConnection.id)
        .join(VoxCall, VoxCall.id == VoxMediaConnection.call_id)
        .where(
            VoxMediaConnection.id == connection_id,
            VoxMediaConnection.user_id == user_id,
            VoxMediaConnection.workspace_id == workspace_id,
            VoxMediaConnection.generation == generation,
            VoxCall.client_call_id == client_call_id,
        )
    )
    if match is None:
        raise service.VoxError(404, "Media connection not found")
