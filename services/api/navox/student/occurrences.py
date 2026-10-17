"""Pure bounded wall-clock projection. No providers, models or inferred class times."""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from navox.student.schemas import (
    BreakView,
    Conflict,
    CourseView,
    ExceptionView,
    FixedBlockView,
    FixedOccurrence,
    MeetingView,
    Occurrence,
)

MAX_CONFLICTS = 200
TRUNCATED_CONFLICTS_WARNING = (
    "Only the first 200 overlaps are shown. Narrow the date range to review more."
)


@dataclass(frozen=True)
class ClassProjectionIssue:
    """An active class has no trustworthy instant on this local calendar day."""

    day: date
    timezone: str
    day_start: datetime
    day_end: datetime


def local_moment(day: date, clock: str, timezone: str, *, strict: bool = False) -> datetime | None:
    """RFC-style first fold for recurring classes; never invent a nonexistent instant.

    An explicit user lookup at an ambiguous local time must be clarified instead.
    """
    zone = ZoneInfo(timezone)
    naive = datetime.combine(day, time.fromisoformat(clock))
    options = sorted(
        {
            naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            for fold in (0, 1)
            if naive.replace(tzinfo=zone, fold=fold)
            .astimezone(UTC)
            .astimezone(zone)
            .replace(tzinfo=None)
            == naive
        }
    )
    if strict and len(options) != 1:
        raise ValueError("That local time is ambiguous or does not exist; choose another time")
    return options[0] if options else None


def occurs_on(meeting: MeetingView, day: date, course: CourseView) -> bool:
    return course.term_start <= day <= course.term_end and (
        day in meeting.dates if meeting.dates is not None else day.weekday() in meeting.weekdays
    )


def project(
    courses: list[CourseView],
    fixed_blocks: list[FixedBlockView],
    exceptions: list[ExceptionView],
    breaks: list[BreakView],
    *,
    start_at: datetime,
    end_at: datetime,
    include_cancelled: bool = True,
    include_conflicts: bool = True,
    class_issues: list[ClassProjectionIssue] | None = None,
) -> tuple[list[Occurrence], list[FixedOccurrence], list[Conflict], list[str]]:
    if start_at.tzinfo is None or end_at.tzinfo is None or not start_at < end_at:
        raise ValueError("An aware, increasing time window is required")
    if end_at - start_at > timedelta(days=32):
        raise ValueError("A timetable window cannot exceed 31 local days")
    edits = {(item.meeting_id, item.date): item for item in exceptions}
    occurrences: list[Occurrence] = []
    fixed: list[FixedOccurrence] = []
    warnings: set[str] = set()
    for course in courses:
        if course.archived:
            continue
        for meeting in course.meetings:
            zone = ZoneInfo(meeting.timezone)
            day = max(course.term_start, start_at.astimezone(zone).date() - timedelta(days=1))
            last = min(course.term_end, end_at.astimezone(zone).date())
            while day <= last:
                if occurs_on(meeting, day, course):
                    edit = edits.get((meeting.id, day))
                    on_break = any(row.start_date <= day <= row.end_date for row in breaks)
                    cancelled = on_break or (edit is not None and edit.cancelled)
                    start = local_moment(
                        day,
                        (edit.start_time if edit and edit.start_time else meeting.start_time),
                        meeting.timezone,
                    )
                    end = local_moment(
                        day,
                        (edit.end_time if edit and edit.end_time else meeting.end_time),
                        meeting.timezone,
                    )
                    if start is None or end is None or end <= start:
                        warnings.add(
                            "A class falls in a daylight-saving clock change "
                            "and needs a one-day correction."
                        )
                        if not cancelled and class_issues is not None:
                            day_start = datetime.combine(day, time.min, zone).astimezone(UTC)
                            day_end = datetime.combine(
                                day + timedelta(days=1), time.min, zone
                            ).astimezone(UTC)
                            if day_end <= day_start:
                                day_end = day_start + timedelta(days=1)
                            if day_start < end_at and day_end > start_at:
                                class_issues.append(
                                    ClassProjectionIssue(day, meeting.timezone, day_start, day_end)
                                )
                    elif start < end_at and end > start_at and (include_cancelled or not cancelled):
                        occurrences.append(
                            Occurrence(
                                id=f"{meeting.id}:{day.isoformat()}",
                                meeting_id=meeting.id,
                                course_id=course.id,
                                course_code=course.code,
                                course_name=course.name,
                                kind=meeting.kind,
                                start_at=start,
                                end_at=end,
                                timezone=meeting.timezone,
                                building=edit.building
                                if edit and edit.building is not None
                                else meeting.building,
                                room=edit.room if edit and edit.room is not None else meeting.room,
                                instructor=meeting.instructor or course.professor,
                                walk_minutes=meeting.walk_minutes,
                                cancelled=cancelled,
                                exception=edit is not None or on_break,
                            )
                        )
                day += timedelta(days=1)
    for block in fixed_blocks:
        zone = ZoneInfo(block.timezone)
        day = max(block.term_start, start_at.astimezone(zone).date() - timedelta(days=1))
        last = min(block.term_end, end_at.astimezone(zone).date())
        while day <= last:
            if day.weekday() in block.weekdays:
                start = local_moment(day, block.start_time, block.timezone)
                end = local_moment(day, block.end_time, block.timezone)
                if start is None or end is None or end <= start:
                    warnings.add(
                        "A fixed block falls in a daylight-saving clock change "
                        "and needs correction."
                    )
                elif start < end_at and end > start_at:
                    fixed.append(
                        FixedOccurrence(
                            id=f"{block.id}:{day.isoformat()}",
                            fixed_block_id=block.id,
                            title=block.title,
                            start_at=start,
                            end_at=end,
                        )
                    )
            day += timedelta(days=1)
    occurrences.sort(key=lambda item: (item.start_at, item.id))
    fixed.sort(key=lambda item: (item.start_at, item.id))
    conflicts: list[Conflict] = []
    if not include_conflicts:
        return occurrences, fixed, conflicts, sorted(warnings)
    active = [item for item in occurrences if not item.cancelled]
    fixed_starts = [item.start_at for item in fixed]
    for index, left in enumerate(active):
        for right_index in range(index + 1, len(active)):
            right = active[right_index]
            if right.start_at >= left.end_at:
                break
            if len(conflicts) >= MAX_CONFLICTS:
                warnings.add(TRUNCATED_CONFLICTS_WARNING)
                return occurrences, fixed, conflicts, sorted(warnings)
            conflicts.append(
                Conflict(
                    left_id=left.id,
                    right_id=right.id,
                    kind="class_class",
                    start_at=max(left.start_at, right.start_at),
                    end_at=min(left.end_at, right.end_at),
                )
            )
        lower = bisect_left(fixed_starts, left.start_at - timedelta(days=1))
        upper = bisect_left(fixed_starts, left.end_at)
        for block_occurrence in fixed[lower:upper]:
            if block_occurrence.start_at < left.end_at and block_occurrence.end_at > left.start_at:
                if len(conflicts) >= MAX_CONFLICTS:
                    warnings.add(TRUNCATED_CONFLICTS_WARNING)
                    return occurrences, fixed, conflicts, sorted(warnings)
                conflicts.append(
                    Conflict(
                        left_id=left.id,
                        right_id=block_occurrence.id,
                        kind="class_fixed",
                        start_at=max(left.start_at, block_occurrence.start_at),
                        end_at=min(left.end_at, block_occurrence.end_at),
                    )
                )
    return occurrences, fixed, conflicts, sorted(warnings)


def select_lookup(
    occurrences: list[Occurrence],
    *,
    mode: Literal["now", "next", "at", "day"],
    at: datetime,
    day: date,
    timezone: str,
) -> list[Occurrence]:
    zone = ZoneInfo(timezone)
    active = [item for item in occurrences if not item.cancelled]
    if mode == "day":
        return [item for item in active if item.start_at.astimezone(zone).date() == day]
    if mode == "next":
        upcoming = [
            item
            for item in active
            if item.start_at > at and item.start_at.astimezone(zone).date() == day
        ]
        if not upcoming:
            return []
        return [item for item in upcoming if item.start_at == upcoming[0].start_at]
    return [item for item in active if item.start_at <= at < item.end_at]
