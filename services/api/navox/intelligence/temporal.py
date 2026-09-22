"""Small, explicit temporal grammar: uncertain dates stay uncertain."""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TemporalResolution:
    expression: str | None
    start_at: datetime | None = None
    end_at: datetime | None = None
    resolved_at: datetime | None = None
    method: str = "absent"
    confidence: float = 1.0

    def as_metadata(self) -> dict[str, object]:
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in asdict(self).items()
        }


def resolve_temporal(
    expression: str | None, *, occurred_at: datetime, timezone_name: str
) -> TemporalResolution:
    """Resolve dates against source time, never processing time or model timestamps.

    Date-only expressions retain a local-day interval. Unknown expressions have
    no guessed deadline. DST folds retain both possible instants; nonexistent
    local clock times are unresolved.
    """
    zone = ZoneInfo(timezone_name)
    if not expression:
        return TemporalResolution(expression)
    original = expression
    text = expression.strip().casefold().replace("’", "'")
    try:
        instant = datetime.fromisoformat(expression.replace("Z", "+00:00"))
        if instant.tzinfo is not None:
            instant = instant.astimezone(UTC)
            return TemporalResolution(original, instant, instant, instant, "explicit_offset", 1)
    except ValueError:
        pass
    local_now = occurred_at.astimezone(zone)
    day: date | None = None
    method = "explicit_date"
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso:
        try:
            day = date.fromisoformat(iso.group(1))
        except ValueError:
            return TemporalResolution(original, method="unresolved", confidence=0.5)
    elif re.search(r"\btomorrow\b", text):
        day, method = local_now.date() + timedelta(days=1), "relative_day"
    elif re.search(r"\btoday\b", text):
        day, method = local_now.date(), "relative_day"
    else:
        weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        for number, weekday in enumerate(weekdays):
            if re.fullmatch(rf"(?:by |on )?(?:this )?{weekday}(?: at .+)?", text):
                delta = (number - local_now.weekday()) % 7
                day, method = local_now.date() + timedelta(days=delta), "upcoming_weekday"
                break
    if day is None:
        return TemporalResolution(original, method="unresolved", confidence=0.5)
    clock = re.search(r"(?:\bat\s+|[tT])(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
        meridiem = clock.group(3)
        if meridiem:
            if not 1 <= hour <= 12:
                return TemporalResolution(original, method="unresolved", confidence=0.5)
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        if hour > 23 or minute > 59:
            return TemporalResolution(original, method="unresolved", confidence=0.5)
        local = datetime.combine(day, time(hour, minute))
        possibilities = sorted(
            {
                aware.astimezone(UTC)
                for fold in (0, 1)
                if (aware := local.replace(tzinfo=zone, fold=fold))
                .astimezone(UTC)
                .astimezone(zone)
                .replace(tzinfo=None)
                == local
            }
        )
        if not possibilities:
            return TemporalResolution(original, method="nonexistent_local_time", confidence=0.5)
        if len(possibilities) == 2:
            return TemporalResolution(
                original,
                possibilities[0],
                possibilities[1],
                method="ambiguous_local_time",
                confidence=0.7,
            )
        instant = possibilities[0]
        return TemporalResolution(original, instant, instant, instant, method, 0.98)
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    return TemporalResolution(original, start, end, method=f"{method}_window", confidence=0.9)
