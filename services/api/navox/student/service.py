"""Owner-scoped timetable persistence and server-clock queries."""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.db.models import AuditEvent, User, Workspace, WorkspaceMembership
from navox.db.student import (
    StudentClassMeeting,
    StudentCourse,
    StudentFixedBlock,
    StudentImportPreview,
    StudentMeetingException,
    StudentProfile,
    StudentTimetableBreak,
)
from navox.student.ics import PARSER_VERSION, parse_timetable_ics
from navox.student.occurrences import (
    TRUNCATED_CONFLICTS_WARNING,
    ClassProjectionIssue,
    local_moment,
    occurs_on,
    project,
    select_lookup,
)
from navox.student.schemas import (
    MAX_BREAKS,
    MAX_COURSES,
    MAX_FIXED_BLOCKS,
    MAX_MEETINGS,
    BreakInput,
    BreakView,
    CourseInput,
    CourseUpdate,
    CourseView,
    ExceptionInput,
    ExceptionView,
    FixedBlockInput,
    FixedBlockUpdate,
    FixedBlockView,
    ImportCandidate,
    ImportConfirmInput,
    ImportPreviewInput,
    ImportPreviewView,
    LookupView,
    MeetingInput,
    MeetingUpdate,
    MeetingView,
    Occurrence,
    ProfileInput,
    ProfileView,
    TimetableView,
)


class StudentError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


async def owner(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, lock: bool = False
) -> User:
    # All student writers and reset serialize on the fresh User row. Membership
    # reads after this lock are nonlocking to avoid reversing other domains' order.
    query = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    user = await database.scalar(query)
    authorized = await database.scalar(
        select(WorkspaceMembership.user_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.role == "owner",
            Workspace.workspace_type == "personal",
        )
    )
    if user is None or authorized is None:
        raise StudentError(403, "Student tools are available in your own personal workspace")
    return user


def audit(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    event: str,
    entity_id: UUID,
    metadata: dict[str, Any] | None = None,
) -> None:
    add_audit_event(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        event_type=event,
        entity_type="student",
        entity_id=entity_id,
        actor_type="user",
        actor_id=str(user_id),
        metadata=metadata or {},
    )


def check_revision(actual: int, expected: int) -> None:
    if actual != expected:
        raise StudentError(409, "This schedule changed. Refresh before saving again.")


async def count_limit(
    database: AsyncSession,
    model: Any,
    user_id: UUID,
    workspace_id: UUID,
    limit: int,
    addition: int = 1,
) -> None:
    count = await database.scalar(
        select(func.count())
        .select_from(model)
        .where(model.user_id == user_id, model.workspace_id == workspace_id)
    )
    if (count or 0) + addition > limit:
        raise StudentError(
            409,
            f"The limit of {limit} saved records was reached. Review your existing schedule.",
        )


async def get_owned(
    database: AsyncSession, model: Any, identifier: UUID, user_id: UUID, workspace_id: UUID
) -> Any:
    row = await database.scalar(
        select(model)
        .where(model.id == identifier, model.user_id == user_id, model.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise StudentError(404, "Student record not found")
    return row


async def profile(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> ProfileView:
    user = await owner(database, user_id, workspace_id)
    row = await database.scalar(
        select(StudentProfile).where(
            StudentProfile.user_id == user_id, StudentProfile.workspace_id == workspace_id
        )
    )
    if row is None:
        return ProfileView(
            school_name=None,
            lms="none",
            display_name=None,
            timezone=user.timezone,
            term_start=None,
            term_end=None,
            revision=0,
        )
    return ProfileView(
        school_name=row.school_name,
        lms=cast(Any, row.lms),
        display_name=row.display_name,
        timezone=user.timezone,
        term_start=row.term_start,
        term_end=row.term_end,
        revision=row.revision,
    )


async def save_profile(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: ProfileInput
) -> ProfileView:
    user = await owner(database, user_id, workspace_id, lock=True)
    row = await database.scalar(
        select(StudentProfile).where(
            StudentProfile.user_id == user_id, StudentProfile.workspace_id == workspace_id
        )
    )
    check_revision(row.revision if row else 0, payload.revision)
    if row is None:
        row = StudentProfile(id=uuid4(), user_id=user_id, workspace_id=workspace_id, revision=0)
        database.add(row)
    for key, value in payload.model_dump(exclude={"revision", "timezone"}).items():
        setattr(row, key, value)
    user.timezone = payload.timezone
    row.revision += 1
    audit(database, user_id, workspace_id, "student.profile.saved", row.id)
    await database.commit()
    return await profile(database, user_id, workspace_id)


async def course_views(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, *, archived: bool | None = None
) -> list[CourseView]:
    query = select(StudentCourse).where(
        StudentCourse.user_id == user_id, StudentCourse.workspace_id == workspace_id
    )
    if archived is not None:
        query = query.where(StudentCourse.archived == archived)
    rows = list(
        await database.scalars(
            query.order_by(StudentCourse.created_at, StudentCourse.id).limit(MAX_COURSES + 1)
        )
    )
    if len(rows) > MAX_COURSES:
        raise StudentError(409, "Too many courses to project safely")
    meetings = list(
        await database.scalars(
            select(StudentClassMeeting)
            .join(StudentCourse, StudentCourse.id == StudentClassMeeting.course_id)
            .where(
                StudentClassMeeting.user_id == user_id,
                StudentClassMeeting.workspace_id == workspace_id,
                StudentCourse.user_id == user_id,
                StudentCourse.workspace_id == workspace_id,
            )
            .order_by(StudentClassMeeting.id)
            .limit(MAX_MEETINGS + 1)
        )
    )
    if len(meetings) > MAX_MEETINGS:
        raise StudentError(409, "Too many class sections to project safely")
    return [
        CourseView(
            id=row.id,
            code=row.code,
            name=row.name,
            professor=row.professor,
            term_start=row.term_start,
            term_end=row.term_end,
            archived=row.archived,
            revision=row.revision,
            meetings=[
                MeetingView.model_validate(meeting)
                for meeting in meetings
                if meeting.course_id == row.id
            ],
        )
        for row in rows
    ]


async def _course_view(database: AsyncSession, row: StudentCourse) -> CourseView:
    courses = await course_views(database, row.user_id, row.workspace_id)
    return next(item for item in courses if item.id == row.id)


async def save_course(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: CourseInput,
    course_id: UUID | None = None,
) -> CourseView:
    await owner(database, user_id, workspace_id, lock=True)
    if course_id is None:
        await count_limit(database, StudentCourse, user_id, workspace_id, MAX_COURSES)
        row = StudentCourse(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            revision=1,
            archived=False,
            **payload.model_dump(),
        )
        database.add(row)
    else:
        row = cast(
            StudentCourse,
            await get_owned(database, StudentCourse, course_id, user_id, workspace_id),
        )
        if not isinstance(payload, CourseUpdate):
            raise StudentError(422, "A course revision is required")
        check_revision(row.revision, payload.revision)
        # A shortened term may exclude classes, but never silently destroys them.
        for key, value in payload.model_dump(exclude={"revision"}).items():
            setattr(row, key, value)
        row.revision += 1
    audit(
        database, user_id, workspace_id, "student.course.saved", row.id, {"archived": row.archived}
    )
    await database.commit()
    return await _course_view(database, row)


async def save_meeting(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: MeetingInput,
    *,
    course_id: UUID | None = None,
    meeting_id: UUID | None = None,
) -> MeetingView:
    await owner(database, user_id, workspace_id, lock=True)
    if meeting_id is None:
        if course_id is None:
            raise StudentError(422, "Choose a course")
        course = cast(
            StudentCourse,
            await get_owned(database, StudentCourse, course_id, user_id, workspace_id),
        )
        if course.archived:
            raise StudentError(409, "Restore this course before adding a section")
        await count_limit(database, StudentClassMeeting, user_id, workspace_id, MAX_MEETINGS)
        row = StudentClassMeeting(
            id=uuid4(),
            course_id=course_id,
            user_id=user_id,
            workspace_id=workspace_id,
            dates=None,
            revision=1,
            **payload.model_dump(),
        )
        database.add(row)
    else:
        row = cast(
            StudentClassMeeting,
            await get_owned(database, StudentClassMeeting, meeting_id, user_id, workspace_id),
        )
        if not isinstance(payload, MeetingUpdate):
            raise StudentError(422, "A section revision is required")
        check_revision(row.revision, payload.revision)
        if row.dates is not None and payload.weekdays != row.weekdays:
            raise StudentError(
                409,
                "Imported sections keep their reviewed dates. "
                "Add a recurring section to change its days.",
            )
        for key, value in payload.model_dump(exclude={"revision"}).items():
            setattr(row, key, value)
        row.revision += 1
    audit(database, user_id, workspace_id, "student.meeting.saved", row.id)
    await database.commit()
    return MeetingView.model_validate(row)


async def remove_record(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    model: Any,
    identifier: UUID,
    revision: int,
) -> None:
    await owner(database, user_id, workspace_id, lock=True)
    row = await get_owned(database, model, identifier, user_id, workspace_id)
    check_revision(row.revision, revision)
    if model is StudentClassMeeting:
        await database.execute(
            delete(StudentMeetingException).where(
                StudentMeetingException.meeting_id == identifier,
                StudentMeetingException.user_id == user_id,
                StudentMeetingException.workspace_id == workspace_id,
            )
        )
    await database.delete(row)
    audit(database, user_id, workspace_id, "student.record.deleted", identifier)
    await database.commit()


async def save_exception(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    meeting_id: UUID,
    day: date,
    payload: ExceptionInput | None,
    revision: int | None = None,
) -> MeetingView:
    await owner(database, user_id, workspace_id, lock=True)
    meeting = cast(
        StudentClassMeeting,
        await get_owned(database, StudentClassMeeting, meeting_id, user_id, workspace_id),
    )
    course = cast(
        StudentCourse,
        await get_owned(database, StudentCourse, meeting.course_id, user_id, workspace_id),
    )
    check_revision(meeting.revision, payload.revision if payload else (revision or 0))
    if not occurs_on(
        MeetingView.model_validate(meeting), day, await _course_view(database, course)
    ):
        raise StudentError(422, "There is no class meeting on that original date")
    edit = await database.scalar(
        select(StudentMeetingException).where(
            StudentMeetingException.meeting_id == meeting_id,
            StudentMeetingException.date == day,
            StudentMeetingException.user_id == user_id,
            StudentMeetingException.workspace_id == workspace_id,
        )
    )
    if payload is None:
        if edit is not None:
            await database.delete(edit)
    else:
        if not payload.cancelled:
            for clock in (
                payload.start_time or meeting.start_time,
                payload.end_time or meeting.end_time,
            ):
                local_moment(day, clock, meeting.timezone, strict=True)
        if edit is None:
            edit = StudentMeetingException(
                id=uuid4(),
                meeting_id=meeting_id,
                user_id=user_id,
                workspace_id=workspace_id,
                date=day,
            )
            database.add(edit)
        for key, value in payload.model_dump(exclude={"revision"}).items():
            setattr(edit, key, value)
    meeting.revision += 1
    audit(database, user_id, workspace_id, "student.meeting.exception_saved", meeting.id)
    await database.commit()
    return MeetingView.model_validate(meeting)


async def fixed_views(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> list[FixedBlockView]:
    rows = await database.scalars(
        select(StudentFixedBlock)
        .where(StudentFixedBlock.user_id == user_id, StudentFixedBlock.workspace_id == workspace_id)
        .order_by(StudentFixedBlock.id)
        .limit(MAX_FIXED_BLOCKS)
    )
    return [FixedBlockView.model_validate(row) for row in rows]


async def save_fixed(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: FixedBlockInput,
    identifier: UUID | None = None,
) -> FixedBlockView:
    await owner(database, user_id, workspace_id, lock=True)
    if identifier is None:
        await count_limit(database, StudentFixedBlock, user_id, workspace_id, MAX_FIXED_BLOCKS)
        row = StudentFixedBlock(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            revision=1,
            **payload.model_dump(),
        )
        database.add(row)
    else:
        row = cast(
            StudentFixedBlock,
            await get_owned(database, StudentFixedBlock, identifier, user_id, workspace_id),
        )
        if not isinstance(payload, FixedBlockUpdate):
            raise StudentError(422, "A work-block revision is required")
        check_revision(row.revision, payload.revision)
        for key, value in payload.model_dump(exclude={"revision"}).items():
            setattr(row, key, value)
        row.revision += 1
    audit(database, user_id, workspace_id, "student.fixed_block.saved", row.id)
    await database.commit()
    return FixedBlockView.model_validate(row)


async def break_views(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> list[BreakView]:
    rows = await database.scalars(
        select(StudentTimetableBreak)
        .where(
            StudentTimetableBreak.user_id == user_id,
            StudentTimetableBreak.workspace_id == workspace_id,
        )
        .order_by(StudentTimetableBreak.start_date)
        .limit(MAX_BREAKS)
    )
    return [BreakView.model_validate(row) for row in rows]


async def save_break(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: BreakInput
) -> BreakView:
    await owner(database, user_id, workspace_id, lock=True)
    await count_limit(database, StudentTimetableBreak, user_id, workspace_id, MAX_BREAKS)
    row = StudentTimetableBreak(
        id=uuid4(), user_id=user_id, workspace_id=workspace_id, revision=1, **payload.model_dump()
    )
    database.add(row)
    audit(database, user_id, workspace_id, "student.break.saved", row.id)
    await database.commit()
    return BreakView.model_validate(row)


async def state_for_window(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, start_at: datetime, end_at: datetime
) -> tuple[list[CourseView], list[FixedBlockView], list[ExceptionView], list[BreakView]]:
    courses = await course_views(database, user_id, workspace_id)
    fixed = await fixed_views(database, user_id, workspace_id)
    breaks = await break_views(database, user_id, workspace_id)
    rows = await database.scalars(
        select(StudentMeetingException)
        .where(
            StudentMeetingException.user_id == user_id,
            StudentMeetingException.workspace_id == workspace_id,
            StudentMeetingException.date >= start_at.date() - timedelta(days=2),
            StudentMeetingException.date <= end_at.date() + timedelta(days=2),
        )
        .limit(10_000)
    )
    edits = [ExceptionView.model_validate(row) for row in rows]
    return courses, fixed, edits, breaks


async def list_occurrences(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    start_at: datetime,
    end_at: datetime,
    include_cancelled: bool = False,
    class_issues: list[ClassProjectionIssue] | None = None,
) -> list[Occurrence]:
    await owner(database, user_id, workspace_id)
    courses, fixed, edits, breaks = await state_for_window(
        database, user_id, workspace_id, start_at, end_at
    )
    result, _, _, _ = project(
        courses,
        [],
        edits,
        breaks,
        start_at=start_at,
        end_at=end_at,
        include_cancelled=include_cancelled,
        include_conflicts=False,
        class_issues=class_issues,
    )
    return result


async def timetable(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    day: date | None = None,
    days: int = 7,
    now: datetime | None = None,
    class_issues: list[ClassProjectionIssue] | None = None,
) -> TimetableView:
    user = await owner(database, user_id, workspace_id)
    moment = now or await database_now(database)
    zone = ZoneInfo(user.timezone)
    selected = day or moment.astimezone(zone).date()
    if not 1 <= days <= 31:
        raise StudentError(422, "Choose between 1 and 31 days")
    start = datetime.combine(selected, time.min, zone).astimezone(UTC)
    end = datetime.combine(selected + timedelta(days=days), time.min, zone).astimezone(UTC)
    courses, fixed, edits, breaks = await state_for_window(
        database, user_id, workspace_id, start, end
    )
    items, fixed_items, conflicts, warnings = project(
        courses, fixed, edits, breaks, start_at=start, end_at=end, class_issues=class_issues
    )
    return TimetableView(
        server_now=moment,
        timezone=user.timezone,
        date=selected,
        days=days,
        courses=courses,
        fixed_blocks=fixed,
        breaks=breaks,
        exceptions=edits,
        occurrences=items,
        fixed_occurrences=fixed_items,
        conflicts=conflicts,
        conflicts_truncated=TRUNCATED_CONFLICTS_WARNING in warnings,
        warnings=warnings,
    )


async def lookup(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    mode: Literal["now", "next", "at", "day"],
    day: date | None = None,
    clock: str | None = None,
    day_offset: int | None = None,
    weekday: int | None = None,
    now: datetime | None = None,
) -> LookupView:
    user = await owner(database, user_id, workspace_id)
    moment = now or await database_now(database)
    zone = ZoneInfo(user.timezone)
    today = moment.astimezone(zone).date()
    if sum(value is not None for value in (day, day_offset, weekday)) > 1:
        raise StudentError(422, "Choose one date selector")
    if mode in {"now", "next"}:
        selected, at = today, moment
    else:
        selected = day or (today + timedelta(days=day_offset or 0))
        if weekday is not None:
            selected = today + timedelta(days=(weekday - today.weekday()) % 7)
        if mode == "at":
            if clock is None:
                raise StudentError(422, "Choose a time for this class lookup")
            try:
                at_value = local_moment(selected, clock, user.timezone, strict=True)
            except ValueError as error:
                raise StudentError(422, str(error)) from None
            if at_value is None:
                raise StudentError(422, "This local time does not exist")
            at = at_value
        else:
            at = datetime.combine(selected, time.min, zone).astimezone(UTC)
    class_issues: list[ClassProjectionIssue] = []
    view = await timetable(
        database,
        user_id,
        workspace_id,
        day=selected,
        days=1,
        now=moment,
        class_issues=class_issues,
    )
    if class_issues:
        raise incomplete_lookup_error(class_issues)
    matches = select_lookup(
        view.occurrences, mode=mode, at=at, day=selected, timezone=user.timezone
    )
    upcoming: Occurrence | None = None
    # At most 367 days and 12 bounded projections; no provider/model work.
    search_from = (
        at if mode != "day" else datetime.combine(selected, time.min, zone).astimezone(UTC)
    )
    for offset in range(0, 367, 31):
        start = search_from + timedelta(days=offset)
        end = search_from + timedelta(days=min(offset + 31, 367))
        search_issues: list[ClassProjectionIssue] = []
        values = await list_occurrences(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            start_at=start,
            end_at=end,
            class_issues=search_issues,
        )
        upcoming = next(
            (item for item in values if not item.cancelled and item.start_at > at), None
        )
        # A later valid class is not necessarily next if an earlier active
        # meeting could not be projected. A known candidate before the affected
        # local day remains trustworthy.
        relevant_issues = [
            issue
            for issue in search_issues
            if upcoming is None or issue.day_start <= upcoming.start_at
        ]
        if relevant_issues:
            raise incomplete_lookup_error(relevant_issues)
        if upcoming is not None:
            break
        # Empty courses cannot produce a later class.
        if not view.courses:
            break
    reason: Literal["matches", "no_classes", "after_last_class", "break"] = (
        "matches" if matches else "no_classes"
    )
    if not matches and any(item.start_date <= selected <= item.end_date for item in view.breaks):
        reason = "break"
    elif not matches and any(not item.cancelled and item.end_at <= at for item in view.occurrences):
        reason = "after_last_class"
    return LookupView(
        server_now=moment,
        timezone=user.timezone,
        mode=mode,
        date=selected,
        at=at,
        occurrences=matches,
        next_upcoming=upcoming,
        reason=reason,
        conflicts=view.conflicts,
    )


def incomplete_lookup_error(issues: list[ClassProjectionIssue]) -> StudentError:
    first = min(issues, key=lambda issue: (issue.day_start, issue.timezone))
    return StudentError(
        409,
        f"A class on {first.day.isoformat()} ({first.timezone}) cannot be placed safely "
        "across a clock change. Add a one-day time correction in Classes, then ask again.",
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


async def preview_import(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: ImportPreviewInput
) -> ImportPreviewView:
    user = await owner(database, user_id, workspace_id, lock=True)
    now = await database_now(database)
    await database.execute(
        delete(StudentImportPreview).where(
            StudentImportPreview.user_id == user_id,
            StudentImportPreview.workspace_id == workspace_id,
            StudentImportPreview.expires_at <= now,
            StudentImportPreview.confirmed_at.is_(None),
        )
    )
    await count_limit(database, StudentImportPreview, user_id, workspace_id, 100)
    candidates, warnings = parse_timetable_ics(
        payload.content,
        timezone=user.timezone,
        term_start=payload.term_start,
        term_end=payload.term_end,
    )
    normalized = [item.model_dump(mode="json") for item in candidates]
    digest = _digest(
        [
            PARSER_VERSION,
            str(user_id),
            str(workspace_id),
            user.timezone,
            str(payload.term_start),
            str(payload.term_end),
            normalized,
        ]
    )
    row = StudentImportPreview(
        id=uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        preview_hash=digest,
        timezone=user.timezone,
        term_start=payload.term_start,
        term_end=payload.term_end,
        candidates=normalized,
        expires_at=now + timedelta(minutes=30),
    )
    database.add(row)
    await database.commit()
    return ImportPreviewView(
        preview_id=row.id,
        preview_hash=digest,
        expires_at=row.expires_at,
        timezone=row.timezone,
        candidates=candidates,
        warnings=warnings,
    )


async def confirm_import(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: ImportConfirmInput
) -> tuple[list[CourseView], bool]:
    user = await owner(database, user_id, workspace_id, lock=True)
    row = cast(
        StudentImportPreview,
        await get_owned(database, StudentImportPreview, payload.preview_id, user_id, workspace_id),
    )
    digest = _digest([item.model_dump(mode="json") for item in payload.candidates])
    if row.confirmed_at is not None:
        if (
            row.request_id != payload.request_id
            or row.confirm_hash != digest
            or row.preview_hash != payload.preview_hash
        ):
            raise StudentError(409, "This import preview was already used")
        return [
            item
            for item in await course_views(database, user_id, workspace_id)
            if str(item.id) in (row.course_ids or [])
        ], True
    now = await database_now(database)
    if (
        row.preview_hash != payload.preview_hash
        or row.timezone != user.timezone
        or utc(row.expires_at) <= now
    ):
        raise StudentError(409, "The preview expired or changed. Review the calendar again.")
    previous = await database.scalar(
        select(StudentImportPreview.id).where(
            StudentImportPreview.user_id == user_id,
            StudentImportPreview.workspace_id == workspace_id,
            StudentImportPreview.request_id == payload.request_id,
        )
    )
    if previous is not None:
        raise StudentError(409, "This request identifier was already used")
    original = {item["id"]: ImportCandidate.model_validate(item) for item in row.candidates}
    seen: set[str] = set()
    for candidate in payload.candidates:
        source = original.get(candidate.id)
        if source is None or candidate.id in seen:
            raise StudentError(422, "Each reviewed class must match one imported candidate")
        seen.add(candidate.id)
        if (candidate.dates, candidate.start_time, candidate.end_time, candidate.timezone) != (
            source.dates,
            source.start_time,
            source.end_time,
            source.timezone,
        ):
            raise StudentError(
                409, "Imported times or dates changed. Add a manual section to change the schedule."
            )
    groups: dict[tuple[str, str], list[ImportCandidate]] = {}
    existing_import = await database.scalar(
        select(StudentClassMeeting.id)
        .where(
            StudentClassMeeting.user_id == user_id,
            StudentClassMeeting.workspace_id == workspace_id,
            StudentClassMeeting.import_series_key.in_(
                [item.id.split(".", 1)[0] for item in payload.candidates]
            ),
        )
        .limit(1)
    )
    if existing_import is not None:
        raise StudentError(
            409,
            "This calendar series is already saved. Edit its existing class "
            "or remove its sections before importing an update.",
        )
    for candidate in payload.candidates:
        key = (candidate.course_code.casefold(), candidate.course_name.casefold())
        groups.setdefault(key, []).append(candidate)
    await count_limit(database, StudentCourse, user_id, workspace_id, MAX_COURSES, len(groups))
    await count_limit(
        database, StudentClassMeeting, user_id, workspace_id, MAX_MEETINGS, len(payload.candidates)
    )
    identifiers: list[str] = []
    for candidates in groups.values():
        first = candidates[0]
        course = StudentCourse(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            code=first.course_code,
            name=first.course_name,
            professor=None,
            term_start=row.term_start,
            term_end=row.term_end,
            archived=False,
            revision=1,
        )
        database.add(course)
        await database.flush()
        identifiers.append(str(course.id))
        for candidate in candidates:
            database.add(
                StudentClassMeeting(
                    id=uuid4(),
                    course_id=course.id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    kind=candidate.kind,
                    weekdays=sorted({day.weekday() for day in candidate.dates}),
                    dates=[str(day) for day in candidate.dates],
                    import_key=candidate.id,
                    import_series_key=candidate.id.split(".", 1)[0],
                    start_time=candidate.start_time,
                    end_time=candidate.end_time,
                    timezone=candidate.timezone,
                    building=candidate.building,
                    room=candidate.room,
                    instructor=candidate.instructor,
                    walk_minutes=None,
                    revision=1,
                )
            )
    row.request_id = payload.request_id
    row.confirm_hash = digest
    row.course_ids = identifiers
    row.confirmed_at = now
    row.candidates = []  # No second retained copy of confirmed class facts.
    audit(
        database,
        user_id,
        workspace_id,
        "student.import.confirmed",
        row.id,
        {"count": len(payload.candidates)},
    )
    await database.commit()
    return [
        item
        for item in await course_views(database, user_id, workspace_id)
        if str(item.id) in identifiers
    ], False


async def reset_data(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    revision: int,
    request_id: UUID,
    settings: Settings,
) -> bool:
    from navox.connectors.secrets import SecretBrokerError
    from navox.coursework.service import clear_coursework_data
    from navox.lms_feeds.service import (
        FeedAccessError,
        erase_all_owned_feeds,
        lock_all_feeds_for_reset,
    )
    from navox.student_email.service import forget_email_data
    from navox.student_goals.service import forget_goals_data
    from navox.student_planning.service import forget_planning_data
    from navox.student_recovery.service import forget_recovery_data

    # Feed workers use connector→membership→User. Lock their authorities before
    # the shared student writer lock, then forget everything in one transaction.
    try:
        feed_connections = await lock_all_feeds_for_reset(
            database, user_id=user_id, workspace_id=workspace_id
        )
    except FeedAccessError as error:
        raise StudentError(error.status, str(error)) from None
    await owner(database, user_id, workspace_id, lock=True)
    previous = await database.scalar(
        select(AuditEvent.id).where(
            AuditEvent.user_id == user_id,
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.event_type == "student.data.reset",
            AuditEvent.event_metadata["request_id"].as_string() == str(request_id),
        )
    )
    if previous is not None:
        return True
    current = await profile(database, user_id, workspace_id)
    check_revision(current.revision, revision)
    try:
        await erase_all_owned_feeds(
            database,
            user_id=user_id,
            workspace_id=workspace_id,
            settings=settings,
            connection_ids=feed_connections,
            now=await database_now(database),
        )
    except FeedAccessError as error:
        raise StudentError(error.status, str(error)) from None
    except SecretBrokerError:
        raise StudentError(
            409, "The saved calendar connection needs review before resetting data"
        ) from None
    await forget_email_data(database, user_id=user_id, workspace_id=workspace_id)
    await forget_planning_data(database, user_id, workspace_id)
    await forget_recovery_data(database, user_id, workspace_id)
    await forget_goals_data(database, user_id, workspace_id)
    await clear_coursework_data(database, user_id=user_id, workspace_id=workspace_id)
    from navox.student_alerts.service import clear_student_alert_data

    await clear_student_alert_data(database, user_id=user_id, workspace_id=workspace_id)
    for model in (
        StudentMeetingException,
        StudentClassMeeting,
        StudentCourse,
        StudentFixedBlock,
        StudentTimetableBreak,
        StudentImportPreview,
        StudentProfile,
    ):
        await database.execute(
            delete(model).where(model.user_id == user_id, model.workspace_id == workspace_id)
        )
    audit(
        database,
        user_id,
        workspace_id,
        "student.data.reset",
        user_id,
        {"request_id": str(request_id)},
    )
    await database.commit()
    return False
