"""Manual goals and explicit self reports; no provider, inference or scheduled actions."""

import hashlib
import hmac
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.student_goals import (
    StudentGoal,
    StudentGoalCheckIn,
    StudentGoalCommand,
    StudentGoalState,
)
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.student import service as schedule
from navox.student.schemas import valid_zone
from navox.student_goals.schemas import (
    BACKFILL_DAYS,
    MAX_ACTIVE,
    MAX_CHECKINS,
    MAX_COMMANDS,
    MAX_FENCES,
    MAX_GOALS,
    CheckInInput,
    CheckInView,
    EraseInput,
    GoalCreate,
    GoalDefinition,
    GoalsView,
    GoalUpdate,
    GoalView,
    PlannerMetrics,
    ProgressView,
    ResetInput,
    Status,
)

DELETIONS = ("delete_goal", "delete_checkin", "reset")
# Goal text has at most 1,200 Unicode characters; check-ins have fewer.
# canonical() can escape each as 12 ASCII bytes. With under 2 KiB of schema
# metadata/binding, Fernet padding and base64 keep valid tokens below 24 KiB.
# Leave bounded headroom without changing existing canonical bytes or retry keys.
MAX_ENCRYPTED_PAYLOAD_BYTES = 32 * 1024


def fail(message: str, status: int = 409) -> schedule.StudentError:
    return schedule.StudentError(status, message)


def owned(model: Any, user_id: UUID, workspace_id: UUID) -> tuple[Any, Any]:
    return model.user_id == user_id, model.workspace_id == workspace_id


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def binding(row: StudentGoal | StudentGoalCheckIn) -> dict[str, object]:
    result: dict[str, object] = {
        "version": 1,
        "purpose": "student_goal" if isinstance(row, StudentGoal) else "student_goal_checkin",
        "id": str(row.id),
        "user_id": str(row.user_id),
        "workspace_id": str(row.workspace_id),
        "revision": row.definition_revision if isinstance(row, StudentGoal) else row.revision,
    }
    if isinstance(row, StudentGoalCheckIn):
        result.update(
            goal_id=str(row.goal_id),
            on_date=row.on_date.isoformat(),
            recorded_at=utc(row.recorded_at).isoformat(),
        )
    return result


def seal(row: StudentGoal | StudentGoalCheckIn, value: dict[str, Any], settings: Settings) -> str:
    try:
        return cipher(settings).encrypt(canonical({**binding(row), "payload": value})).decode()
    except ConnectorRuntimeError:
        raise fail("Private goal encryption is not configured", 503) from None


def reveal(row: StudentGoal | StudentGoalCheckIn, settings: Settings) -> dict[str, Any]:
    try:
        if len(row.encrypted_payload) > MAX_ENCRYPTED_PAYLOAD_BYTES:
            raise ValueError
        value = json.loads(cipher(settings).decrypt(row.encrypted_payload.encode()))
        if not isinstance(value, dict) or {
            k: v for k, v in value.items() if k != "payload"
        } != binding(row):
            raise ValueError
        if not isinstance(value["payload"], dict):
            raise ValueError
        return cast(dict[str, Any], value["payload"])
    except (InvalidToken, ConnectorRuntimeError, ValueError, KeyError, TypeError, UnicodeError):
        raise fail("This private goal item cannot be decrypted", 503) from None


async def state(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> StudentGoalState:
    row = await database.scalar(
        select(StudentGoalState)
        .where(*owned(StudentGoalState, user_id, workspace_id))
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = StudentGoalState(
            id=uuid4(), user_id=user_id, workspace_id=workspace_id, revision=0, generation=0
        )
        database.add(row)
        await database.flush()
    return row


async def goal(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, goal_id: UUID
) -> StudentGoal:
    row = await database.scalar(
        select(StudentGoal)
        .where(StudentGoal.id == goal_id, *owned(StudentGoal, user_id, workspace_id))
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise fail("Goal was not found", 404)
    return row


def zone(user_timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(valid_zone(user_timezone))
    except ValueError:
        raise fail("Review your account time zone before using goals") from None


def fingerprint(settings: Settings | None, value: object) -> str:
    if settings is None:
        return hashlib.sha256(canonical(value)).hexdigest()
    try:
        cipher(settings)
        secret = next(
            key.get_secret_value()
            for key in (
                settings.connector_secret_encryption_key,
                settings.google_token_encryption_key,
            )
            if key is not None and key.get_secret_value()
        )
    except (ConnectorRuntimeError, StopIteration):
        raise fail("Private goal encryption is not configured", 503) from None
    key = hmac.digest(secret.encode(), b"navox.student-goals.retry.v1", "sha256")
    return hmac.new(key, canonical(value), hashlib.sha256).hexdigest()


async def command(
    database: AsyncSession,
    settings: Settings | None,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    operation: str,
    target: UUID | None,
    payload: dict[str, Any],
) -> tuple[StudentGoalCommand | None, str]:
    row = await database.scalar(
        select(StudentGoalCommand)
        .where(
            *owned(StudentGoalCommand, user_id, workspace_id),
            StudentGoalCommand.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is not None and row.status == "erased":
        raise fail("This request belongs to erased goal data. Use a new reviewed request", 410)
    digest = fingerprint(
        settings,
        {
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "operation": operation,
            "target": str(target) if target else None,
            "payload": payload,
        },
    )
    if row is not None:
        if row.operation != operation or not hmac.compare_digest(row.payload_hash, digest):
            raise fail("This request ID was already used with different goal details")
        return row, digest
    count = await database.scalar(
        select(func.count())
        .select_from(StudentGoalCommand)
        .where(*owned(StudentGoalCommand, user_id, workspace_id))
    )
    maximum = MAX_FENCES if operation in DELETIONS else MAX_COMMANDS
    if (count or 0) >= maximum:
        raise fail("The private goal request limit was reached. Erasure remains available")
    return None, digest


def remember(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    operation: str,
    digest: str,
    entity_id: UUID | None,
    goal_id: UUID | None,
    revision: int,
) -> None:
    database.add(
        StudentGoalCommand(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            operation=operation,
            payload_hash=digest,
            entity_id=entity_id,
            goal_id=goal_id,
            applied_revision=revision,
            status="applied",
        )
    )


async def entries(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, goal_id: UUID | None = None
) -> list[StudentGoalCheckIn]:
    query = select(StudentGoalCheckIn).where(*owned(StudentGoalCheckIn, user_id, workspace_id))
    if goal_id is not None:
        query = query.where(StudentGoalCheckIn.goal_id == goal_id)
    rows = list(
        await database.scalars(
            query.order_by(StudentGoalCheckIn.on_date.desc())
            .limit(MAX_CHECKINS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) > MAX_CHECKINS:
        raise fail("Review the saved goal history before continuing")
    return rows


def entry_view(row: StudentGoalCheckIn, settings: Settings) -> CheckInView:
    return CheckInView(
        id=row.id,
        goal_id=row.goal_id,
        revision=row.revision,
        on_date=row.on_date,
        recorded_at=utc(row.recorded_at),
        **reveal(row, settings),
    )


def period(definition: GoalDefinition, today: date) -> tuple[date, date]:
    if definition.cadence == "daily":
        return today, today
    if definition.cadence == "weekly":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    return definition.starts_on, definition.ends_on or max(today, definition.starts_on)


def view(
    row: StudentGoal,
    rows: list[StudentGoalCheckIn],
    settings: Settings,
    timezone: str,
    now: datetime,
) -> GoalView:
    definition = GoalDefinition.model_validate(reveal(row, settings))
    reports = [entry_view(item, settings) for item in rows if item.goal_id == row.id]
    reports.sort(key=lambda item: item.on_date, reverse=True)
    start, end = period(definition, now.astimezone(zone(timezone)).date())
    current = [item for item in reports if start <= item.on_date <= end]
    total = (
        sum(item.count or 0 for item in current)
        if current and definition.tracking == "count"
        else None
    )
    latest = current[0] if current else None
    progress = ProgressView(
        timezone=timezone,
        period_start=start,
        period_end=end,
        days_reported=len(current),
        reported_count=total,
        target=definition.target,
        remaining_count=max(0, definition.target - total)
        if total is not None and definition.target is not None
        else None,
        target_met_from_reports=total >= definition.target
        if total is not None and definition.target is not None
        else None,
        qualitative_latest=("yes" if latest.completed else "no")
        if latest and definition.tracking == "qualitative"
        else "unknown",
        latest_reported_on=latest.on_date if latest else None,
    )
    return GoalView(
        **definition.model_dump(),
        id=row.id,
        revision=row.revision,
        status=cast(Status, row.status),
        progress=progress,
        recent_checkins=reports[:14],
        tracking_definition_locked=bool(reports),
    )


async def planner_metrics(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, timezone: str, now: datetime
) -> PlannerMetrics:
    tz = zone(timezone)
    today = now.astimezone(tz).date()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=7)
    # outcome_at is the recorded event time. Task edits are not completion evidence.
    rows = list(
        await database.execute(
            select(
                StudentStudyBlock.status, func.count(), func.sum(StudentStudyBlock.actual_minutes)
            )
            .join(StudentTask, StudentTask.id == StudentStudyBlock.task_id)
            .where(
                *owned(StudentStudyBlock, user_id, workspace_id),
                *owned(StudentTask, user_id, workspace_id),
                StudentStudyBlock.outcome_at
                >= datetime.combine(start, time.min, tz).astimezone(UTC),
                StudentStudyBlock.outcome_at < datetime.combine(end, time.min, tz).astimezone(UTC),
                StudentStudyBlock.outcome_at <= now,
                StudentStudyBlock.status.in_(["done", "took_longer", "skipped"]),
            )
            .group_by(StudentStudyBlock.status)
        )
    )
    return PlannerMetrics(
        timezone=timezone,
        period_start=start,
        period_end=end - timedelta(days=1),
        completed_blocks=sum(count for status, count, _ in rows if status == "done"),
        skipped_blocks=sum(count for status, count, _ in rows if status == "skipped"),
        recorded_minutes=sum(minutes or 0 for _, _, minutes in rows),
    )


async def board(
    database: AsyncSession, settings: Settings, user_id: UUID, workspace_id: UUID
) -> GoalsView:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    zone(user.timezone)
    now = utc(await database_now(database))
    rows = list(
        await database.scalars(
            select(StudentGoal)
            .where(*owned(StudentGoal, user_id, workspace_id))
            .order_by(StudentGoal.created_at, StudentGoal.id)
            .limit(MAX_GOALS + 1)
        )
    )
    if len(rows) > MAX_GOALS:
        raise fail("Review the saved goal limit before continuing")
    reports = await entries(database, user_id, workspace_id)
    owner_state = await database.scalar(
        select(StudentGoalState)
        .where(*owned(StudentGoalState, user_id, workspace_id))
        .execution_options(populate_existing=True)
    )
    return GoalsView(
        revision=owner_state.revision if owner_state else 0,
        generation=owner_state.generation if owner_state else 0,
        server_now=now,
        timezone=user.timezone,
        goals=[view(row, reports, settings, user.timezone, now) for row in rows],
        planner_metrics=await planner_metrics(database, user_id, workspace_id, user.timezone, now),
        notices=[
            "Goal progress comes only from your explicit self-reports.",
            "Counts describe one reported local day; replacing a day does not add another count.",
            "Weekly periods start Monday. Reported dates keep the time zone "
            "recorded when you saved them.",
            "Planner outcomes are separate records; attendance, sleep and grades are not observed.",
        ],
    )


async def save_goal(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    payload: GoalCreate | GoalUpdate,
    goal_id: UUID | None = None,
) -> GoalView:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    now = utc(await database_now(database))
    operation = "create_goal" if goal_id is None else "update_goal"
    previous, digest = await command(
        database,
        settings,
        user_id,
        workspace_id,
        payload.request_id,
        operation,
        goal_id,
        payload.model_dump(mode="json"),
    )
    if previous:
        row = await goal(database, user_id, workspace_id, cast(UUID, previous.entity_id))
        if row.revision != previous.applied_revision:
            raise fail("This request already succeeded and the goal has since changed. Refresh")
        return view(
            row,
            await entries(database, user_id, workspace_id, row.id),
            settings,
            user.timezone,
            now,
        )
    definition = GoalDefinition.model_validate(
        {
            key: value
            for key, value in payload.model_dump().items()
            if key in GoalDefinition.model_fields
        }
    )
    if goal_id is None:
        owner_state = await state(database, user_id, workspace_id)
        if not isinstance(payload, GoalCreate) or payload.generation != owner_state.generation:
            raise fail("Your goals were reset. Review a fresh goals view before creating a goal")
        await schedule.count_limit(database, StudentGoal, user_id, workspace_id, MAX_GOALS)
        row = StudentGoal(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            status="active",
            revision=1,
            definition_revision=1,
        )
        database.add(row)
    else:
        row = await goal(database, user_id, workspace_id, goal_id)
        if not isinstance(payload, GoalUpdate) or row.revision != payload.revision:
            raise fail("This goal changed. Refresh before saving")
        reports = await entries(database, user_id, workspace_id, row.id)
        if reports:
            old = GoalDefinition.model_validate(reveal(row, settings))
            measurement = ("tracking", "cadence", "target", "unit", "starts_on", "ends_on")
            if any(getattr(old, key) != getattr(definition, key) for key in measurement):
                raise fail(
                    "Create a new goal to change tracking after check-ins; "
                    "existing reports keep their meaning"
                )
        row.status = payload.status
        row.revision += 1
        row.definition_revision += 1
    if row.status == "active":
        with database.no_autoflush:
            active = await database.scalar(
                select(func.count())
                .select_from(StudentGoal)
                .where(
                    *owned(StudentGoal, user_id, workspace_id),
                    StudentGoal.status == "active",
                    StudentGoal.id != row.id,
                )
            )
        if (active or 0) >= MAX_ACTIVE:
            raise fail("Keep at most three active goals. Pause or finish one before adding another")
    row.encrypted_payload = seal(row, definition.model_dump(mode="json"), settings)
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1

    remember(
        database,
        user_id,
        workspace_id,
        payload.request_id,
        operation,
        digest,
        row.id,
        row.id,
        row.revision,
    )
    await database.flush()
    response = view(
        row, await entries(database, user_id, workspace_id, row.id), settings, user.timezone, now
    )
    await database.commit()
    return response


async def checkin_on(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    goal_id: UUID,
    on_date: date,
) -> CheckInView | None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    await goal(database, user_id, workspace_id, goal_id)
    row = await database.scalar(
        select(StudentGoalCheckIn).where(
            *owned(StudentGoalCheckIn, user_id, workspace_id),
            StudentGoalCheckIn.goal_id == goal_id,
            StudentGoalCheckIn.on_date == on_date,
        )
    )
    return entry_view(row, settings) if row else None


async def save_checkin(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    goal_id: UUID,
    payload: CheckInInput,
) -> CheckInView:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    now = utc(await database_now(database))
    previous, digest = await command(
        database,
        settings,
        user_id,
        workspace_id,
        payload.request_id,
        "checkin",
        goal_id,
        payload.model_dump(mode="json"),
    )
    row_goal = await goal(database, user_id, workspace_id, goal_id)
    if previous:
        previous_row = await database.get(StudentGoalCheckIn, previous.entity_id)
        if (
            previous_row is None
            or previous_row.goal_id != row_goal.id
            or (previous_row.user_id, previous_row.workspace_id) != (user_id, workspace_id)
        ):
            raise fail("This check-in was erased", 410)
        if previous_row.revision != previous.applied_revision:
            raise fail("This check-in already succeeded and has since changed. Refresh")
        return entry_view(previous_row, settings)
    if row_goal.revision != payload.goal_revision or row_goal.status != "active":
        raise fail("This goal changed or is not active. Refresh before reporting")
    if payload.expected_timezone != user.timezone:
        raise fail("Your account time zone changed. Review the date before reporting")
    today = now.astimezone(zone(user.timezone)).date()
    day = payload.on_date or today
    definition = GoalDefinition.model_validate(reveal(row_goal, settings))
    if not today - timedelta(days=BACKFILL_DAYS) <= day <= today:
        raise fail("Report today or a local date in the previous 31 days", 422)
    if day < definition.starts_on or (definition.ends_on and day > definition.ends_on):
        raise fail("Choose a date within this goal's period", 422)
    if (definition.tracking == "count") != (payload.count is not None):
        raise fail("Use the goal's saved count or yes/no tracking", 422)
    row = await database.scalar(
        select(StudentGoalCheckIn)
        .where(
            *owned(StudentGoalCheckIn, user_id, workspace_id),
            StudentGoalCheckIn.goal_id == goal_id,
            StudentGoalCheckIn.on_date == day,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        if payload.revision != 0:
            raise fail("This check-in changed. Refresh before reporting")
        await schedule.count_limit(
            database, StudentGoalCheckIn, user_id, workspace_id, MAX_CHECKINS
        )
        row = StudentGoalCheckIn(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            goal_id=goal_id,
            on_date=day,
            revision=1,
            recorded_at=now,
        )
        database.add(row)
    else:
        if row.revision != payload.revision:
            raise fail(
                "This day already has a report. Review its current version before replacing it"
            )
        row.revision += 1
        row.recorded_at = now
    row.encrypted_payload = seal(
        row,
        {
            "reported_timezone": user.timezone,
            "count": payload.count,
            "completed": payload.completed,
            "note": payload.note,
            "goal_revision_when_reported": row_goal.revision,
        },
        settings,
    )
    row_goal.revision += 1
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    remember(
        database,
        user_id,
        workspace_id,
        payload.request_id,
        "checkin",
        digest,
        row.id,
        goal_id,
        row.revision,
    )
    await database.flush()
    response = entry_view(row, settings)
    await database.commit()
    return response


async def invalidate(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    goal_id: UUID | None = None,
    entity_id: UUID | None = None,
) -> None:
    query = update(StudentGoalCommand).where(
        *owned(StudentGoalCommand, user_id, workspace_id),
        StudentGoalCommand.operation.not_in(DELETIONS),
    )
    if goal_id is not None:
        query = query.where(StudentGoalCommand.goal_id == goal_id)
    if entity_id is not None:
        query = query.where(StudentGoalCommand.entity_id == entity_id)
    await database.execute(query.values(status="erased"))


async def erase_goal(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, goal_id: UUID, payload: EraseInput
) -> None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    previous, digest = await command(
        database,
        None,
        user_id,
        workspace_id,
        payload.request_id,
        "delete_goal",
        goal_id,
        payload.model_dump(mode="json"),
    )
    if previous:
        return
    row = await goal(database, user_id, workspace_id, goal_id)
    if row.revision != payload.revision:
        raise fail("This goal or its check-ins changed. Refresh before erasing")
    await invalidate(database, user_id, workspace_id, goal_id=goal_id)
    await database.execute(
        delete(StudentGoalCheckIn).where(
            *owned(StudentGoalCheckIn, user_id, workspace_id), StudentGoalCheckIn.goal_id == goal_id
        )
    )
    await database.delete(row)
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    remember(
        database,
        user_id,
        workspace_id,
        payload.request_id,
        "delete_goal",
        digest,
        goal_id,
        goal_id,
        row.revision,
    )
    await database.commit()


async def erase_checkin(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    goal_id: UUID,
    checkin_id: UUID,
    payload: EraseInput,
) -> None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    previous, digest = await command(
        database,
        None,
        user_id,
        workspace_id,
        payload.request_id,
        "delete_checkin",
        checkin_id,
        {"goal_id": str(goal_id), **payload.model_dump(mode="json")},
    )
    if previous:
        return
    row_goal = await goal(database, user_id, workspace_id, goal_id)
    row = await database.scalar(
        select(StudentGoalCheckIn)
        .where(
            *owned(StudentGoalCheckIn, user_id, workspace_id),
            StudentGoalCheckIn.goal_id == goal_id,
            StudentGoalCheckIn.id == checkin_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise fail("Check-in was not found", 404)
    if row.revision != payload.revision:
        raise fail("This check-in changed. Refresh before erasing")
    await invalidate(database, user_id, workspace_id, entity_id=checkin_id)
    await database.delete(row)
    row_goal.revision += 1
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    remember(
        database,
        user_id,
        workspace_id,
        payload.request_id,
        "delete_checkin",
        digest,
        checkin_id,
        goal_id,
        payload.revision,
    )
    await database.commit()


async def forget_goals_data(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Full-reset hook: caller holds fresh shared User lock and owns the transaction.

    Opaque command fences survive erasure; erased requests can never recreate data.
    This helper neither decrypts data nor grants memory/provider/automation access.
    """
    await invalidate(database, user_id, workspace_id)
    await database.execute(
        delete(StudentGoalCheckIn).where(*owned(StudentGoalCheckIn, user_id, workspace_id))
    )
    await database.execute(delete(StudentGoal).where(*owned(StudentGoal, user_id, workspace_id)))
    owner_state = await state(database, user_id, workspace_id)
    owner_state.revision += 1
    owner_state.generation += 1


async def reset(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: ResetInput
) -> None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    previous, digest = await command(
        database,
        None,
        user_id,
        workspace_id,
        payload.request_id,
        "reset",
        None,
        payload.model_dump(mode="json"),
    )
    if previous:
        return
    owner_state = await state(database, user_id, workspace_id)
    if owner_state.revision != payload.revision:
        raise fail("Your goals changed. Refresh before erasing all goal data")
    await forget_goals_data(database, user_id, workspace_id)
    remember(
        database,
        user_id,
        workspace_id,
        payload.request_id,
        "reset",
        digest,
        None,
        None,
        owner_state.revision,
    )
    await database.commit()
