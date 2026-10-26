"""Encrypted, explicitly disclosed text history and source-linked saved facts.

All mutations serialize on User. Call writers additionally bind the privacy
generation captured when their checkpoint was created. No extraction provider,
raw audio or automatic sensitive-fact learning is enabled by this module.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.communications import AssistantSession
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryPrivacy, MemoryThread
from navox.db.memory import OwnerMemory
from navox.memory.history_schemas import FactCreate, FactUpdate, PrivacyResponse, PrivacyUpdate
from navox.memory.sensitivity import SENSITIVE as SENSITIVE
from navox.memory.service import MemoryError, lock_owner

DOMAIN = "navox-student-memory-v1"
MAX_CONVERSATION_TURNS = 1000
MAX_CONVERSATION_BYTES = 2_000_000
HISTORY_PAGE_LIFETIME = timedelta(minutes=15)
HISTORY_CURSOR_ID = UUID("008aa01c-cbb2-4732-998c-515f0e683903")


class _HistoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: Literal[1]
    generation: int = Field(strict=True, ge=0)
    query_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_size: int = Field(strict=True, ge=1, le=100)
    after_created_at: datetime
    after_id: UUID
    expires_at: datetime


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def cipher(settings: Settings) -> Fernet:
    for secret in (settings.connector_secret_encryption_key, settings.google_token_encryption_key):
        if secret is not None and secret.get_secret_value():
            try:
                return Fernet(secret.get_secret_value().encode())
            except (ValueError, UnicodeError):
                break
    raise MemoryError(503, "Memory is temporarily unavailable. Try again shortly.")


def encrypt(
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    record_id: UUID,
    kind: str,
    content: object,
) -> str:
    value = json.dumps(
        {
            "binding": [DOMAIN, str(user_id), str(workspace_id), str(record_id), kind],
            "content": content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(value.encode()) > MAX_CONVERSATION_BYTES:
        raise MemoryError(409, "This conversation reached its history limit. Start a new call.")
    return cipher(settings).encrypt(value.encode()).decode()


def decrypt(
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    record_id: UUID,
    kind: str,
    encrypted: str | None,
) -> Any:
    try:
        if not encrypted or len(encrypted) > 3_000_000:
            raise ValueError
        value = json.loads(cipher(settings).decrypt(encrypted.encode()))
        if (
            not isinstance(value, dict)
            or set(value) != {"binding", "content"}
            or value["binding"] != [DOMAIN, str(user_id), str(workspace_id), str(record_id), kind]
        ):
            raise ValueError
        return value["content"]
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError):
        raise MemoryError(
            503, "This memory is temporarily unavailable. Try again shortly."
        ) from None


def owner_scope(model: Any, user_id: UUID, workspace_id: UUID) -> tuple[Any, Any]:
    return model.user_id == user_id, model.workspace_id == workspace_id


async def privacy_row(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, create: bool = False
) -> MemoryPrivacy | None:
    row = await database.get(MemoryPrivacy, (user_id, workspace_id), populate_existing=True)
    if row is None and create:
        row = MemoryPrivacy(
            user_id=user_id,
            workspace_id=workspace_id,
            history_enabled=False,
            off_the_record=False,
            retention_days=None,
            disclosure_version=None,
            generation=0,
            updated_at=datetime.now(UTC),
        )
        database.add(row)
        await database.flush()
    return row


def privacy_response(row: MemoryPrivacy | None) -> PrivacyResponse:
    if row is None:
        return PrivacyResponse()
    return PrivacyResponse.model_validate(
        {
            "history_enabled": row.history_enabled,
            "retention_days": row.retention_days,
            "off_the_record": row.off_the_record,
            "generation": row.generation,
            "disclosure_version": row.disclosure_version,
        }
    )


async def recording_generation(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> int | None:
    row = await privacy_row(database, user_id, workspace_id)
    if row is None or not row.history_enabled or row.off_the_record or row.disclosure_version != 1:
        return None
    return row.generation


async def update_privacy(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    payload: PrivacyUpdate,
) -> PrivacyResponse:
    await lock_owner(database, user_id)
    row = await privacy_row(database, user_id, workspace_id, create=True)
    assert row is not None
    if row.generation != payload.generation:
        raise MemoryError(409, "Memory settings changed. Open them again before saving.")
    if payload.history_enabled:
        if not row.history_enabled and not payload.disclosure_accepted:
            raise MemoryError(
                422, "Choose a retention period and agree before saving conversation text."
            )
        cipher(settings)  # Fail closed before recording can become enabled.
    # Turning recording off must never silently turn a 30/90-day transcript
    # into permanent storage. Only an enabled, explicit retention change alters
    # existing deadlines; off-record changes preserve them.
    retained_days = payload.retention_days if payload.history_enabled else row.retention_days
    retention_changed = payload.history_enabled and retained_days != row.retention_days
    changed = (row.history_enabled, row.retention_days, row.off_the_record) != (
        payload.history_enabled,
        retained_days,
        payload.off_the_record,
    )
    if changed:
        from navox.vox.service import invalidate_active_context

        await invalidate_active_context(database, user_id, workspace_id)
        row.generation += 1
    row.history_enabled = payload.history_enabled
    row.retention_days = retained_days
    row.off_the_record = payload.off_the_record
    if payload.history_enabled:
        row.disclosure_version = 1
    row.updated_at = datetime.now(UTC)
    if retention_changed:
        conversations = list(
            await database.scalars(
                select(MemoryConversation).where(
                    *owner_scope(MemoryConversation, user_id, workspace_id),
                    MemoryConversation.erased_at.is_(None),
                )
            )
        )
        for conversation in conversations:
            conversation.expires_at = (
                utc(conversation.created_at) + timedelta(days=retained_days)
                if retained_days is not None
                else None
            )
    await expire_owner_history(database, user_id, workspace_id)
    await database.commit()
    return privacy_response(row)


async def expire_owner_history(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, now: datetime | None = None
) -> int:
    """Expiry erases transcript AND summary; independently visible saved facts remain."""
    now = now or datetime.now(UTC)
    rows = list(
        await database.scalars(
            select(MemoryConversation).where(
                *owner_scope(MemoryConversation, user_id, workspace_id),
                MemoryConversation.erased_at.is_(None),
                MemoryConversation.expires_at.is_not(None),
                MemoryConversation.expires_at <= now,
            )
        )
    )
    for row in rows:
        row.encrypted_transcript = None
        row.encrypted_summary = None
        row.message_count = 0
        row.erased_at = now
        await database.execute(
            delete(MemoryThread).where(
                MemoryThread.source_conversation_id == row.id,
                *owner_scope(MemoryThread, user_id, workspace_id),
            )
        )
    await database.flush()
    return len(rows)


async def sweep_expired_history(
    factory: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> int:
    """Bounded timed erasure, including owners who never reopen their history."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Choose between one and 1000 expired conversations")
    async with factory() as database:
        owners = list(
            await database.execute(
                select(MemoryConversation.user_id, MemoryConversation.workspace_id)
                .where(
                    MemoryConversation.erased_at.is_(None),
                    MemoryConversation.expires_at.is_not(None),
                    MemoryConversation.expires_at <= datetime.now(UTC),
                )
                .order_by(MemoryConversation.expires_at, MemoryConversation.id)
                .limit(limit)
            )
        )
    erased = 0
    for user_id, workspace_id in dict.fromkeys((row[0], row[1]) for row in owners):
        async with factory() as database:
            await lock_owner(database, user_id)
            erased += await expire_owner_history(database, user_id, workspace_id)
            await database.commit()
    return erased


async def append_completed_turn(
    database: AsyncSession,
    settings: Settings,
    *,
    user_id: UUID,
    workspace_id: UUID,
    source_id: UUID,
    source_kind: Literal["vox", "chat"],
    expected_generation: int | None,
    sequence: int,
    question: str,
    answer: str,
    now: datetime | None = None,
) -> bool:
    """Caller holds User lock and commits atomically with completed-turn checkpoint."""
    generation = await recording_generation(database, user_id, workspace_id)
    if expected_generation is None or generation != expected_generation:
        return False
    if not question.strip() or not answer.strip() or len(question) > 8000 or len(answer) > 8000:
        raise MemoryError(422, "This conversation turn is outside its history limit.")
    now = now or datetime.now(UTC)
    await expire_owner_history(database, user_id, workspace_id, now=now)
    row = await database.scalar(
        select(MemoryConversation).where(
            *owner_scope(MemoryConversation, user_id, workspace_id),
            MemoryConversation.source_kind == source_kind,
            MemoryConversation.source_id == source_id,
        )
    )
    if row is not None and (row.erased_at is not None or row.generation != generation):
        return False
    privacy = await privacy_row(database, user_id, workspace_id)
    assert privacy is not None
    if row is None:
        row = MemoryConversation(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            source_kind=source_kind,
            source_id=source_id,
            generation=generation,
            last_sequence=0,
            encrypted_transcript=None,
            encrypted_summary=None,
            message_count=0,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(days=privacy.retention_days)
            if privacy.retention_days
            else None,
        )
        database.add(row)
        await database.flush()
        transcript: dict[str, Any] = {"title": question[:80], "turns": []}
    else:
        if sequence <= row.last_sequence:
            return False
        transcript = decrypt(
            settings, user_id, workspace_id, row.id, "transcript", row.encrypted_transcript
        )
    turns = transcript["turns"]
    if len(turns) >= MAX_CONVERSATION_TURNS * 2:
        raise MemoryError(409, "This conversation reached its history limit. Start a new call.")
    turns.extend(
        [
            {"role": "user", "content": question, "created_at": now.isoformat()},
            {"role": "assistant", "content": answer, "created_at": now.isoformat()},
        ]
    )
    from navox.memory.extraction import learn_completed_turn

    await learn_completed_turn(
        database,
        settings,
        source=row,
        transcript=transcript,
        question=question,
        sequence=sequence,
        now=now,
    )
    row.encrypted_transcript = encrypt(
        settings, user_id, workspace_id, row.id, "transcript", transcript
    )
    # An extractive recap, explicitly derived from transcript, never a verified fact
    # or permission. No provider receives history merely because it was retained.
    summary = "\n".join(turn["content"][:240] for turn in turns if turn["role"] == "user")[-4000:]
    row.encrypted_summary = (
        None
        if transcript.get("recall_superseded")
        else encrypt(settings, user_id, workspace_id, row.id, "summary", summary)
    )
    row.last_sequence, row.message_count, row.updated_at = sequence, len(turns), now
    await database.flush()
    return True


async def owned_conversation(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, identifier: UUID
) -> MemoryConversation:
    row = await database.scalar(
        select(MemoryConversation).where(
            MemoryConversation.id == identifier,
            *owner_scope(MemoryConversation, user_id, workspace_id),
        )
    )
    if row is None or row.erased_at is not None or row.encrypted_transcript is None:
        raise MemoryError(404, "Conversation not found.")
    return row


def history_view(
    row: MemoryConversation,
    settings: Settings,
    *,
    detail: bool = False,
    include_policy: bool = False,
) -> dict[str, Any]:
    transcript = decrypt(
        settings, row.user_id, row.workspace_id, row.id, "transcript", row.encrypted_transcript
    )
    result = {
        "id": row.id,
        "title": transcript["title"],
        "source_kind": row.source_kind,
        "created_at": utc(row.created_at),
        "updated_at": utc(row.updated_at),
        "message_count": row.message_count,
    }
    if detail:
        result["turns"] = transcript["turns"]
        result["summary"] = (
            decrypt(
                settings, row.user_id, row.workspace_id, row.id, "summary", row.encrypted_summary
            )
            if row.encrypted_summary
            else None
        )
    if include_policy:
        result["_automatic_extraction_blocked"] = bool(
            transcript.get("automatic_extraction_blocked")
        )
    return result


async def list_history(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID, query: str = ""
) -> list[dict[str, Any]]:
    await lock_owner(database, user_id)
    await expire_owner_history(database, user_id, workspace_id)
    rows = list(
        await database.scalars(
            select(MemoryConversation)
            .where(
                *owner_scope(MemoryConversation, user_id, workspace_id),
                MemoryConversation.erased_at.is_(None),
            )
            .order_by(MemoryConversation.updated_at.desc(), MemoryConversation.id)
            .limit(100)
        )
    )
    result = []
    for row in rows:
        value = history_view(row, settings, detail=bool(query))
        if (
            not query
            or query.casefold() in json.dumps(value, default=str, ensure_ascii=False).casefold()
        ):
            value.pop("turns", None)
            value.pop("summary", None)
            result.append(value)
    await database.commit()
    return result


def _history_cursor(
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    token: str,
    *,
    generation: int,
    query_digest: str,
    page_size: int,
    now: datetime,
) -> _HistoryCursor:
    try:
        if not token or len(token) > 4096:
            raise ValueError
        value = _HistoryCursor.model_validate(
            decrypt(settings, user_id, workspace_id, HISTORY_CURSOR_ID, "history-page", token)
        )
        if (
            value.generation != generation
            or value.query_digest != query_digest
            or value.page_size != page_size
            or value.after_created_at.tzinfo is None
            or value.expires_at.tzinfo is None
            or utc(value.expires_at) <= now
            or utc(value.expires_at) > now + HISTORY_PAGE_LIFETIME
        ):
            raise ValueError
        return value
    except (MemoryError, ValidationError, ValueError, TypeError):
        # The same response covers foreign, stale, expired and modified tokens.
        # No decrypted query, cursor metadata or retained text reaches errors.
        raise MemoryError(409, "This history page changed. Start the search again.") from None


async def history_page(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    *,
    query: str = "",
    cursor: str | None = None,
    page_size: int = 25,
) -> dict[str, Any]:
    """Traverse every retained conversation with bounded encrypted-text work.

    Ordering uses immutable creation time and ID: continuing an older call cannot
    move it across a page boundary. At most page_size transcripts are decrypted;
    one extra metadata row establishes whether another explicit request is useful.
    Search can return an empty page with a continuation, never a false claim that
    the rest of the owner's retained history has also been searched.
    """
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise MemoryError(422, "Choose between one and 100 conversations per page.")
    if not isinstance(query, str) or len(query) > 200:
        raise MemoryError(422, "Use a history search of up to 200 characters.")
    query = query.strip().casefold()
    query_digest = hashlib.sha256(query.encode()).hexdigest()
    await lock_owner(database, user_id)
    await expire_owner_history(database, user_id, workspace_id)
    privacy = await privacy_row(database, user_id, workspace_id)
    generation = privacy.generation if privacy is not None else 0
    now = utc(await database_now(database))
    after = (
        _history_cursor(
            settings,
            user_id,
            workspace_id,
            cursor,
            generation=generation,
            query_digest=query_digest,
            page_size=page_size,
            now=now,
        )
        if cursor is not None
        else None
    )
    statement = select(MemoryConversation).where(
        *owner_scope(MemoryConversation, user_id, workspace_id),
        MemoryConversation.erased_at.is_(None),
    )
    if after is not None:
        statement = statement.where(
            or_(
                MemoryConversation.created_at < utc(after.after_created_at),
                and_(
                    MemoryConversation.created_at == utc(after.after_created_at),
                    MemoryConversation.id > after.after_id,
                ),
            )
        )
    rows = list(
        await database.scalars(
            statement.order_by(MemoryConversation.created_at.desc(), MemoryConversation.id).limit(
                page_size + 1
            )
        )
    )
    more = len(rows) > page_size
    scanned = rows[:page_size]
    items = []
    for row in scanned:
        value = history_view(row, settings, detail=bool(query))
        if not query or query in json.dumps(value, default=str, ensure_ascii=False).casefold():
            value.pop("turns", None)
            value.pop("summary", None)
            items.append(value)
    next_cursor = None
    if more:
        last = scanned[-1]
        continuation = _HistoryCursor(
            version=1,
            generation=generation,
            query_digest=query_digest,
            page_size=page_size,
            after_created_at=utc(last.created_at),
            after_id=last.id,
            expires_at=after.expires_at if after is not None else now + HISTORY_PAGE_LIFETIME,
        )
        next_cursor = encrypt(
            settings,
            user_id,
            workspace_id,
            HISTORY_CURSOR_ID,
            "history-page",
            continuation.model_dump(mode="json"),
        )
    await database.commit()
    return {
        "items": items,
        "next_cursor": next_cursor,
        "search_complete": not more,
        "scanned": len(scanned),
    }


async def get_history(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID, identifier: UUID
) -> dict[str, Any]:
    await lock_owner(database, user_id)
    await expire_owner_history(database, user_id, workspace_id)
    row = await owned_conversation(database, user_id, workspace_id, identifier)
    result = history_view(row, settings, detail=True)
    await database.commit()
    return result


async def bump_and_invalidate(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    from navox.vox.service import invalidate_active_context

    row = await privacy_row(database, user_id, workspace_id, create=True)
    assert row is not None
    row.generation += 1
    row.updated_at = datetime.now(UTC)
    await invalidate_active_context(database, user_id, workspace_id)


async def forget_conversation(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, identifier: UUID
) -> None:
    await lock_owner(database, user_id)
    row = await owned_conversation(database, user_id, workspace_id, identifier)
    await bump_and_invalidate(database, user_id, workspace_id)
    await database.execute(
        delete(MemoryFact).where(
            *owner_scope(MemoryFact, user_id, workspace_id),
            MemoryFact.source_conversation_id == row.id,
        )
    )
    await database.execute(
        delete(MemoryThread).where(
            *owner_scope(MemoryThread, user_id, workspace_id),
            MemoryThread.source_conversation_id == row.id,
        )
    )
    # Retain a content-free source tombstone to prevent stale extraction replay.
    row.encrypted_transcript = row.encrypted_summary = None
    row.message_count, row.erased_at = 0, datetime.now(UTC)
    await database.commit()


def fact_view(row: MemoryFact, settings: Settings) -> dict[str, Any]:
    content = decrypt(
        settings, row.user_id, row.workspace_id, row.id, "fact", row.encrypted_content
    )
    return {
        "id": row.id,
        "label": content["label"],
        "content": content["content"],
        "source_kind": row.source_kind,
        "source_conversation_id": row.source_conversation_id,
        "sensitive": row.sensitive,
        "created_at": utc(row.created_at),
        "updated_at": utc(row.updated_at),
    }


async def list_facts(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID
) -> list[dict[str, Any]]:
    await lock_owner(database, user_id)
    rows = list(
        await database.scalars(
            select(MemoryFact)
            .where(*owner_scope(MemoryFact, user_id, workspace_id))
            .order_by(MemoryFact.created_at, MemoryFact.id)
            .limit(100)
        )
    )
    result = [fact_view(row, settings) for row in rows]
    await database.commit()
    return result


def require_sensitive_agreement(label: str, content: str, agreement: bool) -> bool:
    sensitive = bool(SENSITIVE.search(label + " " + content))
    if sensitive and not agreement:
        raise MemoryError(
            422, "Want me to remember this sensitive detail? Agree separately before saving."
        )
    return sensitive


async def create_fact(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    payload: FactCreate,
) -> dict[str, Any]:
    await lock_owner(database, user_id)
    privacy = await privacy_row(database, user_id, workspace_id)
    if privacy is not None and privacy.off_the_record:
        raise MemoryError(409, "Turn off Off the record before saving a new fact.")
    rows = list(
        await database.scalars(
            select(MemoryFact.id).where(*owner_scope(MemoryFact, user_id, workspace_id)).limit(100)
        )
    )
    if len(rows) >= 100:
        raise MemoryError(409, "Delete a saved fact before adding another.")
    source_kind = "user_saved"
    if payload.source_conversation_id is not None:
        await expire_owner_history(database, user_id, workspace_id)
        source = await owned_conversation(
            database, user_id, workspace_id, payload.source_conversation_id
        )
        source_kind = source.source_kind
    now = datetime.now(UTC)
    row = MemoryFact(
        id=uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        source_kind=source_kind,
        source_conversation_id=payload.source_conversation_id,
        sensitive=require_sensitive_agreement(
            payload.label, payload.content, payload.sensitive_agreement
        ),
        created_at=now,
        updated_at=now,
    )
    row.encrypted_content = encrypt(
        settings,
        user_id,
        workspace_id,
        row.id,
        "fact",
        {"label": payload.label, "content": payload.content},
    )
    database.add(row)
    await database.commit()
    return fact_view(row, settings)


async def owned_fact(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, identifier: UUID
) -> MemoryFact:
    row = await database.scalar(
        select(MemoryFact).where(
            MemoryFact.id == identifier, *owner_scope(MemoryFact, user_id, workspace_id)
        )
    )
    if row is None:
        raise MemoryError(404, "Saved fact not found.")
    return row


async def edit_fact(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    identifier: UUID,
    payload: FactUpdate,
) -> dict[str, Any]:
    await lock_owner(database, user_id)
    row = await owned_fact(database, user_id, workspace_id, identifier)
    old = fact_view(row, settings)
    old_payload = decrypt(settings, user_id, workspace_id, row.id, "fact", row.encrypted_content)
    label, content = payload.label or old["label"], payload.content or old["content"]
    sensitive = require_sensitive_agreement(label, content, payload.sensitive_agreement)
    await bump_and_invalidate(database, user_id, workspace_id)
    # Preserve the historical transcript and sibling saved facts. A missing
    # derived summary marks this source as obsolete for future recall; every
    # completed archived turn otherwise has a summary. Keep the explicit
    # correction authoritative without rewriting what was originally said.
    if row.source_conversation_id is not None:
        source = await database.get(MemoryConversation, row.source_conversation_id)
        if source is not None:
            source.encrypted_summary = None
    correction: dict[str, Any] = {
        "label": label,
        "content": content,
        "supersedes_source": row.source_conversation_id is not None,
    }
    if old_payload.get("extraction_method") in {"literal_v1", "user_corrected"}:
        correction.update(
            extraction_method="user_corrected",
            key=old_payload["key"],
            source_turn=old_payload.get("source_turn"),
        )
    row.encrypted_content = encrypt(settings, user_id, workspace_id, row.id, "fact", correction)
    row.sensitive, row.updated_at = sensitive, datetime.now(UTC)
    await database.commit()
    return fact_view(row, settings)


async def forget_fact(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, identifier: UUID
) -> None:
    await lock_owner(database, user_id)
    row = await owned_fact(database, user_id, workspace_id, identifier)
    await bump_and_invalidate(database, user_id, workspace_id)
    if row.source_conversation_id is not None:
        source = await database.get(MemoryConversation, row.source_conversation_id)
        if source is not None:
            source.encrypted_transcript = source.encrypted_summary = None
            source.message_count, source.erased_at = 0, datetime.now(UTC)
        await database.execute(
            delete(MemoryFact).where(
                *owner_scope(MemoryFact, user_id, workspace_id),
                MemoryFact.source_conversation_id == row.source_conversation_id,
                MemoryFact.id != row.id,
            )
        )
        await database.execute(
            delete(MemoryThread).where(
                *owner_scope(MemoryThread, user_id, workspace_id),
                MemoryThread.source_conversation_id == row.source_conversation_id,
            )
        )
    await database.delete(row)
    await database.commit()


async def erase_all_memory(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    expected_generation: int | None = None,
) -> None:
    """Shared erasure boundary. Caller holds User lock and owns final commit."""
    row = await privacy_row(database, user_id, workspace_id, create=True)
    assert row is not None
    if expected_generation is not None and row.generation != expected_generation:
        raise MemoryError(409, "Memory changed. Open the deletion controls again.")
    await bump_and_invalidate(database, user_id, workspace_id)
    for model in (MemoryThread, MemoryFact, OwnerMemory, AssistantSession):
        await database.execute(delete(model).where(*owner_scope(model, user_id, workspace_id)))
    conversations = list(
        await database.scalars(
            select(MemoryConversation).where(
                *owner_scope(MemoryConversation, user_id, workspace_id)
            )
        )
    )
    for conversation in conversations:
        conversation.encrypted_transcript = conversation.encrypted_summary = None
        conversation.message_count, conversation.erased_at = 0, datetime.now(UTC)
    row.history_enabled, row.off_the_record, row.disclosure_version = False, False, None
    row.retention_days = None
