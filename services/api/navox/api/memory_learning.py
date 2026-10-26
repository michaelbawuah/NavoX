"""Browser-only status and explicit review of private semantic suggestions."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from navox.api.auth import CurrentAccountDependency, DatabaseSession
from navox.api.memory_history import (
    PrivateMemoryRoute,
    SettingsDependency,
    browser_authority,
    scope,
)
from navox.db.session import get_session_factory
from navox.memory.history_schemas import FactResponse
from navox.memory.learning import (
    LearningConfirmation,
    LearningDismissal,
    confirm_review,
    dismiss_review,
    list_reviews,
)
from navox.memory.learning_runtime import background_configured, native_configured
from navox.memory.service import MemoryError

router = APIRouter(
    prefix="/memory/learning",
    tags=["memory"],
    route_class=PrivateMemoryRoute,
    dependencies=[Depends(browser_authority)],
)


@router.get("")
async def learning_status(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    user_id, workspace_id = scope(current_account)
    factory = get_session_factory()
    configured = await native_configured(
        settings, factory, user_id, workspace_id
    ) or await background_configured(settings, factory, user_id, workspace_id)
    return await list_reviews(database, settings, user_id, workspace_id, configured=configured)


@router.post("/{identifier}/confirm", response_model=FactResponse, status_code=201)
async def confirm(
    identifier: UUID,
    payload: LearningConfirmation,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    try:
        return await confirm_review(
            database, settings, *scope(current_account), identifier, payload
        )
    except MemoryError as error:
        raise HTTPException(error.status_code, str(error)) from None


@router.post("/{identifier}/dismiss", status_code=204)
async def dismiss(
    identifier: UUID,
    payload: LearningDismissal,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    try:
        await dismiss_review(
            database, settings, *scope(current_account), identifier, payload.generation
        )
    except MemoryError as error:
        raise HTTPException(error.status_code, str(error)) from None
    return Response(status_code=204)
