"""Opt-in recovery previews, with bounded private JSON and no external actions."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from navox.api.auth import (
    CurrentAccount,
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
)
from navox.api.connector_management import require_origin
from navox.core.settings import Settings
from navox.coursework.service import CourseworkError
from navox.student.service import StudentError
from navox.student_recovery import service
from navox.student_recovery.schemas import (
    EraseInput,
    ExtensionInput,
    ExtensionView,
    RecoveryInput,
    RecoveryView,
)

PRIVATE = {"Cache-Control": "private, no-store"}
MAX_JSON_BYTES = 64 * 1024
JSON_DEADLINE_SECONDS = 10


class PrivateRecoveryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The recovery request is invalid"},
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
    if request.method != "DELETE" and not settings.student_recovery_enabled:
        raise HTTPException(404, "Student recovery is not enabled", headers=PRIVATE)


router = APIRouter(
    prefix="/student/recovery",
    tags=["student recovery"],
    route_class=PrivateRecoveryRoute,
    dependencies=[Depends(enabled)],
)


def scope(
    account: CurrentAccount, settings: Settings, response: Response, *, allow_forget: bool = False
) -> tuple[UUID, UUID]:
    response.headers.update(PRIVATE)
    if not allow_forget and not getattr(settings, "student_recovery_enabled", False):
        raise HTTPException(404, "Student recovery is not enabled", headers=PRIVATE)
    return account.user.id, account.workspace.id


async def bounded_json[T: BaseModel](
    request: Request, model: type[T], limit: int = MAX_JSON_BYTES
) -> T:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "Use application/json for recovery requests", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= limit:
            raise HTTPException(413, "The recovery request exceeds the size limit", headers=PRIVATE)
        raw = bytearray()
        async with asyncio.timeout(JSON_DEADLINE_SECONDS):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit:
                    raise HTTPException(
                        413, "The recovery request exceeds the size limit", headers=PRIVATE
                    )
                raw.extend(chunk)
        return model.model_validate(json.loads(raw))
    except TimeoutError:
        raise HTTPException(408, "The recovery request took too long", headers=PRIVATE) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The recovery request is invalid", headers=PRIVATE) from None


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except (StudentError, CourseworkError) as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, ValidationError):
        raise HTTPException(422, "The recovery request is invalid", headers=PRIVATE) from None


@router.post("/preview", response_model=RecoveryView)
async def preview(
    request: Request,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RecoveryView:
    user_id, workspace_id = scope(account, settings, response)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, RecoveryInput)
    return await result(service.preview(database, settings, user_id, workspace_id, payload))


@router.get("/previews/{preview_id}", response_model=RecoveryView | ExtensionView)
async def get_preview(
    preview_id: UUID,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RecoveryView | ExtensionView:
    user_id, workspace_id = scope(account, settings, response)
    return await result(service.load(database, settings, user_id, workspace_id, preview_id))


@router.post("/previews/{preview_id}/extension-draft", response_model=ExtensionView)
async def extension(
    preview_id: UUID,
    request: Request,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ExtensionView:
    user_id, workspace_id = scope(account, settings, response)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, ExtensionInput)
    return await result(
        service.extension_draft(database, settings, user_id, workspace_id, preview_id, payload)
    )


@router.delete("/previews/{preview_id}", status_code=204)
async def erase(
    preview_id: UUID,
    request: Request,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    user_id, workspace_id = scope(account, settings, response, allow_forget=True)
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, EraseInput, limit=512)
    await result(service.erase(database, user_id, workspace_id, preview_id, payload.preview_hash))
    return Response(status_code=204, headers=PRIVATE)
