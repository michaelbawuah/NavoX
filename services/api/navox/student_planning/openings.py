"""Read-only real openings shared by Today and Planner."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.db.student_planning import StudentStudyBlock
from navox.student import service as schedule
from navox.student.occurrences import project
from navox.student_planning import calendar_busy, service
from navox.student_planning.alarm import alarm_schedule
from navox.student_planning.planner import available_slots


async def openings(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, days: int = 1
) -> dict:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    now = utc(await service.database_now(database))
    zone = ZoneInfo(user.timezone)
    day = now.astimezone(zone).date()
    left = datetime.combine(day, time.min, zone).astimezone(UTC)
    right = datetime.combine(day + timedelta(days=days), time.min, zone).astimezone(UTC)
    prefs = await service.preferences(database, user_id, workspace_id)
    source = await schedule.state_for_window(database, user_id, workspace_id, left, right)
    classes, fixed, _, warnings = project(
        *source, start_at=left, end_at=right, include_conflicts=False
    )
    if warnings:
        raise schedule.StudentError(
            409, "Resolve timetable clock-change warnings before checking free time"
        )
    external = await calendar_busy.collect(
        database,
        user_id,
        workspace_id,
        start_at=left,
        end_at=right,
        travel_minutes=prefs.travel_minutes,
    )
    busy = [
        (
            r.start_at
            - timedelta(
                minutes=r.walk_minutes if r.walk_minutes is not None else prefs.travel_minutes
            ),
            r.end_at
            + timedelta(
                minutes=r.walk_minutes if r.walk_minutes is not None else prefs.travel_minutes
            ),
        )
        for r in classes
        if not r.cancelled
    ]
    busy.extend(
        (
            r.start_at - timedelta(minutes=prefs.travel_minutes),
            r.end_at + timedelta(minutes=prefs.travel_minutes),
        )
        for r in fixed
    )
    busy.extend(external.intervals)
    studies = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status.in_(["planned", "needs_review"]),
                StudentStudyBlock.start_at < right,
                StudentStudyBlock.end_at > left,
            )
            .limit(601)
        )
    )
    if len(studies) > 600:
        raise schedule.StudentError(409, "Narrow the window to check all study blocks")
    rest = timedelta(minutes=prefs.break_minutes)
    busy.extend((utc(r.start_at) - rest, utc(r.end_at) + rest) for r in studies)
    wakes = alarm_schedule(prefs, user.timezone, day, classes, days=days)
    overrides = {
        datetime.fromisoformat(r["day"]).date(): datetime.fromisoformat(r["scheduled_at"])
        if r["scheduled_at"]
        else None
        for r in wakes
        if r["enabled"] and prefs.alarm.smart
    }
    slots, issues = available_slots(
        prefs, user.timezone, start_at=now, end_at=right, busy=busy, wake_overrides=overrides
    )
    return {
        "timezone": user.timezone,
        "checked_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
        "calendar_checked": external.connected,
        "routine_confirmed": prefs.revision > 0,
        "warnings": [*external.warnings, *sorted(issues)],
        "openings": [
            {
                "start_at": s.start.isoformat(),
                "end_at": s.end.isoformat(),
                "minutes": int((s.end - s.start).total_seconds() // 60),
            }
            for s in slots
            if (s.end - s.start).total_seconds() >= 15 * 60
        ][:200],
    }
