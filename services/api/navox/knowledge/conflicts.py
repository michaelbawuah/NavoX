"""Bounded, source-cited event-time conflicts from explicit calendar identities.

This adapter supports non-recurring calendar records and one explicit VEVENT
inside retained communication. It never joins event names or guesses dates in prose.
UTC and unambiguous IANA-zone timestamps are supported; floating, ambiguous DST,
all-day and recurring invitations stay unclassified. Source statements
remain attributed; conflicting communication is never overwritten by Calendar.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.knowledge import KnowledgeResource, KnowledgeResourceIndex
from navox.db.models import ConnectorResource
from navox.knowledge.conflict_contracts import ConflictValue, KnowledgeConflict
from navox.knowledge.contracts import stored_utc
from navox.knowledge.graph import Authority, _authority
from navox.knowledge.search_contracts import utc_now

CONFLICT_BOUND = 50


@dataclass(frozen=True)
class EventFact:
    uid: str
    value: ConflictValue
    binding: Authority
    fresh_until: datetime | None


def _invitation_start(line: str) -> datetime | None:
    """Convert only fully specified, unambiguous instants to UTC."""
    if re.fullmatch(r"DTSTART:\d{8}T\d{6}Z", line):
        try:
            return datetime.strptime(line[8:], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            return None
    match = re.fullmatch(r"DTSTART;TZID=([A-Za-z0-9_+/\-]{1,128}):(\d{8}T\d{6})", line)
    if match is None:
        return None
    try:
        zone = ZoneInfo(match[1])
        wall_time = datetime.strptime(match[2], "%Y%m%dT%H%M%S")
    except (ValueError, ZoneInfoNotFoundError):
        return None
    # A DST gap has no matching instant; a fold has two. Neither is guessed.
    candidates = {
        instant
        for fold in (0, 1)
        if (instant := wall_time.replace(tzinfo=zone, fold=fold).astimezone(UTC))
        .astimezone(zone)
        .replace(tzinfo=None)
        == wall_time
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def utc_invitation(text: str) -> tuple[str, datetime] | None:
    """Strict single-instance subset returning UTC, not a general calendar parser."""
    if len(text) > 200_000:
        return None
    # RFC5545 line unfolding. Reject multiple components and recurrence ambiguity.
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    if unfolded.count("BEGIN:VEVENT") != 1 or unfolded.count("END:VEVENT") != 1:
        return None
    block = unfolded.split("BEGIN:VEVENT", 1)[1].split("END:VEVENT", 1)[0]
    lines = [line.rstrip("\r") for line in block.splitlines() if line.strip()]
    if any(
        line.startswith(("RRULE", "RDATE", "RECURRENCE-ID", "EXDATE", "BEGIN:")) for line in lines
    ):
        return None
    uids = [line[4:] for line in lines if line.startswith("UID:")]
    starts = [line for line in lines if line.startswith("DTSTART")]
    if len(uids) != 1 or len(starts) != 1:
        return None
    uid = uids[0]
    if not uid or len(uid) > 512 or any(ord(char) < 32 for char in uid):
        return None
    value = _invitation_start(starts[0])
    return (uid, value) if value is not None else None


async def _event_fact(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, resource_id: UUID, now: datetime
) -> EventFact | None:
    binding = await _authority(database, workspace_id, user_id, resource_id, now)
    if binding is None:
        return None
    row = await database.scalar(
        select(KnowledgeResource)
        .where(KnowledgeResource.id == resource_id, KnowledgeResource.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    uid: str | None = None
    start: datetime | None = None
    authority: str
    if row.source_type == "EMAIL" and row.source_read_capability == "communication.messages.read":
        parsed = utc_invitation(row.normalized_text or "")
        if parsed is None:
            return None
        uid, start = parsed
        authority = "DIRECT_COMMUNICATION"
    elif (
        row.source_type == "CALENDAR_EVENT" and row.source_read_capability == "calendar.events.read"
    ):
        canonical = await database.scalar(
            select(ConnectorResource.canonical).where(
                ConnectorResource.id == row.source_resource_id,
                ConnectorResource.workspace_id == workspace_id,
            )
        )
        if not isinstance(canonical, dict) or canonical.get("content_persisted") is False:
            return None
        document = canonical.get("source_document")
        metadata = document.get("metadata", {}) if isinstance(document, dict) else canonical
        if (
            not isinstance(metadata, dict)
            or metadata.get("recurring")
            or metadata.get("recurring_event_id")
        ):
            return None
        raw_uid = metadata.get("ical_uid")
        if isinstance(raw_uid, str) and 0 < len(raw_uid) <= 512:
            uid = raw_uid
        start = await database.scalar(
            select(KnowledgeResourceIndex.structured_at).where(
                KnowledgeResourceIndex.resource_id == resource_id,
                KnowledgeResourceIndex.workspace_id == workspace_id,
                KnowledgeResourceIndex.structured_kind == "calendar.event",
            )
        )
        authority = "CALENDAR_SCHEDULE"
    else:
        return None
    if uid is None or start is None:
        return None
    return EventFact(
        uid,
        ConflictValue(
            resource_id=resource_id,
            source_type=row.source_type,
            value=stored_utc(start),
            title=row.title,
            canonical_url=row.canonical_url,
            source_updated_at=row.source_updated_at,
            authority=authority,
        ),
        binding,
        stored_utc(row.fresh_until) if row.fresh_until else None,
    )


async def detect_conflicts(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_ids: list[UUID],
    now: datetime | None = None,
) -> tuple[KnowledgeConflict, ...]:
    """Compare only supplied visible resources, at most50; recheck before publication."""
    moment = now or utc_now()
    facts: list[EventFact] = []
    for resource_id in list(dict.fromkeys(resource_ids))[:CONFLICT_BOUND]:
        fact = await _event_fact(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_id=resource_id,
            now=moment,
        )
        if fact is not None:
            facts.append(fact)
    # Neither a stored parsed value nor an event UID is an authority grant.
    current = [
        fact
        for fact in facts
        if await _event_fact(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_id=fact.value.resource_id,
            now=moment if now else utc_now(),
        )
        == fact
    ]
    conflicts: list[KnowledgeConflict] = []
    for index, left in enumerate(current):
        for right in current[index + 1 :]:
            if left.uid != right.uid or left.value.value == right.value.value:
                continue
            if {left.value.source_type, right.value.source_type} != {"EMAIL", "CALENDAR_EVENT"}:
                continue
            calendar = left if left.value.source_type == "CALENDAR_EVENT" else right
            preferred = (
                calendar.value.resource_id
                if calendar.fresh_until and calendar.fresh_until > (moment if now else utc_now())
                else None
            )
            conflicts.append(
                KnowledgeConflict(
                    entity_key="ical:" + sha256(left.uid.encode()).hexdigest(),
                    values=(left.value, right.value),
                    preferred_resource_id=preferred,
                )
            )
            if len(conflicts) >= 20:
                return tuple(conflicts)
    return tuple(conflicts)
