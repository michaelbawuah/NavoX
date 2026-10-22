"""Commit before external I/O; an HTTP timeout is never remote quiescence proof."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt
from navox.vox.media import ConnectionIntent
from navox.vox.service import VoxError


async def begin_attempt(database: AsyncSession, intent: ConnectionIntent) -> UUID | None:
    """One physical attempt per epoch, including across processes and replicas.

    A replica retry never starts another RPC or retires the original holder.
    Known acknowledged replay may proceed to fresh authority finalization. Unknown
    outcomes require specific provider/operator evidence, never a guessed timeout.
    """
    cleanup = await database.scalar(
        select(VoxMediaCleanup)
        .where(VoxMediaCleanup.connection_id == intent.connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    connection = await database.get(
        VoxMediaConnection, intent.connection_id, populate_existing=True
    )
    if (
        cleanup is None
        or cleanup.status != "armed"
        or connection is None
        or connection.status == "revoked"
        or cleanup.owner_scope_id != intent.owner_scope_id
        or cleanup.room_name != intent.room_name
        or cleanup.client_identity != intent.client_identity
        or cleanup.agent_identity != intent.agent_identity
        or utc(cleanup.created_at) != intent.created_at
        or utc(cleanup.connection_expires_at) != intent.expires_at
    ):
        await database.rollback()
        raise VoxError(410, "This media connection has ended")
    existing = await database.scalar(
        select(VoxMediaProvisionAttempt)
        .where(VoxMediaProvisionAttempt.connection_id == intent.connection_id)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        status = existing.status
        await database.commit()
        if status == "acknowledged":
            return None
        raise VoxError(409, "This media connection is still being prepared or recovered")
    now = await database_now(database)
    if intent.expires_at <= now:
        await database.rollback()
        raise VoxError(410, "This media connection has ended")
    attempt_id = uuid4()
    database.add(
        VoxMediaProvisionAttempt(
            id=attempt_id,
            connection_id=intent.connection_id,
            status="in-flight",
            started_at=now,
        )
    )
    await database.commit()
    return attempt_id


async def finish_attempt(
    database: AsyncSession, connection_id: UUID, attempt_id: UUID, *, acknowledged: bool
) -> None:
    row = await database.scalar(
        select(VoxMediaProvisionAttempt)
        .where(
            VoxMediaProvisionAttempt.id == attempt_id,
            VoxMediaProvisionAttempt.connection_id == connection_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != "in-flight":
        await database.rollback()
        raise VoxError(409, "The provider operation no longer belongs to this attempt")
    row.status = "acknowledged" if acknowledged else "uncertain"
    row.finished_at = await database_now(database)
    await database.commit()
