"""Authenticated text-memory controls; reads cannot opt an account into retention."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from navox.api.auth import CurrentAccount, CurrentAccountDependency, DatabaseSession
from navox.api.memory import require_personal
from navox.core.settings import Settings, get_settings
from navox.memory import history
from navox.memory.history_schemas import (
    EraseAll,
    FactCreate,
    FactResponse,
    FactUpdate,
    HistoryDetail,
    HistoryResponse,
    PrivacyResponse,
    PrivacyUpdate,
)
from navox.memory.service import MemoryError, lock_owner

router = APIRouter(prefix="/memory", tags=["memory"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


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
) -> list[dict]:
    try:
        return await history.list_history(database, settings, *scope(current_account), query)
    except MemoryError as value:
        raise error(value) from None


@router.get("/history/{identifier}", response_model=HistoryDetail)
async def read_history(
    identifier: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict:
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
) -> list[dict]:
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
) -> dict:
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
) -> dict:
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
