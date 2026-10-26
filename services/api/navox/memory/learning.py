"""Source-bound semantic selection with host-only neutral admission and private review.

Providers may select exact quotes; they cannot classify unknown content as public,
choose an owner, approve sensitive storage, supply dates or resurrect erased text.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.context import reject_credentials
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import Contract
from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryLearningJob
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.memory import history
from navox.memory.extraction_rules import derive_literal_turn, excluded_source
from navox.memory.learning_contracts import MAX_SOURCE_TURNS, EvidenceQuote, LearningOutput
from navox.memory.service import MemoryError, lock_owner

REVIEW_LIFETIME = timedelta(days=7)
CLAIM_LIFETIME = timedelta(minutes=10)
MAX_JOBS_PER_OWNER = 100
MAX_REVIEW_ROWS = 20
MAX_SCAN_ROWS = 100
_UNSAFE_FRAGMENT = re.compile(
    r"\b(?:not|never|no|if|unless|except|would|could|should|might|maybe|perhaps|used|formerly|previously|"
    r"because|but|then|said|says|example|pretend|suppose|imagine|quote|quoted|ignore|"
    r"instruction\w*|system|assistant|model|prompt|rules?|approve|execute)\b",
    re.I,
)


class LearningConfirmation(Contract):
    generation: int = Field(ge=0, strict=True)
    confirmation: Literal["SAVE MEMORY"]
    sensitive_agreement: bool = Field(default=False, strict=True)


class LearningDismissal(Contract):
    generation: int = Field(ge=0, strict=True)


def unavailable() -> MemoryError:
    return MemoryError(409, "This memory suggestion changed or expired. Reload your memory.")


def source_digest(
    settings: Settings, source: MemoryConversation, turns: list[dict[str, Any]]
) -> str:
    # A keyed digest prevents low-entropy personal statements from becoming a
    # dictionary-testable plaintext metadata fingerprint.
    history.cipher(settings)
    secret = next(
        value.get_secret_value()
        for value in (
            settings.connector_secret_encryption_key,
            settings.google_token_encryption_key,
        )
        if value is not None and value.get_secret_value()
    )
    key = hashlib.sha256(b"memory-learning-proof.v1:" + secret.encode()).digest()
    payload = json.dumps(
        [str(source.user_id), str(source.workspace_id), str(source.id), turns],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def retained_turns(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    turns = transcript.get("turns")
    if (
        not isinstance(turns, list)
        or not 1 <= len(turns) <= history.MAX_CONVERSATION_TURNS * 2
        or any(
            not isinstance(turn, dict)
            or turn.get("role") not in {"user", "assistant"}
            or not isinstance(turn.get("content"), str)
            for turn in turns
        )
    ):
        raise unavailable()
    return turns


def selected_turns(
    turns: list[dict[str, Any]],
    sequences: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    # Whole completed student messages only. Long turns are unavailable, never
    # truncated into an apparent neutral declaration or a fabricated quotation.
    return [
        {
            "turn_index": index,
            "content": turn["content"],
            "created_at": turn["created_at"],
            "sequence": (sequences or {}).get(str(index)),
        }
        for index, turn in enumerate(turns)
        if turn["role"] == "user" and 1 <= len(turn["content"]) <= 500
    ][-MAX_SOURCE_TURNS:]


async def enqueue_learning(
    database: AsyncSession,
    settings: Settings,
    source: MemoryConversation,
    transcript: dict[str, Any],
    *,
    now: datetime,
    native: bool = False,
) -> MemoryLearningJob | None:
    if not native and not settings.memory_semantic_learning_enabled:
        return None
    turns = retained_turns(transcript)
    try:
        reject_credentials(
            json.dumps([turn["content"] for turn in turns if turn["role"] == "user"]),
            configured_secrets(settings),
        )
    except ValueError:
        return None
    if (
        transcript.get("automatic_extraction_blocked")
        or transcript.get("voice_recall_suppressed")
        or transcript.get("recall_superseded")
        or excluded_source([turn["content"] for turn in turns if turn["role"] == "user"])
        or not selected_turns(turns)
    ):
        return None
    existing = await database.scalar(
        select(MemoryLearningJob).where(
            MemoryLearningJob.source_conversation_id == source.id,
            MemoryLearningJob.sequence == source.last_sequence,
        )
    )
    if existing is not None:
        return existing
    await database.execute(
        delete(MemoryLearningJob).where(
            *history.owner_scope(MemoryLearningJob, source.user_id, source.workspace_id),
            MemoryLearningJob.source_conversation_id == source.id,
            MemoryLearningJob.status == "queued",
        )
    )
    count = await database.scalar(
        select(func.count())
        .select_from(MemoryLearningJob)
        .where(
            *history.owner_scope(MemoryLearningJob, source.user_id, source.workspace_id),
            MemoryLearningJob.status.in_(("queued", "processing")),
        )
    )
    if count is None or count >= MAX_JOBS_PER_OWNER:
        return None
    job = MemoryLearningJob(
        id=uuid4(),
        user_id=source.user_id,
        workspace_id=source.workspace_id,
        source_conversation_id=source.id,
        generation=source.generation,
        sequence=source.last_sequence,
        message_count=len(turns),
        source_digest=source_digest(settings, source, turns),
        status="queued",
        pending_count=0,
        task_id=uuid4(),
        created_at=now,
        updated_at=now,
        expires_at=min(now + REVIEW_LIFETIME, history.utc(source.expires_at))
        if source.expires_at
        else now + REVIEW_LIFETIME,
    )
    database.add(job)
    await database.flush()
    return job


async def current_source(
    database: AsyncSession,
    settings: Settings,
    job: MemoryLearningJob,
    *,
    now: datetime,
    allow_private: bool = False,
) -> tuple[MemoryConversation, dict[str, Any], list[dict[str, Any]]]:
    privacy = await history.privacy_row(database, job.user_id, job.workspace_id)
    user = await database.get(User, job.user_id, populate_existing=True)
    member = await database.get(WorkspaceMembership, (job.workspace_id, job.user_id))
    workspace = await database.get(Workspace, job.workspace_id)
    if (
        privacy is None
        or privacy.generation != job.generation
        or not privacy.history_enabled
        or privacy.off_the_record
        or privacy.disclosure_version != 1
        or user is None
        or user.agent_paused
        or member is None
        or member.role != "owner"
        or workspace is None
        or workspace.workspace_type != "personal"
        or history.utc(job.expires_at) <= now
    ):
        raise unavailable()
    source = await database.scalar(
        select(MemoryConversation)
        .where(
            *history.owner_scope(MemoryConversation, job.user_id, job.workspace_id),
            MemoryConversation.id == job.source_conversation_id,
            MemoryConversation.generation == job.generation,
            MemoryConversation.erased_at.is_(None),
            MemoryConversation.encrypted_transcript.is_not(None),
            (MemoryConversation.expires_at.is_(None) | (MemoryConversation.expires_at > now)),
        )
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise unavailable()
    transcript = history.decrypt(
        settings,
        job.user_id,
        job.workspace_id,
        source.id,
        "transcript",
        source.encrypted_transcript,
    )
    if not isinstance(transcript, dict):
        raise unavailable()
    turns = retained_turns(transcript)
    try:
        reject_credentials(
            json.dumps([turn["content"] for turn in turns if turn["role"] == "user"]),
            configured_secrets(settings),
        )
    except ValueError:
        raise unavailable() from None
    if (
        len(turns) < job.message_count
        or not hmac.compare_digest(
            source_digest(settings, source, turns[: job.message_count]), job.source_digest
        )
        or transcript.get("recall_superseded")
        or (
            not allow_private
            and (
                source.encrypted_summary is None
                or transcript.get("automatic_extraction_blocked")
                or transcript.get("voice_recall_suppressed")
                or excluded_source([turn["content"] for turn in turns if turn["role"] == "user"])
            )
        )
    ):
        raise unavailable()
    return (
        source,
        transcript,
        selected_turns(turns[: job.message_count], transcript.get("turn_sequences")),
    )


def evidence(quote: EvidenceQuote, turns: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        turn
        for turn in turns
        if quote.quote in turn["content"]
        and (quote.turn_index is None or quote.turn_index == turn["turn_index"])
    ]
    if len(matches) != 1:
        raise MemoryError(422, "A memory quote must match one exact completed student source.")
    return matches[0]


def neutral_fragment(turn: dict[str, Any], quote: str, *, timezone: str, now: datetime) -> bool:
    text = turn["content"]
    sentences = [part.strip() for part in re.split(r"(?<=[.!])\s+", text)]
    if (
        any(char in text for char in "\"'‘’“”`?\n\r\t")
        or _UNSAFE_FRAGMENT.search(text)
        or excluded_source((text,))
        or quote.strip() not in sentences
    ):
        return False
    # Positive certification of the whole parent prevents a model from selecting
    # first-person words that the student is merely reporting or reading aloud.
    for sentence in sentences:
        parent = derive_literal_turn(sentence, timezone=timezone, now=now)
        if not (parent.facts or parent.threads):
            return False
    result = derive_literal_turn(quote, timezone=timezone, now=now)
    return bool(result.facts or result.threads)


def validate_output(output: LearningOutput, turns: list[dict[str, Any]]) -> None:
    seen = set()
    for item in (*output.facts, *output.threads, *output.summary):
        match = evidence(item, turns)
        key = (match["turn_index"], item.quote)
        if item in output.facts and key in seen:
            raise MemoryError(422, "A memory result repeats its source.")
        if item in output.facts:
            seen.add(key)


async def apply_learning(
    database: AsyncSession,
    settings: Settings,
    job: MemoryLearningJob,
    output: LearningOutput,
    *,
    claim_id: UUID,
    now: datetime,
) -> dict[str, int]:
    """Caller owns User lock; validate live provenance before any derivative write."""
    source, transcript, turns = await current_source(database, settings, job, now=now)
    digest = hashlib.sha256(output.model_dump_json().encode()).hexdigest()
    if job.status == "completed" and job.output_digest == digest:
        stored = history.decrypt(
            settings, job.user_id, job.workspace_id, job.id, "learning-result", job.encrypted_result
        )
        return {"saved_count": stored["saved_count"], "review_count": job.pending_count}
    if job.status != "processing" or job.claim_id != claim_id:
        raise unavailable()
    validate_output(output, turns)
    user = await database.get(User, job.user_id)
    assert user is not None
    from navox.memory.extraction import learn_completed_turn

    old_fact_rows = await database.execute(
        select(MemoryFact.id, MemoryFact.encrypted_content).where(
            *history.owner_scope(MemoryFact, job.user_id, job.workspace_id)
        )
    )
    old_facts = {row[0]: row[1] for row in old_fact_rows}
    prior_jobs = list(
        await database.scalars(
            select(MemoryLearningJob)
            .where(
                *history.owner_scope(MemoryLearningJob, job.user_id, job.workspace_id),
                MemoryLearningJob.source_conversation_id == source.id,
                MemoryLearningJob.generation == job.generation,
                MemoryLearningJob.encrypted_result.is_not(None),
                MemoryLearningJob.id != job.id,
            )
            .order_by(MemoryLearningJob.created_at.desc(), MemoryLearningJob.id)
            .limit(MAX_SCAN_ROWS)
        )
    )
    prior_quotes = {
        proposal.get("quote_digest")
        for previous in prior_jobs
        for proposal in stored_result(settings, previous)["proposals"]
    }
    proposals = []
    ordered_facts = sorted(
        enumerate(output.facts),
        key=lambda pair: (
            evidence(pair[1], turns)["turn_index"],
            evidence(pair[1], turns)["content"].find(pair[1].quote),
        ),
    )
    for index, item in ordered_facts:
        turn = evidence(item, turns)
        observed = history.utc(datetime.fromisoformat(turn["created_at"]))
        if (
            neutral_fragment(turn, item.quote, timezone=user.timezone, now=observed)
            and derive_literal_turn(item.quote, timezone=user.timezone, now=observed).facts
        ):
            await learn_completed_turn(
                database,
                settings,
                source=source,
                transcript=transcript,
                question=item.quote,
                sequence=turn.get("sequence") or job.sequence,
                now=observed,
                method="semantic_v1",
                source_turn_index=turn["turn_index"],
                source_turn_sequence=turn.get("sequence"),
                source_span_start=turn["content"].find(item.quote),
            )
        else:
            quote_digest = hashlib.sha256(item.quote.encode()).hexdigest()
            if quote_digest in prior_quotes:
                continue
            proposals.append(
                {
                    "id": str(uuid5(job.id, f"proposal:{index}")),
                    "label": item.label,
                    "content": item.quote,
                    "quote_digest": quote_digest,
                    "turn_index": turn["turn_index"],
                    "status": "pending",
                    "sensitive": True,
                }
            )
    for thread_item in output.threads if source.last_sequence == job.sequence else ():
        turn = evidence(thread_item, turns)
        observed = history.utc(datetime.fromisoformat(turn["created_at"]))
        if (
            neutral_fragment(turn, thread_item.quote, timezone=user.timezone, now=observed)
            and derive_literal_turn(thread_item.quote, timezone=user.timezone, now=observed).threads
        ):
            await learn_completed_turn(
                database,
                settings,
                source=source,
                transcript=transcript,
                question=thread_item.quote,
                sequence=turn.get("sequence") or job.sequence,
                now=observed,
                method="semantic_v1",
                source_turn_index=turn["turn_index"],
                source_turn_sequence=turn.get("sequence"),
                source_span_start=turn["content"].find(thread_item.quote),
            )
    await database.flush()
    new_fact_rows = await database.execute(
        select(MemoryFact.id, MemoryFact.encrypted_content).where(
            *history.owner_scope(MemoryFact, job.user_id, job.workspace_id)
        )
    )
    new_facts = {row[0]: row[1] for row in new_fact_rows}
    if transcript.get("recall_superseded"):
        # This source has lost raw-recall authority. Do not retain or count
        # private reviews that the live source guard must reject.
        proposals = []
    # Summary is still exact-source selection, never invented prose or a fact.
    if (
        output.summary
        and source.last_sequence == job.sequence
        and not transcript.get("recall_superseded")
    ):
        summary = "\n".join(item.quote for item in output.summary)[:4000]
        source.encrypted_summary = history.encrypt(
            settings, job.user_id, job.workspace_id, source.id, "summary", summary
        )
    source.encrypted_transcript = history.encrypt(
        settings, job.user_id, job.workspace_id, source.id, "transcript", transcript
    )
    saved_count = sum(
        old_facts.get(identifier) != content for identifier, content in new_facts.items()
    )
    job.encrypted_result = history.encrypt(
        settings,
        job.user_id,
        job.workspace_id,
        job.id,
        "learning-result",
        {
            "schema_version": "memory-learning-result.v1",
            "saved_count": saved_count,
            "proposals": proposals,
        },
    )
    job.output_digest, job.status, job.updated_at = digest, "completed", now
    job.pending_count = len(proposals)
    await database.flush()
    return {"saved_count": saved_count, "review_count": len(proposals)}


def stored_result(settings: Settings, job: MemoryLearningJob) -> dict[str, Any]:
    value = history.decrypt(
        settings, job.user_id, job.workspace_id, job.id, "learning-result", job.encrypted_result
    )
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "memory-learning-result.v1"
        or not isinstance(value.get("proposals"), list)
        or len(value["proposals"]) > 4
    ):
        raise unavailable()
    return value


async def list_reviews(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    *,
    configured: bool,
) -> dict[str, Any]:
    privacy = await history.privacy_row(database, user_id, workspace_id)
    generation = privacy.generation if privacy else 0
    enabled = bool(
        privacy
        and privacy.history_enabled
        and not privacy.off_the_record
        and privacy.disclosure_version == 1
    )
    reason = (
        "off_record"
        if privacy and privacy.off_the_record
        else "history_required"
        if not enabled
        else "ready"
        if configured
        else "unconfigured"
    )
    result: dict[str, Any] = {
        "enabled": enabled,
        "configured": configured,
        "generation": generation,
        "status_reason": reason,
        "pending": [],
        "has_more": False,
    }
    if not enabled:
        return result
    now = await database_now(database)
    rows = list(
        await database.scalars(
            select(MemoryLearningJob)
            .where(
                *history.owner_scope(MemoryLearningJob, user_id, workspace_id),
                MemoryLearningJob.generation == generation,
                MemoryLearningJob.status == "completed",
                MemoryLearningJob.pending_count > 0,
                MemoryLearningJob.expires_at > now,
            )
            .order_by(MemoryLearningJob.created_at.desc(), MemoryLearningJob.id)
            .limit(MAX_SCAN_ROWS + 1)
        )
    )
    pending = []
    for job in rows[:MAX_SCAN_ROWS]:
        try:
            source, _, _ = await current_source(database, settings, job, now=now)
        except MemoryError:
            continue
        for proposal in stored_result(settings, job)["proposals"]:
            if proposal["status"] != "pending":
                continue
            pending.append(
                {
                    "id": UUID(proposal["id"]),
                    "label": proposal["label"],
                    "content": proposal["content"],
                    "source_conversation_id": source.id,
                    "source_kind": source.source_kind,
                    "sensitive": True,
                    "created_at": history.utc(job.created_at),
                    "expires_at": history.utc(job.expires_at),
                }
            )
            if len(pending) > MAX_REVIEW_ROWS:
                break
        if len(pending) > MAX_REVIEW_ROWS:
            break
    result["pending"] = pending[:MAX_REVIEW_ROWS]
    result["has_more"] = len(pending) > MAX_REVIEW_ROWS or len(rows) > MAX_SCAN_ROWS
    return result


async def sweep_learning_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    limit: int = 100,
) -> int:
    """Independent timed erasure; a source's Never choice cannot extend review TTL."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Choose between one and 1000 expired learning jobs")
    async with factory() as database:
        now = await database_now(database)
        rows = list(
            await database.execute(
                select(
                    MemoryLearningJob.id,
                    MemoryLearningJob.user_id,
                    MemoryLearningJob.workspace_id,
                )
                .where(MemoryLearningJob.expires_at <= now)
                .order_by(MemoryLearningJob.expires_at)
                .limit(limit)
            )
        )
    erased = 0
    for identifier, user_id, workspace_id in rows:
        async with factory() as database:
            await lock_owner(database, user_id)
            job = await database.get(MemoryLearningJob, identifier, populate_existing=True)
            if (
                job is not None
                and job.workspace_id == workspace_id
                and history.utc(job.expires_at) <= await database_now(database)
            ):
                await database.delete(job)
                erased += 1
            await database.commit()
    return erased


async def find_review(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    identifier: UUID,
    generation: int,
    *,
    now: datetime,
) -> tuple[MemoryLearningJob, dict[str, Any], dict[str, Any]]:
    rows = await database.scalars(
        select(MemoryLearningJob)
        .where(
            *history.owner_scope(MemoryLearningJob, user_id, workspace_id),
            MemoryLearningJob.generation == generation,
            MemoryLearningJob.status == "completed",
            MemoryLearningJob.pending_count > 0,
            MemoryLearningJob.expires_at > now,
        )
        .order_by(MemoryLearningJob.created_at.desc(), MemoryLearningJob.id)
        .limit(MAX_SCAN_ROWS)
    )
    for job in rows:
        value = stored_result(settings, job)
        proposal = next(
            (item for item in value["proposals"] if item["id"] == str(identifier)), None
        )
        if proposal is not None and proposal["status"] == "pending":
            return job, value, proposal
    raise unavailable()


async def confirm_review(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    identifier: UUID,
    payload: LearningConfirmation,
) -> dict[str, Any]:
    await lock_owner(database, user_id)
    now = await database_now(database)
    privacy = await history.privacy_row(database, user_id, workspace_id)
    # A matching receipt can be read after this save's own generation bump. It
    # cannot overwrite a correction, recreate a deleted fact or rerun invalidation.
    saved = await database.get(MemoryFact, identifier, populate_existing=True)
    if saved is not None and (saved.user_id, saved.workspace_id) == (user_id, workspace_id):
        content = history.decrypt(
            settings, user_id, workspace_id, identifier, "fact", saved.encrypted_content
        )
        receipt = content.get("learning_receipt", {})
        if (
            content.get("extraction_method") != "reviewed_semantic_v1"
            or not saved.sensitive
            or content.get("private_by_default") is not True
            or privacy is None
            or receipt.get("confirmed_generation") != privacy.generation
            or receipt.get("review_generation") != payload.generation
            or history.utc(datetime.fromisoformat(receipt["expires_at"])) <= now
        ):
            raise unavailable()
        job = await database.get(MemoryLearningJob, UUID(receipt["job_id"]))
        if job is None or (job.user_id, job.workspace_id) != (user_id, workspace_id):
            raise unavailable()
        source = await database.get(MemoryConversation, saved.source_conversation_id)
        if source is None or source.erased_at is not None or source.encrypted_transcript is None:
            raise unavailable()
        return history.fact_view(saved, settings)
    if privacy is None or privacy.generation != payload.generation:
        raise unavailable()
    if not payload.sensitive_agreement:
        raise MemoryError(422, "Agree separately before saving this private or sensitive detail.")
    count = await database.scalar(
        select(func.count())
        .select_from(MemoryFact)
        .where(*history.owner_scope(MemoryFact, user_id, workspace_id))
    )
    if count is None or count >= 100:
        raise MemoryError(409, "Delete a saved fact before adding another.")
    job, value, proposal = await find_review(
        database, settings, user_id, workspace_id, identifier, payload.generation, now=now
    )
    source, transcript, turns = await current_source(database, settings, job, now=now)
    evidence(EvidenceQuote(turn_index=proposal["turn_index"], quote=proposal["content"]), turns)
    await history.bump_and_invalidate(database, user_id, workspace_id)
    assert privacy is not None
    history.suppress_source_voice_recall(settings, source, transcript)
    fact = MemoryFact(
        id=identifier,
        user_id=user_id,
        workspace_id=workspace_id,
        source_conversation_id=source.id,
        source_kind=source.source_kind,
        sensitive=True,
        created_at=now,
        updated_at=now,
        encrypted_content=history.encrypt(
            settings,
            user_id,
            workspace_id,
            identifier,
            "fact",
            {
                "label": proposal["label"],
                "content": proposal["content"],
                "extraction_method": "reviewed_semantic_v1",
                "private_by_default": True,
                "source_turn_index": proposal["turn_index"],
                "learning_receipt": {
                    "job_id": str(job.id),
                    "review_generation": payload.generation,
                    "confirmed_generation": privacy.generation,
                    "expires_at": history.utc(job.expires_at).isoformat(),
                },
            },
        ),
    )
    database.add(fact)
    # Saving one private suggestion withdraws the source and old-generation
    # sibling reviews. Keep only the matching content-free job/Fact receipt.
    job.pending_count, job.encrypted_result, job.status = 0, None, "completed"
    await database.commit()
    return history.fact_view(fact, settings)


async def dismiss_review(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    identifier: UUID,
    generation: int,
) -> None:
    await lock_owner(database, user_id)
    now = await database_now(database)
    privacy = await history.privacy_row(database, user_id, workspace_id)
    if privacy is None or privacy.generation != generation:
        raise unavailable()
    job, value, proposal = await find_review(
        database, settings, user_id, workspace_id, identifier, generation, now=now
    )
    await current_source(database, settings, job, now=now)
    proposal["status"] = "dismissed"
    # Dismissal retains only a content-free receipt. It is never a claim that an
    # uncertain confirmation did not save a fact.
    proposal.pop("label", None)
    proposal.pop("content", None)
    job.pending_count -= 1
    job.encrypted_result = history.encrypt(
        settings, user_id, workspace_id, job.id, "learning-result", value
    )
    await database.commit()
