"""Retained native calendar intervals and short-lived local-plan mutation holds.

Call while holding the student's User lock when admitting a local mutation.
No helper commits, rolls back, changes a receipt, or contacts a provider.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings, get_settings
from navox.db.student_calendars import StudentCalendarEvent
from navox.db.student_planning import StudentPlanningCommand
from navox.student import service as schedule
from navox.student_availability.engine import MEETING_BUFFER, _uncertain_day_span
from navox.student_calendars.schemas import CalendarEventPreview

ACTIVE = ("executing", "uncertain", "created", "changed", "undoing")
HELD = ("executing", "uncertain")
MAX_CLAIMS = 500


@dataclass(frozen=True)
class NativeClaim:
    id: UUID
    start_at: datetime
    end_at: datetime
    unknown_travel: bool
    timezone: str

    def interval(
        self, travel_minutes: int = 0, *, timezone: str | None = None
    ) -> tuple[datetime, datetime]:
        first, last = self.start_at - MEETING_BUFFER, self.end_at + MEETING_BUFFER
        if self.unknown_travel:
            zone = ZoneInfo(timezone or self.timezone)
            left = (first - timedelta(hours=4)).astimezone(zone).date()
            right = (last + timedelta(hours=4) - timedelta(microseconds=1)).astimezone(zone).date()
            return _uncertain_day_span(left, zone, days=(right - left).days + 1)
        padding = timedelta(minutes=travel_minutes)
        return first - padding, last + padding


async def _rows(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, statuses: tuple[str, ...]
) -> list[StudentCalendarEvent]:
    rows = list(
        await database.scalars(
            select(StudentCalendarEvent)
            .where(
                StudentCalendarEvent.user_id == user_id,
                StudentCalendarEvent.workspace_id == workspace_id,
                StudentCalendarEvent.status.in_(statuses),
            )
            .order_by(StudentCalendarEvent.id)
            .limit(MAX_CLAIMS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) > MAX_CLAIMS:
        raise schedule.StudentError(
            409, "Review earlier calendar activity before changing the plan"
        )
    return rows


async def snapshot(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    settings: Settings | None = None,
) -> tuple[NativeClaim, ...]:
    # Local import avoids the scheduling -> availability -> planning cycle.
    from navox.student_calendars.scheduling import _open

    rows = await _rows(database, user_id, workspace_id, ACTIVE)
    if not rows:
        return ()
    configured = settings or get_settings()
    claims = []
    for row in rows:
        try:
            draft = CalendarEventPreview.model_validate(_open(row, configured)["draft"])
            claims.append(
                NativeClaim(
                    row.id,
                    draft.start_at,
                    draft.end_at,
                    draft.availability.travel_assumption == "unknown",
                    draft.timezone,
                )
            )
        except (KeyError, TypeError, ValueError):
            raise schedule.StudentError(
                503, "A held calendar interval could not be checked"
            ) from None
    return tuple(claims)


def fingerprint(claims: tuple[NativeClaim, ...]) -> str | None:
    """Status/ETag churn does not change an immutable interval claim.

    Admission and verified absence change membership. An externally changed
    event retains its original interval until that absence is established.
    """
    if not claims:
        return None
    return hashlib.sha256(
        json.dumps(
            [
                [
                    str(row.id),
                    row.start_at.isoformat(),
                    row.end_at.isoformat(),
                    row.unknown_travel,
                    row.timezone,
                ]
                for row in claims
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def pending_displacement(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
) -> bool:
    # The opaque command index is committed atomically with the local moves and
    # event admission. A native UUID join avoids inspecting unrelated old private
    # receipts and remains portable across PostgreSQL and SQLite UUID storage.
    held = await database.scalar(
        select(StudentCalendarEvent.id)
        .join(
            StudentPlanningCommand,
            StudentPlanningCommand.request_id == StudentCalendarEvent.id,
        )
        .where(
            StudentCalendarEvent.user_id == user_id,
            StudentCalendarEvent.workspace_id == workspace_id,
            StudentCalendarEvent.status.in_(HELD),
            StudentPlanningCommand.user_id == user_id,
            StudentPlanningCommand.workspace_id == workspace_id,
            StudentPlanningCommand.action == "calendar.displacement",
        )
        .limit(1)
    )
    return held is not None


async def require_mutable(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Protect approved destinations; explicit source erasure remains available."""
    if await pending_displacement(database, user_id, workspace_id):
        raise schedule.StudentError(
            409,
            "A calendar request is still being checked. Review its status before changing "
            "the study plan. You can still read or explicitly forget your saved data.",
        )
