import json
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import Commitment, ProactivePreference, User, Workspace, WorkspaceMembership

Recipe = Literal["morning_priorities", "friday_catch_up", "no_reply_check"]


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class RoutineConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    recipe: Recipe
    timezone: str = Field(min_length=1, max_length=64)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    days_of_week: list[int] = Field(min_length=1, max_length=7)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Choose a valid IANA timezone") from error
        return value

    @field_validator("days_of_week")
    @classmethod
    def valid_days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("Days must be 0 (Monday) through 6 (Sunday)")
        return sorted(set(value))


def config_hash(config: RoutineConfig) -> str:
    return sha256(json.dumps(config.model_dump(), sort_keys=True).encode()).hexdigest()


def next_run(config: RoutineConfig, now: datetime) -> datetime:
    """One run per local calendar day, including folds and spring-forward gaps.

    Ambiguous times use the first occurrence. A nonexistent local time advances
    to the first valid local minute on that day, rather than silently missing it.
    """
    zone = ZoneInfo(config.timezone)
    now = aware(now).astimezone(UTC)
    local_date = now.astimezone(zone).date()
    for offset in range(9):
        day = local_date + timedelta(days=offset)
        if day.weekday() not in config.days_of_week:
            continue
        naive = datetime.combine(day, time(config.hour, config.minute))
        for gap_minutes in range(181):
            candidate_naive = naive + timedelta(minutes=gap_minutes)
            if candidate_naive.date() != day:
                break
            candidate = candidate_naive.replace(tzinfo=zone, fold=0).astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) != candidate_naive:
                continue
            if candidate > now:
                return candidate
            break
    raise ValueError("No valid scheduled time in the coming week")


async def owner_is_active(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> User | None:
    return cast(
        User | None,
        await database.scalar(
            select(User)
            .join(WorkspaceMembership, WorkspaceMembership.user_id == User.id)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .where(
                User.id == user_id,
                Workspace.id == workspace_id,
                Workspace.workspace_type.in_(("personal", "PERSONAL")),
                WorkspaceMembership.role == "owner",
            )
        ),
    )


async def quiet_now(database: AsyncSession, user: User, workspace_id: UUID, now: datetime) -> bool:
    preference = await database.scalar(
        select(ProactivePreference).where(
            ProactivePreference.user_id == user.id,
            ProactivePreference.workspace_id == workspace_id,
        )
    )
    start, end = (
        (preference.quiet_hours_start, preference.quiet_hours_end)
        if preference
        else ("22:00", "07:00")
    )
    try:
        local_time = aware(now).astimezone(ZoneInfo(user.timezone)).strftime("%H:%M")
    except (ZoneInfoNotFoundError, ValueError):
        local_time = aware(now).astimezone(UTC).strftime("%H:%M")
    if start == end:
        return False
    return start <= local_time < end if start < end else local_time >= start or local_time < end


async def preview_result(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    config: RoutineConfig,
    now: datetime,
) -> dict[str, object]:
    """Read only saved owner-scoped facts. This does not refresh any provider."""
    now = aware(now).astimezone(UTC)
    items: list[dict[str, object]] = []
    if config.recipe == "no_reply_check":
        from navox.db.thread_watches import ThreadWatch

        watches = list(
            await database.scalars(
                select(ThreadWatch)
                .where(
                    ThreadWatch.user_id == user_id,
                    ThreadWatch.workspace_id == workspace_id,
                    ThreadWatch.status == "due",
                )
                .order_by(ThreadWatch.remind_at, ThreadWatch.id)
                .limit(15)
            )
        )
        for watch in watches:
            items.append(
                {
                    "id": str(watch.id),
                    "title": watch.subject[:256],
                    "status": watch.status,
                    "due_at": aware(watch.remind_at).isoformat(),
                    "kind": "thread_watch",
                }
            )
        headline = f"{len(items)} watched thread(s) need a follow-up review."
        notice = "From saved watch results. This preview does not check Gmail for new replies."
    else:
        query = select(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.workspace_id == workspace_id,
            (Commitment.valid_until.is_(None) | (Commitment.valid_until > now)),
            (Commitment.valid_from.is_(None) | (Commitment.valid_from <= now)),
        )
        if config.recipe == "morning_priorities":
            query = query.where(Commitment.status.in_(("confirmed", "attention")))
            query = query.order_by(
                Commitment.priority.desc(), Commitment.due_at.asc().nulls_last(), Commitment.id
            )
            headline = "Your saved priorities for today."
        else:
            query = query.where(
                (Commitment.status == "waiting")
                | (Commitment.status.in_(("confirmed", "attention")) & (Commitment.due_at < now))
                | (
                    (Commitment.status == "completed")
                    & (Commitment.completed_at >= now - timedelta(days=7))
                )
            ).order_by(Commitment.status, Commitment.due_at.asc().nulls_last(), Commitment.id)
            headline = "Overdue work, waiting items, and completions from the last seven days."
        commitments = list(await database.scalars(query.limit(15)))
        items = [
            {
                "id": str(item.id),
                "title": item.title[:256],
                "status": item.status,
                "due_at": aware(item.due_at).isoformat() if item.due_at else None,
                "kind": "commitment",
            }
            for item in commitments
        ]
        notice = "From saved tasks only. Connected apps were not refreshed. Showing up to 15 items."
    if not items:
        headline = "No saved items match this routine right now."
    return {
        "checked_at": now.isoformat(),
        "basis": "saved_data",
        "headline": headline,
        "items": items,
        "notice": notice,
    }
