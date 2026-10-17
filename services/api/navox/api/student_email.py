"""Explicit email review only: private inputs, fixed routes, independent owner checks."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.coursework.service import CourseworkError
from navox.student import service as schedule
from navox.student_email import service
from navox.student_email.reader import get_email_reader
from navox.student_email.schemas import AcceptInput, AnalyzeInput, CorrectionInput, EraseInput

PRIVATE = {"Cache-Control": "private, no-store"}
MAX_JSON_BYTES = 8 * 1024
JSON_DEADLINE_SECONDS = 10


class PrivateEmailRoute(APIRoute):
    """Do not echo invalid request bodies/path values through framework errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The email review request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code,
                    error.detail,
                    headers={**(error.headers or {}), **PRIVATE},
                ) from None
            response.headers.update(PRIVATE)
            return response

        return private


def enabled(request: Request, settings: SettingsDependency) -> None:
    if request.method == "DELETE":
        return  # Owner erasure remains available during a feature rollback.
    if not getattr(settings, "student_email_enabled", False):
        raise HTTPException(503, "Student email review is not enabled", headers=PRIVATE)


router = APIRouter(
    prefix="/student/email",
    tags=["student email review"],
    dependencies=[Depends(enabled)],
    route_class=PrivateEmailRoute,
)


def private_origin(request: Request, origin: str) -> None:
    require_origin(request, origin)


async def bounded_json[T: BaseModel](request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        raise HTTPException(415, "Use application/json for email review", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= MAX_JSON_BYTES:
            raise HTTPException(413, "The email review request is too large", headers=PRIVATE)
        content = bytearray()
        async with asyncio.timeout(JSON_DEADLINE_SECONDS):
            async for chunk in request.stream():
                if len(content) + len(chunk) > MAX_JSON_BYTES:
                    raise HTTPException(
                        413, "The email review request is too large", headers=PRIVATE
                    )
                content.extend(chunk)
        return model.model_validate(json.loads(content))
    except TimeoutError:
        raise HTTPException(
            408, "The email review request took too long", headers=PRIVATE
        ) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The email review request is invalid", headers=PRIVATE) from None


async def no_body(request: Request) -> None:
    try:
        async with asyncio.timeout(JSON_DEADLINE_SECONDS):
            async for chunk in request.stream():
                if chunk:
                    raise HTTPException(422, "This erasure request has no body", headers=PRIVATE)
    except TimeoutError:
        raise HTTPException(408, "The erasure request took too long", headers=PRIVATE) from None


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except (service.EmailError, schedule.StudentError, CourseworkError) as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except TimeoutError:
        raise HTTPException(
            503, "The selected email could not be read in time", headers=PRIVATE
        ) from None
    except ValueError:
        raise HTTPException(422, "The email review request is invalid", headers=PRIVATE) from None


async def owner(
    database: DatabaseSession, account: CurrentAccountDependency, *, lock: bool = False
) -> tuple[UUID, UUID]:
    user_id, workspace_id = account.user.id, account.workspace.id
    await result(schedule.owner(database, user_id, workspace_id, lock=lock))
    return user_id, workspace_id


@router.get("/sources")
async def sources(
    request: Request, account: CurrentAccountDependency, database: DatabaseSession
) -> dict[str, Any]:
    user_id, workspace_id = await owner(database, account)
    queries = request.query_params.getlist("q")
    if (
        set(request.query_params) != {"q"}
        or len(queries) != 1
        or not 2 <= len(queries[0].strip()) <= 120
        or any(ord(char) < 32 for char in queries[0])
    ):
        raise HTTPException(
            422, "Enter an explicit email search of 2 to 120 characters", headers=PRIVATE
        )
    return await result(
        service.list_sources(
            database, user_id=user_id, workspace_id=workspace_id, query=queries[0].strip()
        )
    )


@router.post("/analyze")
async def analyze(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    private_origin(request, settings.web_origin)
    user_id, workspace_id = await owner(database, account)
    payload = await bounded_json(request, AnalyzeInput)
    return await result(
        service.analyze(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            payload=payload,
            reader=get_email_reader(),
        )
    )


@router.get("/reviews/{review_id}")
async def read_review(
    review_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    user_id, workspace_id = await owner(database, account)
    return await result(
        service.read_review(
            database, settings, user_id=user_id, workspace_id=workspace_id, review_id=review_id
        )
    )


@router.put("/reviews/{review_id}")
async def correct(
    review_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    private_origin(request, settings.web_origin)
    user_id, workspace_id = await owner(database, account)
    payload = await bounded_json(request, CorrectionInput)
    return await result(
        service.correct(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            review_id=review_id,
            payload=payload,
        )
    )


@router.post("/reviews/{review_id}/accept-task")
async def accept_task(
    review_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    private_origin(request, settings.web_origin)
    user_id, workspace_id = await owner(database, account)
    payload = await bounded_json(request, AcceptInput)
    return await result(
        service.accept_task(
            database,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            review_id=review_id,
            payload=payload,
        )
    )


@router.delete("/reviews/{review_id}")
async def erase_review(
    review_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    private_origin(request, settings.web_origin)
    user_id, workspace_id = await owner(database, account)
    payload = await bounded_json(request, EraseInput)
    return await result(
        service.erase_review(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            review_id=review_id,
            revision=payload.revision,
        )
    )


@router.delete("/sources/{source_id}")
async def forget_source(
    source_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    private_origin(request, settings.web_origin)
    await no_body(request)
    user_id, workspace_id = await owner(database, account, lock=True)
    forgotten = await result(
        service.forget_email_data(
            database, user_id=user_id, workspace_id=workspace_id, source_id=source_id
        )
    )
    await database.commit()
    return forgotten
