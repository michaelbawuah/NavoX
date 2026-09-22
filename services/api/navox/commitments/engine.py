import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.commitments.policy import CommitmentConfidencePolicy
from navox.commitments.schema import CommitmentExtraction, ExtractedCommitmentCandidate
from navox.db.models import Commitment, CommitmentRelation, CommitmentSource, IncomingEvent


def normalized_dedupe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def commitment_dedupe_key(candidate: ExtractedCommitmentCandidate) -> str:
    due_date = candidate.due_at.astimezone(UTC).date().isoformat() if candidate.due_at else ""
    canonical_value = "\x00".join(
        (candidate.type, normalized_dedupe_text(candidate.title), due_date)
    )
    return sha256(canonical_value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CommitmentProcessingResult:
    commitment_ids: tuple[UUID, ...]
    created_count: int
    deduplicated_count: int
    suppressed_count: int
    relation_count: int


class CommitmentEngine:
    """Converts validated model output into content-minimized operational truth."""

    def __init__(self, confidence_policy: CommitmentConfidencePolicy) -> None:
        self.confidence_policy = confidence_policy

    async def process_model_output(
        self,
        database: AsyncSession,
        incoming_event_id: UUID,
        model_output: Mapping[str, Any],
    ) -> CommitmentProcessingResult:
        extraction = CommitmentExtraction.model_validate(model_output)
        return await self.process_extraction(database, incoming_event_id, extraction)

    async def process_extraction(
        self,
        database: AsyncSession,
        incoming_event_id: UUID,
        extraction: CommitmentExtraction,
    ) -> CommitmentProcessingResult:
        event = await database.get(IncomingEvent, incoming_event_id)
        if event is None or event.status != "processed":
            raise ValueError("A processed incoming event is required for commitment extraction")

        resolved_commitments: list[Commitment | None] = []
        commitment_ids: list[UUID] = []
        created_count = 0
        deduplicated_count = 0
        suppressed_count = 0

        for candidate in extraction.candidates:
            commitment_status = self.confidence_policy.status_for(candidate)
            if commitment_status is None:
                resolved_commitments.append(None)
                suppressed_count += 1
                continue

            commitment, created = await self.resolve_commitment(
                database,
                event,
                candidate,
                commitment_status,
            )
            await self.attach_provenance(database, commitment, event)
            resolved_commitments.append(commitment)
            commitment_ids.append(commitment.id)
            if created:
                created_count += 1
            else:
                deduplicated_count += 1

        relation_count = await self.persist_relations(database, extraction, resolved_commitments)
        await database.commit()
        return CommitmentProcessingResult(
            commitment_ids=tuple(commitment_ids),
            created_count=created_count,
            deduplicated_count=deduplicated_count,
            suppressed_count=suppressed_count,
            relation_count=relation_count,
        )

    async def resolve_commitment(
        self,
        database: AsyncSession,
        event: IncomingEvent,
        candidate: ExtractedCommitmentCandidate,
        commitment_status: str,
    ) -> tuple[Commitment, bool]:
        dedupe_key = commitment_dedupe_key(candidate)
        existing = await database.scalar(
            select(Commitment).where(
                Commitment.workspace_id == event.workspace_id,
                Commitment.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return existing, False

        commitment = Commitment(
            user_id=event.user_id,
            workspace_id=event.workspace_id,
            commitment_type=candidate.type,
            title=candidate.title,
            description=candidate.description,
            status=commitment_status,
            priority=candidate.priority,
            due_at=candidate.due_at,
            confidence=candidate.confidence,
            created_by="ai",
            dedupe_key=dedupe_key,
            valid_from=event.occurred_at or event.received_at,
            last_verified_at=event.processed_at or event.received_at,
        )
        try:
            async with database.begin_nested():
                database.add(commitment)
                await database.flush()
        except IntegrityError:
            existing = await database.scalar(
                select(Commitment).where(
                    Commitment.workspace_id == event.workspace_id,
                    Commitment.dedupe_key == dedupe_key,
                )
            )
            if existing is not None:
                return existing, False
            raise
        return commitment, True

    async def attach_provenance(
        self,
        database: AsyncSession,
        commitment: Commitment,
        event: IncomingEvent,
    ) -> None:
        source = await database.scalar(
            select(CommitmentSource).where(
                CommitmentSource.commitment_id == commitment.id,
                CommitmentSource.incoming_event_id == event.id,
            )
        )
        if source is None:
            database.add(
                CommitmentSource(
                    commitment_id=commitment.id,
                    connection_id=event.connection_id,
                    incoming_event_id=event.id,
                    provider=event.provider,
                    source_type=event.source,
                    external_resource_id=event.external_resource_id,
                    source_metadata={"event_type": event.event_type},
                )
            )

    async def persist_relations(
        self,
        database: AsyncSession,
        extraction: CommitmentExtraction,
        resolved_commitments: list[Commitment | None],
    ) -> int:
        relation_count = 0
        for relation in extraction.relations:
            source = resolved_commitments[relation.from_index]
            destination = resolved_commitments[relation.to_index]
            if source is None or destination is None or source.id == destination.id:
                continue
            existing = await database.scalar(
                select(CommitmentRelation).where(
                    CommitmentRelation.from_commitment_id == source.id,
                    CommitmentRelation.to_commitment_id == destination.id,
                    CommitmentRelation.relation_type == relation.relation_type,
                )
            )
            if existing is None:
                database.add(
                    CommitmentRelation(
                        from_commitment_id=source.id,
                        to_commitment_id=destination.id,
                        relation_type=relation.relation_type,
                    )
                )
                relation_count += 1
        return relation_count
