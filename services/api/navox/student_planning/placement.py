"""Explicitly place unallocated task effort without replacing another study block."""

from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.db.student_planning import StudentPlanningState, StudentStudyBlock
from navox.student import service as schedule
from navox.student.occurrences import project
from navox.student.schemas import Input
from navox.student_planning import calendar_busy, service
from navox.student_planning.alarm import alarm_schedule
from navox.student_planning.authority import require_session
from navox.student_planning.planner import build_plan
from navox.student_planning.schemas import Preferences, aware


class PlacementInput(Input):
    request_id: UUID
    task_id: UUID
    task_revision: int = Field(ge=1)
    timezone: str = Field(min_length=1, max_length=100)
    start_at: datetime
    window_end_at: datetime
    minutes: int = Field(ge=1, le=90, strict=True)

    _aware = field_validator("start_at", "window_end_at")(aware)


class PlacementConfirm(PlacementInput):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    recovery_acknowledged: bool = Field(default=False, strict=True)

    @field_validator("confirmed", mode="before")
    @classmethod
    def consent(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit approval is required")
        return value


async def place(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: PlacementInput,
    *,
    session_hash: str,
    expected_preview: str | None = None,
    acknowledge_recovery: bool = False,
) -> dict[str, Any]:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    await require_session(database, user_id, session_hash)
    body = {
        "input": payload.model_dump(mode="json"),
        "preview_hash": expected_preview,
        "recovery_acknowledged": acknowledge_recovery,
    }
    if expected_preview:
        previous = await service.command(
            database, user_id, workspace_id, payload.request_id, "place", body
        )
        if previous:
            return previous.result
    now = utc(await service.database_now(database))
    planning = await service.state(database, user_id, workspace_id)
    if payload.timezone != user.timezone or planning.preferences_revision < 1:
        raise schedule.StudentError(
            409, "Review your saved timezone and routine before placing study time"
        )
    prefs = Preferences.model_validate(planning.preferences)
    views = await service.tasks(database, user_id, workspace_id)
    eligible = await service.eligible_tasks(database, user_id, workspace_id, views)
    reviewable = await service.eligible_tasks(
        database, user_id, workspace_id, views, include_stale_email=True
    )
    source_states = await service.current_sources(
        database, user_id, workspace_id, [row for row in reviewable if row.status == "active"]
    )
    if any(source["needs_review"] is True for source in source_states):
        raise schedule.StudentError(
            409,
            "A saved task source changed or is unavailable. "
            "Review its task source or archive it before planning",
        )
    task = next((row for row in eligible if row.id == payload.task_id), None)
    if task is None or task.status != "active" or task.revision != payload.task_revision:
        raise schedule.StudentError(409, "This task changed or its source needs review")
    blocks = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status.in_(["planned", "needs_review"]),
            )
            .order_by(StudentStudyBlock.id)
            .limit(601)
        )
    )
    if len(blocks) >= 600:
        raise schedule.StudentError(409, "Review the existing blocks before adding more study time")
    if any(utc(row.end_at) > now for row in blocks):
        await service.require_current_plan(database, user_id, workspace_id, planning)
    recovery_required = planning.needs_review or bool(
        planning.accepted_source_hash
        and planning.accepted_source_hash
        != await service.stable_source_hash(database, user_id, workspace_id, planning)
    )
    unallocated = task.remaining_minutes - sum(
        row.minutes for row in blocks if row.task_id == task.id
    )
    if not min(15, unallocated) <= payload.minutes <= min(unallocated, prefs.max_study_minutes):
        raise schedule.StudentError(
            409,
            "Choose unplanned task time within your study-block limit, or move an existing block",
        )
    start = payload.start_at
    end = start + timedelta(minutes=payload.minutes)
    if (
        start <= now
        or start > now + timedelta(days=14)
        or end > payload.window_end_at
        or (task.due_at and end > utc(task.due_at))
    ):
        raise schedule.StudentError(
            422, "Choose future study time inside the selected window and before the deadline"
        )
    zone = ZoneInfo(user.timezone)
    day = start.astimezone(zone).date()
    left = datetime.combine(day, time.min, zone).astimezone(UTC)
    right = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    if end > right or payload.window_end_at > right:
        raise schedule.StudentError(422, "Keep the selected window within one local day")
    source = await schedule.state_for_window(database, user_id, workspace_id, left, right)
    classes, fixed, _, warnings = project(
        *source, start_at=left, end_at=right, include_conflicts=False
    )
    if warnings:
        raise schedule.StudentError(
            409, "Resolve timetable clock-change warnings before placing study time"
        )
    busy = [
        (
            row.start_at
            - timedelta(
                minutes=row.walk_minutes if row.walk_minutes is not None else prefs.travel_minutes
            ),
            row.end_at
            + timedelta(
                minutes=row.walk_minutes if row.walk_minutes is not None else prefs.travel_minutes
            ),
        )
        for row in classes
        if not row.cancelled
    ]
    busy.extend(
        (
            row.start_at - timedelta(minutes=prefs.travel_minutes),
            row.end_at + timedelta(minutes=prefs.travel_minutes),
        )
        for row in fixed
    )
    external = await calendar_busy.collect(
        database,
        user_id,
        workspace_id,
        start_at=left,
        end_at=right,
        travel_minutes=prefs.travel_minutes,
    )
    if not external.connected:
        raise schedule.StudentError(
            409, "Connect your calendar before confirming this free-time suggestion"
        )
    busy.extend(external.intervals)
    wake = alarm_schedule(prefs, user.timezone, day, classes, days=1)[0]
    overrides = (
        {day: datetime.fromisoformat(wake["scheduled_at"]) if wake["scheduled_at"] else None}
        if wake["enabled"] and prefs.alarm.smart
        else {}
    )
    day_blocks = [row for row in blocks if utc(row.start_at) < right and utc(row.end_at) > left]
    # Keep the day's reserve and a reserve in the remaining time before this
    # task's deadline. A late placement cannot borrow its buffer from yesterday
    # or from hours after the deadline.
    budgets = []
    for begin, finish in (
        (left, right),
        (start, min(right, utc(task.due_at)) if task.due_at else right),
    ):
        capacity = build_plan(
            [],
            prefs,
            user.timezone,
            start_at=begin,
            end_at=finish,
            busy=busy,
            wake_overrides=overrides,
        )
        if capacity.warnings:
            raise schedule.StudentError(
                409, "Review the routine clock-change warnings before placing study time"
            )
        allowance = sum(row.free_minutes - row.buffer_minutes for row in capacity.capacities)
        used = sum(
            max(
                0,
                int(
                    (min(utc(row.end_at), finish) - max(utc(row.start_at), begin)).total_seconds()
                    // 60
                ),
            )
            for row in day_blocks
        )
        budgets.append(allowance - used)
    if payload.minutes > min(budgets):
        raise schedule.StudentError(
            409,
            "That placement would use the 15% study buffer. Choose fewer minutes or another window",
        )
    rest = timedelta(minutes=prefs.break_minutes)
    occupied = [*busy, *((utc(row.start_at) - rest, utc(row.end_at) + rest) for row in day_blocks)]
    proposal = build_plan(
        [task.model_copy(update={"remaining_minutes": payload.minutes})],
        prefs,
        user.timezone,
        start_at=start,
        end_at=end,
        busy=occupied,
        wake_overrides=overrides,
        reserve_buffer=False,
    )
    if (
        len(proposal.blocks) != 1
        or proposal.blocks[0].start_at != start
        or proposal.blocks[0].end_at != end
    ):
        raise schedule.StudentError(
            409,
            "That time overlaps a class, shift, calendar event, sleep, travel or a protected break",
        )
    candidate = proposal.blocks[0]
    identifier = uuid5(
        NAMESPACE_URL, f"navox:placement:{workspace_id}:{user_id}:{payload.request_id}"
    )
    after = {
        **candidate.model_dump(mode="json"),
        "id": str(identifier),
        "plan_id": str(identifier),
        "revision": 1,
        "status": "planned",
        "actual_minutes": 0,
        "outcome_at": None,
    }
    source_state = StudentPlanningState(
        preferences=planning.preferences,
        window_start=min(utc(planning.window_start), left) if planning.window_start else left,
        window_end=max(utc(planning.window_end), right) if planning.window_end else right,
    )
    source_hash = await service.stable_source_hash(database, user_id, workspace_id, source_state)
    unplanned_tasks = []
    for row in eligible:
        remaining = row.remaining_minutes - sum(
            block.minutes for block in blocks if block.task_id == row.id
        )
        if row.id == task.id:
            remaining -= payload.minutes
        if row.status == "active" and remaining > 0:
            unplanned_tasks.append(
                {
                    "task_id": str(row.id),
                    "title": row.title,
                    "minutes": remaining,
                    "due_at": row.due_at.isoformat() if row.due_at else None,
                }
            )
    preserved_blocks = [
        service.block_view(row).model_dump(mode="json") for row in blocks if utc(row.end_at) > now
    ]
    fingerprint = service.digest(
        {
            "owner": [str(user_id), str(workspace_id)],
            "input": payload.model_dump(mode="json"),
            "plan_revision": planning.revision,
            "source": source_hash,
            "calendar": external.digest,
            "existing_blocks": [service.block_view(row).model_dump(mode="json") for row in blocks],
            "after": after,
            "budgets": budgets,
            "recovery_required": recovery_required,
            "unplanned_tasks": unplanned_tasks,
            "preserved_blocks": preserved_blocks,
        }
    )
    response = {
        "preview_hash": fingerprint,
        "after": after,
        "task": task.model_dump(mode="json"),
        "remaining_unplanned_minutes": unallocated - payload.minutes,
        "warnings": [*external.warnings, *proposal.warnings],
        "other_blocks_change": False,
        "alarms_change": False,
        "calendar_events_change": False,
        "recovery_required": recovery_required,
        "unplanned_tasks": unplanned_tasks,
        "preserved_blocks": preserved_blocks,
    }
    await require_session(database, user_id, session_hash)
    if expected_preview:
        if fingerprint != expected_preview:
            raise schedule.StudentError(
                409, "The task or schedule changed. Review this placement again"
            )
        if recovery_required and not acknowledge_recovery:
            raise schedule.StudentError(
                422, "Acknowledge the remaining unplanned work before starting this reviewed plan"
            )
        database.add(
            StudentStudyBlock(
                id=identifier,
                plan_id=identifier,
                user_id=user_id,
                workspace_id=workspace_id,
                **candidate.model_dump(),
                status="planned",
                actual_minutes=0,
                revision=1,
            )
        )
        planning.revision += 1
        planning.window_start, planning.window_end = (
            source_state.window_start,
            source_state.window_end,
        )
        planning.needs_review = False
        planning.accepted_source_hash = source_hash
        await service.remember(
            database, user_id, workspace_id, payload.request_id, "place", body, response
        )
        schedule.audit(database, user_id, workspace_id, "student.plan.block_placed", identifier)
        await database.commit()
    return response
