"""Browser-only confirmation of an expiring, owner/source-bound memory review."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, hash_session_token
from navox.api.memory_history import (
    PrivateMemoryRoute,
    SettingsDependency,
    browser_authority,
    scope,
)
from navox.db.models import UserSession
from navox.memory.candidates import CandidateConfirmation, confirm_candidate
from navox.memory.history_schemas import FactResponse
from navox.memory.service import MemoryError

router = APIRouter(
    prefix="/memory/candidates",
    tags=["memory"],
    route_class=PrivateMemoryRoute,
    dependencies=[Depends(browser_authority)],
)


@router.post("/confirm", response_model=FactResponse, status_code=201)
async def confirm(
    payload: CandidateConfirmation,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    user_id, workspace_id = scope(current_account)
    # browser_authority already checked this exact cookie under the owner lock.
    # Carry its durable login identity into the source-bound confirmation.
    token = request.cookies.get(settings.session_cookie_name)
    session_id = await database.scalar(
        select(UserSession.id).where(
            UserSession.user_id == user_id,
            UserSession.token_hash == hash_session_token(token or ""),
            UserSession.client_type == "web",
            UserSession.revoked_at.is_(None),
        )
    )
    if session_id is None:
        raise HTTPException(401, "Your sign-in changed. Sign in again")
    try:
        return await confirm_candidate(
            database, settings, user_id, workspace_id, session_id, payload
        )
    except MemoryError as error:
        raise HTTPException(error.status_code, str(error)) from None
