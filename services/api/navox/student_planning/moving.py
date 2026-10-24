"""Review and move exactly one local study block; calendar events remain untouched."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
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
from navox.student_planning.authority import require_session
from navox.student_planning.planner import build_plan
from navox.student_planning.schemas import MoveInput, Preferences


async def move(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    block_id: UUID,
    payload: MoveInput,
    *,
    session_hash: str,
    expected_preview: str | None = None,
) -> dict[str, Any]:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    await require_session(database, user_id, session_hash)
    body = {
        "block_id": str(block_id),
        "input": payload.model_dump(mode="json"),
        "preview_hash": expected_preview,
    }
    if expected_preview:
        previous = await service.command(
            database, user_id, workspace_id, payload.request_id, "move", body
        )
        if previous:
            return previous.result
    now = utc(await service.database_now(database))
    planning = await service.state(database, user_id, workspace_id)
    await service.require_current_plan(database, user_id, workspace_id, planning)
    block = await schedule.get_owned(database, StudentStudyBlock, block_id, user_id, workspace_id)
    task = await service.get_task(database, user_id, workspace_id, block.task_id)
    if (
        block.status != "planned"
        or block.revision != payload.revision
        or utc(block.start_at) <= now
    ):
        raise schedule.StudentError(409, "Only an unchanged future study block can move")
    start, end = payload.start_at, payload.start_at + timedelta(minutes=block.minutes)
    if payload.window_end_at and end > payload.window_end_at:
        raise schedule.StudentError(422, "The block does not fit inside the selected free window")
    if payload.require_calendar and planning.preferences_revision < 1:
        raise schedule.StudentError(409, "Review your routine before using this free window")
    if start <= now or start > now + timedelta(days=14) or (task.due_at and end > utc(task.due_at)):
        raise schedule.StudentError(
            422, "Choose a future time within 14 days and before the deadline"
        )
    zone = ZoneInfo(user.timezone)
    day = start.astimezone(zone).date()
    left = datetime.combine(day, time.min, zone).astimezone(UTC)
    right = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    if end > right:
        raise schedule.StudentError(422, "Keep this study block within one local day")
    source = await schedule.state_for_window(database, user_id, workspace_id, left, right)
    classes, fixed, _, warnings = project(
        *source, start_at=left, end_at=right, include_conflicts=False
    )
    if warnings:
        raise schedule.StudentError(409, "Resolve timetable clock-change warnings before moving")
    prefs = Preferences.model_validate(planning.preferences)
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
    external = await calendar_busy.collect(
        database,
        user_id,
        workspace_id,
        start_at=left,
        end_at=right,
        travel_minutes=prefs.travel_minutes,
    )
    busy.extend(external.intervals)
    if payload.require_calendar and not external.connected:
        raise schedule.StudentError(
            409, "Connect your calendar before confirming this free-time suggestion"
        )
    wake = alarm_schedule(prefs, user.timezone, day, classes, days=1)[0]
    overrides = (
        {day: datetime.fromisoformat(wake["scheduled_at"]) if wake["scheduled_at"] else None}
        if wake["enabled"] and prefs.alarm.smart
        else {}
    )
    capacity = build_plan(
        [], prefs, user.timezone, start_at=left, end_at=right, busy=busy, wake_overrides=overrides
    )
    if capacity.warnings:
        raise schedule.StudentError(
            409, "Resolve routine clock-change warnings before moving study time"
        )
    others = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.id != block.id,
                StudentStudyBlock.end_at > left,
                StudentStudyBlock.start_at < right,
            )
            .order_by(StudentStudyBlock.id)
            .limit(601)
        )
    )
    if len(others) > 600:
        raise schedule.StudentError(409, "Too many blocks to review safely")
    allowance = sum(r.free_minutes - r.buffer_minutes for r in capacity.capacities)
    if sum(r.minutes for r in others) + block.minutes > allowance:
        raise schedule.StudentError(409, "That day has too little free time after its study buffer")
    deadline = min(right, utc(task.due_at)) if task.due_at else right
    remaining_capacity = build_plan(
        [],
        prefs,
        user.timezone,
        start_at=start,
        end_at=deadline,
        busy=busy,
        wake_overrides=overrides,
    )
    if remaining_capacity.warnings:
        raise schedule.StudentError(
            409, "Resolve routine clock-change warnings before moving study time"
        )
    remaining_allowance = sum(
        row.free_minutes - row.buffer_minutes for row in remaining_capacity.capacities
    )
    remaining_used = sum(
        max(
            0,
            int(
                (min(utc(row.end_at), deadline) - max(utc(row.start_at), start)).total_seconds()
                // 60
            ),
        )
        for row in others
    )
    if remaining_used + block.minutes > remaining_allowance:
        raise schedule.StudentError(
            409, "That move would use the 15% buffer before the deadline. Choose an earlier window"
        )
    rest = timedelta(minutes=prefs.break_minutes)
    busy.extend((utc(r.start_at) - rest, utc(r.end_at) + rest) for r in others)
    proposed = service.task_view(task).model_copy(update={"remaining_minutes": block.minutes})
    result = build_plan(
        [proposed],
        prefs,
        user.timezone,
        start_at=start,
        end_at=end,
        busy=busy,
        wake_overrides=overrides,
        reserve_buffer=False,
    )
    if (
        len(result.blocks) != 1
        or result.blocks[0].start_at != start
        or result.blocks[0].end_at != end
    ):
        raise schedule.StudentError(
            409, "That time overlaps sleep, travel, another event, or a protected break"
        )
    before = service.block_view(block).model_dump(mode="json")
    after = {
        **before,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "revision": block.revision + 1,
        "focus_window": result.blocks[0].focus_window,
    }
    fingerprint = service.digest(
        {
            "owner": [str(user_id), str(workspace_id)],
            "input": payload.model_dump(mode="json"),
            "plan_revision": planning.revision,
            "before": before,
            "after": after,
            "source": await service.stable_source_hash(database, user_id, workspace_id, planning),
            "calendar": external.digest,
            "other_blocks": [service.block_view(r).model_dump(mode="json") for r in others],
        }
    )
    response = {
        "preview_hash": fingerprint,
        "before": before,
        "after": after,
        "warnings": list(external.warnings),
        "other_blocks_change": False,
        "alarms_change": False,
        "calendar_events_change": False,
    }
    await require_session(database, user_id, session_hash)
    if expected_preview:
        if fingerprint != expected_preview:
            raise schedule.StudentError(409, "The schedule changed. Review the move again")
        block.start_at, block.end_at = start, end
        block.focus_window = result.blocks[0].focus_window
        block.revision += 1
        planning.revision += 1
        planning.window_start = (
            min(utc(planning.window_start), start) if planning.window_start else start
        )
        planning.window_end = max(utc(planning.window_end), end) if planning.window_end else end
        planning.accepted_source_hash = await service.stable_source_hash(
            database, user_id, workspace_id, planning
        )
        await service.remember(
            database, user_id, workspace_id, payload.request_id, "move", body, response
        )
        schedule.audit(database, user_id, workspace_id, "student.plan.block_moved", block.id)
        await database.commit()
    return response
