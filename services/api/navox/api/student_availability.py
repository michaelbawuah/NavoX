"""Private explicitly requested availability; never schedules or modifies events."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.student import scope
from navox.api.student_calendars import (
    PRIVATE,
    PrivateCalendarRoute,
    SessionHashDependency,
    bounded_json,
    enabled,
    expected_account,
    session_hash,
)
from navox.student import service as schedule
from navox.student_availability import service
from navox.student_availability.schemas import AvailabilityPreview, AvailabilitySnapshot
from navox.student_calendars import service as calendars
from navox.student_planning import service as planning
from navox.student_planning.schemas import PreferencesView

router = APIRouter(
    prefix="/student/availability",
    tags=["student availability"],
    route_class=PrivateCalendarRoute,
    dependencies=[Depends(session_hash), Depends(expected_account), Depends(enabled)],
)


@router.get("/routine", response_model=PreferencesView)
async def routine(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    token_hash: SessionHashDependency,
) -> PreferencesView:
    try:
        await calendars.boundary(database, *scope(account), token_hash)
        result = await planning.preferences(database, *scope(account))
        # The saved routine shares the fresh owner/session boundary used by
        # previews. No provider request or calendar-purpose grant is required.
        await calendars.boundary(database, *scope(account), token_hash)
        await database.commit()
        return result
    except schedule.StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, SQLAlchemyError, TimeoutError):
        raise HTTPException(
            503, "Your saved routine could not be checked. Try again", headers=PRIVATE
        ) from None


@router.post("/preview", response_model=AvailabilitySnapshot)
async def preview(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> AvailabilitySnapshot:
    payload = await bounded_json(request, AvailabilityPreview)
    assert database.bind is not None
    factory = async_sessionmaker(database.bind, expire_on_commit=False)
    try:
        return await service.preview(factory, settings, *scope(account), token_hash, payload)
    except schedule.StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, SQLAlchemyError, TimeoutError):
        raise HTTPException(
            503, "Availability could not be checked. Try again", headers=PRIVATE
        ) from None
