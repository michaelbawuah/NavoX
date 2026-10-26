"""Authenticated text-memory controls; reads cannot opt an account into retention."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from navox.api.auth import (
    CurrentAccount,
    CurrentAccountDependency,
    DatabaseSession,
    hash_session_token,
)
from navox.api.memory import require_personal
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings, get_settings
from navox.db.models import UserSession
from navox.memory import history
from navox.memory.history_schemas import (
    EraseAll,
    FactCreate,
    FactResponse,
    FactUpdate,
    HistoryDetail,
    HistoryPage,
    HistoryPageInput,
    HistoryResponse,
    PrivacyResponse,
    PrivacyUpdate,
)
from navox.memory.service import MemoryError, lock_owner

SettingsDependency = Annotated[Settings, Depends(get_settings)]
PRIVATE = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


class PrivateMemoryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                if request.method not in {"GET", "HEAD"}:
                    declared = request.headers.get("content-length")
                    if declared is not None and (not declared.isdigit() or int(declared) > 32768):
                        raise HTTPException(413, "This memory request is too large")
                response = await original(request)
            except (RequestValidationError, ValidationError):
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Check this memory request"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), **PRIVATE}
                ) from None
            except (SQLAlchemyError, TimeoutError):
                raise HTTPException(
                    503, "Your private memory could not be checked. Try again", headers=PRIVATE
                ) from None
            response.headers.update(PRIVATE)
            return response

        return private


async def browser_authority(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> None:
    if "authorization" in request.headers:
        raise HTTPException(403, "Memory controls require your browser sign-in")
    if request.headers.getlist("X-NavoX-Account") != [f"{account.workspace.id}:{account.user.id}"]:
        raise HTTPException(409, "Your account changed. Reopen your memory settings")
    if request.method not in {"GET", "HEAD"} and request.headers.getlist("origin") != [
        settings.web_origin.rstrip("/")
    ]:
        raise HTTPException(403, "This memory request came from an unexpected page")
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(401, "Sign in to open your memory")
    require_personal(account)
    # Serialize with logout/Forget before trusting the originally loaded account.
    await lock_owner(database, account.user.id)
    session = await database.scalar(
        select(UserSession)
        .where(
            UserSession.user_id == account.user.id,
            UserSession.token_hash == hash_session_token(token),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        session is None
        or session.client_type != "web"
        or session.revoked_at is not None
        or utc(session.expires_at) <= await database_now(database)
    ):
        raise HTTPException(401, "Your sign-in changed. Sign in again")


router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    route_class=PrivateMemoryRoute,
    dependencies=[Depends(browser_authority)],
)


def scope(account: CurrentAccount) -> tuple[UUID, UUID]:
    require_personal(account)
    return account.user.id, account.workspace.id


def error(value: MemoryError) -> HTTPException:
    return HTTPException(value.status_code, str(value))


@router.get("/privacy", response_model=PrivacyResponse)
async def privacy(
    current_account: CurrentAccountDependency, database: DatabaseSession
) -> PrivacyResponse:
    user_id, workspace_id = scope(current_account)
    return history.privacy_response(await history.privacy_row(database, user_id, workspace_id))


@router.put("/privacy", response_model=PrivacyResponse)
async def save_privacy(
    payload: PrivacyUpdate,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> PrivacyResponse:
    try:
        return await history.update_privacy(database, settings, *scope(current_account), payload)
    except MemoryError as value:
        raise error(value) from None


@router.get("/history", response_model=list[HistoryResponse])
async def list_history(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    query: str = Query(default="", max_length=200),
) -> list[dict[str, Any]]:
    try:
        return await history.list_history(database, settings, *scope(current_account), query)
    except MemoryError as value:
        raise error(value) from None


@router.post("/history/page", response_model=HistoryPage)
async def search_history_page(
    payload: HistoryPageInput,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    # The browser uses a body so private search text and continuation tokens do
    # not become URL/access-log/referrer material. The legacy GET list stays intact.
    try:
        return await history.history_page(
            database,
            settings,
            *scope(current_account),
            query=payload.query,
            cursor=payload.cursor,
            page_size=payload.page_size,
        )
    except MemoryError as value:
        raise error(value) from None


@router.get("/history/{identifier}", response_model=HistoryDetail)
async def read_history(
    identifier: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    try:
        return await history.get_history(database, settings, *scope(current_account), identifier)
    except MemoryError as value:
        raise error(value) from None


@router.delete("/history/{identifier}", status_code=204)
async def erase_history(
    identifier: UUID, current_account: CurrentAccountDependency, database: DatabaseSession
) -> Response:
    try:
        await history.forget_conversation(database, *scope(current_account), identifier)
    except MemoryError as value:
        raise error(value) from None
    return Response(status_code=204)


@router.get("/facts", response_model=list[FactResponse])
async def list_facts(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[dict[str, Any]]:
    try:
        return await history.list_facts(database, settings, *scope(current_account))
    except MemoryError as value:
        raise error(value) from None


@router.post("/facts", response_model=FactResponse, status_code=201)
async def add_fact(
    payload: FactCreate,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    try:
        return await history.create_fact(database, settings, *scope(current_account), payload)
    except MemoryError as value:
        raise error(value) from None


@router.patch("/facts/{identifier}", response_model=FactResponse)
async def change_fact(
    identifier: UUID,
    payload: FactUpdate,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    try:
        return await history.edit_fact(
            database, settings, *scope(current_account), identifier, payload
        )
    except MemoryError as value:
        raise error(value) from None


@router.delete("/facts/{identifier}", status_code=204)
async def erase_fact(
    identifier: UUID, current_account: CurrentAccountDependency, database: DatabaseSession
) -> Response:
    try:
        await history.forget_fact(database, *scope(current_account), identifier)
    except MemoryError as value:
        raise error(value) from None
    return Response(status_code=204)


@router.delete("/all", status_code=204)
async def erase_all(
    payload: EraseAll, current_account: CurrentAccountDependency, database: DatabaseSession
) -> Response:
    user_id, workspace_id = scope(current_account)
    try:
        await lock_owner(database, user_id)
        await history.erase_all_memory(
            database, user_id, workspace_id, expected_generation=payload.generation
        )
        await database.commit()
    except MemoryError as value:
        raise error(value) from None
    return Response(status_code=204)
