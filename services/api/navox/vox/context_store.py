"""Encrypted active-call checkpoints fenced by the physical worker-lock connection."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.models import UserSession
from navox.db.vox import VoxCall
from navox.db.vox_context import VoxCallCheckpoint
from navox.vox.session_context import CallContextKey, ContextSnapshot
from navox.vox.worker import DatabaseAuthority, MemoryTurn, WorkerAccessLost

MAX_CIPHERTEXT = 3_000_000
DOMAIN = "navox-active-call-context-v1"


def checkpoint_cipher(settings: Settings) -> Fernet:
    for secret in (settings.connector_secret_encryption_key, settings.google_token_encryption_key):
        if secret is not None and secret.get_secret_value():
            try:
                return Fernet(secret.get_secret_value().encode())
            except (ValueError, UnicodeError):
                break
    raise WorkerAccessLost("Vox conversation recovery is not configured")


def _binding(key: CallContextKey, session_id: UUID, revision: int) -> list[str | int]:
    return [
        DOMAIN,
        str(key.user_id),
        str(key.workspace_id),
        str(key.call_id),
        key.generation,
        str(session_id),
        revision,
    ]


def encode_checkpoint(
    cipher: Fernet,
    key: CallContextKey,
    session_id: UUID,
    snapshot: ContextSnapshot,
    *,
    configuration_id: str = "",
) -> str:
    value = json.dumps(
        {
            "binding": _binding(key, session_id, snapshot.revision),
            "configuration_id": configuration_id,
            "turns": [
                {
                    "question": turn.question,
                    "answer": turn.answer,
                    **({"kind": "summary"} if turn.kind == "summary" else {}),
                }
                for turn in snapshot.turns
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    encrypted = cipher.encrypt(value.encode()).decode()
    if len(encrypted) > MAX_CIPHERTEXT:
        raise WorkerAccessLost("Vox conversation recovery exceeded its bound")
    return encrypted


def decode_checkpoint(
    cipher: Fernet,
    key: CallContextKey,
    session_id: UUID,
    revision: int,
    encrypted: str | None,
    *,
    configuration_id: str = "",
) -> ContextSnapshot:
    try:
        if not isinstance(encrypted, str) or not 0 < len(encrypted) <= MAX_CIPHERTEXT:
            raise ValueError
        value = json.loads(cipher.decrypt(encrypted.encode()))
        if (
            not isinstance(value, dict)
            or set(value) != {"binding", "turns", "configuration_id"}
            or value["configuration_id"] != configuration_id
            or value["binding"] != _binding(key, session_id, revision)
            or not isinstance(value["turns"], list)
        ):
            raise ValueError
        turns = []
        for turn in value["turns"]:
            if not isinstance(turn, dict) or set(turn) not in (
                {"question", "answer"},
                {"question", "answer", "kind"},
            ):
                raise ValueError
            turns.append(MemoryTurn(turn["question"], turn["answer"], turn.get("kind", "dialogue")))
        return ContextSnapshot(revision, tuple(turns))
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
        # Never expose the ciphertext, transcript, key or raw decoder exception.
        raise WorkerAccessLost("Vox conversation recovery is unavailable") from None


class DatabaseContextStore:
    def __init__(self, check: DatabaseAuthority, settings: Settings, *, configuration_id: str):
        if len(configuration_id) != 64 or any(
            c not in "0123456789abcdef" for c in configuration_id
        ):
            raise WorkerAccessLost("Invalid Vox configuration identity")
        self._configuration_id = configuration_id
        self.check = check
        self._cipher = checkpoint_cipher(settings)

    async def _row(self, database: AsyncSession, key: CallContextKey) -> VoxCallCheckpoint:
        call = await database.get(VoxCall, key.call_id, populate_existing=True)
        session = await database.get(UserSession, call.auth_session_id) if call else None
        now = await database_now(database)
        if (
            call is None
            or session is None
            or (call.user_id, call.workspace_id, call.generation)
            != (key.user_id, key.workspace_id, key.generation)
            or call.status != "reserved"
            or utc(call.expires_at) <= now
            or session.revoked_at is not None
            or utc(session.expires_at) <= now
        ):
            raise WorkerAccessLost("The call context has ended")
        row = await database.get(VoxCallCheckpoint, key.call_id, populate_existing=True)
        if row is None:
            row = VoxCallCheckpoint(
                call_id=key.call_id,
                user_id=key.user_id,
                workspace_id=key.workspace_id,
                auth_session_id=call.auth_session_id,
                generation=key.generation,
                revision=0,
                encrypted_context=encode_checkpoint(
                    self._cipher,
                    key,
                    call.auth_session_id,
                    ContextSnapshot(),
                    configuration_id=self._configuration_id,
                ),
                created_at=now,
                expires_at=min(utc(call.expires_at), utc(session.expires_at)),
            )
            database.add(row)
            await database.flush()
        if (
            (row.user_id, row.workspace_id, row.generation, row.auth_session_id)
            != (key.user_id, key.workspace_id, key.generation, call.auth_session_id)
            or utc(row.expires_at) <= now
            or utc(row.expires_at) > min(utc(call.expires_at), utc(session.expires_at))
            or row.encrypted_context is None
        ):
            raise WorkerAccessLost("The call context has ended")
        return row

    @staticmethod
    def _scope(
        key: CallContextKey, user_id: UUID, workspace_id: UUID, call_id: UUID, generation: int
    ) -> None:
        if key != CallContextKey(user_id, workspace_id, call_id, generation):
            raise WorkerAccessLost("The call context owner changed")

    async def load(self, key: CallContextKey) -> ContextSnapshot:
        async with self.check.context_session() as (database, authority, intent):
            self._scope(
                key,
                authority.user_id,
                authority.workspace_id,
                intent.reservation_id,
                intent.generation,
            )
            row = await self._row(database, key)
            return decode_checkpoint(
                self._cipher,
                key,
                row.auth_session_id,
                row.revision,
                row.encrypted_context,
                configuration_id=self._configuration_id,
            )

    async def compare_and_set(
        self, key: CallContextKey, expected_revision: int, turns: tuple[MemoryTurn, ...]
    ) -> bool:
        if type(expected_revision) is not int or not 0 <= expected_revision < 2147483647:
            raise WorkerAccessLost("The call context revision is invalid")
        snapshot = ContextSnapshot(expected_revision + 1, turns)
        async with self.check.context_session() as (database, authority, intent):
            self._scope(
                key,
                authority.user_id,
                authority.workspace_id,
                intent.reservation_id,
                intent.generation,
            )
            row = await self._row(database, key)
            if row.revision != expected_revision:
                return False
            # Reject corrupt/wrong-domain prior state instead of overwriting it.
            decode_checkpoint(
                self._cipher,
                key,
                row.auth_session_id,
                row.revision,
                row.encrypted_context,
                configuration_id=self._configuration_id,
            )
            row.encrypted_context = encode_checkpoint(
                self._cipher,
                key,
                row.auth_session_id,
                snapshot,
                configuration_id=self._configuration_id,
            )
            row.revision = snapshot.revision
            return True

    async def delete(self, key: CallContextKey) -> None:
        # Preserve a content-free tombstone while the call lives: no silent reset
        # to an empty conversation after erasure. Terminal lifecycle deletes rows.
        async with self.check.context_session() as (database, authority, intent):
            self._scope(
                key,
                authority.user_id,
                authority.workspace_id,
                intent.reservation_id,
                intent.generation,
            )
            row = await self._row(database, key)
            row.encrypted_context = None


async def purge_call_checkpoint(database: AsyncSession, call_id: UUID) -> None:
    """Caller holds User lock and commits with the terminal call transition."""
    await database.execute(delete(VoxCallCheckpoint).where(VoxCallCheckpoint.call_id == call_id))


async def renew_call_checkpoint(database: AsyncSession, call: VoxCall, *, now: datetime) -> None:
    """Extend only an unexpired checkpoint, atomically with the authorized call lease."""
    await database.execute(
        update(VoxCallCheckpoint)
        .where(
            VoxCallCheckpoint.call_id == call.id,
            VoxCallCheckpoint.user_id == call.user_id,
            VoxCallCheckpoint.workspace_id == call.workspace_id,
            VoxCallCheckpoint.auth_session_id == call.auth_session_id,
            VoxCallCheckpoint.generation == call.generation,
            VoxCallCheckpoint.expires_at > now,
            VoxCallCheckpoint.encrypted_context.is_not(None),
        )
        .values(expires_at=call.expires_at)
    )


async def sweep_expired_checkpoints(database: AsyncSession, *, limit: int = 100) -> int:
    """Erase a bounded expired batch; tombstones prevent fabricated continuation."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Choose between one and 1000 call checkpoints")
    now = await database_now(database)
    expired = (
        select(VoxCallCheckpoint.call_id)
        .where(
            VoxCallCheckpoint.expires_at <= now,
            VoxCallCheckpoint.encrypted_context.is_not(None),
        )
        .order_by(VoxCallCheckpoint.expires_at, VoxCallCheckpoint.call_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(
        await database.scalars(
            update(VoxCallCheckpoint)
            .where(VoxCallCheckpoint.call_id.in_(expired))
            .values(encrypted_context=None)
            .returning(VoxCallCheckpoint.call_id)
        )
    )
    await database.commit()
    return len(rows)
