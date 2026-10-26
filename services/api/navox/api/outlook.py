"Private browser-only Outlook routes; callback is also bound to the initiating session."

import asyncio
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from navox.ai.context import ContextDenied
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.runtime import GatewayUnavailable
from navox.api.auth import (
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
    hash_session_token,
)
from navox.connectors.secrets import SecretBrokerError
from navox.core.settings import Settings
from navox.coursework.service import CourseworkError
from navox.outlook import replies, service, tasks
from navox.outlook.schemas import Connect, Generate, Preview, ReplyCheck, Revise, Send, TaskPreview
from navox.providers.microsoft_mail import MicrosoftMailError
from navox.student.service import StudentError
from navox.student_calendars import microsoft as calendar_microsoft
from navox.student_email.schemas import AcceptInput, CorrectionInput, EraseInput
from navox.student_email.service import EmailError

PRIVATE = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}


class PrivateOutlookRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                async with asyncio.timeout(90):
                    response = await original(request)
            except (RequestValidationError, ValidationError):
                return JSONResponse(
                    status_code=422,
                    content={"detail": "This Outlook request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), **PRIVATE}
                ) from None
            except (service.OutlookError, EmailError, CourseworkError, StudentError) as error:
                raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
            except MicrosoftMailError as error:
                status = {
                    "reauthorize": 403,
                    "permission": 403,
                    "not_found": 404,
                    "rate_limit": 429,
                }.get(error.code, 503)
                message = (
                    "This email is no longer available"
                    if status == 404
                    else (
                        "Outlook is unavailable. Reopen the connection and try again; check any "
                        "pending send outcome first"
                    )
                )
                raise HTTPException(status, message, headers=PRIVATE) from None
            except ContextDenied:
                raise HTTPException(
                    403,
                    "This email is not available for drafting with the current permissions",
                    headers=PRIVATE,
                ) from None
            except (AIProviderNotConfigured, GatewayUnavailable):
                raise HTTPException(
                    503,
                    "Reply drafting is unavailable right now. You can still write your reply",
                    headers=PRIVATE,
                ) from None
            except (SecretBrokerError, SQLAlchemyError, TimeoutError):
                raise HTTPException(
                    503,
                    (
                        "Outlook could not complete this request. Reopen Inbox and check any "
                        "pending send outcome"
                    ),
                    headers=PRIVATE,
                ) from None
            response.headers.update(PRIVATE)
            return response

        return private


router = APIRouter(prefix="/outlook", tags=["outlook"], route_class=PrivateOutlookRoute)


def browser_session(request: Request, settings: Settings) -> str:
    if "authorization" in request.headers:
        raise HTTPException(403, "Outlook requires your browser session")
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(401, "Sign in to use Outlook")
    if request.method != "GET" and request.headers.getlist("origin") != [
        settings.web_origin.rstrip("/")
    ]:
        raise HTTPException(403, "This Outlook request came from an unexpected page")
    return hash_session_token(token)


def request_scope(
    request: Request, account: CurrentAccountDependency, settings: SettingsDependency
) -> service.Scope:
    session = browser_session(request, settings)
    if request.headers.getlist("X-NavoX-Account") != [f"{account.workspace.id}:{account.user.id}"]:
        raise HTTPException(409, "Your account changed. Reopen Outlook from Inbox")
    return service.Scope(account.user.id, account.workspace.id, session)


ScopeDependency = Annotated[service.Scope, Depends(request_scope)]


async def read_input[T: BaseModel](request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        raise HTTPException(415, "Use application/json for Outlook requests")
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 100_000:
            raise HTTPException(413, "This Outlook request is too large")
    return model.model_validate_json(bytes(body))


@router.get("/status")
async def outlook_status(
    scope: ScopeDependency, database: DatabaseSession, settings: SettingsDependency
) -> dict[str, object]:
    return await service.status(database, settings, scope)


@router.post("/connect")
async def connect(
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    return await service.start_connect(
        database, settings, scope, await read_input(request, Connect)
    )


@router.get("/callback")
async def callback(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RedirectResponse:
    scope = service.Scope(account.user.id, account.workspace.id, browser_session(request, settings))
    # Reject duplicates instead of letting query parsing choose a different state.
    states, codes = request.query_params.getlist("state"), request.query_params.getlist("code")
    if len(states) != 1 or len(codes) != 1 or request.query_params.getlist("error"):
        await service.authority(database, scope, processing=False)
        return RedirectResponse(
            settings.web_origin.rstrip("/")
            + "/connections?source=outlook&outlook_connection=failed",
            status_code=303,
            headers=PRIVATE,
        )
    if await calendar_microsoft.handles(database, scope.user_id, scope.workspace_id, states[0]):
        await calendar_microsoft.finish(
            database,
            settings,
            scope.user_id,
            scope.workspace_id,
            scope.session_hash,
            state=states[0],
            code=codes[0],
        )
        return RedirectResponse(
            settings.web_origin.rstrip("/") + "/connections?calendar_connection=connected",
            status_code=303,
            headers=PRIVATE,
        )
    await service.finish_connect(database, settings, scope, state=states[0], code=codes[0])
    return RedirectResponse(
        settings.web_origin.rstrip("/")
        + "/connections?source=outlook&outlook_connection=connected",
        status_code=303,
        headers=PRIVATE,
    )


@router.delete("/connections/{connection_id}")
async def disconnect(
    connection_id: UUID,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, bool]:
    await service.disconnect(database, settings, scope, connection_id)
    return {"disconnected": True}


@router.get("/connections/{connection_id}/messages")
async def messages(
    connection_id: UUID,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    return await service.messages(database, settings, scope, connection_id)


@router.get("/connections/{connection_id}/messages/{message_id:path}")
async def message(
    connection_id: UUID,
    message_id: str,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    _, source, _ = await service.read_message(database, settings, scope, connection_id, message_id)
    return service.message_view(source)


@router.get("/drafts")
async def drafts(
    scope: ScopeDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[dict[str, object]]:
    return await service.list_drafts(database, settings, scope)


@router.post("/drafts/preview")
async def preview(
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    return await service.preview(database, settings, scope, await read_input(request, Preview))


@router.patch("/drafts/{draft_id}")
async def revise(
    draft_id: UUID,
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    return await service.revise(
        database, settings, scope, draft_id, await read_input(request, Revise)
    )


@router.delete("/drafts/{draft_id}")
async def discard(
    draft_id: UUID, scope: ScopeDependency, database: DatabaseSession
) -> dict[str, bool]:
    await service.discard(database, scope, draft_id)
    return {"discarded": True}


@router.post("/drafts/{draft_id}/send")
async def send(
    draft_id: UUID,
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    return await service.send(database, settings, scope, draft_id, await read_input(request, Send))


@router.post("/drafts/{draft_id}/check")
async def check(
    draft_id: UUID, scope: ScopeDependency, database: DatabaseSession, settings: SettingsDependency
) -> dict[str, object]:
    return await service.check(database, settings, scope, draft_id)


@router.post("/drafts/{draft_id}/check-replies")
async def check_replies(
    draft_id: UUID,
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    payload = await read_input(request, ReplyCheck)
    return await replies.reconcile(database, settings, scope, draft_id, automatic=payload.automatic)


@router.post("/generate")
async def generate(
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    return await service.generate(database, settings, scope, await read_input(request, Generate))


@router.get("/task-context")
async def task_context(
    scope: ScopeDependency, database: DatabaseSession, settings: SettingsDependency
) -> dict[str, Any]:
    return await tasks.context(database, settings, scope)


@router.post("/task-reviews")
async def task_preview(
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    return await tasks.preview(database, settings, scope, await read_input(request, TaskPreview))


@router.get("/task-reviews/{review_id}")
async def task_review(
    review_id: UUID, scope: ScopeDependency, database: DatabaseSession, settings: SettingsDependency
) -> dict[str, Any]:
    return await tasks.read(database, settings, scope, review_id)


@router.patch("/task-reviews/{review_id}")
async def task_correct(
    review_id: UUID,
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    return await tasks.correct(
        database, settings, scope, review_id, await read_input(request, CorrectionInput)
    )


@router.post("/task-reviews/{review_id}/accept-task")
async def task_accept(
    review_id: UUID,
    request: Request,
    scope: ScopeDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, Any]:
    return await tasks.accept(
        database, settings, scope, review_id, await read_input(request, AcceptInput)
    )


@router.delete("/task-reviews/{review_id}")
async def task_erase(
    review_id: UUID, request: Request, scope: ScopeDependency, database: DatabaseSession
) -> dict[str, object]:
    return await tasks.erase(database, scope, review_id, await read_input(request, EraseInput))


@router.delete("/task-sources/{source_id}")
async def task_forget(
    source_id: UUID, scope: ScopeDependency, database: DatabaseSession
) -> dict[str, object]:
    return await tasks.forget(database, scope, source_id)
