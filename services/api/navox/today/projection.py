from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import Commitment, CommitmentSource

ACTIVE_STATUSES = {"candidate", "confirmed", "waiting", "attention"}


class InvalidTimezoneError(ValueError):
    pass


@dataclass(frozen=True)
class TodaySource:
    provider: str
    source_type: str
    external_resource_id: str | None


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


@dataclass(frozen=True)
class TodayProjection:
    generated_at: datetime
    timezone: str
    total: int
    needs_attention: tuple[TodayItem, ...]
    coming_up: tuple[TodayItem, ...]
    renewals: tuple[TodayItem, ...]
    waiting_on: tuple[TodayItem, ...]


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
    reasons: list[str] = []
    score = min(commitment.priority * 8, 40)
    local_today = now.astimezone(timezone).date()

    if commitment.status == "candidate":
        reasons.append("Needs your review")
        score += 35
    if commitment.status == "attention":
        reasons.append("Marked for attention")
        score += 30
    if commitment.status == "waiting":
        reasons.append("Waiting on someone or something")
        score += 8

    if commitment.due_at is not None:
        due_date = aware(commitment.due_at).astimezone(timezone).date()
        if due_date < local_today:
            reasons.append("Overdue")
            score += 50
        elif due_date == local_today:
            reasons.append("Due today")
            score += 45
        elif due_date <= local_today + timedelta(days=7):
            reasons.append("Due within 7 days")
            score += 20
        else:
            reasons.append("Upcoming")
            score += 5
    elif commitment.priority >= 4:
        reasons.append("High priority")
        score += 25

    if commitment.commitment_type == "renewal":
        reasons.append("Renewal")
        score += 5

    return min(score, 100), tuple(reasons)


def needs_attention(commitment: Commitment, now: datetime, timezone: ZoneInfo) -> bool:
    if commitment.status in {"candidate", "attention"}:
        return True
    if commitment.due_at is not None:
        due_date = aware(commitment.due_at).astimezone(timezone).date()
        if due_date <= now.astimezone(timezone).date():
            return True
    return commitment.priority >= 4 and commitment.due_at is None


def sort_key(item: TodayItem) -> tuple[int, float, str]:
    due = aware(item.due_at).timestamp() if item.due_at is not None else float("inf")
    return (-item.score, due, item.title.casefold())


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

    commitments = list(
        await database.scalars(
            select(Commitment).where(
                Commitment.workspace_id == workspace_id,
                Commitment.user_id == user_id,
            )
        )
    )
    active = [commitment for commitment in commitments if active_at(commitment, current_time)]
    commitment_ids = [commitment.id for commitment in active]

    source_map: dict[UUID, list[TodaySource]] = {}
    if commitment_ids:
        sources = list(
            await database.scalars(
                select(CommitmentSource)
                .where(CommitmentSource.commitment_id.in_(commitment_ids))
                .order_by(CommitmentSource.extracted_at)
            )
        )
        for source in sources:
            source_map.setdefault(source.commitment_id, []).append(
                TodaySource(
                    provider=source.provider,
                    source_type=source.source_type,
                    external_resource_id=source.external_resource_id,
                )
            )

    items: list[tuple[Commitment, TodayItem]] = []
    for commitment in active:
        score, reasons = item_score_and_reasons(commitment, current_time, timezone)
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
                    score=score,
                    reasons=reasons,
                    sources=tuple(source_map.get(commitment.id, [])),
                ),
            )
        )

    attention = sorted(
        (item for commitment, item in items if needs_attention(commitment, current_time, timezone)),
        key=sort_key,
    )
    coming = sorted(
        (
            item
            for commitment, item in items
            if not needs_attention(commitment, current_time, timezone)
        ),
        key=sort_key,
    )
    renewals = sorted(
        (item for commitment, item in items if commitment.commitment_type == "renewal"),
        key=sort_key,
    )
    waiting = sorted(
        (item for commitment, item in items if commitment.status == "waiting"),
        key=sort_key,
    )

    return TodayProjection(
        generated_at=current_time,
        timezone=timezone_name,
        total=len(items),
        needs_attention=tuple(attention),
        coming_up=tuple(coming),
        renewals=tuple(renewals),
        waiting_on=tuple(waiting),
    )
