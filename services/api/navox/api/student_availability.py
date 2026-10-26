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
from navox.connectors.sync_state import database_now
from navox.student import service as schedule
from navox.student_availability import service
from navox.student_availability.ranked import ranked_suggestions
from navox.student_availability.schemas import AvailabilityPreview, AvailabilitySnapshot
from navox.student_availability.selector import SuggestionRequest
from navox.student_availability.suggestion_schemas import (
    AvailabilitySuggestionsRequest,
    AvailabilitySuggestionsView,
)
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


@router.post("/suggestions", response_model=AvailabilitySuggestionsView)
async def suggestions(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    token_hash: SessionHashDependency,
) -> AvailabilitySuggestionsView:
    payload = await bounded_json(request, AvailabilitySuggestionsRequest)
    preview_payload = AvailabilityPreview.model_validate(payload.model_dump(exclude={"purpose"}))
    assert database.bind is not None
    factory = async_sessionmaker(database.bind, expire_on_commit=False)
    user_id, workspace_id = scope(account)
    try:
        # Preserve the exact starting grant inventory as well as the fresh
        # preview service's own checks around provider I/O. Nothing submitted by
        # the client is accepted as a snapshot, exam or other-person window.
        async with factory() as boundary_database:
            original_scope = await calendars.scope_snapshot(
                boundary_database, user_id, workspace_id, token_hash
            )
            await boundary_database.commit()
        snapshot = await service.preview(
            factory, settings, user_id, workspace_id, token_hash, preview_payload
        )
        async with factory() as boundary_database:
            await calendars.check_scope(boundary_database, original_scope, token_hash)
            # Reuse the same internal source-digest boundary as the preview
            # service. The owner/session locks above serialize source changes
            # through publication; the source service remains the sole reader.
            current = await service._internal(
                boundary_database, user_id, workspace_id, preview_payload, settings=settings
            )
            if (
                current.digest != snapshot.revision.student_revision
                or current.preferences_revision != snapshot.revision.preferences_revision
                or current.planning_revision != snapshot.revision.planning_revision
                or original_scope.generation != snapshot.revision.availability_generation
                or original_scope.revision != snapshot.revision.availability_revision
                or snapshot.input.user_id != user_id
                or snapshot.input.workspace_id != workspace_id
            ):
                raise schedule.StudentError(
                    409, "Your calendar, routine or study plan changed. Check again"
                )
            now = await database_now(boundary_database)
            if now < snapshot.checked_at or now >= snapshot.expires_at:
                raise schedule.StudentError(409, "This availability check expired. Check again")
            # BusyInterval intentionally carries no exam/deadline meaning or
            # professor hours. Do not invent those optional ranking inputs.
            selection = await ranked_suggestions(
                boundary_database,
                user_id,
                workspace_id,
                snapshot,
                SuggestionRequest(
                    availability=snapshot.result,
                    now=now,
                    purpose=payload.purpose,
                    duration_minutes=payload.duration_minutes,
                    count=2,
                ),
                allow_study_displacement=settings.student_planning_enabled,
            )
            await calendars.check_scope(boundary_database, original_scope, token_hash)
            final = await service._internal(
                boundary_database, user_id, workspace_id, preview_payload, settings=settings
            )
            if (
                final.digest != current.digest
                or final.preferences_revision != current.preferences_revision
                or final.planning_revision != current.planning_revision
            ):
                raise schedule.StudentError(
                    409, "Your calendar, routine or study plan changed. Check again"
                )
            if await database_now(boundary_database) >= snapshot.expires_at:
                raise schedule.StudentError(409, "This availability check expired. Check again")
            result = AvailabilitySuggestionsView(snapshot=snapshot, selection=selection)
            await boundary_database.commit()
            return result
    except schedule.StudentError as error:
        raise HTTPException(error.status_code, str(error), headers=PRIVATE) from None
    except (ValueError, SQLAlchemyError, TimeoutError):
        raise HTTPException(
            503, "Meeting times could not be checked. Try again", headers=PRIVATE
        ) from None
