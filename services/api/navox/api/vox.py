"""Cookie-bound, private metadata reservations; no live media or provider entry point."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from navox.api.auth import (
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
    hash_session_token,
)
from navox.vox import service
from navox.vox.schemas import VoxCallView, VoxContext, VoxLease, VoxStart

PRIVATE = {"Cache-Control": "private, no-store"}
MAX_JSON_BYTES = 1024
JSON_DEADLINE_SECONDS = 10


class PrivateVoxRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The Vox request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), **PRIVATE}
                ) from None
            response.headers.update(PRIVATE)
            return response

        return private


def web_session_hash(request: Request, settings: SettingsDependency) -> str:
    # Bearer tokens belong to other API surfaces. Even an accompanying valid
    # cookie cannot turn an Authorization request into a web Vox reservation.
    if "authorization" in request.headers:
        raise HTTPException(403, "Vox requires a browser session", headers=PRIVATE)
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(401, "Authentication required", headers=PRIVATE)
    if request.method != "GET":
        origins = request.headers.getlist("origin")
        if (
            len(origins) != 1
            or not origins[0]
            or origins[0].rstrip("/") != settings.web_origin.rstrip("/")
        ):
            raise HTTPException(403, "The Vox request origin is invalid", headers=PRIVATE)
    return hash_session_token(token)


SessionHashDependency = Annotated[str, Depends(web_session_hash)]


def expected_account(request: Request, account: CurrentAccountDependency) -> None:
    expected = f"{account.workspace.id}:{account.user.id}"
    if request.headers.getlist("X-NavoX-Account") != [expected]:
        raise HTTPException(409, "The Vox account changed. Refresh and try again", headers=PRIVATE)


def enabled(settings: SettingsDependency) -> None:
    if not settings.student_vox_enabled:
        raise HTTPException(404, "Vox reservations are not enabled", headers=PRIVATE)


router = APIRouter(
    prefix="/vox/calls",
    tags=["vox reservations"],
    route_class=PrivateVoxRoute,
    dependencies=[Depends(web_session_hash), Depends(expected_account)],
)


async def bounded_json[T: BaseModel](request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        raise HTTPException(415, "Use application/json for Vox requests", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= MAX_JSON_BYTES:
            raise HTTPException(413, "The Vox request exceeds the size limit", headers=PRIVATE)
        content = bytearray()
        async with asyncio.timeout(JSON_DEADLINE_SECONDS):
            async for chunk in request.stream():
                if len(content) + len(chunk) > MAX_JSON_BYTES:
                    raise HTTPException(
                        413, "The Vox request exceeds the size limit", headers=PRIVATE
                    )
                content.extend(chunk)

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        return model.model_validate(json.loads(content, object_pairs_hook=unique))
    except TimeoutError:
        raise HTTPException(408, "The Vox request took too long", headers=PRIVATE) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The Vox request is invalid", headers=PRIVATE) from None


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except service.VoxError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, ValidationError):
        raise HTTPException(422, "The Vox request is invalid", headers=PRIVATE) from None


@router.get("/context", response_model=VoxContext, dependencies=[Depends(enabled)])
async def context(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
) -> VoxContext:
    return await result(
        service.context(database, account.user.id, account.workspace.id, session_token_hash)
    )


@router.post("", response_model=VoxCallView, status_code=201, dependencies=[Depends(enabled)])
async def start(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
) -> VoxCallView:
    payload = await bounded_json(request, VoxStart)
    return await result(
        service.start(database, account.user.id, account.workspace.id, session_token_hash, payload)
    )


@router.post("/{client_call_id}/renew", response_model=VoxCallView, dependencies=[Depends(enabled)])
async def renew(
    client_call_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
) -> VoxCallView:
    payload = await bounded_json(request, VoxLease)
    return await result(
        service.renew(
            database,
            account.user.id,
            account.workspace.id,
            session_token_hash,
            client_call_id,
            payload,
        )
    )


@router.post("/{client_call_id}/end", status_code=204)
async def end(
    client_call_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
) -> Response:
    payload = await bounded_json(request, VoxLease)
    await result(
        service.end(
            database,
            account.user.id,
            account.workspace.id,
            session_token_hash,
            client_call_id,
            payload,
        )
    )
    return Response(status_code=204, headers=PRIVATE)
