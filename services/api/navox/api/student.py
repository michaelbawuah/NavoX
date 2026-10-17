"""Personal student tools. Reads never invoke a provider or write source data."""

from collections.abc import Awaitable
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from navox.api.auth import (
    CurrentAccount,
    CurrentAccountDependency,
    DatabaseSession,
    SettingsDependency,
)
from navox.api.connector_management import require_origin
from navox.db.student import StudentClassMeeting, StudentFixedBlock, StudentTimetableBreak
from navox.student import service
from navox.student.ics import TimetableImportError
from navox.student.schemas import (
    BreakInput,
    BreakView,
    CourseInput,
    CourseUpdate,
    CourseView,
    ExceptionInput,
    FixedBlockInput,
    FixedBlockUpdate,
    FixedBlockView,
    ImportConfirmInput,
    ImportPreviewInput,
    ImportPreviewView,
    Input,
    LookupView,
    MeetingInput,
    MeetingUpdate,
    MeetingView,
    ProfileInput,
    ProfileView,
    TimetableView,
)

router = APIRouter(prefix="/student", tags=["student"])


def scope(account: CurrentAccount) -> tuple[UUID, UUID]:
    if account.workspace.workspace_type.casefold() != "personal":
        raise HTTPException(403, "Student tools are available in your own personal workspace")
    return account.user.id, account.workspace.id


async def result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except service.StudentError as error:
        raise HTTPException(error.status_code, str(error)) from None
    except (TimetableImportError, ValueError) as error:
        raise HTTPException(422, str(error)) from None


async def read_import[T: BaseModel](request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        raise HTTPException(415, "Use an application/json request")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 6_000_000:
            raise HTTPException(413, "The calendar import request is too large")
        body.extend(chunk)
    try:
        return model.model_validate_json(body)
    except (ValidationError, ValueError, UnicodeError):
        # Standard validation errors include input values; calendar text must not
        # be copied into diagnostics, request logs or error telemetry.
        raise HTTPException(
            422, "Invalid calendar import or missing explicit confirmation"
        ) from None


@router.get("/profile", response_model=ProfileView)
async def get_profile(account: CurrentAccountDependency, database: DatabaseSession) -> ProfileView:
    return await result(service.profile(database, *scope(account)))


@router.put("/profile", response_model=ProfileView)
async def put_profile(
    payload: ProfileInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ProfileView:
    require_origin(request, settings.web_origin)
    return await result(service.save_profile(database, *scope(account), payload))


@router.get("/courses")
async def get_courses(
    account: CurrentAccountDependency, database: DatabaseSession, archived: bool = False
) -> dict[str, list[CourseView]]:
    user_id, workspace_id = scope(account)
    await result(service.owner(database, user_id, workspace_id))
    return {
        "courses": await result(
            service.course_views(database, user_id, workspace_id, archived=archived)
        )
    }


@router.post("/courses", response_model=CourseView, status_code=201)
async def post_course(
    payload: CourseInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CourseView:
    require_origin(request, settings.web_origin)
    return await result(service.save_course(database, *scope(account), payload))


@router.patch("/courses/{course_id}", response_model=CourseView)
async def patch_course(
    course_id: UUID,
    payload: CourseUpdate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CourseView:
    require_origin(request, settings.web_origin)
    return await result(service.save_course(database, *scope(account), payload, course_id))


@router.post("/courses/{course_id}/meetings", response_model=MeetingView, status_code=201)
async def post_meeting(
    course_id: UUID,
    payload: MeetingInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> MeetingView:
    require_origin(request, settings.web_origin)
    return await result(
        service.save_meeting(database, *scope(account), payload, course_id=course_id)
    )


@router.patch("/meetings/{meeting_id}", response_model=MeetingView)
async def patch_meeting(
    meeting_id: UUID,
    payload: MeetingUpdate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> MeetingView:
    require_origin(request, settings.web_origin)
    return await result(
        service.save_meeting(database, *scope(account), payload, meeting_id=meeting_id)
    )


@router.delete("/meetings/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    revision: Annotated[int, Query(ge=1)],
) -> Response:
    require_origin(request, settings.web_origin)
    await result(
        service.remove_record(database, *scope(account), StudentClassMeeting, meeting_id, revision)
    )
    return Response(status_code=204)


@router.put("/meetings/{meeting_id}/exceptions/{day}", response_model=MeetingView)
async def put_exception(
    meeting_id: UUID,
    day: date,
    payload: ExceptionInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> MeetingView:
    require_origin(request, settings.web_origin)
    return await result(service.save_exception(database, *scope(account), meeting_id, day, payload))


@router.delete("/meetings/{meeting_id}/exceptions/{day}", response_model=MeetingView)
async def delete_exception(
    meeting_id: UUID,
    day: date,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    revision: Annotated[int, Query(ge=1)],
) -> MeetingView:
    require_origin(request, settings.web_origin)
    return await result(
        service.save_exception(database, *scope(account), meeting_id, day, None, revision)
    )


@router.get("/fixed-blocks")
async def get_fixed(
    account: CurrentAccountDependency, database: DatabaseSession
) -> dict[str, list[FixedBlockView]]:
    user_id, workspace_id = scope(account)
    await result(service.owner(database, user_id, workspace_id))
    return {"fixed_blocks": await result(service.fixed_views(database, user_id, workspace_id))}


@router.post("/fixed-blocks", response_model=FixedBlockView, status_code=201)
async def post_fixed(
    payload: FixedBlockInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> FixedBlockView:
    require_origin(request, settings.web_origin)
    return await result(service.save_fixed(database, *scope(account), payload))


@router.patch("/fixed-blocks/{identifier}", response_model=FixedBlockView)
async def patch_fixed(
    identifier: UUID,
    payload: FixedBlockUpdate,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> FixedBlockView:
    require_origin(request, settings.web_origin)
    return await result(service.save_fixed(database, *scope(account), payload, identifier))


@router.delete("/fixed-blocks/{identifier}", status_code=204)
async def delete_fixed(
    identifier: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    revision: Annotated[int, Query(ge=1)],
) -> Response:
    require_origin(request, settings.web_origin)
    await result(
        service.remove_record(database, *scope(account), StudentFixedBlock, identifier, revision)
    )
    return Response(status_code=204)


@router.get("/breaks")
async def get_breaks(
    account: CurrentAccountDependency, database: DatabaseSession
) -> dict[str, list[BreakView]]:
    user_id, workspace_id = scope(account)
    await result(service.owner(database, user_id, workspace_id))
    return {"breaks": await result(service.break_views(database, user_id, workspace_id))}


@router.post("/breaks", response_model=BreakView, status_code=201)
async def post_break(
    payload: BreakInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> BreakView:
    require_origin(request, settings.web_origin)
    return await result(service.save_break(database, *scope(account), payload))


@router.delete("/breaks/{identifier}", status_code=204)
async def delete_break(
    identifier: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    revision: Annotated[int, Query(ge=1)],
) -> Response:
    require_origin(request, settings.web_origin)
    await result(
        service.remove_record(
            database, *scope(account), StudentTimetableBreak, identifier, revision
        )
    )
    return Response(status_code=204)


@router.get("/timetable", response_model=TimetableView)
async def get_timetable(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    day: Annotated[date | None, Query(alias="date")] = None,
    days: Annotated[int, Query(ge=1, le=31)] = 7,
) -> TimetableView:
    return await result(service.timetable(database, *scope(account), day=day, days=days))


@router.get("/lookup", response_model=LookupView)
async def get_lookup(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    mode: Literal["now", "next", "at", "day"] = "now",
    day: Annotated[date | None, Query(alias="date")] = None,
    clock: Annotated[
        str | None, Query(alias="time", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    ] = None,
    day_offset: Annotated[int | None, Query(ge=0, le=6)] = None,
    weekday: Annotated[int | None, Query(ge=0, le=6)] = None,
) -> LookupView:
    return await result(
        service.lookup(
            database,
            *scope(account),
            mode=mode,
            day=day,
            clock=clock,
            day_offset=day_offset,
            weekday=weekday,
        )
    )


@router.post("/imports/preview", response_model=ImportPreviewView)
async def post_preview(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ImportPreviewView:
    require_origin(request, settings.web_origin)
    payload = await read_import(request, ImportPreviewInput)
    return await result(service.preview_import(database, *scope(account), payload))


@router.post("/imports/confirm")
async def post_confirm(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    payload = await read_import(request, ImportConfirmInput)
    courses, reused = await result(service.confirm_import(database, *scope(account), payload))
    return {"courses": courses, "reused": reused}


class ResetInput(Input):
    confirmed: Literal[True]
    revision: int = Field(ge=0)
    request_id: UUID

    @field_validator("confirmed", mode="before")
    @classmethod
    def consent(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


@router.post("/data/reset")
async def post_reset(
    payload: ResetInput,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, bool]:
    require_origin(request, settings.web_origin)
    reused = await result(
        service.reset_data(
            database,
            *scope(account),
            revision=payload.revision,
            request_id=payload.request_id,
            settings=settings,
        )
    )
    return {"cleared": True, "reused": reused}
