"""Deterministic schedule openings; no provider, database, model, or write access.

Confirmed means clear only within the supplied owner-scoped source inventory.
The caller still has to authenticate, establish the complete inventory, and
refresh it before any separately reviewed calendar action. A provisional gap
that crosses movable study work is not a feasible or approved replacement plan.
These are opening windows, not positioned meeting proposals. A future selector
must fit the new meeting's own buffers and recheck every constraint before review.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from navox.student.occurrences import local_moment
from navox.student_availability.schemas import (
    AvailabilityRequest,
    AvailabilityResult,
    AvailabilityWarning,
    Opening,
    Reason,
    SourceCoverage,
    TimeInterval,
    WarningCode,
)

MEETING_BUFFER = timedelta(minutes=10)
MAX_OPENINGS = 2_000
MAX_DIAGNOSTIC_REFERENCES = 20_000
Interval = tuple[datetime, datetime]


def _overlaps(left: Interval, right: Interval) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _merge(intervals: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = result[-1][0], max(result[-1][1], end)
        else:
            result.append((start, end))
    return result


def _subtract(windows: list[Interval], busy: list[Interval]) -> list[Interval]:
    """Both inputs are merged; scan blockers without a quadratic interval loop."""
    result: list[Interval] = []
    index = 0
    for start, end in windows:
        cursor = start
        while index < len(busy) and busy[index][1] <= cursor:
            index += 1
        current = index
        while current < len(busy) and busy[current][0] < end:
            block_start, block_end = busy[current]
            if block_start > cursor:
                result.append((cursor, min(block_start, end)))
            cursor = max(cursor, block_end)
            if cursor >= end:
                break
            current += 1
        if cursor < end:
            result.append((cursor, end))
    return result


def _wall_window(day: date, start: str, end: str, timezone: str) -> Interval:
    first = local_moment(day, start, timezone, strict=True)
    last = local_moment(day + timedelta(days=end <= start), end, timezone, strict=True)
    if first is None or last is None or first >= last:
        raise ValueError("A routine window has no unambiguous increasing instant")
    return first, last


def _uncertain_day_span(day: date, zone: ZoneInfo, *, days: int = 1) -> Interval:
    """Conservative blocker bounds, including both offsets at a midnight change.

    These are used only to WITHHOLD availability, never to claim an invented
    local instant exists. They also cover skipped local dates conservatively.
    """
    first = datetime.combine(day, time.min)
    last = datetime.combine(day + timedelta(days=days), time.min)
    start = min(first.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1))
    end = max(last.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1))
    return start, max(end, start + timedelta(hours=days * 24))


def _coverage_gaps(
    request: AvailabilityRequest,
    window: Interval,
    warnings: list[AvailabilityWarning],
) -> list[tuple[Interval, str]]:
    coverage = {source.source_id: source for source in request.coverage}
    unknown: list[tuple[Interval, str]] = []
    states: dict[str, WarningCode] = {
        "partial": "coverage_partial",
        "stale": "coverage_stale",
        "unavailable": "coverage_unavailable",
        "revoked": "coverage_revoked",
    }
    for source_id in sorted(request.required_source_ids):
        source: SourceCoverage | None = coverage.get(source_id)
        code: WarningCode | None = None
        if source is None:
            code = "coverage_missing"
        elif source.state != "complete":
            code = states[source.state]
        elif source.observed_at > request.now:
            code = "coverage_future"
        elif source.fresh_until <= request.now:
            code = "coverage_stale"
        if code is not None:
            unknown.append((window, source_id))
            warnings.append(AvailabilityWarning(code=code, source_id=source_id))
            continue
        if source is None:  # Narrowing only; missing coverage was handled above.
            continue
        for gap in _subtract([window], [(source.start_at, source.end_at)]):
            unknown.append((gap, source_id))
            warnings.append(
                AvailabilityWarning(
                    code="coverage_outside_window",
                    source_id=source_id,
                    start_at=gap[0],
                    end_at=gap[1],
                )
            )
    for issue in request.uncertainties:
        interval = issue.start_at, issue.end_at
        if _overlaps(interval, window):
            unknown.append((interval, issue.source_id))
            warnings.append(
                AvailabilityWarning(
                    code="projection_uncertainty",
                    source_id=issue.source_id,
                    start_at=issue.start_at,
                    end_at=issue.end_at,
                )
            )
    return unknown


def compute_availability(request: AvailabilityRequest) -> AvailabilityResult:
    """Compute maximal, disjoint hard-free intervals and disclose every condition.

    Default minimum is 30 elapsed minutes. An explicit shorter meeting duration
    uses that duration; an explicit short-slot request uses 15 minutes. Internal
    future movable study work is reported, not moved. Calendar-backed, started,
    stale, or explicitly fixed study blocks remain hard blockers.
    """
    zone = ZoneInfo(request.timezone)
    first_day = request.start_at.astimezone(zone).date()
    last_day = (request.end_at - timedelta(microseconds=1)).astimezone(zone).date()
    if (last_day - first_day).days >= 14:
        raise ValueError("Choose at most 14 local calendar days")
    warnings: list[AvailabilityWarning] = []
    if not request.routine_confirmed:
        warnings.append(AvailabilityWarning(code="routine_unconfirmed"))
    start_at = max(request.start_at, request.now)
    end_at = request.end_at
    if start_at >= end_at:
        return AvailabilityResult(
            timezone=request.timezone,
            checked_at=request.now,
            status="needs_routine" if not request.routine_confirmed else "complete",
            confirmed_openings=(),
            provisional_openings=(),
            busy=(),
            warnings=tuple(warnings),
        )
    query = start_at, end_at
    unknown = _coverage_gaps(request, query, warnings)
    awake: list[Interval] = []
    blocked: list[Interval] = []
    day = first_day - timedelta(days=1)
    while day <= last_day:
        try:
            first, last = _wall_window(
                day,
                request.preferences.wake_time,
                request.preferences.sleep_time,
                request.timezone,
            )
            clipped = max(first, start_at), min(last, end_at)
            if clipped[0] < clipped[1]:
                awake.append(clipped)
        except ValueError:
            span = _uncertain_day_span(day, zone, days=2)
            if _overlaps(span, query):
                warnings.append(
                    AvailabilityWarning(
                        code="routine_clock_change", start_at=span[0], end_at=span[1]
                    )
                )
        for never in request.preferences.never_schedule:
            if day.weekday() not in never.weekdays:
                continue
            try:
                blocked.append(
                    _wall_window(day, never.start_time, never.end_time, request.timezone)
                )
            except ValueError:
                span = _uncertain_day_span(day, zone, days=2)
                blocked.append(span)
                if _overlaps(span, query):
                    warnings.append(
                        AvailabilityWarning(
                            code="never_schedule_clock_change",
                            start_at=span[0],
                            end_at=span[1],
                        )
                    )
        day += timedelta(days=1)

    travel_unknown: list[tuple[Interval, str]] = []
    for record in request.busy:
        padding = MEETING_BUFFER if record.kind in {"class", "meeting"} else timedelta()
        first = record.start_at - padding - timedelta(minutes=record.travel_before_minutes or 0)
        last = record.end_at + padding + timedelta(minutes=record.travel_after_minutes or 0)
        blocked.append((first, last))
        if (record.kind in {"class", "meeting"} or record.travel_required) and (
            record.travel_before_minutes is None or record.travel_after_minutes is None
        ):
            # Include adjacent dates when unknown travel around a midnight event
            # could cross the day boundary. Supported travel inputs are bounded
            # to four hours; missing travel is never treated as sourced zero.
            uncertain_start = (
                record.start_at
                - padding
                - timedelta(
                    minutes=record.travel_before_minutes
                    if record.travel_before_minutes is not None
                    else 240
                )
            )
            uncertain_end = (
                record.end_at
                + padding
                + timedelta(
                    minutes=record.travel_after_minutes
                    if record.travel_after_minutes is not None
                    else 240
                )
            )
            first_day_of_record = uncertain_start.astimezone(zone).date()
            last_day_of_record = (uncertain_end - timedelta(microseconds=1)).astimezone(zone).date()
            span = _uncertain_day_span(
                first_day_of_record,
                zone,
                days=(last_day_of_record - first_day_of_record).days + 1,
            )
            if _overlaps(span, query):
                travel_unknown.append((span, record.id))
                warnings.append(
                    AvailabilityWarning(
                        code="travel_unknown", record_id=record.id, start_at=span[0], end_at=span[1]
                    )
                )

    movable: list[tuple[Interval, str]] = []
    for study in request.study_blocks:
        interval = study.start_at, study.end_at
        if (
            study.movable
            and not study.calendar_backed
            and not study.needs_review
            and study.start_at > request.now
        ):
            movable.append((interval, study.id))
        else:
            blocked.append(interval)

    merged_busy = _merge([(max(first, start_at), min(last, end_at)) for first, last in blocked])
    openings = _subtract(_merge(awake), merged_busy)
    if len(openings) > MAX_OPENINGS:
        raise ValueError("Too many availability openings; narrow the requested window")
    threshold = request.duration_minutes or (15 if request.include_short_slots else 30)
    confirmed: list[Opening] = []
    provisional: list[Opening] = []
    references = len(warnings)
    warned_study: set[str] = set()
    for interval in openings:
        minutes = int((interval[1] - interval[0]).total_seconds() // 60)
        if minutes < threshold:
            continue
        unchecked = tuple(sorted({source for gap, source in unknown if _overlaps(gap, interval)}))
        travel = tuple(
            sorted({identifier for gap, identifier in travel_unknown if _overlaps(gap, interval)})
        )
        displaced = tuple(
            sorted({identifier for gap, identifier in movable if _overlaps(gap, interval)})
        )
        references += len(unchecked) + len(travel) + len(displaced)
        if references > MAX_DIAGNOSTIC_REFERENCES:
            raise ValueError("Too many availability diagnostic references; narrow the window")
        reasons: list[Reason] = []
        if unchecked:
            reasons.append("coverage_unknown")
        if not request.routine_confirmed:
            reasons.append("routine_unconfirmed")
        if travel:
            reasons.append("travel_unknown")
        if displaced:
            reasons.append("movable_study")
            for identifier in displaced:
                if identifier not in warned_study:
                    warnings.append(AvailabilityWarning(code="movable_study", record_id=identifier))
                    warned_study.add(identifier)
                    references += 1
            if references > MAX_DIAGNOSTIC_REFERENCES:
                raise ValueError("Too many availability diagnostic references; narrow the window")
        opening = Opening(
            start_at=interval[0],
            end_at=interval[1],
            minutes=minutes,
            reasons=tuple(reasons),
            unchecked_source_ids=unchecked,
            unknown_travel_record_ids=travel,
            displaced_study_block_ids=displaced,
        )
        (provisional if reasons else confirmed).append(opening)
    unique_warnings = {warning.model_dump_json(): warning for warning in warnings}
    return AvailabilityResult(
        timezone=request.timezone,
        checked_at=request.now,
        status=(
            "needs_routine"
            if not request.routine_confirmed
            else "partial"
            if unique_warnings
            else "complete"
        ),
        confirmed_openings=tuple(confirmed),
        provisional_openings=tuple(provisional),
        busy=tuple(TimeInterval(start_at=first, end_at=last) for first, last in merged_busy),
        warnings=tuple(unique_warnings[key] for key in sorted(unique_warnings)),
    )
