"""Private saved authored Boards; deletion stays available when the flag is off."""

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

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.board import service
from navox.board.schemas import (
    MAX_SNAPSHOT_BYTES,
    BoardCreate,
    BoardErase,
    BoardsView,
    BoardUpdate,
    BoardView,
)
from navox.student.service import StudentError

PRIVATE = {"Cache-Control": "private, no-store"}


class PrivateBoardRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The Board request is invalid"},
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
    if request.method != "DELETE" and not settings.student_board_enabled:
        raise HTTPException(404, "Saved Boards are not enabled", headers=PRIVATE)


def expected_account(request: Request, account: CurrentAccountDependency) -> None:
    expected = f"{account.workspace.id}:{account.user.id}"
    if request.headers.get("X-NavoX-Account") != expected:
        raise HTTPException(
            409, "The Board account changed. Refresh and try again", headers=PRIVATE
        )


router = APIRouter(
    prefix="/board/saved",
    tags=["saved boards"],
    route_class=PrivateBoardRoute,
    dependencies=[Depends(enabled), Depends(expected_account)],
)


async def bounded_json[T: BaseModel](
    request: Request, model: type[T], limit: int = MAX_SNAPSHOT_BYTES
) -> T:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise HTTPException(415, "Use application/json for Board requests", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= limit:
            raise HTTPException(413, "The Board request exceeds the size limit", headers=PRIVATE)
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > limit:
                    raise HTTPException(
                        413, "The Board request exceeds the size limit", headers=PRIVATE
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
        raise HTTPException(408, "The Board request took too long", headers=PRIVATE) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The Board request is invalid", headers=PRIVATE) from None


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, ValidationError):
        raise HTTPException(422, "The Board request is invalid", headers=PRIVATE) from None


@router.get("", response_model=BoardsView)
async def collection(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> BoardsView:
    return await result(
        service.collection(database, settings, account.user.id, account.workspace.id)
    )


@router.get("/{board_id}", response_model=BoardView)
async def detail(
    board_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> BoardView:
    return await result(
        service.detail(database, settings, account.user.id, account.workspace.id, board_id)
    )


@router.post("", response_model=BoardView, status_code=201)
async def create(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> BoardView:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, BoardCreate)
    return await result(
        service.create(database, settings, account.user.id, account.workspace.id, payload)
    )


@router.patch("/{board_id}", response_model=BoardView)
async def change(
    board_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> BoardView:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, BoardUpdate)
    return await result(
        service.change(database, settings, account.user.id, account.workspace.id, board_id, payload)
    )


@router.delete("/{board_id}", status_code=204)
async def erase(
    board_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    require_origin(request, settings.web_origin)
    payload = await bounded_json(request, BoardErase, limit=512)
    await result(service.erase(database, account.user.id, account.workspace.id, board_id, payload))
    return Response(status_code=204, headers=PRIVATE)
