"""Deterministic recovery proposals; private receipts never authorize an action."""

import json
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from cryptography.fernet import InvalidToken
from pydantic import EmailStr, TypeAdapter
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.connectors.sync_state import database_now, utc
from navox.core.settings import Settings
from navox.coursework import service as coursework
from navox.db.coursework import CourseMaterial
from navox.db.student import StudentCourse
from navox.db.student_planning import StudentPlanningState, StudentStudyBlock, StudentTask
from navox.db.student_recovery import StudentRecoveryPreview
from navox.student import service as schedule
from navox.student.occurrences import project
from navox.student_planning import service as planning
from navox.student_planning.planner import build_plan
from navox.student_planning.schemas import Preferences, TaskView
from navox.student_recovery.schemas import (
    ExtensionInput,
    ExtensionView,
    RecoveryInput,
    RecoveryView,
    RecoveryWork,
)

MAX_RECEIPTS = 1000
MAX_ACTIVE = 20
MAX_MATERIALS = 120
TTL = timedelta(minutes=20)
RETRY_WINDOW = timedelta(days=7)
STALE = "Your recovery sources or available time changed. Review a new recovery proposal"


def fail(message: str, status: int = 409) -> schedule.StudentError:
    return schedule.StudentError(status, message)


def mailbox_in_quote(mailbox: str, quote: str) -> bool:
    """Compare complete parsed mailbox tokens, never a suffix of another address."""
    adapter = TypeAdapter(EmailStr)
    for token in re.findall(r'[^\s<>()\[\],;:"]+@[^\s<>()\[\],;:"]+', quote):
        try:
            candidate = adapter.validate_python(token.rstrip(".!?"))
        except ValueError:
            continue
        if str(candidate) == mailbox:
            return True
    return False


def seal(row: StudentRecoveryPreview, payload: dict[str, Any], settings: Settings) -> str:
    envelope = {
        "version": 1,
        "id": str(row.id),
        "user_id": str(row.user_id),
        "workspace_id": str(row.workspace_id),
        "operation": row.operation,
        "preview_hash": row.preview_hash,
        "source_hash": row.source_hash,
        "payload_hash": row.payload_hash,
        "request_id": str(row.request_id),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "expires_at": utc(row.expires_at).isoformat(),
        "payload": payload,
    }
    try:
        return (
            cipher(settings).encrypt(json.dumps(envelope, separators=(",", ":")).encode()).decode()
        )
    except ConnectorRuntimeError:
        raise fail("Private recovery encryption is not configured", 503) from None


def reveal(row: StudentRecoveryPreview, settings: Settings) -> dict[str, Any]:
    if row.encrypted_payload is None:
        raise fail("This recovery preview was erased. Create a new request", 410)
    try:
        data = json.loads(cipher(settings).decrypt(row.encrypted_payload.encode()))
        if (
            data["version"],
            data["id"],
            data["user_id"],
            data["workspace_id"],
            data["operation"],
            data["preview_hash"],
            data["source_hash"],
            data["payload_hash"],
            data["request_id"],
            data["parent_id"],
            data["expires_at"],
        ) != (
            1,
            str(row.id),
            str(row.user_id),
            str(row.workspace_id),
            row.operation,
            row.preview_hash,
            row.source_hash,
            row.payload_hash,
            str(row.request_id),
            str(row.parent_id) if row.parent_id else None,
            utc(row.expires_at).isoformat(),
        ):
            raise ValueError
        if not isinstance(data["payload"], dict):
            raise ValueError
        return cast(dict[str, Any], data["payload"])
    except (InvalidToken, ConnectorRuntimeError, ValueError, KeyError, TypeError, UnicodeError):
        raise fail("This private recovery preview cannot be decrypted", 503) from None


async def active_course(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, course_id: UUID
) -> StudentCourse:
    row: StudentCourse = await schedule.get_owned(
        database, StudentCourse, course_id, user_id, workspace_id
    )
    if row.archived:
        raise fail("Restore the archived course before preparing recovery work")
    return row


async def checked_task(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, task_id: UUID, revision: int
) -> tuple[TaskView, list[dict[str, object]]]:
    row = await planning.get_task(database, user_id, workspace_id, task_id)
    if row.revision != revision or row.status != "active" or row.remaining_minutes <= 0:
        raise fail("This task changed or is no longer incomplete. Choose its current saved version")
    if row.course_id:
        await active_course(database, user_id, workspace_id, row.course_id)
    view = planning.task_view(row)
    sources = await planning.current_sources(database, user_id, workspace_id, [view])
    if any(source["needs_review"] is True for source in sources):
        raise fail("Review this task's current source before recovery planning")
    return view, sources


async def gather(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    payload: RecoveryInput,
    *,
    start_at: datetime,
    end_at: datetime,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    """Caller holds the shared fresh User lock; every source is owner fenced."""
    user = await schedule.owner(database, user_id, workspace_id)
    zone = ZoneInfo(user.timezone)
    today = now.astimezone(zone).date()
    courses: set[UUID] = set(payload.trigger.course_ids)
    evidence: list[dict[str, object]] = []
    task_sources: list[dict[str, object]] = []
    task_views: list[TaskView] = []
    trigger = payload.trigger
    if trigger.kind == "reported_absence":
        assert trigger.from_day is not None and trigger.through_day is not None
        if not (
            today - timedelta(days=90) <= trigger.from_day <= trigger.through_day <= today
            and (trigger.through_day - trigger.from_day).days < 14
        ):
            raise fail("Choose up to fourteen past absence dates within the last ninety days", 422)
        absence_start = datetime.combine(trigger.from_day, time.min, zone).astimezone(UTC)
        absence_end = datetime.combine(
            trigger.through_day + timedelta(days=1), time.min, zone
        ).astimezone(UTC)
        source = await schedule.state_for_window(
            database, user_id, workspace_id, absence_start, absence_end
        )
        for selected_course_id in courses:
            await active_course(database, user_id, workspace_id, selected_course_id)
        selected = [row for row in source[0] if row.id in courses]
        occurrences, _, _, warnings = project(
            selected,
            [],
            source[2],
            source[3],
            start_at=absence_start,
            end_at=absence_end,
            include_conflicts=False,
        )
        if warnings:
            raise fail(
                "Correct the reported dates' timetable clock-change warnings "
                "before recovery planning"
            )
        evidence = [
            {
                "kind": "student_reported_absence",
                "course_id": str(row.course_id),
                "scheduled_start_at": row.start_at.isoformat(),
                "scheduled_end_at": row.end_at.isoformat(),
                "attendance_verified": False,
            }
            for row in occurrences
            if not row.cancelled
        ]
    elif trigger.kind == "overdue_task":
        assert trigger.task_id is not None and trigger.task_revision is not None
        view, sources = await checked_task(
            database, user_id, workspace_id, trigger.task_id, trigger.task_revision
        )
        if view.due_at is None or view.due_at >= now:
            raise fail(
                "This saved task is not past due. Choose an actually overdue incomplete task", 422
            )
        if view.course_id:
            courses.add(view.course_id)
        task_sources.extend(sources)
        task_views.append(view)
        evidence.append(
            {
                "kind": "saved_overdue_task",
                "task_id": str(view.id),
                "revision": view.revision,
                "saved_due_at": view.due_at.isoformat(),
                "remaining_minutes": view.remaining_minutes,
            }
        )
    else:
        for selected_course_id in courses:
            await active_course(database, user_id, workspace_id, selected_course_id)
        statement = (
            select(StudentStudyBlock)
            .join(StudentTask, StudentTask.id == StudentStudyBlock.task_id)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.outcome_at >= now - timedelta(days=14),
                StudentStudyBlock.outcome_at <= now,
                StudentStudyBlock.status.in_(["done", "took_longer", "skipped"]),
                StudentTask.user_id == user_id,
                StudentTask.workspace_id == workspace_id,
            )
        )
        if courses:
            statement = statement.where(StudentTask.course_id.in_(courses))
        outcomes = list(
            await database.scalars(
                statement.order_by(StudentStudyBlock.outcome_at.desc(), StudentStudyBlock.id).limit(
                    3
                )
            )
        )
        if len(outcomes) != 3 or any(
            row.status != "skipped" or row.actual_minutes != 0 for row in outcomes
        ):
            raise fail(
                "Three consecutive recorded skipped blocks in the last fourteen days "
                "are needed for this trigger",
                422,
            )
        evidence = [
            {
                "kind": "recorded_skipped_block",
                "block_id": str(row.id),
                "task_id": str(row.task_id),
                "revision": row.revision,
                "outcome_at": utc(row.outcome_at).isoformat(),
            }
            for row in outcomes
            if row.outcome_at
        ]

    work: list[RecoveryWork] = []
    transient: list[TaskView] = []
    deadline_source: Literal["student_recorded", "school_assignment", "not_recorded"]
    seen: set[UUID] = set()
    for choice in payload.work:
        if not now < choice.personal_catchup_target <= now + timedelta(days=31):
            raise fail("Choose a future personal catch-up target within thirty-one days", 422)
        if choice.task_id is not None:
            assert choice.task_revision is not None
            view, sources = await checked_task(
                database, user_id, workspace_id, choice.task_id, choice.task_revision
            )
            if choice.course_id is not None and choice.course_id != view.course_id:
                raise fail("This task belongs to a different course", 422)
            task_views.append(view)
            task_sources.extend(sources)
            title, course_id, revision = view.title, view.course_id, view.revision
            official_due = view.due_at
            deadline_source = "student_recorded" if official_due else "not_recorded"
            canonical_source = next(
                (
                    source
                    for source in sources
                    if view.source_item_id is not None
                    and source.get("item_id") == str(view.source_item_id)
                    and source.get("source_kind") != "email"
                ),
                None,
            )
            if canonical_source is not None:
                if canonical_source["course_id"] is not None and canonical_source["course_id"] != (
                    str(view.course_id) if view.course_id else None
                ):
                    raise fail(
                        "Match the task's course to its school source before recovery planning"
                    )
                official_due = cast(datetime | None, canonical_source["due_at"])
                deadline_source = "school_assignment" if official_due else "not_recorded"
            work_id = uuid5(NAMESPACE_URL, "navox-recovery-task:" + str(view.id))
        else:
            assert choice.course_id and choice.material_id and choice.material_revision
            await active_course(database, user_id, workspace_id, choice.course_id)
            material = await coursework.owned_material(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                course_id=choice.course_id,
                material_id=choice.material_id,
            )
            if material.revision != choice.material_revision:
                raise fail("The selected course material changed. Choose its current revision")
            detail = coursework.material_view(material, settings)
            title, course_id, revision = (
                "Review " + detail.title,
                choice.course_id,
                material.revision,
            )
            official_due, deadline_source = None, "not_recorded"
            work_id = uuid5(NAMESPACE_URL, "navox-recovery-material:" + str(material.id))
        if work_id in seen:
            raise fail("Select each recovery work item once", 422)
        seen.add(work_id)
        if course_id:
            courses.add(course_id)
        work.append(
            RecoveryWork(
                id=work_id,
                task_id=choice.task_id,
                material_id=choice.material_id,
                course_id=course_id,
                title=title,
                remaining_minutes=choice.remaining_minutes,
                priority=choice.priority,
                official_due_at=official_due,
                deadline_source=deadline_source,
                official_deadline_status="not_recorded"
                if official_due is None
                else "overdue"
                if official_due <= now
                else "upcoming",
                personal_catchup_target=choice.personal_catchup_target,
                source_revision=revision,
                deadline_notice=(
                    "A personal catch-up target does not change the official deadline "
                    "or grant an extension."
                ),
            )
        )
        transient.append(
            TaskView(
                id=work_id,
                title=title[:200],
                course_id=course_id,
                due_at=choice.personal_catchup_target,
                estimated_minutes=choice.remaining_minutes,
                remaining_minutes=choice.remaining_minutes,
                actual_minutes=0,
                priority=choice.priority,
                revision=revision,
                source_item_id=None,
                source_revision=None,
            )
        )
    for policy in payload.policies:
        if policy.course_id not in courses:
            raise fail("Choose a syllabus policy from a selected recovery course", 422)
        await active_course(database, user_id, workspace_id, policy.course_id)
        await coursework.check_quote(
            database,
            policy.source,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=policy.course_id,
            syllabus=True,
        )

    material_rows = (
        list(
            await database.scalars(
                select(CourseMaterial)
                .where(
                    CourseMaterial.user_id == user_id,
                    CourseMaterial.workspace_id == workspace_id,
                    CourseMaterial.course_id.in_(courses),
                )
                .order_by(CourseMaterial.id)
                .limit(MAX_MATERIALS + 1)
            )
        )
        if courses
        else []
    )
    if len(material_rows) > MAX_MATERIALS:
        raise fail("Too many course materials to verify this recovery proposal safely")
    available = [
        {
            "id": str(row.id),
            "course_id": str(row.course_id),
            "revision": row.revision,
            "title": coursework.material_view(row, settings).title,
            "kind": row.kind,
            "claim": "available_saved_material_only",
        }
        for row in material_rows
    ]
    source = await schedule.state_for_window(database, user_id, workspace_id, start_at, end_at)
    occurrences, fixed, _, warnings = project(
        *source, start_at=start_at, end_at=end_at, include_conflicts=False
    )
    if warnings:
        raise fail("Correct timetable clock-change warnings before proposing recovery time")
    prefs_row = await database.scalar(
        select(StudentPlanningState).where(
            StudentPlanningState.user_id == user_id,
            StudentPlanningState.workspace_id == workspace_id,
        )
    )
    prefs = Preferences.model_validate(prefs_row.preferences) if prefs_row else Preferences()
    study = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.end_at > start_at,
                StudentStudyBlock.start_at < end_at,
            )
            .order_by(StudentStudyBlock.id)
            .limit(621)
        )
    )
    if len(study) > 620:
        raise fail("Too many existing study blocks to propose recovery time safely")
    busy = [
        (row.start_at - timedelta(minutes=row.walk_minutes or 0), row.end_at)
        for row in occurrences
        if not row.cancelled
    ]
    busy.extend((row.start_at, row.end_at) for row in fixed)
    rest = timedelta(minutes=prefs.break_minutes)
    busy.extend((utc(row.start_at) - rest, utc(row.end_at) + rest) for row in study)
    result = build_plan(
        transient, prefs, user.timezone, start_at=start_at, end_at=end_at, busy=busy
    )
    result.warnings.append(
        "Catch-up targets are personal proposals. "
        "Official deadlines and your saved plan remain unchanged."
    )
    if study:
        result.warnings.append(
            "Existing planned study blocks were kept busy; this proposal does not replace them."
        )
    unknowns = [
        "Attendance is student-reported; NavoX has not verified which classes you attended.",
        "Saved material titles do not establish which lecture topics, "
        "slides or announcements you missed.",
        "No late-work or extension permission is inferred from a syllabus quote.",
    ]
    if not payload.policies:
        unknowns.append(
            "No late-work or extension policy quote was selected. "
            "Ask your instructor about the policy."
        )
    if trigger.kind == "reported_absence" and not evidence:
        unknowns.append("No active scheduled classes matched the reported courses and dates.")
    fingerprint = planning.digest(
        {
            "version": 1,
            "timezone": user.timezone,
            "input": payload.model_dump(mode="json", exclude={"request_id"}),
            "evidence": evidence,
            "tasks": [row.model_dump(mode="json") for row in task_views],
            "task_sources": task_sources,
            "preferences": prefs.model_dump(mode="json"),
            "schedule": [
                [row.model_dump(mode="json") for row in sorted(rows, key=lambda item: str(item.id))]
                for rows in source
            ],
            "materials": [
                {
                    "id": str(row.id),
                    "course_id": str(row.course_id),
                    "revision": row.revision,
                    "content_hash": row.content_hash,
                    "kind": row.kind,
                }
                for row in material_rows
            ],
            "existing_study": [planning.block_view(row).model_dump(mode="json") for row in study],
        }
    )
    return fingerprint, {
        "timezone": user.timezone,
        "trigger": trigger,
        "evidence": evidence,
        "available_materials": available,
        "policies": payload.policies,
        "work": work,
        "result": result,
        "unknowns": unknowns,
    }


async def receipt(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    operation: str,
    payload: object,
) -> StudentRecoveryPreview | None:
    row = await database.scalar(
        select(StudentRecoveryPreview).where(
            StudentRecoveryPreview.user_id == user_id,
            StudentRecoveryPreview.workspace_id == workspace_id,
            StudentRecoveryPreview.request_id == request_id,
        )
    )
    if row and (row.operation != operation or row.payload_hash != planning.digest(payload)):
        raise fail("This recovery request ID was already used for different content")
    return row


async def prepare_receipt(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, now: datetime
) -> None:
    await database.execute(
        update(StudentRecoveryPreview)
        .where(
            StudentRecoveryPreview.user_id == user_id,
            StudentRecoveryPreview.workspace_id == workspace_id,
            StudentRecoveryPreview.expires_at <= now,
        )
        .values(encrypted_payload=None)
        .execution_options(synchronize_session=False)
    )
    await database.execute(
        delete(StudentRecoveryPreview)
        .where(
            StudentRecoveryPreview.user_id == user_id,
            StudentRecoveryPreview.workspace_id == workspace_id,
            # Parent/child receipts differ in creation time by at most TTL.
            # Keep every child's idempotency tombstone for at least seven days.
            StudentRecoveryPreview.created_at < now - RETRY_WINDOW - TTL,
        )
        .execution_options(synchronize_session=False)
    )
    await schedule.count_limit(
        database, StudentRecoveryPreview, user_id, workspace_id, MAX_RECEIPTS
    )
    active = list(
        await database.scalars(
            select(StudentRecoveryPreview.id)
            .where(
                StudentRecoveryPreview.user_id == user_id,
                StudentRecoveryPreview.workspace_id == workspace_id,
                StudentRecoveryPreview.encrypted_payload.is_not(None),
                StudentRecoveryPreview.expires_at > now,
            )
            .limit(MAX_ACTIVE)
        )
    )
    if len(active) >= MAX_ACTIVE:
        raise fail("Erase an older recovery preview before creating another", 429)


async def owned_receipt(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, preview_id: UUID
) -> StudentRecoveryPreview:
    return cast(
        StudentRecoveryPreview,
        await schedule.get_owned(
            database, StudentRecoveryPreview, preview_id, user_id, workspace_id
        ),
    )


async def load_recovery(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    row: StudentRecoveryPreview,
    *,
    now: datetime,
) -> RecoveryView:
    if utc(row.expires_at) <= now:
        raise fail("This recovery preview expired. Request a new proposal", 410)
    stored = reveal(row, settings)
    if row.operation != "recovery":
        raise fail("Choose a recovery proposal rather than an extension draft", 422)
    payload = RecoveryInput.model_validate(stored["input"])
    view = RecoveryView.model_validate(stored["view"])
    source_hash, _ = await gather(
        database,
        settings,
        user_id,
        workspace_id,
        payload,
        start_at=view.result.start_at,
        end_at=view.result.end_at,
        now=now,
    )
    if (
        source_hash != row.source_hash
        or any(block.start_at < now for block in view.result.blocks)
        or any(
            work.official_deadline_status == "upcoming"
            and work.official_due_at is not None
            and work.official_due_at <= now
            for work in view.work
        )
    ):
        raise fail(STALE)
    return view.model_copy(update={"server_now": now})


async def preview(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    payload: RecoveryInput,
) -> RecoveryView:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    now = await database_now(database)
    body = payload.model_dump(mode="json")
    previous = await receipt(database, user_id, workspace_id, payload.request_id, "recovery", body)
    if previous:
        return await load_recovery(database, settings, user_id, workspace_id, previous, now=now)
    start = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    start += timedelta(minutes=(5 - start.minute % 5) % 5)
    day = now.astimezone(ZoneInfo(user.timezone)).date()
    end = datetime.combine(
        day + timedelta(days=payload.days), time.min, ZoneInfo(user.timezone)
    ).astimezone(UTC)
    source_hash, values = await gather(
        database, settings, user_id, workspace_id, payload, start_at=start, end_at=end, now=now
    )
    await prepare_receipt(database, user_id, workspace_id, now)
    identifier = uuid4()
    preview_hash = planning.digest(
        {
            "id": str(identifier),
            "source_hash": source_hash,
            "input": body,
            "result": values["result"].model_dump(mode="json"),
        }
    )
    view = RecoveryView(
        preview_id=identifier,
        preview_hash=preview_hash,
        expires_at=now + TTL,
        server_now=now,
        **values,
    )
    row = StudentRecoveryPreview(
        id=identifier,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=payload.request_id,
        operation="recovery",
        payload_hash=planning.digest(body),
        source_hash=source_hash,
        preview_hash=preview_hash,
        expires_at=view.expires_at,
        created_at=now,
    )
    row.encrypted_payload = seal(
        row, {"input": body, "view": view.model_dump(mode="json")}, settings
    )
    database.add(row)
    await database.commit()
    return view


async def draft_values(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    parent: StudentRecoveryPreview,
    payload: ExtensionInput,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    view = await load_recovery(database, settings, user_id, workspace_id, parent, now=now)
    if payload.preview_hash != parent.preview_hash:
        raise fail("The selected recovery preview changed")
    work = next((row for row in view.work if row.id == payload.work_id), None)
    if work is None or work.task_id is None or work.official_due_at is None:
        raise fail(
            "Choose an assignment with a recorded deadline before requesting an extension draft",
            422,
        )
    if work.course_id is None or payload.recipient.course_id != work.course_id:
        raise fail("Choose and confirm the recipient for this assignment's course", 422)
    await active_course(database, user_id, workspace_id, work.course_id)
    if (
        not max(now, work.official_due_at)
        < payload.requested_extension_until
        <= now + timedelta(days=90)
    ):
        raise fail(
            "Choose a requested extension after the recorded deadline and within ninety days", 422
        )
    if payload.recipient.source:
        await coursework.check_quote(
            database,
            payload.recipient.source,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=work.course_id,
        )
        if not mailbox_in_quote(str(payload.recipient.email), payload.recipient.source.quote):
            raise fail("The selected recipient email is not in that exact source quote", 422)
    if payload.policy:
        if payload.policy.course_id != work.course_id or payload.policy.kind != "extension":
            raise fail("Choose an extension policy quote from this assignment's syllabus", 422)
        await coursework.check_quote(
            database,
            payload.policy.source,
            settings,
            user_id=user_id,
            workspace_id=workspace_id,
            course_id=work.course_id,
            syllabus=True,
        )
    target = payload.requested_extension_until.isoformat()
    subject = ("Extension request: " + work.title)[:200]
    body = (
        f"Hello {payload.recipient.name},\n\nI am writing about {work.title}. "
        f"The recorded deadline is {work.official_due_at.isoformat()}.\n\n"
        f"My reason, in my own words: {payload.student_reason}\n\n"
        f"Would an extension until {target} be possible? "
        "I understand this request has not been approved.\n\nThank you."
    )
    notices = [
        "Review and edit this draft. No message or external draft has been created.",
        "A requested extension is not permission to submit late; "
        "the recorded deadline remains unchanged.",
    ]
    if payload.policy is None:
        notices.append(
            "The extension policy is unknown. Ask the recipient whether an extension is possible."
        )
    if payload.recipient.source is None:
        notices.append(
            "The recipient email was explicitly supplied by you "
            "and has not been verified from course material."
        )
    values = {
        "timezone": view.timezone,
        "recipient": str(payload.recipient.email),
        "recipient_name": payload.recipient.name,
        "recipient_source": payload.recipient.source,
        "subject": subject,
        "body": body,
        "student_reported_reason": payload.student_reason,
        "official_due_at": work.official_due_at,
        "deadline_source": work.deadline_source,
        "requested_extension_until": payload.requested_extension_until,
        "policy": payload.policy,
        "notices": notices,
    }
    return planning.digest(
        {"parent": parent.source_hash, "input": payload.model_dump(mode="json")}
    ), values


async def extension_draft(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    preview_id: UUID,
    payload: ExtensionInput,
) -> ExtensionView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    now = await database_now(database)
    body = {"parent_id": str(preview_id), "input": payload.model_dump(mode="json")}
    previous = await receipt(database, user_id, workspace_id, payload.request_id, "extension", body)
    if previous:
        loaded = await load(database, settings, user_id, workspace_id, previous.id, now=now)
        assert isinstance(loaded, ExtensionView)
        return loaded
    parent = await owned_receipt(database, user_id, workspace_id, preview_id)
    source_hash, values = await draft_values(
        database, settings, user_id, workspace_id, parent, payload, now
    )
    await prepare_receipt(database, user_id, workspace_id, now)
    identifier = uuid4()
    preview_hash = planning.digest(
        {"id": str(identifier), "source_hash": source_hash, "input": body}
    )
    view = ExtensionView(
        preview_id=identifier,
        preview_hash=preview_hash,
        expires_at=min(now + TTL, utc(parent.expires_at)),
        **values,
    )
    row = StudentRecoveryPreview(
        id=identifier,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=payload.request_id,
        parent_id=parent.id,
        operation="extension",
        payload_hash=planning.digest(body),
        source_hash=source_hash,
        preview_hash=preview_hash,
        expires_at=view.expires_at,
        created_at=now,
    )
    row.encrypted_payload = seal(
        row,
        {"input": payload.model_dump(mode="json"), "view": view.model_dump(mode="json")},
        settings,
    )
    database.add(row)
    await database.commit()
    return view


async def load(
    database: AsyncSession,
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    preview_id: UUID,
    *,
    now: datetime | None = None,
) -> RecoveryView | ExtensionView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    now = now or await database_now(database)
    row = await owned_receipt(database, user_id, workspace_id, preview_id)
    if row.operation == "recovery":
        return await load_recovery(database, settings, user_id, workspace_id, row, now=now)
    if utc(row.expires_at) <= now:
        raise fail("This extension draft expired. Request a new preview", 410)
    stored = reveal(row, settings)
    if row.parent_id is None:
        raise fail("This extension draft no longer has a recovery source", 410)
    parent = await owned_receipt(database, user_id, workspace_id, row.parent_id)
    source_hash, _ = await draft_values(
        database,
        settings,
        user_id,
        workspace_id,
        parent,
        ExtensionInput.model_validate(stored["input"]),
        now,
    )
    if source_hash != row.source_hash:
        raise fail(STALE)
    return ExtensionView.model_validate(stored["view"])


async def erase(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, preview_id: UUID, preview_hash: str
) -> None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    row = await owned_receipt(database, user_id, workspace_id, preview_id)
    if row.preview_hash != preview_hash:
        raise fail("This recovery preview changed. Refresh before erasing")
    await database.execute(
        update(StudentRecoveryPreview)
        .where(
            StudentRecoveryPreview.user_id == user_id,
            StudentRecoveryPreview.workspace_id == workspace_id,
            (StudentRecoveryPreview.id == row.id) | (StudentRecoveryPreview.parent_id == row.id),
        )
        .values(encrypted_payload=None)
        .execution_options(synchronize_session=False)
    )
    await database.commit()


async def invalidate_recovery_previews(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> None:
    """Caller holds User lock/transaction; remove private copies, retain retry tombstones."""
    await database.execute(
        update(StudentRecoveryPreview)
        .where(
            StudentRecoveryPreview.user_id == user_id,
            StudentRecoveryPreview.workspace_id == workspace_id,
        )
        .values(encrypted_payload=None)
        .execution_options(synchronize_session=False)
    )


async def forget_recovery_data(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Full owner reset helper: caller holds User lock and controls the transaction."""
    await database.execute(
        delete(StudentRecoveryPreview)
        .where(
            StudentRecoveryPreview.user_id == user_id,
            StudentRecoveryPreview.workspace_id == workspace_id,
        )
        .execution_options(synchronize_session=False)
    )


async def purge_expired_recovery_previews(
    database: AsyncSession, *, limit: int = 500, now: datetime | None = None
) -> int:
    """Bounded retention cleanup for a worker transaction; no commit/provider calls.

    Expired receipts cannot be read before cleanup. A periodic caller removes
    ciphertext without waiting for the owner to return. Content-free request IDs
    remain for the seven-day retry window.
    """
    if not 1 <= limit <= 500:
        raise ValueError("Choose a cleanup batch from one to five hundred")
    moment = now or await database_now(database)
    identifiers = list(
        await database.scalars(
            select(StudentRecoveryPreview.id)
            .where(
                StudentRecoveryPreview.expires_at <= moment,
                StudentRecoveryPreview.encrypted_payload.is_not(None),
            )
            .order_by(StudentRecoveryPreview.expires_at, StudentRecoveryPreview.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if not identifiers:
        return 0
    await database.execute(
        update(StudentRecoveryPreview)
        .where(
            StudentRecoveryPreview.id.in_(identifiers),
            StudentRecoveryPreview.expires_at <= moment,
        )
        .values(encrypted_payload=None)
        .execution_options(synchronize_session=False)
    )
    return len(identifiers)
