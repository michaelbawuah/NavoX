"""Private model-free own-work PDF downloads; no source or conversation imports."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from navox.api.auth import (
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
    get_current_account,
)
from navox.api.connector_management import require_origin
from navox.student.service import StudentError
from navox.student_work_export import service
from navox.student_work_export.schemas import MAX_REQUEST_BYTES, ExportContext, ExportInput

PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class PrivateExportRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The own-work export request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), **PRIVATE}
                ) from None
            except Exception:
                # This private route must not delegate an unexpected failure to
                # a global error handler that can omit its cache boundary or
                # include the document in diagnostics. Cancellation propagates.
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Student work export is temporarily unavailable"},
                    headers=PRIVATE,
                )
            response.headers.update(PRIVATE)
            return response

        return private


def enabled(settings: SettingsDependency) -> None:
    if not getattr(settings, "student_work_export_enabled", False):
        raise HTTPException(404, "Student work export is not enabled", headers=PRIVATE)


router = APIRouter(
    prefix="/student/courses/{course_id}/work-export",
    tags=["student own-work export"],
    route_class=PrivateExportRoute,
    dependencies=[Depends(enabled)],
)


async def bounded_json(request: Request) -> ExportInput:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "Use application/json for own-work exports", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= MAX_REQUEST_BYTES:
            raise HTTPException(
                413, "The own-work export exceeds the request limit", headers=PRIVATE
            )
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                    raise HTTPException(
                        413, "The own-work export exceeds the request limit", headers=PRIVATE
                    )
                raw.extend(chunk)

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        return ExportInput.model_validate(json.loads(raw, object_pairs_hook=unique))
    except TimeoutError:
        raise HTTPException(
            408, "The own-work export request took too long", headers=PRIVATE
        ) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(
            422, "The own-work export request is invalid", headers=PRIVATE
        ) from None


@router.get("/context", response_model=ExportContext)
async def context(
    course_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> ExportContext:
    try:
        return await service.context(database, account.user.id, account.workspace.id, course_id)
    except (service.ExportError, StudentError) as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None


@router.post("/pdf")
async def download(
    course_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request)
    user_id, workspace_id = account.user.id, account.workspace.id

    async def reauthorize() -> tuple[UUID, UUID]:
        if await request.is_disconnected():
            raise service.ExportError("This export was canceled", 409)
        current = await get_current_account(request, database, settings)
        return current.user.id, current.workspace.id

    try:
        value = await service.export(
            database, settings, user_id, workspace_id, course_id, payload, reauthorize
        )
    except (service.ExportError, StudentError) as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, ValidationError):
        raise HTTPException(
            422, "The own-work export request is invalid", headers=PRIVATE
        ) from None
    return Response(
        value,
        media_type="application/pdf",
        headers={**PRIVATE, "Content-Disposition": 'attachment; filename="navox-own-work.pdf"'},
    )
