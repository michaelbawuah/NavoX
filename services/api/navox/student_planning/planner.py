"""Bounded deterministic scheduling. Infeasible work remains explicitly unfitted."""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from navox.student.occurrences import local_moment
from navox.student_planning.schemas import (
    MAX_BLOCKS,
    DayCapacity,
    PlanResult,
    Preferences,
    ProposedBlock,
    TaskView,
    UnfittedWork,
)


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    day: date
    focus: bool = False


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Planning instants must include a timezone")
    return value.astimezone(UTC)


def subtract(slots: list[Slot], start: datetime, end: datetime) -> list[Slot]:
    result: list[Slot] = []
    for slot in slots:
        if end <= slot.start or start >= slot.end:
            result.append(slot)
            continue
        if start > slot.start:
            result.append(Slot(slot.start, start, slot.day, slot.focus))
        if end < slot.end:
            result.append(Slot(end, slot.end, slot.day, slot.focus))
    return result


def _window(day: date, start: str, end: str, timezone: str) -> tuple[datetime, datetime]:
    first = local_moment(day, start, timezone, strict=True)
    last = local_moment(day + timedelta(days=end <= start), end, timezone, strict=True)
    if first is None or last is None or last <= first:
        raise ValueError("A routine window crosses an invalid clock change")
    return first, last


def _focus(slots: list[Slot], start: datetime, end: datetime) -> list[Slot]:
    result: list[Slot] = []
    for slot in slots:
        if slot.focus or end <= slot.start or start >= slot.end:
            result.append(slot)
            continue
        if slot.start < start:
            result.append(Slot(slot.start, start, slot.day))
        result.append(Slot(max(slot.start, start), min(slot.end, end), slot.day, True))
        if slot.end > end:
            result.append(Slot(end, slot.end, slot.day))
    return result


def _score(task: TaskView, now: datetime) -> float:
    due_hours = (
        max(1 / 60, (utc(task.due_at) - now).total_seconds() / 3600) if task.due_at else 8760.0
    )
    return (
        1_000_000 / due_hours
        + task.priority * 100
        + task.course_weight * task.remaining_minutes / 60
    )


def available_slots(
    preferences: Preferences,
    timezone: str,
    *,
    start_at: datetime,
    end_at: datetime,
    busy: list[tuple[datetime, datetime]],
    wake_overrides: dict[date, datetime | None] | None = None,
) -> tuple[list[Slot], set[str]]:
    start_at, end_at = utc(start_at), utc(end_at)
    if end_at <= start_at or end_at - start_at > timedelta(days=15):
        raise ValueError("Choose an increasing planning window of at most 14 local days")
    if len(busy) > 10_000:
        raise ValueError("Too many records to plan safely")
    zone = ZoneInfo(timezone)
    slots: list[Slot] = []
    warnings: set[str] = set()
    first_day = start_at.astimezone(zone).date()
    last_day = (end_at - timedelta(microseconds=1)).astimezone(zone).date()
    day = first_day - timedelta(days=1)
    while day <= last_day:
        try:
            awake_start, awake_end = _window(
                day,
                preferences.wake_times[day.weekday()]
                if preferences.wake_times
                else preferences.wake_time,
                preferences.sleep_time,
                timezone,
            )
            if wake_overrides and day in wake_overrides:
                wake = wake_overrides[day]
                if wake is None:
                    raise ValueError("Smart wake time is unresolved")
                awake_start = utc(wake)
            clipped_start, clipped_end = max(awake_start, start_at), min(awake_end, end_at)
            # Split at midnight so each actual local day keeps its own 15% buffer.
            current = clipped_start
            while current < clipped_end:
                local_day = current.astimezone(zone).date()
                midnight = datetime.combine(
                    local_day + timedelta(days=1), time.min, zone
                ).astimezone(UTC)
                stop = min(midnight, clipped_end)
                if stop <= current:
                    break
                slots.append(Slot(current, stop, local_day))
                current = stop
        except ValueError:
            warnings.add(
                "A routine window crosses a clock change; that window was left unscheduled."
            )
        day += timedelta(days=1)
    for start, end in busy:
        slots = subtract(slots, utc(start), utc(end))
    day = first_day - timedelta(days=1)
    while day <= last_day:
        for window in preferences.never_schedule:
            if day.weekday() not in window.weekdays:
                continue
            try:
                start, end = _window(day, window.start_time, window.end_time, timezone)
            except ValueError:
                start = datetime.combine(day, time.min, zone).astimezone(UTC)
                end = datetime.combine(day + timedelta(days=2), time.min, zone).astimezone(UTC)
                warnings.add(
                    "A never-schedule window crosses a clock change; "
                    "the affected days were kept free."
                )
            slots = subtract(slots, start, end)
        for window in preferences.focus_windows:
            if day.weekday() in window.weekdays:
                try:
                    start, end = _window(day, window.start_time, window.end_time, timezone)
                    slots = _focus(slots, start, end)
                except ValueError:
                    warnings.add("A focus window crosses a clock change and was not assumed.")
        day += timedelta(days=1)
    return slots, warnings


def build_plan(
    tasks: list[TaskView],
    preferences: Preferences,
    timezone: str,
    *,
    start_at: datetime,
    end_at: datetime,
    busy: list[tuple[datetime, datetime]],
    wake_overrides: dict[date, datetime | None] | None = None,
    reserve_buffer: bool = True,
) -> PlanResult:
    if len(tasks) > 200:
        raise ValueError("Too many records to plan safely")
    start_at, end_at = utc(start_at), utc(end_at)
    slots, warnings = available_slots(
        preferences,
        timezone,
        start_at=start_at,
        end_at=end_at,
        busy=busy,
        wake_overrides=wake_overrides,
    )
    free_by_day: dict[date, int] = {}
    for slot in slots:
        free_by_day[slot.day] = free_by_day.get(slot.day, 0) + int(
            (slot.end - slot.start).total_seconds() // 60
        )
    # Reserve real unscheduled time first. Breaks are additional, not counted as
    # the 15% buffer. Taking it from the last free slots is stable and explainable.
    for day, free in free_by_day.items():
        reserve = math.ceil(free * 0.15) if reserve_buffer else 0
        for slot in sorted(
            [item for item in slots if item.day == day], key=lambda item: item.end, reverse=True
        ):
            take = min(reserve, int((slot.end - slot.start).total_seconds() // 60))
            if take:
                slots = subtract(slots, slot.end - timedelta(minutes=take), slot.end)
                reserve -= take
            if reserve == 0:
                break
    budgets = {
        day: free - (math.ceil(free * 0.15) if reserve_buffer else 0)
        for day, free in free_by_day.items()
    }
    planned_by_day = {day: 0 for day in free_by_day}
    blocks: list[ProposedBlock] = []
    unfitted: list[UnfittedWork] = []
    ordered = sorted(
        [task for task in tasks if task.status == "active" and task.remaining_minutes > 0],
        key=lambda task: (
            -_score(task, start_at),
            utc(task.due_at) if task.due_at else end_at + timedelta(days=365),
            str(task.id),
        ),
    )
    for task in ordered:
        remaining = task.remaining_minutes
        task_days: dict[date, int] = {}
        deadline = min(utc(task.due_at), end_at) if task.due_at else end_at
        if deadline > start_at:
            while remaining and len(blocks) < MAX_BLOCKS:
                candidates = sorted(
                    slots,
                    key=lambda slot: (
                        task_days.get(slot.day, 0),
                        not slot.focus if task.difficulty >= 4 else slot.focus,
                        slot.start,
                    ),
                )
                selected = None
                for slot in candidates:
                    minutes = min(
                        remaining,
                        preferences.max_study_minutes,
                        budgets.get(slot.day, 0) - planned_by_day.get(slot.day, 0),
                        int((min(slot.end, deadline) - slot.start).total_seconds() // 60),
                    )
                    # Do not turn narrow gaps into unusable fragments. A short final
                    # remainder is allowed only when it completes the task.
                    if minutes >= min(15, remaining):
                        selected = slot, minutes
                        break
                if selected is None:
                    break
                slot, minutes = selected
                end = slot.start + timedelta(minutes=minutes)
                blocks.append(
                    ProposedBlock(
                        task_id=task.id,
                        title=task.title,
                        start_at=slot.start,
                        end_at=end,
                        minutes=minutes,
                        focus_window=slot.focus,
                    )
                )
                planned_by_day[slot.day] += minutes
                task_days[slot.day] = task_days.get(slot.day, 0) + minutes
                remaining -= minutes
                rest = timedelta(minutes=preferences.break_minutes)
                slots = subtract(slots, slot.start - rest, end + rest)
                if task.difficulty >= 4 and preferences.focus_windows and not slot.focus:
                    warnings.add(
                        "Some difficult work is outside your focus hours "
                        "because the available focus time was insufficient."
                    )
        if remaining:
            reason: Literal[
                "deadline_passed", "cannot_meet_deadline", "outside_horizon", "insufficient_time"
            ] = (
                "deadline_passed"
                if task.due_at and utc(task.due_at) <= start_at
                else "cannot_meet_deadline"
                if task.due_at and utc(task.due_at) <= end_at
                else "outside_horizon"
                if task.due_at and utc(task.due_at) > end_at
                else "insufficient_time"
            )
            unfitted.append(
                UnfittedWork(
                    task_id=task.id,
                    title=task.title,
                    minutes=remaining,
                    due_at=task.due_at,
                    reason=reason,
                )
            )
    if any(task.due_at is None for task in ordered):
        warnings.add(
            "Tasks without a deadline were planned after more urgent work; "
            "add a deadline when you know it."
        )
    return PlanResult(
        start_at=start_at,
        end_at=end_at,
        blocks=sorted(blocks, key=lambda block: (block.start_at, str(block.task_id))),
        unfitted_work=unfitted,
        capacities=[
            DayCapacity(
                day=day,
                free_minutes=free,
                buffer_minutes=math.ceil(free * 0.15) if reserve_buffer else 0,
                planned_minutes=planned_by_day[day],
            )
            for day, free in sorted(free_by_day.items())
        ],
        warnings=sorted(warnings),
    )
