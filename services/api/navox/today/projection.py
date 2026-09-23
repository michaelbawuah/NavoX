import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    Commitment,
    CommitmentSource,
    ObservationEvidence,
    OperationalObservation,
)
from navox.intelligence.attention import AttentionResult, score_commitment, workspace_attention

ACTIVE_STATUSES = {
    "candidate",
    "confirmed",
    "waiting",
    "waiting_on_external",
    "attention",
    "upcoming",
}


class InvalidTimezoneError(ValueError):
    pass


@dataclass(frozen=True)
class TodaySource:
    provider: str
    source_type: str
    external_resource_id: str | None
    evidence_locator: dict[str, object] | None = None
    observed_at: datetime | None = None
    evidence_id: UUID | None = None
    connection_id: UUID | None = None


@dataclass(frozen=True)
class TodayItem:
    id: UUID
    type: str
    title: str
    description: str | None
    status: str
    priority: int
    due_at: datetime | None
    confidence: float
    created_by: str
    score: int
    reasons: tuple[str, ...]
    sources: tuple[TodaySource, ...]
    category: str = "COMING_UP"
    band: str = "DASHBOARD"
    factors: dict[str, float] = field(default_factory=dict)
    suggested_capability: str | None = None


@dataclass(frozen=True)
class TodayProjection:
    generated_at: datetime
    timezone: str
    total: int
    needs_attention: tuple[TodayItem, ...]
    coming_up: tuple[TodayItem, ...]
    renewals: tuple[TodayItem, ...]
    waiting_on: tuple[TodayItem, ...]
    completed_recently: tuple[TodayItem, ...]


def validated_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise InvalidTimezoneError(f"Unknown IANA timezone: {name}") from error


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def active_at(commitment: Commitment, now: datetime) -> bool:
    if commitment.status not in ACTIVE_STATUSES:
        return False
    if commitment.valid_from is not None and aware(commitment.valid_from) > now:
        return False
    if commitment.valid_until is not None and aware(commitment.valid_until) < now:
        return False
    return True


def item_score_and_reasons(
    commitment: Commitment, now: datetime, timezone: ZoneInfo
) -> tuple[int, tuple[str, ...]]:
    result = score_commitment(commitment, now=now)
    return result.score, result.reasons


def needs_attention(commitment: Commitment, now: datetime, timezone: ZoneInfo) -> bool:
    if commitment.status in {"candidate", "attention"}:
        return True
    if commitment.due_at is not None:
        due_date = aware(commitment.due_at).astimezone(timezone).date()
        if due_date <= now.astimezone(timezone).date():
            return True
    return commitment.priority >= 4 and commitment.due_at is None


def category_for(
    commitment: Commitment,
    result: AttentionResult,
    now: datetime,
    timezone: ZoneInfo,
) -> str:
    if commitment.status in {"waiting", "waiting_on_external"}:
        return "WAITING_ON"
    if commitment.commitment_type == "renewal":
        return "RENEWALS"
    if needs_attention(commitment, now, timezone) or result.score >= 70:
        return "NEEDS_ATTENTION"
    return "COMING_UP"


def sort_key(item: TodayItem) -> tuple[int, float, str]:
    due = aware(item.due_at).timestamp() if item.due_at is not None else float("inf")
    return (-item.score, due, item.title.casefold())


def bounded_locator(value: dict[str, object]) -> dict[str, object]:
    """Return locators only; no raw source body or arbitrary model metadata."""
    raw = value.get("spans", [value])
    spans: list[dict[str, object]] = []
    if isinstance(raw, list):
        for candidate in raw[:16]:
            if not isinstance(candidate, dict):
                continue
            source = candidate.get("source")
            start, end = candidate.get("start_char"), candidate.get("end_char")
            if (
                source in {"subject", "content"}
                and isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end
            ):
                spans.append({"source": source, "start_char": start, "end_char": end})
    return {"spans": spans}


async def build_today_projection(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    timezone_name: str,
    now: datetime | None = None,
) -> TodayProjection:
    timezone = validated_timezone(timezone_name)
    current_time = aware(now or datetime.now(UTC))

    commitments, attention_results = await workspace_attention(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        now=current_time,
    )
    active = [
        commitment
        for commitment in commitments
        if active_at(commitment, current_time) and not attention_results[commitment.id].suppressed
    ]
    completed_cutoff = current_time - timedelta(days=7)
    completed_recently = [
        commitment
        for commitment in commitments
        if commitment.status == "completed"
        and commitment.completed_at is not None
        and aware(commitment.completed_at) >= completed_cutoff
    ]
    commitment_ids = [commitment.id for commitment in [*active, *completed_recently]]

    source_map: dict[UUID, list[TodaySource]] = {}
    if commitment_ids:
        sources = list(
            await database.scalars(
                select(CommitmentSource)
                .where(CommitmentSource.commitment_id.in_(commitment_ids))
                .order_by(CommitmentSource.extracted_at)
            )
        )
        observation_ids: dict[UUID, UUID] = {}
        for source in sources:
            value = source.source_metadata.get("observation_id")
            if value:
                try:
                    observation_ids[source.id] = UUID(value)
                except (ValueError, TypeError):
                    pass
        evidence_rows = (
            list(
                await database.scalars(
                    select(ObservationEvidence)
                    .join(OperationalObservation)
                    .where(
                        OperationalObservation.id.in_(list(observation_ids.values())),
                        OperationalObservation.workspace_id == workspace_id,
                        OperationalObservation.user_id == user_id,
                    )
                )
            )
            if observation_ids
            else []
        )
        seen_evidence: set[tuple[object, ...]] = set()
        for source in sources:
            evidence = next(
                (
                    row
                    for row in evidence_rows
                    if row.observation_id == observation_ids.get(source.id)
                    and row.connection_id == source.connection_id
                    and row.provider == source.provider
                    and row.source_type == source.source_type
                    and row.external_resource_id == source.external_resource_id
                ),
                None,
            )
            locator = None
            if evidence and evidence.evidence_locator:
                locator = bounded_locator(evidence.evidence_locator)
            spans = locator.get("spans") if locator else None
            if evidence and evidence.source_hash and isinstance(spans, list) and spans:
                key = (
                    source.commitment_id,
                    source.connection_id,
                    source.provider,
                    source.source_type,
                    source.external_resource_id,
                    evidence.source_hash,
                    tuple(sorted({json.dumps(span, sort_keys=True) for span in spans})),
                )
                if key in seen_evidence:
                    continue
                seen_evidence.add(key)
            source_map.setdefault(source.commitment_id, []).append(
                TodaySource(
                    provider=source.provider,
                    source_type=source.source_type,
                    external_resource_id=source.external_resource_id,
                    evidence_locator=locator,
                    observed_at=evidence.observed_at if evidence else None,
                    evidence_id=evidence.id if evidence else None,
                    connection_id=source.connection_id,
                )
            )

    items: list[tuple[Commitment, TodayItem]] = []
    for commitment in active:
        result = attention_results[commitment.id]
        items.append(
            (
                commitment,
                TodayItem(
                    id=commitment.id,
                    type=commitment.commitment_type,
                    title=commitment.title,
                    description=commitment.description,
                    status=commitment.status,
                    priority=commitment.priority,
                    due_at=commitment.due_at,
                    confidence=commitment.confidence,
                    created_by=commitment.created_by,
                    score=result.score,
                    reasons=result.reasons,
                    factors=result.factors,
                    band=result.band,
                    category=category_for(commitment, result, current_time, timezone),
                    suggested_capability=result.suggested_capability,
                    sources=tuple(source_map.get(commitment.id, [])),
                ),
            )
        )

    attention = sorted(
        (
            item
            for commitment, item in items
            if needs_attention(commitment, current_time, timezone)
            or attention_results[commitment.id].score >= 70
        ),
        key=sort_key,
    )
    coming = sorted(
        (
            item
            for commitment, item in items
            if not needs_attention(commitment, current_time, timezone)
            and attention_results[commitment.id].score < 70
        ),
        key=sort_key,
    )
    renewals = sorted(
        (item for commitment, item in items if commitment.commitment_type == "renewal"),
        key=sort_key,
    )
    waiting = sorted(
        (
            item
            for commitment, item in items
            if commitment.status in {"waiting", "waiting_on_external"}
        ),
        key=sort_key,
    )
    completed_items = sorted(
        (
            TodayItem(
                id=commitment.id,
                type=commitment.commitment_type,
                title=commitment.title,
                description=commitment.description,
                status=commitment.status,
                priority=commitment.priority,
                due_at=commitment.due_at,
                confidence=commitment.confidence,
                created_by=commitment.created_by,
                score=0,
                reasons=("Completed",),
                category="COMPLETED",
                band="SUPPRESS",
                sources=tuple(source_map.get(commitment.id, [])),
            )
            for commitment in completed_recently
        ),
        key=lambda item: item.title.casefold(),
    )

    return TodayProjection(
        generated_at=current_time,
        timezone=timezone_name,
        total=len(items),
        needs_attention=tuple(attention),
        coming_up=tuple(coming),
        renewals=tuple(renewals),
        waiting_on=tuple(waiting),
        completed_recently=tuple(completed_items),
    )
