"""Private explicit goal reports; erasure remains available when the flag is off."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.student.service import StudentError
from navox.student_goals import service
from navox.student_goals.schemas import (
    CheckInInput,
    CheckInView,
    EraseInput,
    GoalCreate,
    GoalsView,
    GoalUpdate,
    GoalView,
    ResetInput,
)

PRIVATE = {"Cache-Control": "private, no-store"}


class PrivateGoalsRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The goal request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), **PRIVATE}
                ) from None
            response.headers.update(PRIVATE)
            return response

        return private


def enabled(request: Request, settings: SettingsDependency) -> None:
    if request.method != "DELETE" and not getattr(settings, "student_goals_enabled", False):
        raise HTTPException(404, "Student goals are not enabled", headers=PRIVATE)


router = APIRouter(
    prefix="/student/goals",
    tags=["student goals"],
    route_class=PrivateGoalsRoute,
    dependencies=[Depends(enabled)],
)


async def bounded_json[T: BaseModel](request: Request, model: type[T], limit: int = 16384) -> T:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "Use application/json for goal requests", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= limit:
            raise HTTPException(413, "The goal request exceeds the size limit", headers=PRIVATE)
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit:
                    raise HTTPException(
                        413, "The goal request exceeds the size limit", headers=PRIVATE
                    )
                raw.extend(chunk)

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        return model.model_validate(json.loads(raw, object_pairs_hook=unique))
    except TimeoutError:
        raise HTTPException(408, "The goal request took too long", headers=PRIVATE) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The goal request is invalid", headers=PRIVATE) from None


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, ValidationError):
        raise HTTPException(422, "The goal request is invalid", headers=PRIVATE) from None


@router.get("", response_model=GoalsView)
async def goals(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> GoalsView:
    return await result(service.board(database, settings, account.user.id, account.workspace.id))


@router.post("", response_model=GoalView, status_code=201)
async def create(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> GoalView:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, GoalCreate)
    return await result(
        service.save_goal(database, settings, account.user.id, account.workspace.id, payload)
    )


@router.patch("/{goal_id}", response_model=GoalView)
async def change(
    goal_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> GoalView:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, GoalUpdate)
    return await result(
        service.save_goal(
            database, settings, account.user.id, account.workspace.id, payload, goal_id
        )
    )


@router.get("/{goal_id}/checkins", response_model=CheckInView | None)
async def report_on(
    goal_id: UUID,
    on_date: date,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CheckInView | None:
    return await result(
        service.checkin_on(
            database, settings, account.user.id, account.workspace.id, goal_id, on_date
        )
    )


@router.post("/{goal_id}/checkins", response_model=CheckInView, status_code=201)
async def report(
    goal_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CheckInView:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, CheckInInput, limit=8192)
    return await result(
        service.save_checkin(
            database, settings, account.user.id, account.workspace.id, goal_id, payload
        )
    )


@router.delete("/{goal_id}", status_code=204)
async def erase_goal(
    goal_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, EraseInput, limit=512)
    await result(
        service.erase_goal(database, account.user.id, account.workspace.id, goal_id, payload)
    )
    return Response(status_code=204, headers=PRIVATE)


@router.delete("/{goal_id}/checkins/{checkin_id}", status_code=204)
async def erase_checkin(
    goal_id: UUID,
    checkin_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, EraseInput, limit=512)
    await result(
        service.erase_checkin(
            database, account.user.id, account.workspace.id, goal_id, checkin_id, payload
        )
    )
    return Response(status_code=204, headers=PRIVATE)


@router.delete("", status_code=204)
async def erase_all(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, ResetInput, limit=512)
    await result(service.reset(database, account.user.id, account.workspace.id, payload))
    return Response(status_code=204, headers=PRIVATE)
