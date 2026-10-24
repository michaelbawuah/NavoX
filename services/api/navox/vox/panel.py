"""Private views and typed input for the student's current off-record call."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.core.settings import Settings
from navox.db.vox import VoxCall
from navox.db.vox_context import VoxCallCheckpoint
from navox.db.vox_media import VoxMediaConnection
from navox.vox import service
from navox.vox.commands import enqueue_wake
from navox.vox.context_store import checkpoint_cipher, decode_checkpoint
from navox.vox.inference_config import load_inference_config
from navox.vox.session_context import CallContextKey
from navox.vox.worker import WorkerAccessLost


async def _owned(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
) -> tuple[service.Boundary, VoxCall]:
    boundary = await service._locked(database, user_id, workspace_id, session_token_hash)
    call = await database.scalar(
        select(VoxCall)
        .where(
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.client_call_id == client_call_id,
        )
        .execution_options(populate_existing=True)
    )
    if call is None:
        raise service.VoxError(404, "This conversation is not available")
    if (
        call.auth_session_id != boundary.session.id
        or call.status != "reserved"
        or call.generation != boundary.generation
        or utc(call.expires_at) <= boundary.now
    ):
        raise service.VoxError(410, "This conversation has ended")
    return boundary, call


async def transcript(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    after: int | None = None,
) -> dict[str, object]:
    async def operation() -> dict[str, object]:
        boundary, call = await _owned(
            database, user_id, workspace_id, session_token_hash, client_call_id
        )
        row = await database.get(VoxCallCheckpoint, call.id, populate_existing=True)
        if row is None:
            return {"revision": 0, "turns": [] if after != 0 else None}
        if (
            (row.user_id, row.workspace_id, row.auth_session_id, row.generation)
            != (user_id, workspace_id, boundary.session.id, call.generation)
            or utc(row.expires_at) <= boundary.now
            or utc(row.expires_at) > utc(call.expires_at)
            or row.encrypted_context is None
        ):
            raise service.VoxError(410, "This conversation has ended")
        if row.revision == after:
            return {"revision": row.revision, "turns": None}
        config = load_inference_config(settings)
        if config is None:
            raise service.VoxError(503, "Conversation text is not available yet")
        try:
            snapshot = decode_checkpoint(
                checkpoint_cipher(settings),
                CallContextKey(user_id, workspace_id, call.id, call.generation),
                boundary.session.id,
                row.revision,
                row.encrypted_context,
                configuration_id=hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
            )
        except WorkerAccessLost:
            raise service.VoxError(503, "Conversation text could not load. Try again") from None
        return {
            "revision": snapshot.revision,
            "turns": [
                {"question": turn.question, "answer": turn.answer, "kind": turn.kind}
                for turn in snapshot.turns
            ],
        }

    return await service._complete(database, operation())


async def send_message(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    request_id: UUID,
    text: str,
) -> None:
    async def operation() -> None:
        boundary, call = await _owned(
            database, user_id, workspace_id, session_token_hash, client_call_id
        )
        connection = await database.scalar(
            select(VoxMediaConnection)
            .where(
                VoxMediaConnection.call_id == call.id,
                VoxMediaConnection.generation == boundary.generation,
                VoxMediaConnection.status == "confirmed",
            )
            .order_by(VoxMediaConnection.created_at.desc())
            .limit(1)
        )
        if connection is None:
            raise service.VoxError(409, "Wait for Vox to reconnect, then try again")
        await enqueue_wake(
            database,
            user_id,
            workspace_id,
            session_token_hash,
            client_call_id,
            connection.id,
            generation=boundary.generation,
            request_id=request_id,
            text=text,
            settings=settings,
            typed=True,
        )

    await service._complete(database, operation())
