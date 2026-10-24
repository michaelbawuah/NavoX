"""Shared local wake routine and preview-only web alarm groundwork."""

from datetime import UTC, date, timedelta
from zoneinfo import ZoneInfo

from navox.student.occurrences import local_moment
from navox.student_planning.schemas import Preferences


def wake_clock(preferences: Preferences, day: date) -> str:
    return (
        preferences.wake_times[day.weekday()] if preferences.wake_times else preferences.wake_time
    )


def alarm_schedule(
    preferences: Preferences,
    timezone: str,
    first_day: date,
    classes: list,
    *,
    days: int = 7,
) -> list[dict]:
    zone = ZoneInfo(timezone)
    result = []
    for index in range(days):
        day = first_day + timedelta(days=index)
        first = min(
            (
                row
                for row in classes
                if not row.cancelled and row.start_at.astimezone(zone).date() == day
            ),
            key=lambda row: row.start_at,
            default=None,
        )
        try:
            scheduled = local_moment(day, wake_clock(preferences, day), timezone, strict=True)
        except ValueError:
            scheduled = None
        reason = "Your saved wake time"
        if preferences.alarm.smart and first is not None:
            if first.walk_minutes is None:
                reason = "Add the walk time for your first class before using a smart alarm"
                scheduled = None
            else:
                scheduled = first.start_at.astimezone(UTC) - timedelta(
                    minutes=preferences.alarm.prep_minutes + first.walk_minutes
                )
                reason = "First class, minus preparation and walking time"
        enabled = (
            preferences.alarm.enabled
            and day.weekday() in preferences.alarm.days
            and day != preferences.alarm.skip_date
        )
        result.append(
            {
                "day": day.isoformat(),
                "enabled": enabled,
                "scheduled_at": scheduled.isoformat() if scheduled is not None else None,
                "reason": reason
                if scheduled is not None or preferences.alarm.smart
                else "This wake time crosses a clock change; choose another time",
                "first_class_at": first.start_at.isoformat() if first else None,
                "first_class_title": (first.course_code or first.course_name) if first else None,
            }
        )
    return result
