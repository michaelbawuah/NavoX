"""Narrow literal learning from retained, completed student text.

The caller owns the User lock and the archive/generation transaction. This has
no provider, network, audio, scheduling or automatic sensitive-save capability.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryThread
from navox.db.models import User
from navox.memory.extraction_rules import LiteralExtraction, derive_literal_turn, excluded_source

METHOD = "literal_v1"
AUTOMATIC_METHODS = frozenset({METHOD, "semantic_v1"})
MAX_AUTOMATIC_ROWS = 100


async def learn_completed_turn(
    database: AsyncSession,
    settings: Settings,
    *,
    source: MemoryConversation,
    transcript: dict[str, Any],
    question: str,
    sequence: int,
    now: datetime,
    method: Literal["literal_v1", "semantic_v1"] = "literal_v1",
    source_turn_index: int | None = None,
    source_turn_sequence: int | None = None,
    source_span_start: int | None = None,
    source_timezone: str | None = None,
) -> None:
    """Derive only recognized declarations, with exact source turn provenance."""
    from navox.memory import history

    user = await database.get(User, source.user_id)
    if user is None:
        return
    checked = bool(transcript.get("literal_memory_checked"))
    student_texts = [turn["content"] for turn in transcript["turns"] if turn["role"] == "user"]
    texts = student_texts[-3:] if checked else student_texts
    blocked = bool(transcript.get("automatic_extraction_blocked")) or excluded_source(texts)
    transcript["literal_memory_checked"] = True
    transcript["automatic_extraction_blocked"] = blocked
    facts = list(
        await database.scalars(
            select(MemoryFact)
            .where(*history.owner_scope(MemoryFact, source.user_id, source.workspace_id))
            .order_by(MemoryFact.updated_at.desc(), MemoryFact.id)
            .limit(MAX_AUTOMATIC_ROWS)
        )
    )
    fact_values = [
        (
            fact,
            history.decrypt(
                settings,
                source.user_id,
                source.workspace_id,
                fact.id,
                "fact",
                fact.encrypted_content,
            ),
        )
        for fact in facts
    ]
    threads = list(
        await database.scalars(
            select(MemoryThread)
            .where(*history.owner_scope(MemoryThread, source.user_id, source.workspace_id))
            .order_by(MemoryThread.updated_at.desc(), MemoryThread.id)
            .limit(MAX_AUTOMATIC_ROWS)
        )
    )
    thread_values = [
        (
            thread,
            history.decrypt(
                settings,
                source.user_id,
                source.workspace_id,
                thread.id,
                "thread",
                thread.encrypted_content,
            ),
        )
        for thread in threads
    ]
    if blocked:
        # A later sensitive/crisis turn retracts earlier AUTOMATIC derivatives
        # from this source. Independently reviewed saved facts are untouched.
        for fact, value in fact_values:
            if (
                fact.source_conversation_id == source.id
                and value.get("extraction_method") in AUTOMATIC_METHODS
            ):
                await database.delete(fact)
        for thread, value in thread_values:
            if (
                thread.source_conversation_id == source.id
                and value.get("extraction_method") in AUTOMATIC_METHODS
            ):
                await database.delete(thread)
        return
    timezone = source_timezone if method == "semantic_v1" else user.timezone
    extracted = derive_literal_turn(question, now=now, timezone=timezone or "UTC")
    if method == "semantic_v1" and source_timezone is None:
        # Legacy history can still certify a date-independent neutral fact.
        # It cannot supply an invented timezone for relative event dates.
        extracted = LiteralExtraction(facts=extracted.facts, excluded=extracted.excluded)
    for candidate in extracted.facts:
        key = "employer" if candidate.key == "employer_departure" else candidate.key
        if any(
            value.get("extraction_method") == "user_corrected" and value.get("key") == key
            for _, value in fact_values
        ):
            continue  # An independently reviewed correction stays under student control.
        existing = next(
            (
                (fact, value)
                for fact, value in fact_values
                if value.get("extraction_method") in AUTOMATIC_METHODS and value.get("key") == key
            ),
            None,
        )
        if candidate.key == "employer_departure":
            departed = re.search(r"I quit my job at (.+?)\.?$", candidate.content, re.I)
            current = (
                re.search(r"I work at (.+?)\.?$", existing[1]["content"], re.I)
                if existing
                else None
            )
            if (
                departed is None
                or current is None
                or departed[1].casefold() != current[1].casefold()
            ):
                continue
        if existing:
            fact, old = existing
            if history.utc(fact.updated_at) > history.utc(now):
                continue  # A delayed job cannot replace a more recent declaration.
            if method == "semantic_v1" and history.utc(fact.updated_at) == history.utc(now):
                previous_index = old.get("source_turn_index")
                previous_span = old.get("source_span_start", -1)
                if (
                    fact.source_conversation_id != source.id
                    or source_turn_index is None
                    or not isinstance(previous_index, int)
                    or (source_turn_index, source_span_start or 0)
                    <= (previous_index, previous_span)
                ):
                    continue  # Equal-clock ambiguity never grants an older job overwrite rights.
            if (
                method == "semantic_v1"
                and old["content"] == candidate.content
                and fact.source_conversation_id == source.id
            ):
                continue  # An already archived fact is not a new semantic write.
            if old["content"] != candidate.content and fact.source_conversation_id is not None:
                old_source = await database.get(MemoryConversation, fact.source_conversation_id)
                if old_source is not None:
                    old_source.encrypted_summary = None
                    await history.clear_learning_proposals(
                        database, source.user_id, source.workspace_id, source_id=old_source.id
                    )
                    if old_source.id == source.id:
                        transcript["recall_superseded"] = True
        else:
            if len(facts) >= MAX_AUTOMATIC_ROWS:
                continue
            fact = MemoryFact(
                id=uuid4(),
                user_id=source.user_id,
                workspace_id=source.workspace_id,
                created_at=now,
                sensitive=False,
            )
            database.add(fact)
            facts.append(fact)
        value = {
            "label": candidate.label,
            "content": candidate.content,
            "extraction_method": method,
            "key": key,
            "source_turn": sequence,
            "supersedes_source": bool(transcript.get("recall_superseded")),
        }
        if source_turn_index is not None:
            value["source_turn_index"] = source_turn_index
            value["source_turn"] = source_turn_sequence
            value["source_span_start"] = source_span_start
            value["source_timezone"] = source_timezone
        fact.source_conversation_id, fact.source_kind = source.id, source.source_kind
        fact.updated_at = now
        fact.encrypted_content = history.encrypt(
            settings, source.user_id, source.workspace_id, fact.id, "fact", value
        )
    if transcript.get("recall_superseded"):
        # Each surviving automatic category is its latest literal declaration;
        # retain those current values while suppressing contradictory raw history.
        for fact in facts:
            if fact.source_conversation_id != source.id:
                continue
            value = history.decrypt(
                settings,
                source.user_id,
                source.workspace_id,
                fact.id,
                "fact",
                fact.encrypted_content,
            )
            if value.get("extraction_method") in AUTOMATIC_METHODS:
                value["supersedes_source"] = True
                fact.encrypted_content = history.encrypt(
                    settings, source.user_id, source.workspace_id, fact.id, "fact", value
                )
    for thread_candidate in extracted.threads:
        if (
            any(
                value.get("extraction_method") in AUTOMATIC_METHODS
                and value.get("key") == thread_candidate.key
                for _, value in thread_values
            )
            or len(threads) >= MAX_AUTOMATIC_ROWS
        ):
            continue
        thread = MemoryThread(
            id=uuid4(),
            user_id=source.user_id,
            workspace_id=source.workspace_id,
            source_conversation_id=source.id,
            status="open",
            created_at=now,
            updated_at=now,
        )
        value = {
            "schema_version": "literal-thread.v1",
            "extraction_method": method,
            "key": thread_candidate.key,
            "topic": thread_candidate.topic,
            "statement": thread_candidate.statement,
            "event_date": thread_candidate.event_date.isoformat(),
            "timezone": thread_candidate.timezone,
            "prompt": thread_candidate.prompt,
            "source_turn": sequence,
        }
        if source_turn_index is not None:
            value["source_turn_index"] = source_turn_index
            value["source_turn"] = source_turn_sequence
        thread.encrypted_content = history.encrypt(
            settings, source.user_id, source.workspace_id, thread.id, "thread", value
        )
        database.add(thread)
        threads.append(thread)
    await mark_responded_followups(
        database,
        settings,
        source.user_id,
        source.workspace_id,
        transcript["turns"][-1]["content"],
        now=now,
        context_key=f"{source.source_kind}:{source.source_id}",
        generation=source.generation,
    )


def eligible_followup(value: dict[str, Any], *, now: datetime, context_key: str | None) -> bool:
    """Never ask how an event went on the same local day it may still happen."""
    if value.get("extraction_method") not in AUTOMATIC_METHODS or context_key is None:
        return False
    if value.get("offered_context") not in (None, context_key):
        return False
    try:
        event_day = date.fromisoformat(value["event_date"])
        today = now.astimezone(ZoneInfo(value["timezone"])).date()
        return event_day < today <= event_day + timedelta(days=7)
    except (ValueError, TypeError, KeyError):
        return False


async def mark_responded_followups(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    answer: str,
    *,
    now: datetime,
    context_key: str,
    generation: int,
) -> None:
    """A completed/heard answer is stronger evidence than a provider-only ack.

    This records a host response, not proof that a student read or heard it.
    Exact prompting avoids inferring follow-up delivery from unrelated prose.
    """
    from navox.memory import history

    rows = list(
        await database.scalars(
            select(MemoryThread)
            .where(
                *history.owner_scope(MemoryThread, user_id, workspace_id),
                MemoryThread.status == "offered",
            )
            .limit(MAX_AUTOMATIC_ROWS)
        )
    )
    normalized = " ".join(answer.casefold().split())
    for thread in rows:
        value = history.decrypt(
            settings, user_id, workspace_id, thread.id, "thread", thread.encrypted_content
        )
        prompt = value.get("prompt")
        if (
            value.get("extraction_method") in AUTOMATIC_METHODS
            and value.get("offered_context") == context_key
            and value.get("offered_generation") == generation
            and isinstance(prompt, str)
            and prompt.casefold() in normalized
        ):
            value["responded_at"] = now.astimezone(UTC).isoformat()
            thread.status, thread.updated_at = "responded", now
            thread.encrypted_content = history.encrypt(
                settings, user_id, workspace_id, thread.id, "thread", value
            )


async def _offered_followups(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    *,
    context_key: str,
    generation: int,
    thread_ids: frozenset[UUID],
    now: datetime,
) -> list[tuple[MemoryThread, dict[str, Any]]]:
    """Retain the owner's lock through the caller's receipt transaction."""
    from navox.memory import history
    from navox.memory.service import lock_owner

    await lock_owner(database, user_id)
    privacy = await history.privacy_row(database, user_id, workspace_id)
    if (
        not thread_ids
        or len(thread_ids) > MAX_AUTOMATIC_ROWS
        or privacy is None
        or not privacy.history_enabled
        or privacy.off_the_record
        or privacy.disclosure_version != 1
        or privacy.generation != generation
    ):
        return []
    rows = list(
        await database.scalars(
            select(MemoryThread)
            .join(MemoryConversation, MemoryConversation.id == MemoryThread.source_conversation_id)
            .where(
                *history.owner_scope(MemoryThread, user_id, workspace_id),
                MemoryThread.id.in_(thread_ids),
                MemoryThread.status == "offered",
                *history.owner_scope(MemoryConversation, user_id, workspace_id),
                MemoryConversation.erased_at.is_(None),
                MemoryConversation.encrypted_summary.is_not(None),
                (MemoryConversation.expires_at.is_(None) | (MemoryConversation.expires_at > now)),
            )
        )
    )
    result = []
    for row in rows:
        value = history.decrypt(
            settings, user_id, workspace_id, row.id, "thread", row.encrypted_content
        )
        if (
            isinstance(value, dict)
            and value.get("extraction_method") in AUTOMATIC_METHODS
            and value.get("offered_context") == context_key
            and value.get("offered_generation") == generation
        ):
            result.append((row, value))
    return result


async def mark_followup_exposure(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    *,
    context_key: str,
    generation: int,
    thread_ids: frozenset[UUID],
    now: datetime | None = None,
) -> frozenset[UUID]:
    """Record possible exposure BEFORE output; commit before publishing audio.

    A receipt does not prove that the student heard the follow-up. It prevents a
    crash or uncertain partial output from offering it in a different context.
    The caller owns commit/rollback; this never starts a nested transaction.
    """
    from navox.memory import history

    moment = now or datetime.now(UTC)
    rows = await _offered_followups(
        database,
        settings,
        user_id,
        workspace_id,
        context_key=context_key,
        generation=generation,
        thread_ids=thread_ids,
        now=moment,
    )
    for row, value in rows:
        if "exposure_started_at" not in value:
            value["exposure_started_at"] = moment.astimezone(UTC).isoformat()
            row.updated_at = moment
            row.encrypted_content = history.encrypt(
                settings, user_id, workspace_id, row.id, "thread", value
            )
    return frozenset(row.id for row, _ in rows)


async def release_unexposed_followups(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    *,
    context_key: str,
    generation: int,
    thread_ids: frozenset[UUID],
    now: datetime | None = None,
) -> frozenset[UUID]:
    """Release a known-unsounded offer, never a legacy/uncertain output attempt.

    Only new delivery-version offers can establish the absence of exposure.
    A missing receipt on an older offer is not evidence that it was unheard.
    Call only after the context can no longer publish its old response.
    """
    from navox.memory import history

    moment = now or datetime.now(UTC)
    rows = await _offered_followups(
        database,
        settings,
        user_id,
        workspace_id,
        context_key=context_key,
        generation=generation,
        thread_ids=thread_ids,
        now=moment,
    )
    released = set()
    for row, value in rows:
        if (
            type(value.get("delivery_version")) is not int
            or value["delivery_version"] != 1
            or "exposure_started_at" in value
            or "responded_at" in value
        ):
            continue
        for key in ("delivery_version", "offered_context", "offered_generation", "offered_at"):
            value.pop(key, None)
        row.status, row.updated_at = "open", moment
        row.encrypted_content = history.encrypt(
            settings, user_id, workspace_id, row.id, "thread", value
        )
        released.add(row.id)
    return frozenset(released)
