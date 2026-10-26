"""Exact, bounded local study moves reviewed with a proposed personal event.

Provider submission is owned by scheduling.py. These helpers never commit or
contact a provider. All database helpers run under that caller's User lock.
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.core.settings import Settings, get_settings
from navox.db.student_calendars import StudentCalendarEvent
from navox.db.student_planning import (
    StudentPlanningCommand,
    StudentPlanningState,
    StudentStudyBlock,
)
from navox.student import service as schedule
from navox.student.occurrences import project
from navox.student.schemas import Input
from navox.student_availability.engine import MEETING_BUFFER
from navox.student_availability.schemas import AvailabilitySnapshot, TimeInterval
from navox.student_calendars import claims
from navox.student_calendars.schemas import CalendarEventPreview
from navox.student_planning import service as planning
from navox.student_planning.alarm import alarm_schedule
from navox.student_planning.planner import Slot, available_slots
from navox.student_planning.schemas import BlockView, TaskView

MAX_MOVES = 24
MAX_ATTEMPTS = 512
INDEX_ACTION = "calendar.displacement"
MAX_INDEX = 2000


class StudyMove(Input):
    before: BlockView
    after: BlockView
    task_revision: int = Field(ge=1)
    due_at: datetime | None


class StudyReview(Input):
    v: Literal[1] = 1
    planning_revision: int = Field(ge=0)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    student_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    window_start: datetime
    window_end: datetime
    moves: list[StudyMove] = Field(min_length=1, max_length=MAX_MOVES)


@dataclass
class Context:
    state: StudentPlanningState
    blocks: list[BlockView]
    tasks: dict[UUID, TaskView]
    source_hash: str
    wakes: dict[date, datetime | None]
    pending_displacement: bool = False
    planning_enabled: bool = False


class MeetingInterval(Protocol):
    @property
    def start_at(self) -> datetime: ...

    @property
    def end_at(self) -> datetime: ...


def _overlaps(first: datetime, last: datetime, start: datetime, end: datetime) -> bool:
    return first < end and last > start


def fits(snapshot: AvailabilitySnapshot, draft: MeetingInterval) -> bool:
    """Only verified hard-free space may be exchanged for reviewed local study."""
    if (
        snapshot.result.status == "needs_routine"
        or any(row.code != "movable_study" for row in snapshot.result.warnings)
        or any(row.state != "complete" for row in snapshot.source_summary)
    ):
        return False
    return any(
        row.start_at <= draft.start_at - MEETING_BUFFER
        and row.end_at >= draft.end_at + MEETING_BUFFER
        and not row.unchecked_source_ids
        and not row.unknown_travel_record_ids
        and set(row.reasons) <= {"movable_study"}
        for row in (*snapshot.result.confirmed_openings, *snapshot.result.provisional_openings)
    )


async def load_context(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    snapshot: AvailabilitySnapshot,
    *,
    planning_enabled: bool = False,
) -> Context:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    state = await planning.state(database, user_id, workspace_id)
    await planning.require_current_plan(database, user_id, workspace_id, state)
    rest = timedelta(minutes=snapshot.input.preferences.break_minutes)
    blocks = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.start_at < snapshot.input.end_at + rest,
                StudentStudyBlock.end_at > snapshot.input.start_at - rest,
            )
            .order_by(StudentStudyBlock.start_at, StudentStudyBlock.id)
            .limit(601)
            .execution_options(populate_existing=True)
        )
    )
    if len(blocks) > 600:
        raise schedule.StudentError(409, "Narrow the review window before moving study work")
    task_views = await planning.tasks(database, user_id, workspace_id)
    eligible = await planning.eligible_tasks(database, user_id, workspace_id, task_views)
    tasks = {row.id: row for row in eligible if row.status == "active"}
    source = await schedule.state_for_window(
        database, user_id, workspace_id, snapshot.input.start_at, snapshot.input.end_at
    )
    classes, _, _, warnings = project(
        *source,
        start_at=snapshot.input.start_at,
        end_at=snapshot.input.end_at,
        include_conflicts=False,
    )
    if warnings:
        raise schedule.StudentError(409, "Resolve timetable clock changes before moving study work")
    day = snapshot.input.start_at.astimezone(ZoneInfo(user.timezone)).date()
    alarms = alarm_schedule(snapshot.input.preferences, user.timezone, day, classes, days=15)
    wakes = {
        date.fromisoformat(row["day"]): datetime.fromisoformat(row["scheduled_at"])
        if row["scheduled_at"]
        else None
        for row in alarms
        if row["enabled"] and snapshot.input.preferences.alarm.smart
    }
    return Context(
        state,
        [planning.block_view(row) for row in blocks],
        tasks,
        await planning.stable_source_hash(database, user_id, workspace_id, state),
        wakes,
        await claims.pending_displacement(database, user_id, workspace_id),
        planning_enabled,
    )


def _window(snapshot: AvailabilitySnapshot, now: datetime) -> tuple[datetime, datetime]:
    first = max(snapshot.input.start_at, now + timedelta(minutes=1))
    first = first.replace(second=0, microsecond=0) + timedelta(minutes=(5 - first.minute % 5) % 5)
    return first, min(snapshot.input.end_at, now + timedelta(days=14))


def _base_slots(
    snapshot: AvailabilitySnapshot,
    draft: MeetingInterval,
    context: Context,
    now: datetime,
) -> list[Slot]:
    first, last = _window(snapshot, now)
    busy = [(row.start_at, row.end_at) for row in snapshot.result.busy]
    busy.append((draft.start_at - MEETING_BUFFER, draft.end_at + MEETING_BUFFER))
    slots, warnings = available_slots(
        snapshot.input.preferences,
        snapshot.input.timezone,
        start_at=first,
        end_at=last,
        busy=busy,
        wake_overrides=context.wakes,
    )
    if warnings:
        raise schedule.StudentError(409, "Resolve routine clock changes before moving study work")
    return slots


def _future(context: Context, now: datetime) -> list[BlockView]:
    return [row for row in context.blocks if row.start_at > now]


def _affected(
    context: Context, snapshot: AvailabilitySnapshot, draft: MeetingInterval, now: datetime
) -> list[BlockView]:
    rest = timedelta(minutes=snapshot.input.preferences.break_minutes)
    first, last = draft.start_at - MEETING_BUFFER, draft.end_at + MEETING_BUFFER
    if any(
        row.start_at <= now and _overlaps(row.start_at - rest, row.end_at + rest, first, last)
        for row in context.blocks
    ):
        raise schedule.StudentError(
            409, "An affected study block has already started. Check availability again"
        )
    affected = [
        row
        for row in _future(context, now)
        if _overlaps(row.start_at - rest, row.end_at + rest, first, last)
    ]
    if len(affected) > MAX_MOVES:
        raise schedule.StudentError(
            409, "This meeting affects too much study work. Choose another time"
        )
    for row in affected:
        task = context.tasks.get(row.task_id)
        source = next(
            (item for item in snapshot.input.study_blocks if item.id == str(row.id)), None
        )
        if (
            task is None
            or source is None
            or not source.movable
            or source.calendar_backed
            or source.needs_review
            or row.actual_minutes != 0
            or row.outcome_at is not None
            or row.minutes > task.remaining_minutes
            or sum(block.minutes for block in _future(context, now) if block.task_id == row.task_id)
            > task.remaining_minutes
            or not 1 <= row.minutes <= snapshot.input.preferences.max_study_minutes
            or row.end_at - row.start_at != timedelta(minutes=row.minutes)
        ):
            raise schedule.StudentError(409, "An affected study block changed or cannot be moved")
    return affected


def _used(blocks: list[BlockView], first: datetime, last: datetime) -> int:
    return sum(
        max(0, int((min(row.end_at, last) - max(row.start_at, first)).total_seconds() // 60))
        for row in blocks
    )


def _budget_ok(
    slots: list[Slot], blocks: list[BlockView], context: Context, snapshot: AvailabilitySnapshot
) -> bool:
    zone = ZoneInfo(snapshot.input.timezone)
    days = {row.day for row in slots}
    for day in days:
        first = max(
            datetime.combine(day, time.min, zone).astimezone(UTC),
            snapshot.input.start_at,
            snapshot.checked_at,
        )
        last = min(
            datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC),
            snapshot.input.end_at,
        )
        free = sum(
            int((row.end - row.start).total_seconds() // 60) for row in slots if row.day == day
        )
        if _used(blocks, first, last) > free - math.ceil(free * 0.15):
            return False
    first = min((row.start for row in slots), default=snapshot.input.end_at)
    deadlines = {
        task.due_at
        for task in context.tasks.values()
        if task.due_at is not None and first < task.due_at <= snapshot.input.end_at
    }
    for deadline in deadlines:
        free = sum(
            max(0, int((min(row.end, deadline) - max(row.start, first)).total_seconds() // 60))
            for row in slots
        )
        if _used(blocks, first, deadline) > free - math.ceil(free * 0.15):
            return False
    for row in blocks:
        task = context.tasks.get(row.task_id)
        if task is None or (task.due_at is not None and row.end_at > task.due_at):
            return False
    return True


def _assignment_valid(
    slots: list[Slot],
    context: Context,
    snapshot: AvailabilitySnapshot,
    moves: list[StudyMove],
    now: datetime,
    *,
    ignored: set[UUID] | None = None,
) -> bool:
    changed = ignored if ignored is not None else {move.before.id for move in moves}
    untouched = [row for row in _future(context, now) if row.id not in changed]
    after = [move.after for move in moves]
    rest = timedelta(minutes=snapshot.input.preferences.break_minutes)
    first, last = _window(snapshot, now)
    for row in untouched:
        start, end = max(row.start_at, first), min(row.end_at, last)
        if start < end and not any(slot.start <= start and end <= slot.end for slot in slots):
            return False
    for row in after:
        if (
            row.start_at <= now
            or not any(slot.start <= row.start_at and row.end_at <= slot.end for slot in slots)
            or any(
                other.id != row.id
                and _overlaps(row.start_at, row.end_at, other.start_at - rest, other.end_at + rest)
                for other in [*untouched, *after]
            )
        ):
            return False
    return _budget_ok(slots, [*untouched, *after], context, snapshot)


async def prepare(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    draft: CalendarEventPreview,
    snapshot: AvailabilitySnapshot,
    now: datetime,
    *,
    settings: Settings,
) -> StudyReview | None:
    if not fits(snapshot, draft):
        raise schedule.StudentError(409, "That meeting does not fit confirmed calendar coverage")
    # A routine without a saved study plan has nothing to displace.
    if not snapshot.input.study_blocks:
        return None
    context = await load_context(
        database,
        user_id,
        workspace_id,
        snapshot,
        planning_enabled=settings.student_planning_enabled,
    )
    return certify(context, snapshot, draft.start_at, draft.end_at, now)


def certify(
    context: Context,
    snapshot: AvailabilitySnapshot,
    start_at: datetime,
    end_at: datetime,
    now: datetime,
) -> StudyReview | None:
    """Pure candidate certification using one freshly loaded owned Context.

    Callers must preserve that context's owner/source/session fences around
    publication. None means the candidate requires no actual study move; a 409
    rejects an infeasible/unknown candidate. The returned moves identify only
    exact buffered intersections, never every ID in a provisional opening.
    """
    draft = TimeInterval(start_at=start_at, end_at=end_at)
    if (
        context.state.user_id != snapshot.input.user_id
        or context.state.workspace_id != snapshot.input.workspace_id
        or context.state.revision != snapshot.revision.planning_revision
        or not snapshot.checked_at <= now < snapshot.expires_at
        or not fits(snapshot, draft)
    ):
        raise schedule.StudentError(409, "The candidate does not match this owned planning context")
    affected = _affected(context, snapshot, draft, now)
    if not affected:
        return None
    if not context.planning_enabled:
        raise schedule.StudentError(404, "Study changes are not enabled")
    if context.pending_displacement:
        raise schedule.StudentError(
            409, "A calendar request is still being checked before study can move"
        )
    slots = _base_slots(snapshot, draft, context, now)
    changed = {row.id for row in affected}
    rest = timedelta(minutes=snapshot.input.preferences.break_minutes)
    occupied = [row for row in _future(context, now) if row.id not in changed]
    moves: list[StudyMove] = []
    attempts = 0
    for row in sorted(
        affected,
        key=lambda item: (
            context.tasks[item.task_id].due_at or snapshot.input.end_at,
            -item.minutes,
            str(item.id),
        ),
    ):
        task = context.tasks[row.task_id]
        chosen = None
        for slot in sorted(
            slots,
            key=lambda item: (not item.focus if task.difficulty >= 4 else item.focus, item.start),
        ):
            start = slot.start
            while start + timedelta(minutes=row.minutes) <= slot.end:
                attempts += 1
                if attempts > MAX_ATTEMPTS:
                    raise schedule.StudentError(
                        409, "A complete replacement plan could not be bounded safely"
                    )
                end = start + timedelta(minutes=row.minutes)
                clashes = [
                    other
                    for other in occupied
                    if _overlaps(start, end, other.start_at - rest, other.end_at + rest)
                ]
                if clashes:
                    start = max(other.end_at + rest for other in clashes)
                    continue
                if task.due_at and end > task.due_at:
                    break
                after = row.model_copy(
                    update={
                        "start_at": start,
                        "end_at": end,
                        "focus_window": slot.focus,
                        "revision": row.revision + 1,
                    }
                )
                candidate = StudyMove(
                    before=row, after=after, task_revision=task.revision, due_at=task.due_at
                )
                if _assignment_valid(
                    slots, context, snapshot, [*moves, candidate], now, ignored=changed
                ):
                    chosen = candidate
                    break
                start += timedelta(minutes=5)
            if chosen is not None:
                break
        if chosen is None:
            raise schedule.StudentError(
                409,
                "The displaced study work cannot fit before its deadlines with protected "
                "breaks and buffers. Choose another time",
            )
        moves.append(chosen)
        occupied.append(chosen.after)
    if not _assignment_valid(slots, context, snapshot, moves, now):
        raise schedule.StudentError(
            409, "A complete feasible replacement plan could not be confirmed"
        )
    return StudyReview(
        planning_revision=context.state.revision,
        source_hash=context.source_hash,
        student_revision=snapshot.revision.student_revision,
        window_start=snapshot.input.start_at,
        window_end=snapshot.input.end_at,
        moves=moves,
    )


async def validate(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    draft: CalendarEventPreview,
    snapshot: AvailabilitySnapshot,
    review: StudyReview,
    now: datetime,
    *,
    settings: Settings,
) -> None:
    if not settings.student_planning_enabled:
        raise schedule.StudentError(404, "Study changes are not enabled")
    context = await load_context(database, user_id, workspace_id, snapshot, planning_enabled=True)
    await claims.require_mutable(database, user_id, workspace_id)
    if (
        not fits(snapshot, draft)
        or context.state.revision != review.planning_revision
        or context.source_hash != review.source_hash
        or snapshot.revision.student_revision != review.student_revision
        or snapshot.input.start_at != review.window_start
        or snapshot.input.end_at != review.window_end
        or {row.id for row in _affected(context, snapshot, draft, now)}
        != {move.before.id for move in review.moves}
    ):
        raise schedule.StudentError(409, "The affected study plan changed. Review the event again")
    current = {row.id: row for row in context.blocks}
    for move in review.moves:
        task = context.tasks.get(move.before.task_id)
        if (
            current.get(move.before.id) != move.before
            or task is None
            or task.revision != move.task_revision
            or task.due_at != move.due_at
            or move.before.start_at <= now
            or move.before.minutes != move.after.minutes
            or move.after.id != move.before.id
            or move.after.task_id != move.before.task_id
            or move.after.revision != move.before.revision + 1
            or move.after.end_at - move.after.start_at != timedelta(minutes=move.before.minutes)
        ):
            raise schedule.StudentError(
                409, "An affected study block changed. Review the event again"
            )
    if not _assignment_valid(
        _base_slots(snapshot, draft, context, now), context, snapshot, review.moves, now
    ):
        raise schedule.StudentError(409, "The reviewed replacement times are no longer feasible")


async def apply(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    review: StudyReview,
    event_id: UUID,
    now: datetime,
    *,
    settings: Settings,
) -> StudentPlanningState:
    """Caller validates, seals the executing event, flushes, then sets source hash."""
    if not settings.student_planning_enabled:
        raise schedule.StudentError(404, "Study changes are not enabled")
    state = await planning.state(database, user_id, workspace_id)
    if state.revision != review.planning_revision:
        raise schedule.StudentError(409, "The study plan changed before event admission")
    index = await database.scalar(
        select(StudentPlanningCommand).where(
            StudentPlanningCommand.user_id == user_id,
            StudentPlanningCommand.workspace_id == workspace_id,
            StudentPlanningCommand.request_id == event_id,
            StudentPlanningCommand.action == INDEX_ACTION,
        )
    )
    if (
        index is None
        or index.payload_hash != planning.digest(review.model_dump(mode="json"))
        or index.result.get("applied_revision") is not None
        or index.result.get("redacted") is True
    ):
        raise schedule.StudentError(409, "This study review changed or was already submitted")
    for move in review.moves:
        row = await schedule.get_owned(
            database, StudentStudyBlock, move.before.id, user_id, workspace_id
        )
        if planning.block_view(row) != move.before:
            raise schedule.StudentError(409, "A study block changed before event admission")
        row.start_at, row.end_at = move.after.start_at, move.after.end_at
        row.focus_window, row.revision = move.after.focus_window, move.after.revision
    state.revision += 1
    state.window_start = (
        min(utc(state.window_start), *(move.after.start_at for move in review.moves))
        if state.window_start
        else min(move.after.start_at for move in review.moves)
    )
    state.window_end = (
        max(utc(state.window_end), *(move.after.end_at for move in review.moves))
        if state.window_end
        else max(move.after.end_at for move in review.moves)
    )
    index.result = {**index.result, "applied_revision": state.revision}
    schedule.audit(
        database, user_id, workspace_id, "student.calendar.study_moves_applied", event_id
    )
    return state


async def index_review(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    event_id: UUID,
    review: StudyReview,
    now: datetime,
) -> None:
    """Index every private review from creation, including unsent reviews.

    These content-free indices are not seven-day retry snapshots. They remain
    until source erasure/reset, so deletion never misses an old private copy.
    Admission shares the existing 2,000 command bound; no unbounded scan is used.
    """
    await schedule.count_limit(database, StudentPlanningCommand, user_id, workspace_id, MAX_INDEX)
    database.add(
        StudentPlanningCommand(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=event_id,
            action=INDEX_ACTION,
            payload_hash=planning.digest(review.model_dump(mode="json")),
            result={
                "event_id": str(event_id),
                "task_ids": sorted({str(move.before.task_id) for move in review.moves}),
                "applied_revision": None,
            },
            undo_snapshot=None,
            created_at=now,
        )
    )


async def redact(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    task_ids: set[UUID] | None = None,
    settings: Settings | None = None,
) -> None:
    """Erase matching derived study details without rewriting external intent.

    Caller holds User. Pending matching reviews become permanently failed;
    submitted operations retain only their independent external event proof and
    any unrelated study details. No erased move can be applied again.
    """
    from navox.student_calendars.scheduling import _open, _seal

    indices = list(
        await database.scalars(
            select(StudentPlanningCommand)
            .where(
                StudentPlanningCommand.user_id == user_id,
                StudentPlanningCommand.workspace_id == workspace_id,
                StudentPlanningCommand.action == INDEX_ACTION,
            )
            .order_by(StudentPlanningCommand.id)
            .limit(MAX_INDEX + 1)
        )
    )
    if len(indices) > MAX_INDEX:
        raise schedule.StudentError(503, "The saved study review index could not be bounded safely")
    targets = {str(value) for value in task_ids} if task_ids is not None else None
    for index in indices:
        ids = index.result.get("task_ids")
        if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
            raise schedule.StudentError(503, "A saved study review could not be safely forgotten")
    if not indices:
        return
    rows = {
        row.id: row
        for row in await database.scalars(
            select(StudentCalendarEvent).where(
                StudentCalendarEvent.user_id == user_id,
                StudentCalendarEvent.workspace_id == workspace_id,
                StudentCalendarEvent.id.in_([index.request_id for index in indices]),
            )
        )
    }
    configured = settings or get_settings()
    for index in indices:
        row = rows.get(index.request_id)
        if row is None:
            index.result = {**index.result, "task_ids": [], "redacted": True}
            continue
        payload = _open(row, configured)
        value = payload.get("study_displacement")
        if not isinstance(value, dict) or value.get("v") != 1:
            raise schedule.StudentError(503, "A saved study review could not be safely forgotten")
        try:
            moves = [StudyMove.model_validate(move) for move in value["review"]["moves"]]
        except (ValueError, TypeError, KeyError):
            raise schedule.StudentError(
                503, "A saved study review could not be safely forgotten"
            ) from None
        if not any(targets is None or str(move.before.task_id) in targets for move in moves):
            continue
        retained = [
            move
            for move in moves
            if targets is not None and str(move.before.task_id) not in targets
        ]
        value["review"]["moves"] = [move.model_dump(mode="json") for move in retained]
        value["redacted"] = True
        index.result = {
            **index.result,
            "task_ids": sorted({str(move.before.task_id) for move in retained}),
            "redacted": True,
        }
        if row.status == "pending":
            row.status = "failed"
        row.revision += 1
        row.encrypted_payload = _seal(row, payload, configured)
