"""Pick the two best meeting times from computed availability openings.

Pure and deterministic: no provider, database, model or write access. The input
must be a fresh `AvailabilityResult` produced by `compute_availability` for the
same owner and window. The caller must honor any shorter snapshot expiry or
compute immediately before selection; this result's sixty-second age cap is not
an authorization or source-expiry check. This module only positions a NEW meeting inside confirmed
openings, reserving ten minutes before and after it.

Honesty rules:
- Only confirmed openings with no uncertainty or displacement metadata are offered.
  Unknown coverage, unconfirmed routine and unknown travel are never free time.
- Movable study work is not offered without a feasible replacement plan. This
  conservative slice cannot recover unaffected portions of a large provisional
  opening that contains a movable study block; it can return fewer choices.
- Ten-minute buffers also fit inside the confirmed opening and after `now`.
  Existing class/meeting padding may make this conservative around those edges.
- Another person's sourced hours are a preference, never assumed agreement.
- Nothing here creates, holds or approves an event.
"""

from datetime import datetime, time, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from navox.student.schemas import valid_zone
from navox.student_availability.schemas import (
    AvailabilityResult,
    FrozenInput,
    Opening,
    TimeInterval,
    instant,
)

Purpose = Literal["lunch", "coffee", "office_hours", "project_meeting", "study_session", "other"]
SuggestionReason = Literal[
    "within_their_hours",
    "right_after_commitment",
    "right_before_commitment",
    "lunch_time",
    "soonest",
    "clear_of_deadlines",
    "moves_study_block",
]
SelectionStatus = Literal[
    "ok",
    "partial_information",
    "needs_duration",
    "needs_routine",
    "no_time",
]

DEFAULT_MINUTES: dict[Purpose, int | None] = {
    "lunch": 60,
    "coffee": 30,
    "office_hours": 30,
    "project_meeting": 60,
    "study_session": 60,
    "other": None,
}
LUNCH_WINDOW = (time(11, 30), time(14, 0))
COFFEE_WINDOW = (time(8, 0), time(18, 0))
CANDIDATE_STRIDE = timedelta(minutes=30)
HARD_CRUNCH = timedelta(minutes=60)
SOFT_CRUNCH = timedelta(hours=3)
SPREAD = timedelta(minutes=90)
MAX_CANDIDATES = 5_000
MEETING_BUFFER = timedelta(minutes=10)
MAX_AGE = timedelta(seconds=60)


class Crunch(FrozenInput):
    """An exam or deadline the student should not be stacked against."""

    at: datetime
    kind: Literal["exam", "deadline"]

    _at = field_validator("at")(instant)


class SuggestionRequest(FrozenInput):
    availability: AvailabilityResult
    now: datetime
    purpose: Purpose
    duration_minutes: int | None = Field(default=None, ge=5, le=480, strict=True)
    other_windows: tuple[TimeInterval, ...] = Field(default=(), max_length=200)
    require_other_windows: bool = Field(default=False, strict=True)
    crunch: tuple[Crunch, ...] = Field(default=(), max_length=200)
    count: int = Field(default=2, ge=1, le=5, strict=True)

    _now = field_validator("now")(instant)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        valid_zone(self.availability.timezone)
        checked_at = instant(self.availability.checked_at)
        if self.now < checked_at or self.now - checked_at >= MAX_AGE:
            raise ValueError("Compute fresh availability before suggesting times")
        if self.require_other_windows and not self.other_windows:
            raise ValueError("Required other-person windows must be supplied")
        return self


class Suggestion(TimeInterval):
    minutes: int = Field(ge=1)
    reasons: tuple[SuggestionReason, ...]
    displaced_study_block_ids: tuple[str, ...] = ()


class SuggestionResult(FrozenInput):
    status: SelectionStatus
    timezone: str
    duration_minutes: int | None
    suggestions: tuple[Suggestion, ...]
    alternatives_available: int = Field(ge=0)
    changes_applied: Literal[False] = False


def duration_for(purpose: Purpose, explicit: int | None) -> int | None:
    return explicit if explicit is not None else DEFAULT_MINUTES[purpose]


def _offerable(opening: Opening) -> bool:
    return not (
        opening.reasons
        or opening.unchecked_source_ids
        or opening.unknown_travel_record_ids
        or opening.displaced_study_block_ids
    )


def _ceil(moment: datetime, zone: ZoneInfo) -> datetime:
    """Round up to the next local :00 or :30 (an already-aligned time stays)."""
    local = moment.astimezone(zone)
    floor = local.replace(minute=local.minute - local.minute % 30, second=0, microsecond=0)
    if floor < local:
        floor += CANDIDATE_STRIDE
    return floor.astimezone(moment.tzinfo)


def _inside_daily(
    start: datetime, end: datetime, zone: ZoneInfo, window: tuple[time, time]
) -> bool:
    """True when the whole meeting sits inside one local day's [from, to] window."""
    first, last = start.astimezone(zone), end.astimezone(zone)
    return first.date() == last.date() and window[0] <= first.time() and last.time() <= window[1]


def _within(start: datetime, end: datetime, windows: tuple[TimeInterval, ...]) -> bool:
    return any(item.start_at <= start and end <= item.end_at for item in windows)


def _candidate_starts(
    opening: Opening,
    length: timedelta,
    zone: ZoneInfo,
    now: datetime,
    other_windows: tuple[TimeInterval, ...],
) -> list[datetime]:
    """Buffered edges, half hours and exact sourced other-person boundaries."""
    earliest = max(opening.start_at, now) + MEETING_BUFFER
    latest = opening.end_at - length - MEETING_BUFFER
    if latest < earliest:
        return []
    starts = {earliest, latest}
    for window in other_windows:
        starts.update(
            boundary
            for boundary in (window.start_at, window.end_at - length)
            if earliest <= boundary <= latest
        )
    cursor = _ceil(earliest, zone)
    aligned_count = 0
    while cursor <= latest and aligned_count < 200:
        starts.add(cursor)
        cursor += CANDIDATE_STRIDE
        aligned_count += 1
    starts.add(latest)
    return sorted(start for start in starts if earliest <= start <= latest)


def _ends(busy: tuple[TimeInterval, ...]) -> tuple[set[datetime], set[datetime]]:
    return {item.end_at for item in busy}, {item.start_at for item in busy}


def suggest(request: SuggestionRequest) -> SuggestionResult:
    """Return up to `count` ranked, non-overlapping options, best first."""
    availability = request.availability
    zone = ZoneInfo(availability.timezone)
    minutes = duration_for(request.purpose, request.duration_minutes)

    def result(
        status: SelectionStatus, picks: tuple[Suggestion, ...] = (), rest: int = 0
    ) -> SuggestionResult:
        return SuggestionResult(
            status=status,
            timezone=availability.timezone,
            duration_minutes=minutes,
            suggestions=picks,
            alternatives_available=rest,
        )

    if minutes is None:
        return result("needs_duration")
    if availability.status == "needs_routine":
        return result("needs_routine")
    length = timedelta(minutes=minutes)
    busy_ends, busy_starts = _ends(availability.busy)
    openings = [item for item in availability.confirmed_openings if _offerable(item)]

    scored: list[tuple[tuple[float, datetime], Suggestion]] = []
    for opening in openings:
        for start in _candidate_starts(opening, length, zone, request.now, request.other_windows):
            end = start + length
            if request.purpose == "lunch" and not _inside_daily(start, end, zone, LUNCH_WINDOW):
                continue
            if request.purpose == "coffee" and not _inside_daily(start, end, zone, COFFEE_WINDOW):
                continue
            theirs = _within(start, end, request.other_windows)
            if request.require_other_windows and not theirs:
                continue
            gaps = [item.at - end for item in request.crunch if item.at >= end]
            if any(gap < HARD_CRUNCH for gap in gaps) or any(
                item.at > start and item.at < end for item in request.crunch
            ):
                continue
            near_crunch = any(gap < SOFT_CRUNCH for gap in gaps)
            # Busy intervals already include buffers and known travel, so an
            # opening that starts where one ends follows a real commitment
            # (not merely wake time), and likewise for the end edge.
            after = start - MEETING_BUFFER == opening.start_at and opening.start_at in busy_ends
            before = end + MEETING_BUFFER == opening.end_at and opening.end_at in busy_starts

            penalty = 0.0
            if request.other_windows and not theirs:
                penalty += 100
            if near_crunch:
                penalty += 30
            if request.purpose == "lunch":
                middle = start.astimezone(zone) + length / 2
                noon = middle.replace(hour=12, minute=30, second=0, microsecond=0)
                penalty += abs((middle - noon).total_seconds()) / 600
            penalty += (start - request.now).total_seconds() / 3600 * 0.5
            if after:
                # Keeps the day compact; worth about two hours of "sooner".
                penalty -= 1

            reasons: list[SuggestionReason] = []
            if theirs:
                reasons.append("within_their_hours")
            if after:
                reasons.append("right_after_commitment")
            elif before:
                reasons.append("right_before_commitment")
            if request.purpose == "lunch":
                reasons.append("lunch_time")
            if request.crunch and not near_crunch:
                reasons.append("clear_of_deadlines")
            scored.append(
                (
                    (penalty, start),
                    Suggestion(
                        start_at=start,
                        end_at=end,
                        minutes=minutes,
                        reasons=tuple(reasons),
                    ),
                )
            )
            if len(scored) > MAX_CANDIDATES:
                raise ValueError("Too many candidate times; narrow the requested window")

    scored.sort(key=lambda pair: pair[0])
    picks: list[Suggestion] = []
    # Prefer alternatives spread through the day; relax that preference if the
    # only second option is nearby. Never invent a second time or overlap picks.
    for spread_required in (True, False):
        for _, option in scored:
            if len(picks) >= request.count:
                break
            if any(
                option.start_at < chosen.end_at and chosen.start_at < option.end_at
                for chosen in picks
            ):
                continue
            if spread_required and any(
                option.start_at.astimezone(zone).date() == chosen.start_at.astimezone(zone).date()
                and abs(option.start_at - chosen.start_at) < SPREAD
                for chosen in picks
            ):
                continue
            picks.append(option)
        if len(picks) >= request.count:
            break
    if not picks:
        return result("partial_information" if availability.status == "partial" else "no_time")
    earliest = min(option.start_at for _, option in scored)
    picks = [
        option.model_copy(update={"reasons": (*option.reasons, "soonest")})
        if option.start_at == earliest
        else option
        for option in picks
    ]
    status: SelectionStatus = "partial_information" if availability.status == "partial" else "ok"
    return result(status, tuple(picks), max(0, len(scored) - len(picks)))
