"""Authenticated wake-question delivery, with no plaintext persistence or logs."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.models import User
from navox.db.vox_commands import VoxWakeCommand
from navox.db.vox_media import VoxMediaCleanup
from navox.vox import media, service
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost

MAX_WAKE_TEXT = 4000
MAX_QUEUED = 8
MAX_COMMANDS = 200


@dataclass(frozen=True)
class WakeCommand:
    request_id: UUID
    text: str = field(repr=False)


def _crypto(settings: Settings) -> tuple[Fernet, bytes]:
    for secret in (settings.connector_secret_encryption_key, settings.google_token_encryption_key):
        if secret is not None and secret.get_secret_value():
            try:
                key = secret.get_secret_value().encode()
                return Fernet(key), hashlib.sha256(b"vox-wake-v1:" + key).digest()
            except (ValueError, UnicodeError):
                break
    raise service.VoxError(503, "Vox question delivery is not configured")


def _text(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.strip()) <= MAX_WAKE_TEXT
        or "\x00" in value
    ):
        raise service.VoxError(422, "The wake question is invalid")
    return value.strip()


async def enqueue_wake(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
    connection_id: UUID,
    *,
    generation: int,
    request_id: UUID,
    text: str,
    settings: Settings,
) -> None:
    """Acknowledgement means queued, never spoken or an approved external action."""
    question = _text(text)
    cipher, digest_key = _crypto(settings)

    async def operation() -> None:
        boundary = await service._locked(database, user_id, workspace_id, session_token_hash)
        connection = await media._owned_connection(
            database, boundary, user_id, workspace_id, connection_id
        )
        call = await media._call(database, connection)
        if (
            call is None
            or call.client_call_id != client_call_id
            or connection.generation != generation
            or connection.status != "confirmed"
            or await media._invalid_reason(database, connection, boundary.user, boundary.now)
            is not None
        ):
            raise service.VoxError(410, "This Vox connection has ended")
        cleanup = await database.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == connection_id)
        )
        if cleanup is None or cleanup.status != "armed":
            raise service.VoxError(410, "This Vox connection has ended")
        binding = f"{user_id}:{workspace_id}:{connection_id}:{request_id}:"
        digest = hmac.new(digest_key, (binding + question).encode(), hashlib.sha256).hexdigest()
        await _clear_expired(database, connection_id, boundary.now)
        previous = await database.scalar(
            select(VoxWakeCommand).where(
                VoxWakeCommand.connection_id == connection_id,
                VoxWakeCommand.request_id == request_id,
            )
        )
        if previous is not None:
            if not hmac.compare_digest(previous.digest, digest):
                raise service.VoxError(409, "The wake request changed; use a new request")
            return
        total = await database.scalar(
            select(func.count())
            .select_from(VoxWakeCommand)
            .where(VoxWakeCommand.connection_id == connection_id)
        )
        queued = await database.scalar(
            select(func.count())
            .select_from(VoxWakeCommand)
            .where(
                VoxWakeCommand.connection_id == connection_id,
                VoxWakeCommand.encrypted_text.is_not(None),
            )
        )
        if (total or 0) >= MAX_COMMANDS or (queued or 0) >= MAX_QUEUED:
            raise service.VoxError(429, "Vox is still receiving your earlier question")
        envelope = json.dumps({"binding": binding, "text": question}, ensure_ascii=False)
        database.add(
            VoxWakeCommand(
                connection_id=connection_id,
                user_id=user_id,
                workspace_id=workspace_id,
                request_id=request_id,
                digest=digest,
                encrypted_text=cipher.encrypt(envelope.encode()).decode(),
                created_at=boundary.now,
                expires_at=min(boundary.now + timedelta(seconds=30), utc(connection.expires_at)),
            )
        )

    await service._complete(database, operation())


async def _clear_expired(database: AsyncSession, connection_id: UUID, now: datetime) -> None:
    await database.execute(
        update(VoxWakeCommand)
        .where(
            VoxWakeCommand.connection_id == connection_id,
            VoxWakeCommand.expires_at <= now,
            VoxWakeCommand.encrypted_text.is_not(None),
        )
        .values(encrypted_text=None)
    )


async def take_wake_commands(check: DatabaseAuthority, settings: Settings) -> list[WakeCommand]:
    """Trusted worker only. Take at most one question without discarding the rest."""
    authority, intent = await check.intent()
    cipher, _ = _crypto(settings)
    result: list[WakeCommand] = []
    async with check.factory() as database:
        await database.scalar(select(User.id).where(User.id == authority.user_id).with_for_update())
        if await check() != authority:
            raise WorkerAccessLost("The Vox account changed")
        now = await database_now(database)
        await _clear_expired(database, intent.connection_id, now)
        rows = list(
            await database.scalars(
                select(VoxWakeCommand)
                .where(
                    VoxWakeCommand.connection_id == intent.connection_id,
                    VoxWakeCommand.user_id == authority.user_id,
                    VoxWakeCommand.workspace_id == authority.workspace_id,
                    VoxWakeCommand.encrypted_text.is_not(None),
                    VoxWakeCommand.expires_at > now,
                )
                .order_by(VoxWakeCommand.created_at, VoxWakeCommand.id)
                .limit(1)
                .with_for_update()
            )
        )
        for row in rows:
            encrypted = row.encrypted_text
            row.encrypted_text = None
            try:
                if encrypted is None:
                    continue
                envelope = json.loads(cipher.decrypt(encrypted.encode()))
                binding = (
                    f"{authority.user_id}:{authority.workspace_id}:"
                    f"{intent.connection_id}:{row.request_id}:"
                )
                if envelope.get("binding") != binding:
                    continue
                result.append(WakeCommand(row.request_id, _text(envelope["text"])))
            except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
                continue
        await database.commit()
    if await check() != authority:
        raise WorkerAccessLost("The Vox account changed")
    return result


async def sweep_expired_wake_commands(database: AsyncSession, *, limit: int = 200) -> int:
    """Clear a bounded batch in an isolated cleanup session after worker crashes.

    Commits its own transaction. Call from the independent periodic reconciler,
    before provider I/O. Replay digests remain until the media epoch is deleted.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Choose between one and 1000 wake commands")
    now = await database_now(database)
    expired = (
        select(VoxWakeCommand.id)
        .where(
            VoxWakeCommand.expires_at <= now,
            VoxWakeCommand.encrypted_text.is_not(None),
        )
        .order_by(VoxWakeCommand.expires_at, VoxWakeCommand.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    cleared = list(
        await database.scalars(
            update(VoxWakeCommand)
            .where(VoxWakeCommand.id.in_(expired))
            .values(encrypted_text=None)
            .returning(VoxWakeCommand.id)
        )
    )
    await database.commit()
    return len(cleared)
