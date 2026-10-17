"""Bounded local ICS review. Calendar text is discarded after normalization.

icalendar decodes RFC properties. The supported DAILY/WEEKLY subset is expanded
only over the reviewed term; unsupported rules are rejected, never approximated.
No alarm, attachment, URL, custom time-zone rule or embedded code is executed.
"""

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Calendar

from navox.student.occurrences import local_moment
from navox.student.schemas import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_OCCURRENCES,
    MAX_MEETINGS,
    ImportCandidate,
    valid_term,
    valid_zone,
)

PARSER_VERSION = "student-ics.v1"
DAY_CODES = {name: index for index, name in enumerate(("MO", "TU", "WE", "TH", "FR", "SA", "SU"))}


class TimetableImportError(ValueError):
    """Fixed diagnostics without echoing private file contents."""


def _property(event: Any, name: str) -> Any:
    value = event.get(name)
    if isinstance(value, list):
        raise TimetableImportError("A calendar event repeats a singleton property.")
    return value


def _stamp(prop: Any, default_zone: str) -> tuple[datetime, str]:
    value = getattr(prop, "dt", None)
    if not isinstance(value, datetime):
        raise TimetableImportError("Timed class events need date-time values, not all-day dates.")
    if value.second or value.microsecond:
        raise TimetableImportError(
            "Class times with seconds are not supported; review them manually."
        )
    tzid = prop.params.get("TZID")
    if tzid:
        try:
            zone = valid_zone(str(tzid))
        except ValueError:
            raise TimetableImportError(
                "The file uses an unsupported custom time zone; export with an IANA zone or UTC."
            ) from None
        # Use trusted IANA data, not an embedded VTIMEZONE definition.
        local = value.replace(tzinfo=None)
        instant = local_moment(local.date(), local.strftime("%H:%M"), zone)
    elif value.tzinfo is None:
        zone = default_zone
        instant = local_moment(value.date(), value.strftime("%H:%M"), zone)
    else:
        zone = "UTC"
        instant = value.astimezone(UTC)
    if instant is None:
        raise TimetableImportError(
            "An event starts in a nonexistent daylight-saving time; "
            "correct the file or add a one-day meeting."
        )
    return instant, zone


def _duration(
    event: Any, start: datetime, default_zone: str, inherited: timedelta | None = None
) -> timedelta:
    end_prop = _property(event, "DTEND")
    duration_prop = _property(event, "DURATION")
    if end_prop is not None and duration_prop is not None:
        raise TimetableImportError("An event cannot contain both DTEND and DURATION.")
    duration: object
    if end_prop is not None:
        end, _ = _stamp(end_prop, default_zone)
        duration = end - start
    elif duration_prop is not None:
        duration = getattr(duration_prop, "dt", None)
    else:
        duration = inherited
    if not isinstance(duration, timedelta) or not timedelta(0) < duration < timedelta(days=1):
        raise TimetableImportError(
            "Every timed class needs a positive end time or duration shorter than one day."
        )
    return duration


def _text(event: Any, field: str, limit: int, default: str = "") -> str:
    prop = _property(event, field)
    value = str(prop) if prop is not None else default
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise TimetableImportError(
            "An event title or location is too long or contains control characters."
        )
    return value.strip()


def _date_list(event: Any, field: str, zone: str) -> set[datetime]:
    values = event.get(field, [])
    if not isinstance(values, list):
        values = [values]
    result: set[datetime] = set()
    for prop in values:
        for value in getattr(prop, "dts", []):
            instant, _ = _stamp(value, zone)
            result.add(instant)
            if len(result) > 1_000:
                raise TimetableImportError("An event has too many explicit recurrence dates.")
    return result


def _starts(event: Any, start: datetime, zone: str, term_end: date) -> set[datetime]:
    rule = _property(event, "RRULE")
    if rule is None:
        return {start} | _date_list(event, "RDATE", zone)
    if set(rule) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "WKST"}:
        raise TimetableImportError(
            "This recurrence rule is not supported yet; "
            "export individual meetings or add the class manually."
        )
    frequency = str(rule.get("FREQ", [""])[0])
    if frequency not in {"DAILY", "WEEKLY"}:
        raise TimetableImportError(
            "Only daily and weekly class recurrence is supported; "
            "export other rules as individual meetings."
        )
    try:
        interval = int(rule.get("INTERVAL", [1])[0])
        count = int(rule["COUNT"][0]) if "COUNT" in rule else None
        week_start = DAY_CODES[str(rule.get("WKST", ["MO"])[0])]
        weekdays = {DAY_CODES[str(value)] for value in rule.get("BYDAY", [])}
    except (ValueError, KeyError, IndexError, TypeError):
        raise TimetableImportError("A recurrence rule has invalid days or counts.") from None
    if not 1 <= interval <= 366 or (count is not None and not 1 <= count <= MAX_IMPORT_OCCURRENCES):
        raise TimetableImportError("A recurrence interval or count exceeds the supported limit.")
    until = rule.get("UNTIL", [None])[0]
    if until is not None and count is not None:
        raise TimetableImportError("A recurrence cannot have both COUNT and UNTIL.")
    if until is not None and (not isinstance(until, datetime) or until.tzinfo is None):
        raise TimetableImportError("A timed recurrence UNTIL must include a UTC time zone.")
    local_start = start.astimezone(ZoneInfo(zone))
    if weekdays and local_start.weekday() not in weekdays:
        raise TimetableImportError(
            "The first meeting does not match its recurrence days; correct the file before import."
        )
    if frequency == "WEEKLY" and not weekdays:
        weekdays = {local_start.weekday()}
    anchor = local_start.date()
    anchor_week = anchor - timedelta(days=(anchor.weekday() - week_start) % 7)
    day = anchor
    generated = 0
    result: set[datetime] = set()
    while day <= term_end:
        offset = (day - anchor).days
        week_offset = (
            (day - timedelta(days=(day.weekday() - week_start) % 7)) - anchor_week
        ).days // 7
        matches = (
            (offset % interval == 0 and (not weekdays or day.weekday() in weekdays))
            if frequency == "DAILY"
            else (week_offset % interval == 0 and day.weekday() in weekdays)
        )
        if matches:
            instant = local_moment(day, local_start.strftime("%H:%M"), zone)
            if instant is not None:
                if until is not None and instant > until.astimezone(UTC):
                    break
                generated += 1
                if count is not None and generated > count:
                    break
                result.add(instant)
        day += timedelta(days=1)
    return result | _date_list(event, "RDATE", zone)


def parse_timetable_ics(
    content: str, *, timezone: str, term_start: date, term_end: date
) -> tuple[list[ImportCandidate], list[str]]:
    valid_zone(timezone)
    valid_term(term_start, term_end)
    try:
        raw = content.encode("utf-8")
    except UnicodeError:
        raise TimetableImportError("Choose a UTF-8 calendar file.") from None
    if not 0 < len(raw) <= MAX_IMPORT_BYTES or "\x00" in content:
        raise TimetableImportError("Choose a UTF-8 calendar file no larger than 2 MB.")
    if any(
        (ord(char) < 32 and char not in "\r\n\t") or char in "\x7f\x85\u2028\u2029"
        for char in content
    ):
        raise TimetableImportError("The calendar contains unsupported control characters.")
    # Unfold before examining component names: BEGIN:VTIME\n ZONE is legal.
    raw_lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(raw_lines) > 30_000:
        raise TimetableImportError("The calendar has too many or oversized properties.")
    lines: list[str] = []
    for line in raw_lines:
        if line.startswith((" ", "\t")):
            if not lines:
                raise TimetableImportError("The calendar starts with an invalid folded line.")
            if len(lines[-1]) + len(line) - 1 > 24_000:
                raise TimetableImportError("An unfolded calendar property is too long.")
            lines[-1] += line[1:]
        else:
            if len(line) > 24_000:
                raise TimetableImportError("A calendar property is too long.")
            lines.append(line)
    if sum(line.upper().startswith("BEGIN:") for line in lines) > 3_100:
        raise TimetableImportError("The calendar has too many components.")
    # Embedded VTIMEZONE rules are untrusted input. Remove them before parsing,
    # so the library cannot expand attacker-defined transition recurrences.
    # DTSTART/TZID is still validated against trusted IANA data in _stamp.
    filtered: list[str] = []
    timezone_depth = 0
    nesting = 0
    for line in lines:
        head, _, value = line.partition(":")
        boundary_key = head.split(";", 1)[0].strip().upper()
        if boundary_key in {"BEGIN", "END"} and head.strip().upper() != boundary_key:
            raise TimetableImportError("Calendar component boundaries cannot contain parameters.")
        component = value.strip().upper()
        if boundary_key == "BEGIN":
            nesting += 1
            if nesting > 8:
                raise TimetableImportError("The calendar nesting exceeds the supported limit.")
        if boundary_key == "BEGIN" and component == "VTIMEZONE":
            timezone_depth += 1
        if timezone_depth == 0:
            filtered.append(line)
        if boundary_key == "END" and component == "VTIMEZONE":
            timezone_depth -= 1
        if boundary_key == "END":
            nesting -= 1
        if nesting < 0 or timezone_depth < 0:
            raise TimetableImportError("Calendar component boundaries do not match.")
    if nesting or timezone_depth:
        raise TimetableImportError("Calendar component boundaries do not match.")
    try:
        calendar = Calendar.from_ical("\r\n".join(filtered).encode("utf-8"))
        events = calendar.walk("VEVENT")
    except Exception:
        raise TimetableImportError("The calendar file could not be read.") from None
    if getattr(calendar, "name", None) != "VCALENDAR" or not events or len(events) > 1_000:
        raise TimetableImportError("Choose a complete calendar with 1 to 1,000 events.")
    masters: dict[str, Any] = {}
    overrides: dict[str, dict[datetime, Any]] = {}
    seen: dict[tuple[str, str], bytes] = {}
    warnings: set[str] = set()
    for event in events:
        if event.errors:
            raise TimetableImportError("The calendar contains an invalid event property.")
        uid = _text(event, "UID", 512)
        if not uid:
            raise TimetableImportError("Every calendar event needs a UID.")
        recurrence = _property(event, "RECURRENCE-ID")
        key_time: datetime | None = None
        if recurrence is not None:
            if recurrence.params.get("RANGE"):
                raise TimetableImportError(
                    "THISANDFUTURE recurrence edits are not supported; export individual meetings."
                )
            key_time, _ = _stamp(recurrence, timezone)
        key = (uid, key_time.isoformat() if key_time else "")
        encoded = event.to_ical()
        if key in seen:
            if seen[key] != encoded:
                raise TimetableImportError(
                    "The calendar has conflicting duplicate events; "
                    "export a single current version."
                )
            warnings.add("Identical duplicate events were included once.")
            continue
        seen[key] = encoded
        if key_time is None:
            masters[uid] = event
        else:
            overrides.setdefault(uid, {})[key_time] = event
    if set(overrides) - masters.keys():
        raise TimetableImportError(
            "A changed recurring meeting is missing its original series; "
            "export the complete calendar."
        )
    groups: dict[tuple[str, str, str, str, str, str], list[date]] = {}
    source_identity = [
        _text(calendar, "PRODID", 512),
        _text(calendar, "X-WR-RELCALID", 512),
    ]
    total = 0
    for uid, master in masters.items():
        if str(_property(master, "STATUS") or "").upper() == "CANCELLED":
            warnings.add("Cancelled class series were excluded.")
            continue
        start_prop = _property(master, "DTSTART")
        if start_prop is None:
            raise TimetableImportError("Every class needs an explicit start time.")
        if not isinstance(getattr(start_prop, "dt", None), datetime):
            warnings.add("All-day items were excluded because no class time was specified.")
            continue
        start, zone = _stamp(start_prop, timezone)
        if start.astimezone(ZoneInfo(zone)).date() < term_start - timedelta(days=366):
            raise TimetableImportError(
                "A series begins more than one year before this term; export only the current term."
            )
        duration = _duration(master, start, zone)
        title = _text(master, "SUMMARY", 200)
        if not title:
            raise TimetableImportError("Every class needs a title for review.")
        location = _text(master, "LOCATION", 160)
        starts = _starts(master, start, zone, term_end)
        excluded = _date_list(master, "EXDATE", zone)
        edits = overrides.get(uid, {})
        if set(edits) - starts:
            raise TimetableImportError(
                "A changed meeting does not match an occurrence in its original series."
            )
        for original in sorted(starts):
            edit = edits.get(original)
            if edit is None and original in excluded:
                continue
            if edit is not None and str(_property(edit, "STATUS") or "").upper() == "CANCELLED":
                warnings.add("Cancelled meetings were excluded.")
                continue
            actual = original
            actual_zone = zone
            actual_duration = duration
            actual_title, actual_location = title, location
            if edit is not None:
                changed_start = _property(edit, "DTSTART")
                if changed_start is None:
                    raise TimetableImportError("A changed class needs an explicit start time.")
                actual, actual_zone = _stamp(changed_start, zone)
                actual_duration = _duration(edit, actual, actual_zone, duration)
                actual_title = _text(edit, "SUMMARY", 200, title)
                actual_location = _text(edit, "LOCATION", 160, location)
            local = actual.astimezone(ZoneInfo(actual_zone))
            end = (actual + actual_duration).astimezone(ZoneInfo(actual_zone))
            if not term_start <= local.date() <= term_end:
                continue
            if local.date() != end.date() or end.strftime("%H:%M") <= local.strftime("%H:%M"):
                raise TimetableImportError(
                    "Overnight classes or clock-change durations need manual review."
                )
            group = (
                uid,
                actual_title,
                actual_zone,
                local.strftime("%H:%M"),
                end.strftime("%H:%M"),
                actual_location,
            )
            groups.setdefault(group, []).append(local.date())
            total += 1
            if total > MAX_IMPORT_OCCURRENCES or len(groups) > MAX_MEETINGS:
                raise TimetableImportError(
                    "The import exceeds 200 sections or 5,000 term meetings; split the file."
                )
    candidates: list[ImportCandidate] = []
    for group_key, days in sorted(groups.items()):
        uid, title, zone, start_clock, end_clock, location = group_key
        family = hashlib.sha256(repr([source_identity, uid]).encode()).hexdigest()
        variant = hashlib.sha256(repr(group_key).encode()).hexdigest()[:32]
        identifier = f"{family}.{variant}"
        candidates.append(
            ImportCandidate(
                id=identifier,
                course_name=title,
                course_code="",
                kind="other",
                dates=sorted(set(days)),
                start_time=start_clock,
                end_time=end_clock,
                timezone=zone,
                building=location or None,
            )
        )
    if not candidates:
        raise TimetableImportError("No timed class meetings fall within the selected term.")
    warnings.add(
        "Review every class name and section. "
        "Calendar items are not assumed to be classes or assignments."
    )
    return candidates, sorted(warnings)
