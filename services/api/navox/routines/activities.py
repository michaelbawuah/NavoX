from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from navox.db.models import User
from navox.db.routines import PersonalRoutine, RoutineRun
from navox.db.session import get_session_factory
from navox.routines.service import (
    RoutineConfig,
    aware,
    next_run,
    owner_is_active,
    preview_result,
    quiet_now,
)


@dataclass(frozen=True)
class RoutineTickInput:
    routine_id: str
    user_id: str
    workspace_id: str
    revision: int


@dataclass(frozen=True)
class RoutineTickResult:
    state: str
    delay_seconds: int


async def lock_routine_owner(database: AsyncSession, payload: RoutineTickInput) -> None:
    # Shared with connector deletion and routine edits: User must precede
    # Routine. Deletion removes result rows before it updates the Routine row,
    # so holding only Routine would let a new private result escape that delete.
    await database.scalar(
        select(User)
        .where(User.id == UUID(payload.user_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def prune_runs(database: AsyncSession, routine_id: UUID) -> None:
    keep = list(
        await database.scalars(
            select(RoutineRun.id)
            .where(
                RoutineRun.routine_id == routine_id,
            )
            .order_by(RoutineRun.checked_at.desc(), RoutineRun.id.desc())
            .limit(20)
        )
    )
    if keep:
        await database.execute(
            delete(RoutineRun).where(
                RoutineRun.routine_id == routine_id,
                RoutineRun.id.not_in(keep),
            )
        )


async def tick_routine(
    database: AsyncSession, payload: RoutineTickInput, now: datetime
) -> RoutineTickResult:
    now = aware(now).astimezone(UTC)
    await lock_routine_owner(database, payload)
    routine = await database.scalar(
        select(PersonalRoutine)
        .where(
            PersonalRoutine.id == UUID(payload.routine_id),
            PersonalRoutine.user_id == UUID(payload.user_id),
            PersonalRoutine.workspace_id == UUID(payload.workspace_id),
        )
        .with_for_update()
    )
    if routine is None or not routine.enabled or routine.revision != payload.revision:
        return RoutineTickResult("terminal", 0)
    owner = await owner_is_active(database, routine.user_id, routine.workspace_id)
    if owner is None:
        routine.enabled = False
        routine.schedule_status = "blocked"
        routine.next_run_at = None
        await database.commit()
        return RoutineTickResult("terminal", 0)
    if routine.next_run_at is None:
        routine.enabled = False
        routine.schedule_status = "dispatch_failed"
        await database.commit()
        return RoutineTickResult("terminal", 0)
    slot = aware(routine.next_run_at)
    if slot > now:
        return RoutineTickResult("waiting", max(1, min(60, int((slot - now).total_seconds()))))
    config = RoutineConfig.model_validate(
        {
            "name": routine.name,
            "recipe": routine.recipe,
            "timezone": routine.timezone,
            "hour": routine.hour,
            "minute": routine.minute,
            "days_of_week": routine.days_of_week,
        }
    )
    existing = await database.scalar(
        select(RoutineRun).where(
            RoutineRun.routine_id == routine.id,
            RoutineRun.revision == routine.revision,
            RoutineRun.scheduled_for == slot,
        )
    )
    if existing is None:
        result = None
        status = "completed"
        notice = "Saved-data check completed. No messages were sent."
        if owner.agent_paused:
            status, notice = "skipped", "Skipped because NavoX was paused."
        elif await quiet_now(database, owner, routine.workspace_id, now):
            status, notice = "skipped", "Skipped during your quiet hours."
        else:
            try:
                result = await preview_result(
                    database,
                    user_id=routine.user_id,
                    workspace_id=routine.workspace_id,
                    config=config,
                    now=now,
                )
            except (ValueError, TypeError):
                status, notice = (
                    "failed",
                    "Saved-data check could not be completed. Try a preview before enabling again.",
                )
        database.add(
            RoutineRun(
                routine_id=routine.id,
                revision=routine.revision,
                scheduled_for=slot,
                checked_at=now,
                status=status,
                result=result,
                notice=notice,
            )
        )
        routine.last_run_at = now
        if result is not None:
            routine.last_result = result
        await database.flush()
        await prune_runs(database, routine.id)
    # No catch-up storm after downtime. The saved slot is processed once, then
    # move to the first future local occurrence, rather than replaying old days.
    routine.next_run_at = next_run(config, now)
    routine.schedule_status = "scheduled"
    await database.commit()
    return RoutineTickResult("waiting", 60)


@activity.defn
async def routine_tick_activity(payload: RoutineTickInput) -> RoutineTickResult:
    async with get_session_factory()() as database:
        return await tick_routine(database, payload, datetime.now(UTC))


@activity.defn
async def routine_failure_activity(payload: RoutineTickInput) -> str:
    """Persist exhausted activity retries without disabling a newer revision."""
    async with get_session_factory()() as database:
        await lock_routine_owner(database, payload)
        routine = await database.scalar(
            select(PersonalRoutine)
            .where(
                PersonalRoutine.id == UUID(payload.routine_id),
                PersonalRoutine.user_id == UUID(payload.user_id),
                PersonalRoutine.workspace_id == UUID(payload.workspace_id),
                PersonalRoutine.revision == payload.revision,
            )
            .with_for_update()
        )
        if routine is None or not routine.enabled:
            return "stopped"
        now = datetime.now(UTC)
        slot = routine.next_run_at or now
        existing = await database.scalar(
            select(RoutineRun).where(
                RoutineRun.routine_id == routine.id,
                RoutineRun.revision == routine.revision,
                RoutineRun.scheduled_for == slot,
            )
        )
        if existing is None:
            database.add(
                RoutineRun(
                    routine_id=routine.id,
                    revision=routine.revision,
                    scheduled_for=slot,
                    checked_at=now,
                    status="failed",
                    result=None,
                    notice="Background check failed after retries. Preview and enable to retry.",
                )
            )
            await database.flush()
            await prune_runs(database, routine.id)
        routine.enabled = False
        routine.schedule_status = "failed"
        routine.next_run_at = None
        await database.commit()
        return "failed"


@activity.defn
async def reconcile_routines_activity() -> int:
    from navox.core.settings import get_settings
    from navox.routines.dispatcher import TemporalRoutineDispatcher
    from navox.routines.recovery import reconcile_routines

    return await reconcile_routines(
        get_session_factory(),
        dispatcher=TemporalRoutineDispatcher(),
        settings=get_settings(),
    )
