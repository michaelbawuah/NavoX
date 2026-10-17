from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.api.auth import (
    CurrentAccount,
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
)
from navox.core.settings import Settings
from navox.db.models import User
from navox.db.routines import PersonalRoutine, RoutinePreview, RoutineRun
from navox.routines.activities import RoutineTickInput, prune_runs
from navox.routines.dispatcher import RoutineDispatcher, TemporalRoutineDispatcher
from navox.routines.service import (
    RoutineConfig,
    aware,
    config_hash,
    next_run,
    owner_is_active,
    preview_result,
)

router = APIRouter(prefix="/routines", tags=["personal routines"])


def get_routine_dispatcher() -> RoutineDispatcher:
    return TemporalRoutineDispatcher()


DispatcherDependency = Annotated[RoutineDispatcher, Depends(get_routine_dispatcher)]


class RoutineWrite(RoutineConfig):
    enabled: bool = False
    preview_token: UUID | None = None


class RoutineResponse(RoutineConfig):
    id: UUID
    enabled: bool
    schedule_status: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_result: dict[str, object] | None
    created_at: datetime

    @field_validator("next_run_at", "last_run_at", "created_at")
    @classmethod
    def utc_datetime(cls, value: datetime | None) -> datetime | None:
        return aware(value).astimezone(UTC) if value is not None else None


class RoutineList(BaseModel):
    routines: list[RoutineResponse]
    paused: bool


class PreviewResponse(BaseModel):
    preview_token: UUID
    expires_at: datetime
    result: dict[str, object]

    @field_validator("expires_at")
    @classmethod
    def utc_datetime(cls, value: datetime) -> datetime:
        return aware(value).astimezone(UTC)


class RunResponse(BaseModel):
    id: UUID
    scheduled_for: datetime
    checked_at: datetime
    status: str
    result: dict[str, object] | None
    notice: str

    @field_validator("scheduled_for", "checked_at")
    @classmethod
    def utc_datetime(cls, value: datetime) -> datetime:
        return aware(value).astimezone(UTC)


class RunList(BaseModel):
    runs: list[RunResponse]


def response(routine: PersonalRoutine) -> RoutineResponse:
    return RoutineResponse.model_validate(
        dict(
            id=routine.id,
            name=routine.name,
            recipe=routine.recipe,
            timezone=routine.timezone,
            hour=routine.hour,
            minute=routine.minute,
            days_of_week=routine.days_of_week,
            enabled=routine.enabled,
            schedule_status=routine.schedule_status,
            next_run_at=routine.next_run_at,
            last_run_at=routine.last_run_at,
            last_result=routine.last_result,
            created_at=routine.created_at,
        )
    )


async def require_owner(database: AsyncSession, account: CurrentAccount) -> None:
    if await owner_is_active(database, account.user.id, account.workspace.id) is None:
        raise HTTPException(
            status_code=403, detail="Routines require your personal owner workspace"
        )


async def owned_routine(
    database: AsyncSession, account: CurrentAccount, routine_id: UUID, *, lock: bool = False
) -> PersonalRoutine:
    query = select(PersonalRoutine).where(
        PersonalRoutine.id == routine_id,
        PersonalRoutine.user_id == account.user.id,
        PersonalRoutine.workspace_id == account.workspace.id,
    )
    routine = await database.scalar(query.with_for_update() if lock else query)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")
    return routine


@router.get("", response_model=RoutineList)
async def list_routines(
    current_account: CurrentAccountDependency, database: DatabaseSession
) -> RoutineList:
    await require_owner(database, current_account)
    routines = await database.scalars(
        select(PersonalRoutine)
        .where(
            PersonalRoutine.user_id == current_account.user.id,
            PersonalRoutine.workspace_id == current_account.workspace.id,
        )
        .order_by(PersonalRoutine.created_at, PersonalRoutine.id)
        .limit(50)
    )
    return RoutineList(
        routines=[response(routine) for routine in routines],
        paused=current_account.user.agent_paused,
    )


@router.post("/preview", response_model=PreviewResponse)
async def preview_routine(
    payload: RoutineConfig, current_account: CurrentAccountDependency, database: DatabaseSession
) -> PreviewResponse:
    await require_owner(database, current_account)
    await database.scalar(select(User).where(User.id == current_account.user.id).with_for_update())
    now = datetime.now(UTC)
    result = await preview_result(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        config=payload,
        now=now,
    )
    # The receipt proves this exact configuration was previewed. The preview
    # content is not persisted, and never refreshes providers or calls a model.
    await database.execute(
        delete(RoutinePreview).where(
            RoutinePreview.user_id == current_account.user.id,
            RoutinePreview.workspace_id == current_account.workspace.id,
            RoutinePreview.expires_at <= now,
        )
    )
    receipt = RoutinePreview(
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        config_hash=config_hash(payload),
        expires_at=now + timedelta(minutes=30),
    )
    database.add(receipt)
    await database.flush()
    keep = list(
        await database.scalars(
            select(RoutinePreview.id)
            .where(
                RoutinePreview.user_id == current_account.user.id,
                RoutinePreview.workspace_id == current_account.workspace.id,
            )
            .order_by(RoutinePreview.created_at.desc(), RoutinePreview.id.desc())
            .limit(20)
        )
    )
    # Ensure this receipt survives when SQLite timestamps have one-second precision.
    keep = list(dict.fromkeys([receipt.id, *keep]))[:20]
    await database.execute(
        delete(RoutinePreview).where(
            RoutinePreview.user_id == current_account.user.id,
            RoutinePreview.workspace_id == current_account.workspace.id,
            RoutinePreview.id.not_in(keep),
        )
    )
    await database.commit()
    return PreviewResponse(preview_token=receipt.id, expires_at=receipt.expires_at, result=result)


async def consume_preview(
    database: AsyncSession, account: CurrentAccount, payload: RoutineWrite
) -> None:
    receipt = (
        await database.scalar(
            select(RoutinePreview)
            .where(
                RoutinePreview.id == payload.preview_token,
                RoutinePreview.user_id == account.user.id,
                RoutinePreview.workspace_id == account.workspace.id,
            )
            .with_for_update()
        )
        if payload.preview_token
        else None
    )
    config = RoutineConfig(**payload.model_dump(include=set(RoutineConfig.model_fields)))
    if (
        receipt is None
        or aware(receipt.expires_at) <= datetime.now(UTC)
        or receipt.config_hash != config_hash(config)
    ):
        raise HTTPException(status_code=409, detail="Preview this exact routine before enabling it")
    await database.delete(receipt)


async def save_routine(
    database: AsyncSession,
    account: CurrentAccount,
    payload: RoutineWrite,
    settings: Settings,
    dispatcher: RoutineDispatcher,
    routine: PersonalRoutine | None = None,
) -> RoutineResponse:
    # Serialize per-owner writes, including the five-enabled-routine cap.
    owner = await database.scalar(
        select(User)
        .where(User.id == account.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payload.enabled:
        if owner is None or owner.agent_paused:
            raise HTTPException(status_code=409, detail="Resume NavoX before enabling a routine")
        await consume_preview(database, account, payload)
        enabled_count = await database.scalar(
            select(func.count())
            .select_from(PersonalRoutine)
            .where(
                PersonalRoutine.user_id == account.user.id,
                PersonalRoutine.workspace_id == account.workspace.id,
                PersonalRoutine.enabled.is_(True),
                PersonalRoutine.id != routine.id if routine else true(),
            )
        )
        if (enabled_count or 0) >= 5:
            raise HTTPException(
                status_code=409, detail="Pause a routine before enabling another; the limit is five"
            )
    config = RoutineConfig(**payload.model_dump(include=set(RoutineConfig.model_fields)))
    now = datetime.now(UTC)
    if routine is None:
        total = await database.scalar(
            select(func.count())
            .select_from(PersonalRoutine)
            .where(
                PersonalRoutine.user_id == account.user.id,
                PersonalRoutine.workspace_id == account.workspace.id,
            )
        )
        if (total or 0) >= 20:
            raise HTTPException(
                status_code=409,
                detail="Delete a routine before saving another; the limit is twenty",
            )
        routine = PersonalRoutine(
            user_id=account.user.id, workspace_id=account.workspace.id, revision=0
        )
        database.add(routine)
    else:
        previous_config = RoutineConfig.model_validate(
            {
                "name": routine.name,
                "recipe": routine.recipe,
                "timezone": routine.timezone,
                "hour": routine.hour,
                "minute": routine.minute,
                "days_of_week": routine.days_of_week,
            }
        )
        if config_hash(previous_config) != config_hash(config):
            # A previous recipe's result is kept in history, but must not appear
            # to be a result of the newly edited configuration.
            routine.last_result = None
            routine.last_run_at = None
    for key, value in config.model_dump().items():
        setattr(routine, key, value)
    routine.revision += 1
    routine.enabled = payload.enabled
    routine.schedule_status = "starting" if payload.enabled else "paused"
    routine.next_run_at = next_run(config, now) if payload.enabled else None
    routine.dispatch_failure_count = 0
    routine.next_dispatch_check_at = now if payload.enabled else None
    await database.flush()
    add_audit_event(
        database,
        user_id=account.user.id,
        workspace_id=account.workspace.id,
        event_type="routine.updated",
        entity_type="personal_routine",
        entity_id=routine.id,
        metadata={"enabled": payload.enabled, "recipe": payload.recipe},
        actor_type="user",
        actor_id=str(account.user.id),
    )
    await database.commit()
    if payload.enabled:
        dispatched_revision = routine.revision
        dispatch_failed = False
        try:
            await dispatcher.start(
                RoutineTickInput(
                    str(routine.id),
                    str(account.user.id),
                    str(account.workspace.id),
                    routine.revision,
                ),
                settings,
            )
        except Exception:
            dispatch_failed = True
        # A pause or edit during network dispatch must win over this older
        # response. Reload under a row lock before changing schedule state.
        current = await database.scalar(
            select(PersonalRoutine)
            .where(
                PersonalRoutine.id == routine.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None:
            raise HTTPException(status_code=409, detail="Routine was deleted while scheduling")
        routine = current
        if routine.revision == dispatched_revision and routine.enabled:
            if dispatch_failed:
                routine.enabled = False
                routine.schedule_status = "dispatch_failed"
                routine.next_run_at = None
                routine.next_dispatch_check_at = None
                routine.dispatch_failure_count = 1
                database.add(
                    RoutineRun(
                        routine_id=routine.id,
                        revision=routine.revision,
                        scheduled_for=now,
                        checked_at=now,
                        status="failed",
                        result=None,
                        notice="Scheduling could not be confirmed. Preview and enable to retry.",
                    )
                )
                await database.flush()
                await prune_runs(database, routine.id)
            else:
                routine.schedule_status = "scheduled"
                routine.next_dispatch_check_at = datetime.now(UTC) + timedelta(minutes=5)
        await database.commit()
    return response(routine)


@router.post("", response_model=RoutineResponse, status_code=status.HTTP_201_CREATED)
async def create_routine(
    payload: RoutineWrite,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: DispatcherDependency,
) -> RoutineResponse:
    await require_owner(database, current_account)
    return await save_routine(database, current_account, payload, settings, dispatcher)


@router.put("/{routine_id}", response_model=RoutineResponse)
async def update_routine(
    routine_id: UUID,
    payload: RoutineWrite,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: DispatcherDependency,
) -> RoutineResponse:
    await require_owner(database, current_account)
    await database.scalar(select(User).where(User.id == current_account.user.id).with_for_update())
    routine = await owned_routine(database, current_account, routine_id, lock=True)
    return await save_routine(database, current_account, payload, settings, dispatcher, routine)


@router.delete("/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routine(
    routine_id: UUID, current_account: CurrentAccountDependency, database: DatabaseSession
) -> Response:
    await require_owner(database, current_account)
    routine = await owned_routine(database, current_account, routine_id, lock=True)
    await database.delete(routine)
    await database.commit()
    return Response(status_code=204)


@router.get("/{routine_id}/runs", response_model=RunList)
async def routine_runs(
    routine_id: UUID, current_account: CurrentAccountDependency, database: DatabaseSession
) -> RunList:
    await require_owner(database, current_account)
    await owned_routine(database, current_account, routine_id)
    runs = await database.scalars(
        select(RoutineRun)
        .where(RoutineRun.routine_id == routine_id)
        .order_by(RoutineRun.checked_at.desc(), RoutineRun.id.desc())
        .limit(20)
    )
    return RunList(
        runs=[
            RunResponse(
                id=run.id,
                scheduled_for=run.scheduled_for,
                checked_at=run.checked_at,
                status=run.status,
                result=run.result,
                notice=run.notice,
            )
            for run in runs
        ]
    )
