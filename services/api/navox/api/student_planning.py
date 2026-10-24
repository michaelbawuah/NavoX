"""Disabled-by-default, explicitly accepted study plans. Never writes a calendar."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.student import result, scope
from navox.student import service as schedule
from navox.student_planning import service
from navox.student_planning.schemas import (
    AcceptInput,
    MoveConfirm,
    MoveInput,
    OutcomeConfirmInput,
    OutcomeInput,
    OutcomeView,
    PlanView,
    PreferencesConfirm,
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


def expected_routine_owner(request: Request, account: CurrentAccountDependency) -> None:
    if request.headers.getlist("X-NavoX-Account") != [f"{account.workspace.id}:{account.user.id}"]:
        raise HTTPException(409, "Your account changed. Reopen Settings and try again")


@router.post("/preferences/preview", dependencies=[Depends(expected_routine_owner)])
async def preview_preferences(
    payload: PreferencesUpdate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict:
    require_origin(request, settings.web_origin)
    return await result(service.preference_preview(database, *scope(account), payload))


@router.post(
    "/preferences/confirm",
    response_model=PreferencesView,
    dependencies=[Depends(expected_routine_owner)],
)
async def confirm_preferences(
    payload: PreferencesConfirm,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> PreferencesView:
    require_origin(request, settings.web_origin)
    candidate = PreferencesUpdate.model_validate(payload.model_dump(exclude={"preview_hash"}))
    await result(schedule.owner(database, *scope(account), lock=True))
    previous = await result(
        service.command(
            database,
            *scope(account),
            candidate.request_id,
            "preferences",
            candidate.model_dump(mode="json"),
        )
    )
    if previous:
        return await result(service.preferences(database, *scope(account)))
    preview = await result(service.preference_preview(database, *scope(account), candidate))
    if preview["warnings"] or any(
        row["enabled"] and row["scheduled_at"] is None for row in preview["alarm_occurrences"]
    ):
        raise HTTPException(409, "Resolve timetable or wake-time warnings before approving")
    if preview["preview_hash"] != payload.preview_hash:
        raise HTTPException(
            409, "Your routine, classes or study blocks changed. Review a fresh preview"
        )
    return await result(service.save_preferences(database, *scope(account), candidate))


@router.get("/alarm", dependencies=[Depends(expected_routine_owner)])
async def alarm_view(account: CurrentAccountDependency, database: DatabaseSession) -> dict:
    current = await result(service.preferences(database, *scope(account)))
    payload = PreferencesUpdate.model_validate(
        {**current.model_dump(exclude={"timezone"}), "request_id": uuid4()}
    )
    preview = await result(service.preference_preview(database, *scope(account), payload))
    return {
        "timezone": preview["timezone"],
        "alarm_occurrences": preview["alarm_occurrences"],
        "native_ringing_available": False,
    }


@router.post("/blocks/{block_id}/move/preview", dependencies=[Depends(expected_routine_owner)])
async def preview_move(
    block_id: UUID,
    payload: MoveInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict:
    from navox.student_planning.moving import move

    require_origin(request, settings.web_origin)
    return await result(move(database, *scope(account), block_id, payload))


@router.post("/blocks/{block_id}/move/confirm", dependencies=[Depends(expected_routine_owner)])
async def confirm_move(
    block_id: UUID,
    payload: MoveConfirm,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict:
    from navox.student_planning.moving import move

    require_origin(request, settings.web_origin)
    candidate = MoveInput.model_validate(payload.model_dump(exclude={"preview_hash"}))
    return await result(
        move(database, *scope(account), block_id, candidate, expected_preview=payload.preview_hash)
    )


@router.get("/openings", dependencies=[Depends(expected_routine_owner)])
async def get_openings(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    days: Annotated[int, Query(ge=1, le=7)] = 1,
) -> dict:
    from navox.student_planning.openings import openings

    return await result(openings(database, *scope(account), days))
