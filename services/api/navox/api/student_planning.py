"""Disabled-by-default, explicitly accepted study plans. Never writes a calendar."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.student import result, scope
from navox.student import service as schedule
from navox.student_planning import service
from navox.student_planning.schemas import (
    AcceptInput,
    OutcomeConfirmInput,
    OutcomeInput,
    OutcomeView,
    PlanView,
    PreferencesUpdate,
    PreferencesView,
    PreviewInput,
    PreviewView,
    TaskCreate,
    TaskImport,
    TaskUpdate,
    TaskView,
    UndoInput,
)


def enabled(request: Request, settings: SettingsDependency) -> None:
    # Explicit owner erasure remains available during a feature rollback. The
    # sole DELETE route still checks Origin, ownership, revision and request ID.
    if request.method == "DELETE":
        return
    if not getattr(settings, "student_planning_enabled", False):
        raise HTTPException(404, "Study planning is not enabled")


router = APIRouter(
    prefix="/student/planning", tags=["student planning"], dependencies=[Depends(enabled)]
)


@router.get("/tasks")
async def list_tasks(
    account: CurrentAccountDependency, database: DatabaseSession
) -> dict[str, list[TaskView]]:
    await result(schedule.owner(database, *scope(account)))
    return {
        "tasks": await result(service.tasks(database, *scope(account), include_source_review=True))
    }


@router.post("/tasks", response_model=TaskView, status_code=201)
async def create_task(
    payload: TaskCreate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> TaskView:
    require_origin(request, settings.web_origin)
    return await result(service.save_task(database, *scope(account), payload))


@router.post("/tasks/from-assignment", response_model=TaskView, status_code=201)
async def import_task(
    payload: TaskImport,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> TaskView:
    require_origin(request, settings.web_origin)
    return await result(service.save_task(database, *scope(account), payload))


@router.patch("/tasks/{task_id}", response_model=TaskView)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> TaskView:
    require_origin(request, settings.web_origin)
    return await result(service.save_task(database, *scope(account), payload, task_id))


@router.delete("/tasks/{task_id}", status_code=204)
async def forget_task(
    task_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    revision: Annotated[int, Query(ge=1)],
    request_id: UUID,
) -> Response:
    require_origin(request, settings.web_origin)
    await result(
        service.remove_task(
            database, *scope(account), task_id, revision=revision, request_id=request_id
        )
    )
    return Response(status_code=204)


@router.get("/preferences", response_model=PreferencesView)
async def get_preferences(
    account: CurrentAccountDependency, database: DatabaseSession
) -> PreferencesView:
    return await result(service.preferences(database, *scope(account)))


@router.put("/preferences", response_model=PreferencesView)
async def put_preferences(
    payload: PreferencesUpdate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> PreferencesView:
    require_origin(request, settings.web_origin)
    return await result(service.save_preferences(database, *scope(account), payload))


@router.post("/preview", response_model=PreviewView)
async def preview_plan(
    payload: PreviewInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> PreviewView:
    require_origin(request, settings.web_origin)
    return await result(service.preview(database, *scope(account), payload))


@router.post("/accept", response_model=PlanView)
async def accept_plan(
    payload: AcceptInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> PlanView:
    require_origin(request, settings.web_origin)
    return await result(service.accept(database, *scope(account), payload))


@router.get("/plan", response_model=PlanView)
async def get_plan(account: CurrentAccountDependency, database: DatabaseSession) -> PlanView:
    return await result(service.plan_view(database, *scope(account)))


@router.post("/blocks/{block_id}/outcome", response_model=OutcomeView)
async def record_outcome(
    block_id: UUID,
    payload: OutcomeInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> OutcomeView:
    require_origin(request, settings.web_origin)
    return await result(service.outcome(database, *scope(account), block_id, payload))


@router.post("/blocks/{block_id}/outcome/preview", response_model=OutcomeView)
async def preview_outcome(
    block_id: UUID,
    payload: OutcomeInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> OutcomeView:
    require_origin(request, settings.web_origin)
    return await result(
        service.outcome(database, *scope(account), block_id, payload, preview_only=True)
    )


@router.post("/blocks/{block_id}/outcome/confirm", response_model=OutcomeView)
async def confirm_outcome(
    block_id: UUID,
    payload: OutcomeConfirmInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> OutcomeView:
    require_origin(request, settings.web_origin)
    outcome_input = OutcomeInput.model_validate(payload.model_dump(exclude={"preview_hash"}))
    return await result(
        service.outcome(
            database,
            *scope(account),
            block_id,
            outcome_input,
            expected_preview=payload.preview_hash,
        )
    )


@router.post("/undo", response_model=PlanView)
async def undo_progress(
    payload: UndoInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> PlanView:
    require_origin(request, settings.web_origin)
    return await result(service.undo_outcome(database, *scope(account), payload))
