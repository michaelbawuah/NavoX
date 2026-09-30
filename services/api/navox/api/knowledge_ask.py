"""Grounded Ask routes: explicit paid question, owned sessions, free search turns.

Registered through the existing knowledge router, so no application wiring
change is needed. Every route is origin-checked for writes, requires the Ask
feature flag, and never accepts workspace, user or permission authority from the
client.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.knowledge.ask import (
    AskDisabled,
    AskGateway,
    AskQuotaExceeded,
    AskRejected,
    AskRequestReplay,
    AskUnavailable,
    ReferentInvalid,
    answer_question,
    clear_history,
    delete_session,
    list_sessions,
    read_session,
    record_search_turn,
    turn_response,
)
from navox.knowledge.ask_contracts import (
    AskRequest,
    AskResponse,
    AskSessionSummary,
    AskSessionView,
    SearchTurnRequest,
)
from navox.knowledge.service import KnowledgeUnavailable

router = APIRouter(tags=["knowledge"])


def ask_enabled(enabled: bool) -> None:
    """Grounded Ask is default off; enabling it in tests is not deployment."""
    if not enabled:
        raise HTTPException(404, "Grounded Ask is not enabled")


def injected_gateway(request: Request) -> AskGateway | None:
    """Test-only seam. Production builds the registered gateway from settings."""
    return getattr(request.app.state, "knowledge_ask_gateway", None)


async def _replay(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    error: AskRequestReplay,
) -> AskResponse:
    """An exact replay returns the owned turn; it never buys another attempt."""
    turn = error.turn
    if turn is None:
        raise HTTPException(409, "This request identifier was already used") from error
    response = await turn_response(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=turn.session_id,
        turn_id=turn.id,
    )
    if response is None:
        raise HTTPException(409, "This request identifier was already used") from error
    return response


@router.post("/knowledge/ask", response_model=AskResponse)
async def ask(
    request: Request,
    payload: AskRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AskResponse:
    require_origin(request, settings.web_origin)
    ask_enabled(settings.knowledge_enabled and settings.knowledge_ask_enabled)
    try:
        return await answer_question(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload,
            settings=settings,
            gateway=injected_gateway(request),
        )
    except AskRequestReplay as error:
        return await _replay(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            error=error,
        )
    except AskQuotaExceeded as error:
        raise HTTPException(429, str(error)) from error
    except ReferentInvalid as error:
        raise HTTPException(422, str(error)) from error
    except AskRejected as error:
        raise HTTPException(422, str(error)) from error
    except AskDisabled as error:
        raise HTTPException(404, str(error)) from error
    except AskUnavailable as error:
        raise HTTPException(403, str(error)) from error


@router.post("/knowledge/sessions/search-turns", response_model=AskResponse)
async def search_turn(
    request: Request,
    payload: SearchTurnRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AskResponse:
    """Record a free retrieval turn; this path never buys a provider request."""
    require_origin(request, settings.web_origin)
    if not settings.knowledge_enabled:
        raise HTTPException(404, "Connected search is not enabled")
    try:
        return await record_search_turn(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            payload=payload,
            settings=settings,
        )
    except AskRejected as error:
        raise HTTPException(422, str(error)) from error
    except AskDisabled as error:
        raise HTTPException(404, str(error)) from error
    except AskUnavailable as error:
        raise HTTPException(403, str(error)) from error
    except KnowledgeUnavailable as error:
        raise HTTPException(403, str(error)) from error


@router.get("/knowledge/sessions", response_model=list[AskSessionSummary])
async def sessions(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[AskSessionSummary]:
    ask_enabled(settings.knowledge_enabled and settings.knowledge_ask_enabled)
    try:
        return await list_sessions(
            database, workspace_id=account.workspace.id, user_id=account.user.id
        )
    except KnowledgeUnavailable as error:
        raise HTTPException(403, str(error)) from error


@router.get("/knowledge/sessions/{session_id}", response_model=AskSessionView)
async def session_detail(
    session_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> AskSessionView:
    ask_enabled(settings.knowledge_enabled and settings.knowledge_ask_enabled)
    try:
        view = await read_session(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            session_id=session_id,
        )
    except KnowledgeUnavailable as error:
        raise HTTPException(403, str(error)) from error
    if view is None:
        # A foreign or unknown session is indistinguishable.
        raise HTTPException(404, "Session not found")
    return view


@router.delete("/knowledge/sessions")
async def clear_sessions(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, int]:
    """Clear owned history; paid reservations survive so quota is never refunded.

    Owned deletion stays available even when the Ask or search flags are off.
    """
    require_origin(request, settings.web_origin)
    removed = await clear_history(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )
    return {"cleared": removed}


@router.delete("/knowledge/sessions/{session_id}")
async def remove_session(
    request: Request,
    session_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, bool]:
    require_origin(request, settings.web_origin)
    removed = await delete_session(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        session_id=session_id,
    )
    if not removed:
        raise HTTPException(404, "Session not found")
    return {"removed": True}
