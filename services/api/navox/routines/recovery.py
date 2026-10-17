"""Recover the committed routine opt-in after an interrupted workflow dispatch."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.core.settings import Settings
from navox.db.routines import PersonalRoutine, RoutineRun
from navox.routines.activities import RoutineTickInput, lock_routine_owner, prune_runs
from navox.routines.dispatcher import RoutineDispatcher
from navox.routines.service import aware

MAX_DISPATCH_FAILURES = 3
MAX_RECONCILIATION_BATCH = 100


async def pending_routines(database: AsyncSession, now: datetime) -> list[RoutineTickInput]:
    rows = await database.scalars(
        select(PersonalRoutine)
        .where(
            PersonalRoutine.enabled.is_(True),
            or_(
                PersonalRoutine.next_dispatch_check_at.is_(None),
                PersonalRoutine.next_dispatch_check_at <= aware(now),
            ),
        )
        .order_by(PersonalRoutine.next_dispatch_check_at.asc().nulls_first(), PersonalRoutine.id)
        .limit(MAX_RECONCILIATION_BATCH)
    )
    return [
        RoutineTickInput(str(row.id), str(row.user_id), str(row.workspace_id), row.revision)
        for row in rows
    ]


async def record_dispatch(
    database: AsyncSession, payload: RoutineTickInput, *, confirmed: bool, now: datetime
) -> str:
    from uuid import UUID

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
        .execution_options(populate_existing=True)
    )
    if routine is None or not routine.enabled or routine.revision != payload.revision:
        return "superseded"
    if confirmed:
        routine.schedule_status = "scheduled"
        routine.dispatch_failure_count = 0
        routine.next_dispatch_check_at = now + timedelta(minutes=5)
        state = "scheduled"
    else:
        routine.dispatch_failure_count += 1
        if routine.dispatch_failure_count >= MAX_DISPATCH_FAILURES:
            routine.enabled = False
            routine.schedule_status = "dispatch_failed"
            routine.next_run_at = None
            routine.next_dispatch_check_at = None
            # Use the dispatch attempt time, not the due slot. A scheduling
            # failure must not consume the idempotency slot of a real check.
            failure_time = now
            for _ in range(21):
                existing = await database.scalar(
                    select(RoutineRun.id).where(
                        RoutineRun.routine_id == routine.id,
                        RoutineRun.revision == routine.revision,
                        RoutineRun.scheduled_for == failure_time,
                    )
                )
                if existing is None:
                    break
                failure_time += timedelta(microseconds=1)
            database.add(
                RoutineRun(
                    routine_id=routine.id,
                    revision=routine.revision,
                    scheduled_for=failure_time,
                    checked_at=now,
                    status="failed",
                    result=None,
                    notice=(
                        "Scheduling was unconfirmed after three attempts. "
                        "Preview and enable to retry."
                    ),
                )
            )
            await database.flush()
            await prune_runs(database, routine.id)
            state = "disabled"
        else:
            routine.schedule_status = "starting"
            routine.next_dispatch_check_at = now + timedelta(minutes=1)
            state = "retrying"
    await database.commit()
    return state


async def reconcile_routines(
    factory: async_sessionmaker[AsyncSession],
    *,
    dispatcher: RoutineDispatcher,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    current = aware(now or datetime.now(UTC))
    async with factory() as database:
        pending = await pending_routines(database, current)
    # No database transaction or row lock is held across Temporal RPCs. Bound
    # concurrency and batch size so an outage cannot monopolize the worker.
    semaphore = asyncio.Semaphore(8)

    async def recover(payload: RoutineTickInput) -> None:
        async with semaphore:
            try:
                await dispatcher.start(payload, settings)
            except Exception:
                confirmed = False
            else:
                confirmed = True
            async with factory() as database:
                await record_dispatch(database, payload, confirmed=confirmed, now=current)

    results = await asyncio.gather(
        *(recover(payload) for payload in pending), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return len(pending)
