"""Select and fingerprint older cards without re-running source ingestion."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    IntelligenceFeedback,
    ObservationEvidence,
    OperationalObservation,
)

POLICY_VERSION = "gmail-recheck.v1"
ACTIVE_STATUSES = ("candidate", "confirmed", "attention", "upcoming")
MAX_SOURCES = 3
MAX_REFERENCES = 32


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class CardSnapshot:
    digest: str
    evidence: tuple[ObservationEvidence, ...]
    reason: str | None


async def snapshot_card(
    database: AsyncSession, commitment: Commitment, connection_id: UUID
) -> CardSnapshot:
    """Include every support/state change; do not treat missing support as irrelevant."""
    sources = list(
        await database.scalars(
            select(CommitmentSource)
            .where(CommitmentSource.commitment_id == commitment.id)
            .order_by(CommitmentSource.id)
            .limit(MAX_REFERENCES + 1)
            .execution_options(populate_existing=True)
        )
    )
    metadata = commitment.intelligence_metadata or {}
    user_feedback = await database.scalar(
        select(IntelligenceFeedback.id)
        .where(
            IntelligenceFeedback.target_type == "commitment",
            IntelligenceFeedback.target_id == commitment.id,
            IntelligenceFeedback.user_id == commitment.user_id,
            IntelligenceFeedback.workspace_id == commitment.workspace_id,
        )
        .limit(1)
    )
    user_action = await database.scalar(
        select(AuditEvent.id)
        .where(
            AuditEvent.entity_type == "commitment",
            AuditEvent.entity_id == commitment.id,
            AuditEvent.actor_type == "user",
            AuditEvent.user_id == commitment.user_id,
            AuditEvent.workspace_id == commitment.workspace_id,
        )
        .limit(1)
    )
    reason = None
    if (
        commitment.created_by != "ai"
        or commitment.status not in ACTIVE_STATUSES
        or metadata.get("user_review")
        or user_feedback is not None
        or user_action is not None
    ):
        reason = "user_decision_or_inactive"
    elif metadata.get("email_relevance") is not None:
        reason = "already_current_policy"
    elif not sources or len(sources) > MAX_REFERENCES:
        reason = "unverified_evidence"
    elif any(
        source.provider != "google"
        or source.source_type != "gmail_message"
        or source.connection_id != connection_id
        or not source.external_resource_id
        for source in sources
    ):
        reason = "multiple_source_types"

    evidence: list[ObservationEvidence] = []
    observation_state: list[dict[str, object]] = []
    if reason is None:
        for source in sources:
            try:
                observation_id = UUID(source.source_metadata.get("observation_id", ""))
            except (ValueError, TypeError, AttributeError):
                reason = "unverified_evidence"
                break
            observation = await database.scalar(
                select(OperationalObservation)
                .where(
                    OperationalObservation.id == observation_id,
                    OperationalObservation.user_id == commitment.user_id,
                    OperationalObservation.workspace_id == commitment.workspace_id,
                )
                .execution_options(populate_existing=True)
            )
            if observation is None:
                reason = "unverified_evidence"
                break
            observation_state.append(
                {
                    "id": str(observation.id),
                    "status": observation.status,
                    "version": observation.extractor_version,
                }
            )
            if observation.status in {"SUPERSEDED", "HISTORICAL"}:
                continue
            references = list(
                await database.scalars(
                    select(ObservationEvidence)
                    .where(
                        ObservationEvidence.observation_id == observation_id,
                    )
                    .limit(MAX_REFERENCES + 1)
                    .execution_options(populate_existing=True)
                )
            )
            if (
                observation.status not in {"ACTIVE", "NEEDS_CONFIRMATION"}
                or observation.extractor_version != "operational-extraction.v1"
                or len(references) != 1
            ):
                reason = "unverified_evidence"
                break
            reference = references[0]
            if (
                reference.connection_id != connection_id
                or reference.provider != "google"
                or reference.source_type != "gmail_message"
                or reference.external_resource_id != source.external_resource_id
                or not reference.source_hash
                or reference.source_hash != source.source_metadata.get("source_hash")
            ):
                reason = "unverified_evidence"
                break
            evidence.append(reference)
        if not evidence and reason is None:
            reason = "unverified_evidence"
        if len({item.external_resource_id for item in evidence}) > MAX_SOURCES:
            reason = "too_many_sources"

    meaningful = {
        name: getattr(commitment, name)
        for name in (
            "id",
            "user_id",
            "workspace_id",
            "created_by",
            "status",
            "title",
            "description",
            "commitment_type",
            "priority",
            "due_at",
            "remind_at",
            "confidence",
            "completed_at",
            "waiting_since",
            "last_verified_at",
            "valid_from",
            "valid_until",
            "intelligence_metadata",
        )
    }
    data = {
        "card": meaningful,
        "sources": [
            [
                str(s.id),
                str(s.connection_id),
                s.provider,
                s.source_type,
                s.external_resource_id,
                s.source_metadata,
            ]
            for s in sources
        ],
        "observations": observation_state,
        "evidence": [
            [str(e.id), e.source_hash, e.evidence_locator, e.observed_at] for e in evidence
        ],
        "feedback": str(user_feedback),
        "user_action": str(user_action),
        "reason": reason,
    }
    digest = sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    return CardSnapshot(digest, tuple(evidence), reason)
