"""Cookie/account-bound calendar-purpose consent. No calendar writes."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from navox.api.auth import (
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
    hash_session_token,
)
from navox.api.student import result, scope
from navox.student_calendars import microsoft, oauth, scheduling, service
from navox.student_calendars.schemas import (
    CalendarAuthorization,
    CalendarCommand,
    CalendarEventApproval,
    CalendarEventCommand,
    CalendarEventPreview,
    CalendarEventView,
    CalendarSchedulingStart,
    CalendarSchedulingView,
    CalendarStart,
    CalendarTargetsRequest,
    CalendarTargetsView,
    CalendarView,
)

PRIVATE = {"Cache-Control": "private, no-store"}


class PrivateCalendarRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def private(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "The calendar request is invalid"},
                    headers=PRIVATE,
                )
            except HTTPException as error:
                raise HTTPException(
                    error.status_code, error.detail, headers={**(error.headers or {}), **PRIVATE}
                ) from None
            except (SQLAlchemyError, TimeoutError):
                # Database exception strings can contain bound private values.
                raise HTTPException(
                    503, "Calendar permissions could not be checked. Try again", headers=PRIVATE
                ) from None
            response.headers.update(PRIVATE)
            return response

        return private


def session_hash(request: Request, settings: SettingsDependency) -> str:
    if "authorization" in request.headers:
        raise HTTPException(
            403, "Calendar availability requires a browser session", headers=PRIVATE
        )
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(401, "Authentication required", headers=PRIVATE)
    if request.method != "GET":
        if request.headers.getlist("origin") != [settings.web_origin.rstrip("/")]:
            raise HTTPException(403, "The calendar request origin is invalid", headers=PRIVATE)
    return hash_session_token(token)


def expected_account(request: Request, account: CurrentAccountDependency) -> None:
    if request.headers.getlist("X-NavoX-Account") != [f"{account.workspace.id}:{account.user.id}"]:
        raise HTTPException(
            409, "The calendar account changed. Refresh and try again", headers=PRIVATE
        )


SessionHashDependency = Annotated[str, Depends(session_hash)]
ExpectedAccountDependency = Annotated[None, Depends(expected_account)]


def enabled(settings: SettingsDependency) -> None:
    if not settings.student_calendar_availability_enabled:
        raise HTTPException(404, "Calendar availability is not enabled", headers=PRIVATE)


def enabled_writes(settings: SettingsDependency) -> None:
    enabled(settings)
    if not getattr(settings, "student_calendar_writes_enabled", False):
        raise HTTPException(404, "Calendar scheduling is not enabled", headers=PRIVATE)


async def bounded_json[T: BaseModel](request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        raise HTTPException(415, "Use application/json for calendar requests", headers=PRIVATE)
    try:
        declared = request.headers.get("content-length")
        if declared is not None and not 0 <= int(declared) <= 4096:
            raise HTTPException(413, "The calendar request exceeds its size limit", headers=PRIVATE)
        body = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 4096:
                    raise HTTPException(
                        413, "The calendar request exceeds its size limit", headers=PRIVATE
                    )
                body.extend(chunk)

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        return model.model_validate(json.loads(body, object_pairs_hook=unique))
    except TimeoutError:
        raise HTTPException(408, "The calendar request took too long", headers=PRIVATE) from None
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "The calendar request is invalid", headers=PRIVATE) from None


router = APIRouter(
    prefix="/student/calendars",
    tags=["student calendars"],
    route_class=PrivateCalendarRoute,
    dependencies=[Depends(session_hash), Depends(expected_account)],
)


@router.get("", response_model=CalendarView, dependencies=[Depends(enabled)])
async def calendars(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarView:
    return await result(service.view(database, *scope(account), token_hash, settings))


@router.post("/google/start", response_model=CalendarAuthorization, dependencies=[Depends(enabled)])
async def start_google(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarAuthorization:
    payload = await bounded_json(request, CalendarStart)
    return await result(oauth.start(database, *scope(account), token_hash, payload, settings))


@router.post(
    "/outlook/start", response_model=CalendarAuthorization, dependencies=[Depends(enabled)]
)
async def start_outlook(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarAuthorization:
    payload = await bounded_json(request, CalendarStart)
    return await result(microsoft.start(database, *scope(account), token_hash, payload, settings))


@router.post("/{grant_id}/disconnect", response_model=CalendarView)
async def disconnect(
    grant_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarView:
    # Withdrawing purpose consent works while the release is disabled or paused.
    payload = await bounded_json(request, CalendarCommand)
    return await result(
        service.disconnect(database, *scope(account), token_hash, grant_id, payload, settings)
    )


@router.get("/scheduling", response_model=CalendarSchedulingView)
async def scheduling_permissions(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarSchedulingView:
    return await result(scheduling.scheduling_view(database, *scope(account), token_hash, settings))


@router.post(
    "/google/scheduling/start",
    response_model=CalendarAuthorization,
    dependencies=[Depends(enabled_writes)],
)
async def start_scheduling(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarAuthorization:
    payload = await bounded_json(request, CalendarSchedulingStart)
    return await result(
        oauth.start(database, *scope(account), token_hash, payload, settings, scheduling=True)
    )


@router.post("/scheduling/{grant_id}/disconnect", response_model=CalendarSchedulingView)
async def stop_scheduling(
    grant_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarSchedulingView:
    payload = await bounded_json(request, CalendarCommand)
    return await result(
        scheduling.disconnect(database, *scope(account), token_hash, grant_id, payload, settings)
    )


def factory(database: DatabaseSession) -> async_sessionmaker[Any]:
    assert database.bind is not None
    return async_sessionmaker(database.bind, expire_on_commit=False)


@router.post(
    "/scheduling/targets",
    response_model=CalendarTargetsView,
    dependencies=[Depends(enabled_writes)],
)
async def scheduling_targets(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarTargetsView:
    payload = await bounded_json(request, CalendarTargetsRequest)
    return await result(
        scheduling.targets(factory(database), settings, *scope(account), token_hash, payload)
    )


@router.get("/events")
async def calendar_events(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
    cursor: UUID | None = None,
) -> dict[str, Any]:
    return await result(
        scheduling.list_events(
            database,
            *scope(account),
            token_hash,
            settings,
            cursor=cursor,
        )
    )


@router.post(
    "/events/preview", response_model=CalendarEventView, dependencies=[Depends(enabled_writes)]
)
async def event_preview(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarEventView:
    payload = await bounded_json(request, CalendarEventPreview)
    return await result(
        scheduling.preview(factory(database), settings, *scope(account), token_hash, payload)
    )


@router.get("/events/{event_id}", response_model=CalendarEventView)
async def calendar_event(
    event_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarEventView:
    return await result(
        scheduling.event_view(
            database,
            *scope(account),
            token_hash,
            event_id,
            settings,
        )
    )


@router.post(
    "/events/{event_id}/approve",
    response_model=CalendarEventView,
    dependencies=[Depends(enabled_writes)],
)
async def event_approve(
    event_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarEventView:
    payload = await bounded_json(request, CalendarEventApproval)
    return await result(
        scheduling.approve(
            factory(database), settings, *scope(account), token_hash, event_id, payload
        )
    )


@router.post("/events/{event_id}/reconcile", response_model=CalendarEventView)
async def event_reconcile(
    event_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarEventView:
    payload = await bounded_json(request, CalendarEventCommand)
    return await result(
        scheduling.reconcile(
            factory(database), settings, *scope(account), token_hash, event_id, payload
        )
    )


@router.post(
    "/events/{event_id}/undo",
    response_model=CalendarEventView,
    dependencies=[Depends(enabled_writes)],
)
async def event_undo(
    event_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> CalendarEventView:
    payload = await bounded_json(request, CalendarEventApproval)
    return await result(
        scheduling.undo(factory(database), settings, *scope(account), token_hash, event_id, payload)
    )
