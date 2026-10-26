"""Source-bound memory proposals. A model can prepare a review, never save it.

Reviews are short-lived encrypted capabilities delivered only to the browser.
They bind the issuing call/login, owner, source turn and privacy generation.
Unknown semantics are private by default; no model classification grants recall.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.conversation_memory import MemoryConversation, MemoryFact
from navox.db.models import User, UserSession
from navox.db.vox import VoxCall, VoxState
from navox.memory import history
from navox.memory.extraction_rules import derive_literal_turn, excluded_source
from navox.memory.schemas import safe_text
from navox.memory.service import MemoryError, lock_owner

REVIEW_TTL_SECONDS = 300
MAX_PROPOSAL_LENGTH = 8192
REVIEW_KIND = "reviewed-memory-candidate.v1"
_CREDENTIAL_TOPIC = re.compile(
    r"\b(?:password|passcode|credentials?|api\s*key|access\s*token|refresh\s*token|"
    r"client\s*secret|private\s*key|secret\s*key|credit\s*card|bank\s*account|"
    r"social\s*security|ssn)\b",
    re.IGNORECASE,
)


def candidate_text(value: str) -> str:
    safe_text(value)
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith("C") or character.isspace()
    )
    if _CREDENTIAL_TOPIC.search(normalized):
        raise ValueError("Do not save credentials in memory")
    return value


class CandidateArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )
    label: str = Field(min_length=1, max_length=80, repr=False)
    quote: str = Field(min_length=1, max_length=500, repr=False)

    @field_validator("label", "quote")
    @classmethod
    def safe(cls, value: str) -> str:
        return candidate_text(value)


class CandidateConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    review_id: UUID
    proposal: str = Field(min_length=1, max_length=MAX_PROPOSAL_LENGTH, repr=False)
    confirmation: Literal["SAVE MEMORY"]
    sensitive_agreement: bool = Field(default=False, strict=True)


class CandidateProof(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["memory-save-review.v1"] = "memory-save-review.v1"
    call_id: UUID
    auth_session_id: UUID
    call_generation: int = Field(ge=0)
    source_conversation_id: UUID
    privacy_generation: int = Field(ge=0)
    turn_index: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=80, repr=False)
    quote: str = Field(min_length=1, max_length=500, repr=False)
    sensitive: bool
    issued_at: datetime
    expires_at: datetime


def unavailable() -> MemoryError:
    return MemoryError(409, "This memory review changed or expired. Ask for a new review.")


async def privacy_generation(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> int:
    privacy = await history.privacy_row(database, user_id, workspace_id)
    if (
        privacy is None
        or not privacy.history_enabled
        or privacy.off_the_record
        or privacy.disclosure_version != 1
    ):
        raise unavailable()
    return privacy.generation


async def origin_call(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    call_id: UUID,
    *,
    now: datetime,
    active: bool,
) -> VoxCall:
    row = await database.scalar(
        select(VoxCall)
        .join(VoxState, (VoxState.user_id == user_id) & (VoxState.workspace_id == workspace_id))
        .join(UserSession, UserSession.id == VoxCall.auth_session_id)
        .join(User, User.id == VoxCall.user_id)
        .where(
            VoxCall.id == call_id,
            VoxCall.user_id == user_id,
            VoxCall.workspace_id == workspace_id,
            VoxCall.generation == VoxState.generation,
            User.agent_paused.is_(False),
            UserSession.user_id == user_id,
            UserSession.client_type == "web",
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .execution_options(populate_existing=True)
    )
    if row is None or active and (row.status != "reserved" or history.utc(row.expires_at) <= now):
        raise unavailable()
    return row


async def source_record(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    call_id: UUID,
    generation: int,
    *,
    now: datetime,
) -> tuple[MemoryConversation, dict[str, Any]]:
    source = await database.scalar(
        select(MemoryConversation)
        .where(
            *history.owner_scope(MemoryConversation, user_id, workspace_id),
            MemoryConversation.source_kind == "vox",
            MemoryConversation.source_id == call_id,
            MemoryConversation.generation == generation,
            MemoryConversation.erased_at.is_(None),
            MemoryConversation.encrypted_transcript.is_not(None),
            MemoryConversation.encrypted_summary.is_not(None),
            (MemoryConversation.expires_at.is_(None) | (MemoryConversation.expires_at > now)),
        )
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise unavailable()
    transcript = history.decrypt(
        settings, user_id, workspace_id, source.id, "transcript", source.encrypted_transcript
    )
    if (
        not isinstance(transcript, dict)
        or not isinstance(transcript.get("turns"), list)
        or len(transcript["turns"]) > history.MAX_CONVERSATION_TURNS * 2
        or any(
            not isinstance(turn, dict)
            or turn.get("role") not in {"user", "assistant"}
            or not isinstance(turn.get("content"), str)
            for turn in transcript["turns"]
        )
        or transcript.get("recall_superseded")
    ):
        raise unavailable()
    return source, transcript


def source_sensitivity(
    quote: str, label: str, transcript: dict[str, Any], *, now: datetime, timezone: str
) -> bool:
    """Only an existing closed neutral rule may lower the default private class."""
    if transcript.get("automatic_extraction_blocked") or excluded_source((quote, label)):
        return True
    derived = derive_literal_turn(quote, now=now, timezone=timezone)
    return not any(item.content == quote and item.label == label for item in derived.facts)


async def prepare_candidate(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    call_id: UUID,
    arguments: CandidateArguments,
) -> dict[str, Any]:
    await lock_owner(database, user_id)
    now = history.utc(await database_now(database))
    generation = await privacy_generation(database, user_id, workspace_id)
    call = await origin_call(database, user_id, workspace_id, call_id, now=now, active=True)
    source, transcript = await source_record(
        database, settings, user_id, workspace_id, call_id, generation, now=now
    )
    user = await database.get(User, user_id)
    assert user is not None
    turns = transcript["turns"]
    # Complete messages avoid quote-mining a negation/reported statement. Only
    # the last four completed exchanges from this exact call are candidates.
    turn_index = next(
        (
            index
            for index in range(len(turns) - 1, max(-1, len(turns) - 9), -1)
            if isinstance(turns[index], dict)
            and turns[index].get("role") == "user"
            and turns[index].get("content", "").strip() == arguments.quote
        ),
        None,
    )
    if turn_index is None:
        raise unavailable()
    sensitive = source_sensitivity(
        arguments.quote, arguments.label, transcript, now=now, timezone=user.timezone
    )
    identifier = uuid4()
    proof = CandidateProof(
        call_id=call.id,
        auth_session_id=call.auth_session_id,
        call_generation=call.generation,
        source_conversation_id=source.id,
        privacy_generation=generation,
        turn_index=turn_index,
        label=arguments.label,
        quote=arguments.quote,
        sensitive=sensitive,
        issued_at=now,
        expires_at=now + timedelta(seconds=REVIEW_TTL_SECONDS),
    )
    sealed = history.encrypt(
        settings, user_id, workspace_id, identifier, REVIEW_KIND, proof.model_dump(mode="json")
    )
    if len(sealed) > MAX_PROPOSAL_LENGTH:
        raise unavailable()
    return {
        "status": "review_required",
        "applied": False,
        "review": {
            "id": str(identifier),
            "proposal": sealed,
            "label": proof.label,
            "content": proof.quote,
            "source_conversation_id": str(source.id),
            "source_kind": "vox",
            "source_turn": sum(turn.get("role") == "user" for turn in turns[: turn_index + 1]),
            "sensitive": sensitive,
            "expires_at": proof.expires_at.isoformat(),
        },
        "destination": "/settings#memory-privacy",
        "message": "Review this exact statement in Memory & privacy. Nothing has been saved.",
    }


async def confirm_candidate(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    auth_session_id: UUID,
    payload: CandidateConfirmation,
) -> dict[str, Any]:
    await lock_owner(database, user_id)
    now = history.utc(await database_now(database))
    try:
        proof = CandidateProof.model_validate(
            history.decrypt(
                settings, user_id, workspace_id, payload.review_id, REVIEW_KIND, payload.proposal
            )
        )
        if (
            proof.issued_at.tzinfo is None
            or proof.expires_at.tzinfo is None
            or not proof.issued_at <= now <= proof.expires_at
            or proof.expires_at - proof.issued_at != timedelta(seconds=REVIEW_TTL_SECONDS)
            or proof.auth_session_id != auth_session_id
        ):
            raise unavailable()
        candidate_text(proof.label)
        candidate_text(proof.quote)
    except (MemoryError, ValidationError, ValueError, TypeError):
        raise unavailable() from None
    generation = await privacy_generation(database, user_id, workspace_id)
    if generation != proof.privacy_generation:
        raise unavailable()
    call = await origin_call(database, user_id, workspace_id, proof.call_id, now=now, active=False)
    if (call.auth_session_id, call.generation) != (proof.auth_session_id, proof.call_generation):
        raise unavailable()
    source, transcript = await source_record(
        database, settings, user_id, workspace_id, call.id, generation, now=now
    )
    turns = transcript["turns"]
    user = await database.get(User, user_id)
    assert user is not None
    if (
        source.id != proof.source_conversation_id
        or proof.turn_index >= len(turns)
        or turns[proof.turn_index].get("role") != "user"
        or turns[proof.turn_index].get("content", "").strip() != proof.quote
        or source_sensitivity(proof.quote, proof.label, transcript, now=now, timezone=user.timezone)
        != proof.sensitive
    ):
        raise unavailable()
    if proof.sensitive and not payload.sensitive_agreement:
        raise MemoryError(422, "Agree separately before saving this private or sensitive detail.")
    existing = await database.get(MemoryFact, payload.review_id)
    if existing is not None:
        if (existing.user_id, existing.workspace_id) != (user_id, workspace_id):
            raise unavailable()
        # The review ID is the write ID. Retries return the current saved fact;
        # they cannot reset a later edit or create a duplicate.
        return history.fact_view(existing, settings)
    count = list(
        await database.scalars(
            select(MemoryFact.id)
            .where(*history.owner_scope(MemoryFact, user_id, workspace_id))
            .limit(100)
        )
    )
    if len(count) >= 100:
        raise MemoryError(409, "Delete a saved fact before adding another.")
    row = MemoryFact(
        id=payload.review_id,
        user_id=user_id,
        workspace_id=workspace_id,
        source_conversation_id=source.id,
        source_kind="vox",
        sensitive=proof.sensitive,
        created_at=now,
        updated_at=now,
        encrypted_content=history.encrypt(
            settings,
            user_id,
            workspace_id,
            payload.review_id,
            "fact",
            {
                "label": proof.label,
                "content": proof.quote,
                "extraction_method": "reviewed_candidate_v1",
                "source_turn": proof.turn_index,
                "private_by_default": proof.sensitive,
            },
        ),
    )
    database.add(row)
    await database.commit()
    return history.fact_view(row, settings)
