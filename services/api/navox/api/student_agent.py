"""Unregistered Phase 2 student selector hook. Disabled unless explicitly configured."""

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.runtime import GatewayUnavailable
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.sync_state import database_now
from navox.student.schemas import valid_zone
from navox.student.service import StudentError, owner
from navox.student_agent.contracts import (
    MAX_SELECTION_BODY_BYTES,
    ClockResponse,
    SelectorRequest,
    SelectorResponse,
    SessionAccessResponse,
)
from navox.student_agent.selector import require_session, select_next


def enabled(settings: SettingsDependency) -> None:
    if not getattr(settings, "student_agent_enabled", False):
        raise HTTPException(404, "Student agent is not enabled")


router = APIRouter(prefix="/student-agent", tags=["student-agent"], dependencies=[Depends(enabled)])


@router.get("/clock", response_model=ClockResponse)
async def server_clock(
    account: CurrentAccountDependency, database: DatabaseSession, response: Response
) -> ClockResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        user = await owner(database, account.user.id, account.workspace.id)
        timezone = valid_zone(user.timezone)
    except StudentError as error:
        raise HTTPException(error.status_code, "Personal student clock is unavailable") from None
    except ValueError:
        raise HTTPException(409, "Choose a valid timezone before using the student clock") from None
    return ClockResponse(server_now=await database_now(database), timezone=timezone)


@router.get("/sessions/{session_id}", response_model=SessionAccessResponse)
async def session_access(
    session_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
) -> SessionAccessResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        await require_session(
            database,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            session_id=session_id,
        )
    except ContextDenied:
        raise HTTPException(404, "Student assistant session is unavailable") from None
    return SessionAccessResponse(session_id=session_id)


async def read_selector_request(request: Request) -> SelectorRequest:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        raise HTTPException(415, "A JSON selector request is required")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) < 0 or int(content_length) > MAX_SELECTION_BODY_BYTES:
                raise HTTPException(413, "Selector request is too large")
        except ValueError:
            raise HTTPException(400, "Invalid request size") from None
    content = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(content) + len(chunk) > MAX_SELECTION_BODY_BYTES:
                    raise HTTPException(413, "Selector request is too large")
                content.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Selector request took too long") from None
    try:
        return SelectorRequest.model_validate(json.loads(content))
    except (ValueError, UnicodeError, ValidationError, RecursionError):
        raise HTTPException(422, "Selector request is invalid or exceeds its bounds") from None


@router.post("/select", response_model=SelectorResponse)
async def select_student_tool(
    request: Request,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SelectorResponse:
    response.headers["Cache-Control"] = "no-store"
    require_origin(request, settings.web_origin)
    if not getattr(settings, "student_agent_model_enabled", False):
        raise HTTPException(
            503,
            "Student model tool selection is not enabled",
            headers={"Cache-Control": "no-store"},
        )
    payload = await read_selector_request(request)
    user_id, workspace_id = account.user.id, account.workspace.id
    try:
        await require_session(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=payload.session_id,
        )
        await database.rollback()  # No database transaction spans model I/O.
        runtime = await build_runtime(settings)
        return await select_next(
            runtime,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            request=payload,
        )
    except (ContextDenied, PermissionError):
        raise HTTPException(403, "Student assistant access is unavailable or changed") from None
    except (AIProviderNotConfigured, GatewayUnavailable, LookupError):
        raise HTTPException(
            503, "No qualified student selector is available", headers={"Cache-Control": "no-store"}
        ) from None
    except ValueError:
        raise HTTPException(
            409, "Student assistant selection was not accepted; refresh and retry"
        ) from None
