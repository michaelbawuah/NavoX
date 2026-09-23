"""Evidence-first resolution from validated proposals into tenant-owned state.

The caller owns the transaction. A connection row lock serializes its resource
updates, while stable IDs make replays safe without retaining source bodies.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentRelation,
    CommitmentSource,
    Connection,
    ObservationEvidence,
    OperationalObservation,
    Person,
    PersonIdentity,
    User,
    WorkspaceMembership,
)
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.intelligence.extraction import (
    INSTRUCTION_LIKE_MARKERS,
    EvidenceSpan,
    OperationalExtractionResult,
    OperationalObservationCandidate,
    source_document_hash,
)
from navox.intelligence.state import TERMINAL_STATUSES, infer_state
from navox.intelligence.temporal import TemporalResolution, resolve_temporal

_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "i",
        "you",
        "we",
        "please",
        "to",
        "for",
        "by",
        "before",
        "after",
        "my",
        "your",
        "our",
        "is",
        "it",
        "at",
        "on",
        "of",
        "and",
        "tomorrow",
        "today",
        "send",
        "sent",
        "submit",
        "submitted",
        "provide",
        "get",
        "obtain",
        "have",
        "has",
        "been",
        "was",
        "attached",
        "approve",
        "approved",
        "complete",
        "completed",
        "here",
    }
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if token not in _STOP}


def _stable_id(*parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, "navox:spec002:" + ":".join(str(part) for part in parts))


def _fact_key(candidate: OperationalObservationCandidate) -> str:
    return " ".join(sorted(_tokens(f"{candidate.action_text or ''} {candidate.object_text or ''}")))


def _identity_value(identity: SourceIdentity) -> str:
    # Email equality is stable; display names are never sufficient for a merge.
    return (
        identity.identity_value.casefold()
        if identity.identity_type == "email"
        else identity.identity_value
    )


async def resolve_identity(
    database: AsyncSession, *, workspace_id: UUID, identity: SourceIdentity
) -> UUID:
    value = _identity_value(identity)
    identity_type = identity.identity_type
    if identity_type != "email" and identity.provider:
        identity_type = f"{identity.provider}:{identity_type}"[:64]
    existing = await database.scalar(
        select(PersonIdentity).where(
            PersonIdentity.workspace_id == workspace_id,
            PersonIdentity.identity_type == identity_type,
            PersonIdentity.identity_value == value,
        )
    )
    if existing is not None:
        person = await database.scalar(
            select(Person).where(
                Person.id == existing.person_id,
                Person.workspace_id == workspace_id,
            )
        )
        if person is None:
            raise ValueError("Identity points outside its workspace")
        return person.id
    person_id = _stable_id("person", workspace_id, identity_type, value)
    # Savepoint handles the same stable identity arriving on two connections.
    from sqlalchemy.exc import IntegrityError

    try:
        async with database.begin_nested():
            database.add(
                Person(
                    id=person_id,
                    workspace_id=workspace_id,
                    canonical_name=identity.display_name or value,
                )
            )
            await database.flush()
            database.add(
                PersonIdentity(
                    id=_stable_id("identity", workspace_id, identity_type, value),
                    workspace_id=workspace_id,
                    person_id=person_id,
                    provider=identity.provider,
                    identity_type=identity_type,
                    identity_value=value,
                    confidence=Decimal("1"),
                )
            )
            await database.flush()
    except IntegrityError:
        existing = await database.scalar(
            select(PersonIdentity).where(
                PersonIdentity.workspace_id == workspace_id,
                PersonIdentity.identity_type == identity_type,
                PersonIdentity.identity_value == value,
            )
        )
        if existing is None:
            raise
        return UUID(str(existing.person_id))
    return person_id


async def _record_observation(
    database: AsyncSession,
    *,
    connection: Connection,
    document: SourceDocument,
    result: OperationalExtractionResult,
    candidate: OperationalObservationCandidate,
    temporal: TemporalResolution,
    person_id: UUID | None,
    confidence: float,
) -> OperationalObservation:
    fact_digest = sha256(candidate.model_dump_json().encode()).hexdigest()
    observation_id = _stable_id(
        "observation",
        connection.id,
        document.external_id,
        result.source_hash,
        fact_digest,
    )
    existing = await database.get(OperationalObservation, observation_id)
    if existing:
        return existing
    observation = OperationalObservation(
        id=observation_id,
        workspace_id=connection.workspace_id,
        user_id=connection.user_id,
        observation_type=candidate.observation_type,
        subject_person_id=person_id,
        action_text=candidate.action_text,
        object_text=candidate.object_text,
        effective_at=temporal.resolved_at,
        confidence=Decimal(str(round(confidence, 3))),
        status="ACTIVE" if confidence >= 0.7 else "SUPPRESSED",
        extractor_version=result.extractor_version,
        model_provider=result.model_provider,
        model_name=result.model_name,
    )
    database.add(observation)
    await database.flush()
    database.add(
        ObservationEvidence(
            id=_stable_id("evidence", observation_id),
            observation_id=observation_id,
            connection_id=connection.id,
            provider=document.provider,
            source_type=document.source_type,
            external_resource_id=document.external_id,
            # Offsets and hashes permit inspection after authorized source retrieval.
            # We deliberately do not duplicate the cited passage or whole message.
            evidence_locator={
                "spans": [
                    {
                        "source": span.source,
                        "start_char": span.start_char,
                        "end_char": span.end_char,
                    }
                    for span in candidate.evidence
                ],
                "temporal": temporal.as_metadata(),
                "external_parent_id": document.external_parent_id,
            },
            source_hash=result.source_hash,
            observed_at=document.occurred_at,
        )
    )
    return observation


async def _record_auxiliary_fact(
    database: AsyncSession,
    *,
    connection: Connection,
    document: SourceDocument,
    result: OperationalExtractionResult,
    fact_type: str,
    action_text: str,
    object_text: str,
    confidence: float,
    spans: list[EvidenceSpan],
    temporal: TemporalResolution | None = None,
    subject_person_id: UUID | None = None,
    object_person_id: UUID | None = None,
) -> UUID:
    """Keep grounded relationships/dates/people even when no commitment follows."""
    observation_id = _stable_id(
        "auxiliary",
        connection.id,
        document.source_type,
        document.external_id,
        result.source_hash,
        fact_type,
        action_text,
        object_text,
    )
    if await database.get(OperationalObservation, observation_id):
        return observation_id
    database.add(
        OperationalObservation(
            id=observation_id,
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
            observation_type=fact_type,
            subject_person_id=subject_person_id,
            object_person_id=object_person_id,
            action_text=action_text,
            object_text=object_text,
            effective_at=temporal.resolved_at if temporal else None,
            confidence=Decimal(str(round(confidence, 3))),
            status="ACTIVE" if confidence >= 0.7 else "SUPPRESSED",
            extractor_version=result.extractor_version,
            model_provider=result.model_provider,
            model_name=result.model_name,
        )
    )
    await database.flush()
    database.add(
        ObservationEvidence(
            id=_stable_id("evidence", observation_id),
            observation_id=observation_id,
            connection_id=connection.id,
            provider=document.provider,
            source_type=document.source_type,
            external_resource_id=document.external_id,
            source_hash=result.source_hash,
            observed_at=document.occurred_at,
            evidence_locator={
                "spans": [
                    {
                        "source": span.source,
                        "start_char": span.start_char,
                        "end_char": span.end_char,
                    }
                    for span in spans
                ],
                "temporal": temporal.as_metadata() if temporal else None,
            },
        )
    )
    return observation_id


def _audit(
    database: AsyncSession,
    *,
    connection: Connection,
    commitment: Commitment,
    observation_id: UUID | None,
    outcome: str,
) -> None:
    database.add(
        AuditEvent(
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
            event_type="intelligence.resolution",
            actor_type="system",
            entity_type="commitment",
            entity_id=commitment.id,
            event_metadata={
                "outcome": outcome,
                "observation_id": str(observation_id) if observation_id else None,
            },
        )
    )


async def _attach_source(
    database: AsyncSession,
    *,
    commitment: Commitment,
    observation: OperationalObservation,
    connection: Connection,
    document: SourceDocument,
    result: OperationalExtractionResult,
) -> bool:
    source_id = _stable_id("commitment_source", commitment.id, observation.id)
    if await database.get(CommitmentSource, source_id):
        return False
    database.add(
        CommitmentSource(
            id=source_id,
            commitment_id=commitment.id,
            connection_id=connection.id,
            provider=document.provider,
            source_type=document.source_type,
            external_resource_id=document.external_id,
            source_metadata={
                "observation_id": str(observation.id),
                "source_hash": result.source_hash,
                "external_parent_id": document.external_parent_id or "",
                "observed_at": document.occurred_at.isoformat(),
            },
        )
    )
    return True


async def _existing_candidates(
    database: AsyncSession,
    *,
    connection: Connection,
    document: SourceDocument,
) -> list[Commitment]:
    resource_filters = [CommitmentSource.external_resource_id == document.external_id]
    if document.source_type == "gmail_message" and document.external_parent_id:
        resource_filters.append(
            CommitmentSource.source_metadata["external_parent_id"].as_string()
            == document.external_parent_id
        )
    pairs = (
        await database.execute(
            select(Commitment, CommitmentSource)
            .join(
                CommitmentSource,
                CommitmentSource.commitment_id == Commitment.id,
            )
            .where(
                Commitment.workspace_id == connection.workspace_id,
                Commitment.user_id == connection.user_id,
                CommitmentSource.connection_id == connection.id,
                CommitmentSource.source_type == document.source_type,
                or_(*resource_filters),
            )
        )
    ).all()
    matching: dict[UUID, Commitment] = {}
    for commitment, source in pairs:
        same_resource = (
            source.source_type == document.source_type
            and source.external_resource_id == document.external_id
        )
        same_thread = (
            document.source_type == "gmail_message"
            and source.source_type == "gmail_message"
            and bool(document.external_parent_id)
            and source.source_metadata.get("external_parent_id") == document.external_parent_id
        )
        if same_resource or same_thread:
            matching[commitment.id] = commitment
    return list(matching.values())


def _match(
    candidates: list[Commitment],
    candidate: OperationalObservationCandidate,
) -> tuple[Commitment | None, bool]:
    return _match_text(candidates, f"{candidate.action_text or ''} {candidate.object_text or ''}")


def _match_text(candidates: list[Commitment], text: str) -> tuple[Commitment | None, bool]:
    tokens = _tokens(text)
    if not tokens:
        return None, False
    matches: list[tuple[float, Commitment]] = []
    for commitment in candidates:
        existing = _tokens(commitment.title)
        overlap = len(tokens & existing) / max(len(tokens | existing), 1)
        if overlap >= 0.65 or (tokens <= existing and len(tokens) >= 2):
            matches.append((overlap, commitment))
    matches.sort(key=lambda entry: entry[0], reverse=True)
    if not matches:
        return None, False
    if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.2:
        return None, True
    return matches[0][1], False


async def _meeting_context(
    database: AsyncSession,
    *,
    connection: Connection,
    temporal: TemporalResolution,
    candidate: OperationalObservationCandidate,
    document: SourceDocument,
) -> tuple[TemporalResolution, UUID | None]:
    if (
        not temporal.expression
        or not re.search(
            r"\bbefore\b.*\b(meeting|review)\b",
            temporal.expression.casefold(),
        )
        or temporal.start_at is None
        or temporal.end_at is None
    ):
        return temporal, None
    meetings = list(
        await database.scalars(
            select(Commitment).where(
                Commitment.workspace_id == connection.workspace_id,
                Commitment.user_id == connection.user_id,
                Commitment.commitment_type == "meeting",
                Commitment.status == "confirmed",
                Commitment.due_at >= temporal.start_at,
                Commitment.due_at < temporal.end_at,
            )
        )
    )
    context_tokens = _tokens(
        f"{document.subject or ''} {candidate.action_text or ''} {candidate.object_text or ''}"
    ) - {"review", "meeting", "attend", "prepare"}
    meetings = [meeting for meeting in meetings if context_tokens & _tokens(meeting.title)]
    if len(meetings) == 1 and meetings[0].due_at:
        instant = _utc(meetings[0].due_at)
        return TemporalResolution(
            temporal.expression,
            instant,
            instant,
            instant,
            "unique_meeting_context",
            0.95,
        ), meetings[0].id
    return temporal, None


async def resolve_extraction(
    database: AsyncSession,
    *,
    connection: Connection,
    document: SourceDocument,
    result: OperationalExtractionResult,
    timezone_name: str = "UTC",
) -> list[UUID]:
    """Persist authorized facts and conservative graph/state updates atomically.

    Returns affected commitment IDs. Raises before writes on invalid ownership,
    evidence, paused user, inactive connection, or stale extraction content.
    """
    stored = await database.scalar(
        select(Connection)
        .where(
            Connection.id == connection.id,
            Connection.workspace_id == document.workspace_id,
            Connection.user_id == connection.user_id,
            Connection.provider == document.provider,
        )
        .with_for_update()
    )
    membership = await database.get(
        WorkspaceMembership, (document.workspace_id, connection.user_id)
    )
    user = await database.get(User, connection.user_id)
    if stored is None or stored.status != "active" or membership is None or user is None:
        raise PermissionError("Source is not owned by an active authorized connection")
    if user.agent_paused:
        raise PermissionError("Operational processing is paused")
    if result.source_hash != source_document_hash(document):
        raise ValueError("Extraction source hash does not match this resource version")
    result.extraction.validate_evidence(document)
    # Fail closed before persistence when external content tries to grant authority.
    source_text = f"{document.subject or ''}\n{document.content or ''}".casefold()
    if any(marker in source_text for marker in INSTRUCTION_LIKE_MARKERS):
        return []
    identities = ([document.author] if document.author else []) + list(document.recipients)
    resolved_people: dict[str, UUID] = {}
    ambiguous_names: set[str] = set()
    for identity in identities:
        identity_person_id = await resolve_identity(
            database, workspace_id=document.workspace_id, identity=identity
        )
        resolved_people[_identity_value(identity)] = identity_person_id
        if identity.display_name:
            # Display names associate evidence only when unambiguous within this source.
            name = identity.display_name.casefold()
            if name in resolved_people and resolved_people[name] != identity_person_id:
                resolved_people.pop(name)
                ambiguous_names.add(name)
            elif name not in ambiguous_names:
                resolved_people[name] = identity_person_id
    for mention in result.extraction.people:
        if mention.identity_type == "email" and mention.identity_value:
            identity_key = mention.identity_value.casefold()
            # Extraction can resolve a grounded identity already present in this
            # source, but cannot merge unrelated people based on a model guess.
            if identity_key in resolved_people and mention.name.casefold() not in ambiguous_names:
                resolved_people[mention.name.casefold()] = resolved_people[identity_key]
            elif mention.confidence >= 0.9 and re.fullmatch(
                r"[^@\s]+@[^@\s]+\.[^@\s]+", identity_key
            ):
                mentioned_person_id = await resolve_identity(
                    database,
                    workspace_id=document.workspace_id,
                    identity=SourceIdentity(
                        provider=document.provider,
                        identity_type="email",
                        identity_value=identity_key,
                        display_name=mention.name,
                    ),
                )
                resolved_people[identity_key] = mentioned_person_id
                # Body-only identities do not establish unambiguous name aliases.

    prior = list(
        (
            await database.execute(
                select(OperationalObservation, ObservationEvidence)
                .join(
                    ObservationEvidence,
                    ObservationEvidence.observation_id == OperationalObservation.id,
                )
                .where(
                    OperationalObservation.workspace_id == document.workspace_id,
                    OperationalObservation.user_id == connection.user_id,
                    ObservationEvidence.connection_id == connection.id,
                    ObservationEvidence.source_type == document.source_type,
                    ObservationEvidence.external_resource_id == document.external_id,
                )
            )
        ).all()
    )
    if any(_utc(evidence.observed_at) > document.occurred_at for _, evidence in prior):
        return []  # Out-of-order delivery cannot undo a newer resource version.
    superseded_ids: set[str] = set()
    for observation, evidence in prior:
        if evidence.source_hash != result.source_hash and observation.status == "ACTIVE":
            observation.status = "SUPERSEDED"
            superseded_ids.add(str(observation.id))

    candidates = await _existing_candidates(database, connection=connection, document=document)
    affected: list[UUID] = []
    retained: set[UUID] = set()
    cancelled = (
        document.source_type == "calendar_event" and document.metadata.get("status") == "cancelled"
    )
    labels = document.metadata.get("label_ids", [])
    marketing = isinstance(labels, list) and "CATEGORY_PROMOTIONS" in labels
    marketing = marketing or document.metadata.get("list_unsubscribe") is True
    for candidate in result.extraction.observations:
        temporal = resolve_temporal(
            candidate.temporal_expression,
            occurred_at=document.occurred_at,
            timezone_name=timezone_name,
        )
        if candidate.observation_type == "meeting" and document.source_type == "calendar_event":
            start_at = document.metadata.get("start_at")
            if isinstance(start_at, str):
                temporal = resolve_temporal(
                    start_at,
                    occurred_at=document.occurred_at,
                    timezone_name=timezone_name,
                )
        temporal, related_meeting_id = await _meeting_context(
            database,
            connection=connection,
            temporal=temporal,
            candidate=candidate,
            document=document,
        )
        subject = (candidate.subject_text or "").casefold()
        person_id = resolved_people.get(subject)
        entity_cap = (
            1.0 if not subject or person_id or subject in {"i", "you", "me", "we"} else 0.89
        )
        confidence = min(candidate.confidence, entity_cap)
        subject_only = document.source_type == "gmail_message" and all(
            span.source == "subject" for span in candidate.evidence
        )
        if subject_only:
            # A headline can name an action without establishing a personal
            # obligation. It cannot bypass review or drive a state transition.
            confidence = min(confidence, 0.89)
        if candidate.temporal_expression:
            confidence = min(confidence, max(0.75, temporal.confidence))
        if marketing or cancelled:
            confidence = min(confidence, 0.5)
        observation = await _record_observation(
            database,
            connection=connection,
            document=document,
            result=result,
            candidate=candidate,
            temporal=temporal,
            person_id=person_id,
            confidence=confidence,
        )
        if confidence < 0.7 or not _fact_key(candidate):
            continue
        existing, ambiguous_match = _match(candidates, candidate)
        state_fact = candidate.observation_type in {"completion", "waiting"}
        if ambiguous_match or (state_fact and (existing is None or confidence < 0.9)):
            observation.status = "NEEDS_CONFIRMATION"
            continue
        title = " ".join(part for part in (candidate.action_text, candidate.object_text) if part)
        created = existing is None
        if existing is None:
            parent_id = (
                document.external_parent_id or document.external_id
                if document.source_type == "gmail_message"
                else document.external_id
            )
            dedupe = sha256(
                f"{connection.id}:{document.source_type}:{parent_id}:{_fact_key(candidate)}".encode()
            ).hexdigest()
            existing = await database.scalar(
                select(Commitment).where(
                    Commitment.workspace_id == connection.workspace_id,
                    Commitment.user_id == connection.user_id,
                    Commitment.dedupe_key == dedupe,
                )
            )
            if existing is None:
                existing = Commitment(
                    id=_stable_id("commitment", connection.workspace_id, dedupe),
                    workspace_id=connection.workspace_id,
                    user_id=connection.user_id,
                    commitment_type={"request": "task"}.get(
                        candidate.observation_type, candidate.observation_type
                    ),
                    title=title[:256],
                    status="confirmed" if confidence >= 0.9 else "candidate",
                    confidence=confidence,
                    priority=3,
                    due_at=temporal.resolved_at,
                    created_by="ai",
                    dedupe_key=dedupe,
                    last_verified_at=document.occurred_at,
                    intelligence_metadata={
                        "temporal": temporal.as_metadata(),
                        "resolution": "CREATE_NEW",
                        "source_updated_at": document.occurred_at.isoformat(),
                        **({"evidence_review_reason": "subject_only"} if subject_only else {}),
                    },
                )
                database.add(existing)
                await database.flush()
                candidates.append(existing)
            else:
                created = False
        retained.add(existing.id)
        new_source = await _attach_source(
            database,
            commitment=existing,
            observation=observation,
            connection=connection,
            document=document,
            result=result,
        )
        if not new_source:
            affected.append(existing.id)
            continue
        if existing.last_verified_at and _utc(existing.last_verified_at) > document.occurred_at:
            observation.status = "HISTORICAL"
            affected.append(existing.id)
            continue
        outcome = "CREATE_NEW" if created else "MERGE_EVIDENCE"
        metadata = dict(existing.intelligence_metadata or {})
        if (
            existing.status == "superseded"
            and existing.created_by == "ai"
            and metadata.get("reason") in {"source_cancelled", "source_corrected"}
            and document.source_type == "calendar_event"
            and candidate.observation_type == "meeting"
            and document.metadata.get("status") == "confirmed"
            and confidence >= 0.9
            and temporal.start_at is not None
            and existing.valid_until is not None
            and document.occurred_at > _utc(existing.valid_until)
        ):
            # A newer authoritative calendar revision can restore its own withdrawn
            # meeting. User completion/rejection and stale revisions stay terminal.
            existing.status = "confirmed"
            existing.valid_until = None
            existing.due_at = temporal.resolved_at
            existing.confidence = confidence
            metadata.pop("reason", None)
            metadata.pop("conflicting_temporal", None)
            metadata.pop("calendar_end_at", None)
            metadata.pop("completion_condition", None)
            metadata["temporal"] = temporal.as_metadata()
            metadata["lifecycle_state"] = "CONFIRMED"
            outcome = "UPDATE_EXISTING"
        if document.source_type == "calendar_event" and candidate.observation_type == "meeting":
            end_at = document.metadata.get("end_at")
            if isinstance(end_at, str):
                resolved_end = resolve_temporal(
                    end_at,
                    occurred_at=document.occurred_at,
                    timezone_name=timezone_name,
                )
                if resolved_end.resolved_at:
                    metadata["calendar_end_at"] = resolved_end.resolved_at.isoformat()
                    metadata["completion_condition"] = "EVENT_OCCURRED"
        if related_meeting_id and related_meeting_id != existing.id:
            relation_id = _stable_id("meeting_relation", existing.id, related_meeting_id)
            if not await database.get(CommitmentRelation, relation_id):
                database.add(
                    CommitmentRelation(
                        id=relation_id,
                        from_commitment_id=existing.id,
                        to_commitment_id=related_meeting_id,
                        relation_type="prepares_for",
                    )
                )
            metadata["related_meeting_id"] = str(related_meeting_id)
        if not created and existing.status not in TERMINAL_STATUSES:
            if state_fact:
                decision = infer_state(
                    existing, candidate=candidate, document=document, connection=connection
                )
                if decision.status:
                    existing.status = decision.status
                    metadata["completion_condition"] = decision.condition
                    metadata["lifecycle_state"] = (
                        "WAITING_ON_EXTERNAL" if decision.status == "waiting" else "COMPLETED"
                    )
                    if decision.status == "completed":
                        existing.completed_at = document.occurred_at
                        outcome = "MARK_COMPLETED"
                    else:
                        existing.waiting_since = document.occurred_at
                        metadata["waiting_for_identities"] = [
                            _identity_value(identity)
                            for identity in document.recipients
                            if identity.identity_type == "email"
                        ]
                        outcome = "MARK_WAITING"
                else:
                    observation.status = "NEEDS_CONFIRMATION"
                    outcome = "NEEDS_CONFIRMATION"
            elif (
                temporal.resolved_at
                and existing.due_at
                and _utc(existing.due_at) != temporal.resolved_at
            ):
                correction = bool(
                    re.search(r"\b(instead|rescheduled|moved|changed|new deadline)\b", source_text)
                )
                if correction or document.source_type == "calendar_event":
                    existing.due_at = temporal.resolved_at
                    metadata["temporal"] = temporal.as_metadata()
                    outcome = "SUPERSEDE"
                else:
                    metadata["conflicting_temporal"] = temporal.as_metadata()
                    existing.confidence = min(existing.confidence, 0.89)
                    outcome = "NEEDS_CONFIRMATION"
            elif temporal.resolved_at and existing.due_at is None:
                existing.due_at = temporal.resolved_at
                metadata["temporal"] = temporal.as_metadata()
                outcome = "UPDATE_EXISTING"
        metadata["resolution"] = outcome
        metadata["source_updated_at"] = document.occurred_at.isoformat()
        existing.intelligence_metadata = metadata
        existing.last_verified_at = document.occurred_at
        _audit(
            database,
            connection=connection,
            commitment=existing,
            observation_id=observation.id,
            outcome=outcome,
        )
        affected.append(existing.id)

    for mention in result.extraction.people:
        identity_key = (mention.identity_value or "").casefold()
        person_id = resolved_people.get(identity_key)
        await _record_auxiliary_fact(
            database,
            connection=connection,
            document=document,
            result=result,
            fact_type="person_mention",
            action_text="mentions",
            object_text=mention.name,
            confidence=min(mention.confidence, 0.89),
            spans=mention.evidence,
            subject_person_id=person_id,
        )
    for temporal_mention in result.extraction.temporals:
        temporal = resolve_temporal(
            temporal_mention.expression,
            occurred_at=document.occurred_at,
            timezone_name=timezone_name,
        )
        await _record_auxiliary_fact(
            database,
            connection=connection,
            document=document,
            result=result,
            fact_type="temporal",
            action_text=temporal_mention.kind,
            object_text=temporal_mention.expression,
            confidence=min(temporal_mention.confidence, temporal.confidence),
            spans=temporal_mention.evidence,
            temporal=temporal,
        )
    for relationship in result.extraction.relationships:
        relationship_confidence = min(relationship.confidence, 0.5 if marketing else 1.0)
        observation_id = await _record_auxiliary_fact(
            database,
            connection=connection,
            document=document,
            result=result,
            fact_type="relationship",
            action_text=relationship.relationship_type,
            object_text=f"{relationship.subject_text} → {relationship.object_text}",
            confidence=relationship_confidence,
            spans=relationship.evidence,
            subject_person_id=resolved_people.get(relationship.subject_text.casefold()),
            object_person_id=resolved_people.get(relationship.object_text.casefold()),
        )
        if relationship_confidence < 0.9 or relationship.relationship_type not in {
            "depends_on",
            "prepares_for",
            "relates_to",
        }:
            continue
        origin, origin_ambiguous = _match_text(candidates, relationship.subject_text)
        target, target_ambiguous = _match_text(candidates, relationship.object_text)
        if (
            origin is None
            or target is None
            or origin_ambiguous
            or target_ambiguous
            or origin.id == target.id
            or origin.status in TERMINAL_STATUSES
            or target.status in TERMINAL_STATUSES
            or any(
                c.last_verified_at and _utc(c.last_verified_at) > document.occurred_at
                for c in (origin, target)
            )
        ):
            continue
        relation_id = _stable_id("relation", origin.id, target.id, relationship.relationship_type)
        existing_relation = await database.scalar(
            select(CommitmentRelation).where(
                CommitmentRelation.from_commitment_id == origin.id,
                CommitmentRelation.to_commitment_id == target.id,
                CommitmentRelation.relation_type == relationship.relationship_type,
            )
        )
        if existing_relation is None:
            database.add(
                CommitmentRelation(
                    id=relation_id,
                    from_commitment_id=origin.id,
                    to_commitment_id=target.id,
                    relation_type=relationship.relationship_type,
                )
            )
            database.add(
                AuditEvent(
                    workspace_id=connection.workspace_id,
                    user_id=connection.user_id,
                    event_type="intelligence.relation",
                    actor_type="system",
                    entity_type="commitment_relation",
                    entity_id=relation_id,
                    event_metadata={"observation_id": str(observation_id)},
                )
            )

    # Resource correction may withdraw a previously inferred fact. Other source
    # evidence keeps a corroborated fact alive; user terminal decisions survive.
    for commitment in candidates:
        if commitment.id in retained or commitment.status in TERMINAL_STATUSES:
            continue
        sources = list(
            await database.scalars(
                select(CommitmentSource).where(
                    CommitmentSource.commitment_id == commitment.id,
                )
            )
        )
        from_resource = [
            s
            for s in sources
            if s.connection_id == connection.id
            and s.external_resource_id == document.external_id
            and s.source_type == document.source_type
        ]
        other_sources = [s for s in sources if s not in from_resource]
        withdrawn = bool(from_resource) and (
            cancelled
            or any(s.source_metadata.get("observation_id") in superseded_ids for s in from_resource)
        )
        if withdrawn and not other_sources and commitment.created_by == "ai":
            commitment.status = "superseded"
            commitment.valid_until = document.occurred_at
            commitment.intelligence_metadata = {
                **(commitment.intelligence_metadata or {}),
                "resolution": "SUPERSEDE",
                "reason": "source_cancelled" if cancelled else "source_corrected",
            }
            _audit(
                database,
                connection=connection,
                commitment=commitment,
                observation_id=None,
                outcome="SUPERSEDE",
            )
            affected.append(commitment.id)
    await database.flush()
    return list(dict.fromkeys(affected))
