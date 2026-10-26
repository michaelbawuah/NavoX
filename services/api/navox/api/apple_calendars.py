"""Cookie/account/origin-bound Apple app-specific calendar authorization."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.student import result, scope
from navox.api.student_calendars import (
    PrivateCalendarRoute,
    SessionHashDependency,
    bounded_json,
    enabled,
    expected_account,
    session_hash,
)
from navox.student_calendars import apple
from navox.student_calendars.schemas import CalendarCommand

router = APIRouter(
    prefix="/student/calendars/apple",
    tags=["student calendars"],
    route_class=PrivateCalendarRoute,
    dependencies=[Depends(session_hash), Depends(expected_account)],
)


@router.get("", response_model=apple.AppleCalendarView)
async def view(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> apple.AppleCalendarView:
    return await result(apple.view(database, *scope(account), token_hash, settings))


@router.post("/connect", response_model=apple.AppleCalendarView, dependencies=[Depends(enabled)])
async def connect(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> apple.AppleCalendarView:
    payload = await bounded_json(request, apple.AppleCalendarConnect)
    return await result(apple.connect(database, *scope(account), token_hash, payload, settings))


@router.post("/{grant_id}/disconnect", response_model=apple.AppleCalendarView)
async def disconnect(
    grant_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> apple.AppleCalendarView:
    payload = await bounded_json(request, CalendarCommand)
    return await result(
        apple.disconnect(database, *scope(account), token_hash, grant_id, payload, settings)
    )
