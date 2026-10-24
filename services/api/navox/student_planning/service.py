"""Owner-serialized study plans with exact-source acceptance and reversible outcomes."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from typing import TypeVar
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.db.lms_feeds import LmsFeedItem
from navox.db.student import StudentCourse
from navox.db.student_planning import (
    StudentPlanningCommand,
    StudentPlanningPreview,
    StudentPlanningState,
    StudentStudyBlock,
    StudentTask,
)
from navox.student import service as schedule
from navox.student.occurrences import project
from navox.student_planning.planner import build_plan
from navox.student_planning.schemas import (
    MAX_TASKS,
    AcceptInput,
    BlockView,
    OutcomeInput,
    OutcomeView,
    PlanResult,
    PlanView,
    Preferences,
    PreferencesUpdate,
    PreferencesView,
    PreviewInput,
    PreviewView,
    TaskCreate,
    TaskImport,
    TaskUpdate,
    TaskView,
    UndoInput,
)

T = TypeVar("T", bound=BaseModel)
REVIEW_NOTICE = (
    "Your tasks, routine or timetable changed. Preview and accept an updated plan "
    "before recording progress."
)
SOURCE_REVIEW_FIELDS = {
    "source_current_revision",
    "source_current_title",
    "source_current_due_at",
    "source_needs_review",
    "source_review_notice",
    "source_reviewable",
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def task_view(row: StudentTask) -> TaskView:
    return TaskView.model_validate(
        {
            **{
                name: getattr(row, name)
                for name in TaskView.model_fields
                if name not in SOURCE_REVIEW_FIELDS
            },
            "due_at": utc(row.due_at) if row.due_at else None,
        }
    )


def block_view(row: StudentStudyBlock) -> BlockView:
    return BlockView.model_validate(
        dict(
            id=row.id,
            plan_id=row.plan_id,
            task_id=row.task_id,
            title=row.title,
            start_at=utc(row.start_at),
            end_at=utc(row.end_at),
            minutes=row.minutes,
            focus_window=row.focus_window,
            revision=row.revision,
            status=row.status,
            actual_minutes=row.actual_minutes,
            outcome_at=utc(row.outcome_at) if row.outcome_at else None,
        )
    )


async def state(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> StudentPlanningState:
    row = await database.scalar(
        select(StudentPlanningState)
        .where(
            StudentPlanningState.user_id == user_id,
            StudentPlanningState.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = StudentPlanningState(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            revision=0,
            preferences_revision=0,
            needs_review=False,
            preferences=Preferences().model_dump(mode="json"),
        )
        database.add(row)
        await database.flush()
    return row


async def command(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    action: str,
    payload: object,
) -> StudentPlanningCommand | None:
    row = await database.scalar(
        select(StudentPlanningCommand).where(
            StudentPlanningCommand.user_id == user_id,
            StudentPlanningCommand.workspace_id == workspace_id,
            StudentPlanningCommand.request_id == request_id,
        )
    )
    if row is not None and (row.action != action or row.payload_hash != digest(payload)):
        raise schedule.StudentError(409, "This request ID was already used for a different change")
    if row is not None and row.result.get("invalidated") is True:
        raise schedule.StudentError(
            409, "This previous change included forgotten data. Refresh your saved tasks"
        )
    return row


async def remember(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    action: str,
    payload: object,
    response: BaseModel | dict[str, object],
    *,
    undo: dict[str, object] | None = None,
) -> None:
    # A bounded seven-day idempotency window; never prune study or completion history.
    now = await database_now(database)
    await database.execute(
        delete(StudentPlanningCommand)
        .where(
            StudentPlanningCommand.user_id == user_id,
            StudentPlanningCommand.workspace_id == workspace_id,
            StudentPlanningCommand.created_at < now - timedelta(days=7),
        )
        .execution_options(synchronize_session=False)
    )
    await schedule.count_limit(database, StudentPlanningCommand, user_id, workspace_id, 2000)
    database.add(
        StudentPlanningCommand(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            action=action,
            payload_hash=digest(payload),
            result=response.model_dump(mode="json")
            if isinstance(response, BaseModel)
            else response,
            undo_snapshot=undo,
            created_at=now,
        )
    )


async def get_task(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, task_id: UUID
) -> StudentTask:
    return await schedule.get_owned(database, StudentTask, task_id, user_id, workspace_id)  # type: ignore[no-any-return]


async def tasks(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    *,
    include_source_review: bool = False,
) -> list[TaskView]:
    rows = list(
        await database.scalars(
            select(StudentTask)
            .where(StudentTask.user_id == user_id, StudentTask.workspace_id == workspace_id)
            .order_by(StudentTask.created_at, StudentTask.id)
            .limit(MAX_TASKS + 1)
        )
    )
    if len(rows) > MAX_TASKS:
        raise schedule.StudentError(409, "Too many saved tasks to plan safely")
    views = [task_view(row) for row in rows]
    if not include_source_review:
        return views
    return await reviewed_task_views(database, user_id, workspace_id, views)


async def reviewed_task_views(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    views: list[TaskView],
) -> list[TaskView]:
    states = {
        UUID(str(item["task_id"])): item
        for item in await current_sources(database, user_id, workspace_id, views)
    }
    result = []
    for view in views:
        source = states.get(view.id)
        if source is None:
            result.append(view)
            continue
        allowed = source["authorized"] is True
        if source.get("source_kind") == "email":
            result.append(
                view.model_copy(
                    update={
                        "source_needs_review": source["needs_review"],
                        "source_reviewable": False,
                        "source_current_revision": None,
                        "source_current_title": None,
                        "source_current_due_at": None,
                        "source_review_notice": "This email source changed or is unavailable. "
                        "Open Email actions to refresh this existing task, or archive it."
                        if source["needs_review"]
                        else None,
                    }
                )
            )
            continue
        notice = None
        if source["needs_review"]:
            notice = (
                "The school assignment source is unavailable. Reconnect it, or archive this task."
                if not allowed
                else "The school assignment was canceled. Archive this task before planning."
                if source["cancelled"]
                else "The school's feeds disagree. Review the feed sources, or archive this task."
                if source["conflicted"]
                else "The school assignment changed. Review its current details before planning."
            )
        result.append(
            view.model_copy(
                update={
                    "source_current_revision": source["current_revision"] if allowed else None,
                    "source_current_title": source["title"] if allowed else None,
                    "source_current_due_at": source["due_at"] if allowed else None,
                    "source_needs_review": source["needs_review"],
                    "source_reviewable": allowed
                    and not source["cancelled"]
                    and not source["conflicted"],
                    "source_review_notice": notice,
                }
            )
        )
    return result


async def eligible_tasks(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    views: list[TaskView],
    *,
    include_stale_email: bool = False,
) -> list[TaskView]:
    """Archived course work stays saved, but is not an active planning input."""
    course_ids = {row.course_id for row in views if row.course_id is not None}
    active_ids = (
        set(
            await database.scalars(
                select(StudentCourse.id).where(
                    StudentCourse.user_id == user_id,
                    StudentCourse.workspace_id == workspace_id,
                    StudentCourse.id.in_(course_ids),
                    StudentCourse.archived.is_(False),
                )
            )
        )
        if course_ids
        else set()
    )
    eligible = [row for row in views if row.course_id is None or row.course_id in active_ids]
    if include_stale_email:
        return eligible
    from navox.student_email.service import email_task_snapshots

    snapshots = await email_task_snapshots(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        task_ids=[row.id for row in eligible if row.status == "active"],
    )
    stale = {str(row["task_id"]) for row in snapshots if row["needs_review"] is True}
    return [row for row in eligible if str(row.id) not in stale]


async def current_sources(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    views: list[TaskView],
) -> list[dict[str, object]]:
    from navox.lms_feeds.service import item_sources_authorized

    ids = [row.source_item_id for row in views if row.source_item_id is not None]
    rows = (
        list(
            await database.scalars(
                select(LmsFeedItem).where(
                    LmsFeedItem.user_id == user_id,
                    LmsFeedItem.workspace_id == workspace_id,
                    LmsFeedItem.id.in_(ids),
                )
            )
        )
        if ids
        else []
    )
    items = {row.id: row for row in rows}
    result: list[dict[str, object]] = []
    for view in sorted(views, key=lambda row: str(row.id)):
        if view.source_item_id is None:
            continue
        item = items.get(view.source_item_id)
        authorized = item is not None and await item_sources_authorized(
            database, item, require_active=False
        )
        due = None
        if item and authorized and isinstance(item.payload.get("due_at"), str):
            try:
                due = utc(datetime.fromisoformat(str(item.payload["due_at"])))
            except ValueError:
                pass
        result.append(
            {
                "task_id": str(view.id),
                "item_id": str(view.source_item_id),
                "current_revision": item.revision if item else None,
                "payload_hash": digest(item.payload) if item else None,
                "course_id": str(item.course_id) if item and item.course_id else None,
                "authorized": bool(authorized),
                "cancelled": item.cancelled if item else True,
                "conflicted": item.conflicted if item else False,
                "title": str(item.payload.get("title", "")) if item and authorized else None,
                "due_at": due,
                "needs_review": item is None
                or not authorized
                or item.cancelled
                or item.conflicted
                or item.revision != view.source_revision,
            }
        )
    from navox.student_email.service import email_task_snapshots

    email_sources = await email_task_snapshots(
        database, user_id=user_id, workspace_id=workspace_id, task_ids=[view.id for view in views]
    )
    result.extend({**source, "source_kind": "email"} for source in email_sources)
    return sorted(result, key=lambda source: str(source["task_id"]))


async def _course(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, course_id: UUID | None
) -> None:
    if course_id is not None:
        course: StudentCourse = await schedule.get_owned(
            database, StudentCourse, course_id, user_id, workspace_id
        )
        if course.archived:
            raise schedule.StudentError(409, "Choose an active course")


async def save_task(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: TaskCreate | TaskUpdate | TaskImport,
    task_id: UUID | None = None,
) -> TaskView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    action = "task.update" if task_id else "task.create"
    body = {"task_id": str(task_id), "payload": payload.model_dump(mode="json")}
    previous = await command(database, user_id, workspace_id, payload.request_id, action, body)
    if previous:
        current = await get_task(database, user_id, workspace_id, UUID(str(previous.result["id"])))
        return (await reviewed_task_views(database, user_id, workspace_id, [task_view(current)]))[0]
    await _course(database, user_id, workspace_id, payload.course_id)
    remaining = (
        payload.remaining_minutes
        if payload.remaining_minutes is not None
        else payload.estimated_minutes
    )
    if payload.status == "active" and remaining == 0:
        raise schedule.StudentError(422, "A completed task should have status completed")
    if payload.status == "completed" and remaining != 0:
        raise schedule.StudentError(422, "Set remaining work to zero before completing this task")
    if task_id:
        row = await get_task(database, user_id, workspace_id, task_id)
        if not isinstance(payload, TaskUpdate) or row.revision != payload.revision:
            raise schedule.StudentError(409, "This task changed. Refresh before saving again")
        row.revision += 1
        if payload.reviewed_source_revision is not None:
            from navox.lms_feeds.service import FeedAccessError, owned_item

            if row.source_item_id is None:
                raise schedule.StudentError(422, "This task has no linked school assignment")
            try:
                source = await owned_item(
                    database,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    item_id=row.source_item_id,
                    require_active=False,
                )
            except FeedAccessError as error:
                raise schedule.StudentError(error.status, str(error)) from None
            if (
                source.cancelled
                or source.conflicted
                or source.revision != payload.reviewed_source_revision
            ):
                raise schedule.StudentError(
                    409,
                    "The assignment source changed. Review its current details or archive the task",
                )
            row.source_revision = source.revision
    else:
        await schedule.count_limit(database, StudentTask, user_id, workspace_id, MAX_TASKS)
        row = StudentTask(
            id=uuid4(), user_id=user_id, workspace_id=workspace_id, revision=1, actual_minutes=0
        )
        if isinstance(payload, TaskImport):
            from navox.lms_feeds.service import FeedAccessError, owned_item

            try:
                item = await owned_item(
                    database,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    item_id=payload.source_item_id,
                    require_active=False,
                )
            except FeedAccessError as error:
                raise schedule.StudentError(error.status, str(error)) from None
            if item.cancelled or item.conflicted:
                raise schedule.StudentError(
                    409, "Review this assignment's source before adding a task"
                )
            if item.revision != payload.source_revision:
                raise schedule.StudentError(
                    409, "This assignment changed. Review it again before adding a task"
                )
            identity = digest(item.identity_key)
            duplicate = await database.scalar(
                select(StudentTask.id).where(
                    StudentTask.user_id == user_id,
                    StudentTask.workspace_id == workspace_id,
                    StudentTask.source_identity == identity,
                )
            )
            if duplicate:
                raise schedule.StudentError(
                    409, "This assignment already has a task. Edit the existing task"
                )
            row.source_item_id, row.source_identity, row.source_revision = (
                item.id,
                identity,
                item.revision,
            )
        database.add(row)
    for key, value in payload.model_dump(
        exclude={
            "request_id",
            "revision",
            "source_item_id",
            "source_revision",
            "confirmed",
            "remaining_minutes",
            "reviewed_source_revision",
            "confirmed_source",
        }
    ).items():
        setattr(row, key, value)
    row.remaining_minutes = remaining
    planning = await state(database, user_id, workspace_id)
    planning.revision += 1
    await invalidate_future(database, user_id, workspace_id, planning)
    await database.flush()
    result = (await reviewed_task_views(database, user_id, workspace_id, [task_view(row)]))[0]
    await remember(database, user_id, workspace_id, payload.request_id, action, body, result)
    schedule.audit(database, user_id, workspace_id, "student.task.saved", row.id)
    await database.commit()
    return result


async def remove_task(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    task_id: UUID,
    *,
    revision: int,
    request_id: UUID,
) -> None:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    body = {"task_id": str(task_id), "revision": revision}
    if await command(database, user_id, workspace_id, request_id, "task.delete", body):
        return
    row = await get_task(database, user_id, workspace_id, task_id)
    if row.revision != revision:
        raise schedule.StudentError(409, "This task changed. Refresh before forgetting it")
    # Remove all derived copies, including old preview/outcome snapshots containing titles.
    from navox.vox.service import invalidate_active_context

    await invalidate_active_context(database, user_id, workspace_id)
    await invalidate_snapshots(database, user_id, workspace_id)
    await database.execute(
        delete(StudentStudyBlock).where(
            StudentStudyBlock.user_id == user_id,
            StudentStudyBlock.workspace_id == workspace_id,
            StudentStudyBlock.task_id == task_id,
        )
    )
    await database.delete(row)
    planning = await state(database, user_id, workspace_id)
    planning.revision += 1
    await invalidate_future(database, user_id, workspace_id, planning)
    planning.accepted_source_hash = None
    await remember(database, user_id, workspace_id, request_id, "task.delete", body, {})
    schedule.audit(database, user_id, workspace_id, "student.task.forgotten", task_id)
    await database.commit()


async def preferences(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> PreferencesView:
    user = await schedule.owner(database, user_id, workspace_id)
    row = await database.scalar(
        select(StudentPlanningState).where(
            StudentPlanningState.user_id == user_id,
            StudentPlanningState.workspace_id == workspace_id,
        )
    )
    return PreferencesView(
        **(row.preferences if row else Preferences().model_dump()),
        revision=row.preferences_revision if row else 0,
        timezone=user.timezone,
    )


def scheduling_preferences(value: dict) -> dict:
    # Alarm sounds, enabled days and snooze do not invalidate accepted study work.
    # Omitting new defaults preserves source hashes of pre-redesign plans.
    result = {key: item for key, item in value.items() if key != "alarm"}
    if result.get("wake_times") is None:
        result.pop("wake_times", None)
    return result


async def save_preferences(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: PreferencesUpdate
) -> PreferencesView:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    body = payload.model_dump(mode="json")
    previous = await command(
        database, user_id, workspace_id, payload.request_id, "preferences", body
    )
    if previous:
        return await preferences(database, user_id, workspace_id)
    row = await state(database, user_id, workspace_id)
    if row.preferences_revision != payload.revision:
        raise schedule.StudentError(409, "Your routine changed. Refresh before saving again")
    candidate = payload.model_dump(mode="json", exclude={"request_id", "revision"})
    routine_changed = scheduling_preferences(row.preferences) != scheduling_preferences(candidate)
    row.preferences = candidate
    row.preferences_revision += 1
    if routine_changed:
        row.revision += 1
        await invalidate_future(database, user_id, workspace_id, row)
    result = PreferencesView.model_validate(
        {
            **row.preferences,
            "revision": row.preferences_revision,
            "timezone": user.timezone,
        }
    )
    await remember(database, user_id, workspace_id, payload.request_id, "preferences", body, result)
    await database.commit()
    return result


async def stable_source_hash(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    planning: StudentPlanningState,
) -> str:
    """Saved-plan freshness excludes clock passage and mutable plan bookkeeping.

    Source projection is bounded to the accepted horizon. Owner-entered course
    definitions and all saved tasks are intentionally included conservatively.
    """
    if planning.window_start is None or planning.window_end is None:
        raise schedule.StudentError(409, "Preview and accept a study plan first")
    user = await schedule.owner(database, user_id, workspace_id)
    source = await schedule.state_for_window(
        database, user_id, workspace_id, utc(planning.window_start), utc(planning.window_end)
    )
    task_rows = await tasks(database, user_id, workspace_id)
    eligible = await eligible_tasks(database, user_id, workspace_id, task_rows)
    return digest(
        {
            "version": 1,
            "timezone": user.timezone,
            "preferences": scheduling_preferences(planning.preferences),
            "tasks": [row.model_dump(mode="json") for row in task_rows],
            "eligible_tasks": sorted(str(row.id) for row in eligible),
            "current_sources": await current_sources(
                database,
                user_id,
                workspace_id,
                [row for row in eligible if row.status == "active"],
            ),
            "schedule": [
                [
                    row.model_dump(mode="json")
                    for row in sorted(collection, key=lambda row: str(row.model_dump()["id"]))
                ]
                for collection in source
            ],
        }
    )


async def require_current_plan(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    planning: StudentPlanningState,
) -> None:
    if (
        planning.needs_review
        or planning.accepted_source_hash is None
        or (
            planning.accepted_source_hash
            != await stable_source_hash(database, user_id, workspace_id, planning)
        )
    ):
        raise schedule.StudentError(409, REVIEW_NOTICE)


async def invalidate_future(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    planning: StudentPlanningState,
) -> None:
    """Called under User lock; past/ongoing blocks and actual history stay intact."""
    if planning.window_end is None and planning.accepted_source_hash is None:
        return
    planning.needs_review = True
    now = await database_now(database)
    future = list(
        await database.scalars(
            select(StudentStudyBlock).where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.start_at >= now,
            )
        )
    )
    for row in future:
        row.status = "superseded"
        row.revision += 1


async def plan_view(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> PlanView:
    await schedule.owner(database, user_id, workspace_id)
    row = await database.scalar(
        select(StudentPlanningState).where(
            StudentPlanningState.user_id == user_id,
            StudentPlanningState.workspace_id == workspace_id,
        )
    )
    now = await database_now(database)
    needs_review = False
    if row is not None and row.window_end is not None:
        needs_review = (
            row.needs_review
            or row.accepted_source_hash is None
            or (
                row.accepted_source_hash
                != await stable_source_hash(database, user_id, workspace_id, row)
            )
        )
    active = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.end_at > now,
            )
            .order_by(StudentStudyBlock.start_at, StudentStudyBlock.id)
            .limit(620)
        )
    )
    history = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                or_(StudentStudyBlock.status != "planned", StudentStudyBlock.end_at <= now),
            )
            .order_by(
                StudentStudyBlock.outcome_at.desc().nulls_last(),
                StudentStudyBlock.start_at.desc(),
                StudentStudyBlock.id,
            )
            .limit(201)
        )
    )
    visible = [
        block_view(block)
        for block in sorted(
            [*active, *history[:200]], key=lambda item: (utc(item.start_at), str(item.id))
        )
    ]
    if needs_review:
        visible = [
            block.model_copy(update={"status": "needs_review"})
            if block.status == "planned" and block.start_at >= utc(now)
            else block
            for block in visible
        ]
    return PlanView(
        revision=row.revision if row else 0,
        blocks=visible,
        has_more_history=len(history) > 200,
        needs_review=needs_review,
        review_notice=REVIEW_NOTICE if needs_review else None,
    )


async def snapshot(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    planning: StudentPlanningState,
    *,
    start_at: datetime,
    end_at: datetime,
    now: datetime,
) -> tuple[str, PlanResult]:
    user = await schedule.owner(database, user_id, workspace_id)
    source = await schedule.state_for_window(database, user_id, workspace_id, start_at, end_at)
    courses, fixed, edits, breaks = source
    occurrences, fixed_occurrences, _, warnings = project(
        courses,
        fixed,
        edits,
        breaks,
        start_at=start_at,
        end_at=end_at,
        include_conflicts=False,
    )
    if warnings:
        raise schedule.StudentError(
            409, "Correct timetable clock-change warnings before planning this window"
        )
    task_rows = await tasks(database, user_id, workspace_id)
    eligible = await eligible_tasks(database, user_id, workspace_id, task_rows)
    reviewable = await eligible_tasks(
        database, user_id, workspace_id, task_rows, include_stale_email=True
    )
    source_states = await current_sources(
        database, user_id, workspace_id, [row for row in reviewable if row.status == "active"]
    )
    if any(source["needs_review"] is True for source in source_states):
        raise schedule.StudentError(
            409,
            "A saved task source changed or is unavailable. "
            "Review its task source or archive it before planning",
        )
    history = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.end_at > now,
                StudentStudyBlock.start_at < end_at,
                or_(
                    (StudentStudyBlock.status == "planned") & (StudentStudyBlock.start_at < now),
                    StudentStudyBlock.status == "skipped",
                ),
            )
            .order_by(StudentStudyBlock.start_at, StudentStudyBlock.id)
            .limit(621)
        )
    )
    if len(history) > 620:
        raise schedule.StudentError(409, "Too many preserved study windows to replan safely")
    busy = [
        (row.start_at - timedelta(minutes=row.walk_minutes or 0), row.end_at)
        for row in occurrences
        if not row.cancelled
    ]
    busy.extend((row.start_at, row.end_at) for row in fixed_occurrences)
    prefs = Preferences.model_validate(planning.preferences)
    busy.extend(
        (utc(row.start_at), utc(row.end_at) + timedelta(minutes=prefs.break_minutes))
        for row in history
    )
    fingerprint = digest(
        {
            "version": 1,
            "revision": planning.revision,
            "timezone": user.timezone,
            "preferences": scheduling_preferences(planning.preferences),
            "tasks": [row.model_dump(mode="json") for row in task_rows],
            "eligible_tasks": sorted(str(row.id) for row in eligible),
            "current_sources": source_states,
            "schedule": [
                [
                    row.model_dump(mode="json")
                    for row in sorted(collection, key=lambda row: str(row.model_dump()["id"]))
                ]
                for collection in source
            ],
            "preserved": [block_view(row).model_dump(mode="json") for row in history],
        }
    )
    allocated: dict[UUID, int] = {}
    for block in history:
        if block.status == "planned":
            allocated[block.task_id] = allocated.get(block.task_id, 0) + block.minutes
    planning_tasks = [
        task.model_copy(
            update={
                "remaining_minutes": max(0, task.remaining_minutes - allocated.get(task.id, 0)),
            }
        )
        for task in eligible
    ]
    result = build_plan(
        planning_tasks, prefs, user.timezone, start_at=start_at, end_at=end_at, busy=busy
    )
    if any(block.status == "planned" for block in history):
        result.warnings.append(
            "Your in-progress study block stays in place; its planned work is already allocated."
        )
    if any(block.status == "skipped" for block in history):
        result.warnings.append("Skipped study windows stay free; unfinished work moves later.")
    return fingerprint, result


async def preview(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: PreviewInput,
    *,
    now: datetime | None = None,
) -> PreviewView:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    moment = utc(now or await database_now(database))
    zone = ZoneInfo(user.timezone)
    day = payload.day or moment.astimezone(zone).date()
    if day < moment.astimezone(zone).date() or day > moment.astimezone(zone).date() + timedelta(
        days=31
    ):
        raise schedule.StudentError(422, "Choose today or a date within the next 31 days")
    start = max(
        datetime.combine(day, time.min, zone).astimezone(UTC), moment + timedelta(minutes=1)
    )
    start = start.replace(second=0, microsecond=0) + timedelta(minutes=(5 - start.minute % 5) % 5)
    end = datetime.combine(day + timedelta(days=payload.days), time.min, zone).astimezone(UTC)
    row = await state(database, user_id, workspace_id)
    source_hash, result = await snapshot(
        database, user_id, workspace_id, row, start_at=start, end_at=end, now=moment
    )
    await database.execute(
        delete(StudentPlanningPreview)
        .where(
            StudentPlanningPreview.user_id == user_id,
            StudentPlanningPreview.workspace_id == workspace_id,
            StudentPlanningPreview.expires_at <= moment,
        )
        .execution_options(synchronize_session=False)
    )
    await schedule.count_limit(database, StudentPlanningPreview, user_id, workspace_id, 20)
    identifier = uuid4()
    preview_hash = digest(
        {"id": str(identifier), "source": source_hash, "result": result.model_dump(mode="json")}
    )
    record = StudentPlanningPreview(
        id=identifier,
        user_id=user_id,
        workspace_id=workspace_id,
        source_hash=source_hash,
        preview_hash=preview_hash,
        base_revision=row.revision,
        result=result.model_dump(mode="json"),
        expires_at=moment + timedelta(minutes=20),
    )
    database.add(record)
    await database.commit()
    return PreviewView(
        preview_id=identifier,
        preview_hash=preview_hash,
        expires_at=record.expires_at,
        base_revision=row.revision,
        result=result,
    )


async def apply_result(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    result: PlanResult,
    *,
    now: datetime,
    plan_id: UUID,
) -> list[StudentStudyBlock]:
    future = list(
        await database.scalars(
            select(StudentStudyBlock).where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.start_at >= now,
            )
        )
    )
    for row in future:
        row.status = "superseded"
        row.revision += 1
    await schedule.count_limit(
        database, StudentStudyBlock, user_id, workspace_id, 5000, len(result.blocks)
    )
    for block in result.blocks:
        database.add(
            StudentStudyBlock(
                id=uuid4(),
                plan_id=plan_id,
                user_id=user_id,
                workspace_id=workspace_id,
                **block.model_dump(),
                status="planned",
                actual_minutes=0,
                revision=1,
            )
        )
    await database.flush()
    return future


async def accept(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: AcceptInput,
    *,
    now: datetime | None = None,
) -> PlanView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    body = payload.model_dump(mode="json")
    previous = await command(database, user_id, workspace_id, payload.request_id, "accept", body)
    if previous:
        return await plan_view(database, user_id, workspace_id)
    moment = utc(now or await database_now(database))
    record: StudentPlanningPreview = await schedule.get_owned(
        database, StudentPlanningPreview, payload.preview_id, user_id, workspace_id
    )
    planning = await state(database, user_id, workspace_id)
    if (
        record.accepted_at
        or utc(record.expires_at) <= moment
        or record.preview_hash != payload.preview_hash
        or planning.revision != record.base_revision
    ):
        raise schedule.StudentError(409, "This plan preview changed or expired. Preview it again")
    result = PlanResult.model_validate(record.result)
    source_hash, _ = await snapshot(
        database,
        user_id,
        workspace_id,
        planning,
        start_at=result.start_at,
        end_at=result.end_at,
        now=moment,
    )
    if source_hash != record.source_hash or any(block.start_at < moment for block in result.blocks):
        raise schedule.StudentError(
            409, "Your schedule, tasks or the current time changed. Preview again"
        )
    await apply_result(database, user_id, workspace_id, result, now=moment, plan_id=record.id)
    planning.revision += 1
    planning.window_start, planning.window_end = result.start_at, result.end_at
    planning.needs_review = False
    planning.accepted_source_hash = await stable_source_hash(
        database, user_id, workspace_id, planning
    )
    record.accepted_at = moment
    await database.flush()
    response = await plan_view(database, user_id, workspace_id)
    await remember(database, user_id, workspace_id, payload.request_id, "accept", body, response)
    schedule.audit(database, user_id, workspace_id, "student.plan.accepted", record.id)
    await database.commit()
    return response


async def outcome(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    block_id: UUID,
    payload: OutcomeInput,
    *,
    now: datetime | None = None,
    preview_only: bool = False,
    expected_preview: str | None = None,
) -> OutcomeView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    body = {"block_id": str(block_id), "payload": payload.model_dump(mode="json")}
    previous = (
        None
        if preview_only
        else await command(database, user_id, workspace_id, payload.request_id, "outcome", body)
    )
    if previous:
        recorded = OutcomeView.model_validate(previous.result)
        if expected_preview is not None and expected_preview != recorded.preview_hash:
            raise schedule.StudentError(409, "Review the current changes before approving")
        current = await plan_view(database, user_id, workspace_id)
        if current.needs_review or current.revision != recorded.plan.revision:
            raise schedule.StudentError(
                409,
                "That progress update was already recorded. "
                "Refresh the current plan before continuing",
            )
        return recorded.model_copy(update={"plan": current})
    moment = utc(now or await database_now(database))
    block: StudentStudyBlock = await schedule.get_owned(
        database, StudentStudyBlock, block_id, user_id, workspace_id
    )
    task = await get_task(database, user_id, workspace_id, block.task_id)
    planning = await state(database, user_id, workspace_id)
    if task.status != "active" or task.remaining_minutes <= 0:
        raise schedule.StudentError(
            409, "This task is archived or completed. Preview an updated plan"
        )
    await _course(database, user_id, workspace_id, task.course_id)
    await require_current_plan(database, user_id, workspace_id, planning)
    if (
        block.status != "planned"
        or block.revision != payload.revision
        or task.revision != payload.task_revision
    ):
        raise schedule.StudentError(
            409, "This block or task changed. Refresh before recording progress"
        )
    if payload.outcome != "skipped" and utc(block.start_at) > moment:
        raise schedule.StudentError(422, "Record progress after this study block starts")
    prior_task = {
        "remaining_minutes": task.remaining_minutes,
        "actual_minutes": task.actual_minutes,
        "status": task.status,
    }
    prior_block = {
        "id": str(block.id),
        "status": block.status,
        "actual_minutes": block.actual_minutes,
    }
    future_rows = list(
        await database.scalars(
            select(StudentStudyBlock).where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.start_at >= moment,
                StudentStudyBlock.id != block.id,
            )
        )
    )
    old_future = [str(row.id) for row in future_rows]
    replaced_blocks = sorted(
        [block_view(row) for row in future_rows], key=lambda row: (row.start_at, str(row.id))
    )
    base_revision = planning.revision
    base_source = await stable_source_hash(database, user_id, workspace_id, planning)
    task.actual_minutes += payload.actual_minutes
    if payload.outcome == "done":
        task.remaining_minutes = (
            0
            if payload.complete_task
            else (
                payload.remaining_minutes
                if payload.remaining_minutes is not None
                else max(0, task.remaining_minutes - block.minutes)
            )
        )
    elif payload.outcome == "took_longer":
        assert payload.remaining_minutes is not None
        task.remaining_minutes = payload.remaining_minutes
    task.status = "completed" if task.remaining_minutes == 0 else "active"
    task.revision += 1
    block.status, block.actual_minutes, block.outcome_at = (
        payload.outcome,
        payload.actual_minutes,
        moment,
    )
    block.revision += 1
    planning.revision += 1
    await database.flush()
    zone = ZoneInfo((await schedule.owner(database, user_id, workspace_id)).timezone)
    start = (moment + timedelta(minutes=1)).replace(second=0, microsecond=0)
    if planning.window_start is not None:
        start = max(start, utc(planning.window_start))
    end = utc(planning.window_end) if planning.window_end else start
    if end <= start:
        end = datetime.combine(
            moment.astimezone(zone).date() + timedelta(days=7), time.min, zone
        ).astimezone(UTC)
    planning.window_start, planning.window_end = start, end
    source_hash, result = await snapshot(
        database, user_id, workspace_id, planning, start_at=start, end_at=end, now=moment
    )
    preview_hash = digest(
        {
            "owner": [str(user_id), str(workspace_id)],
            "block": str(block_id),
            "input": payload.model_dump(mode="json", exclude={"request_id"}),
            "revision": base_revision,
            "source": base_source,
            "before": [row.model_dump(mode="json") for row in replaced_blocks],
            "result": result.model_dump(mode="json"),
        }
    )
    if expected_preview is not None and expected_preview != preview_hash:
        await database.rollback()
        raise schedule.StudentError(
            409, "The schedule changed. Preview the changes again before approving"
        )
    plan_id = uuid4()
    await apply_result(database, user_id, workspace_id, result, now=moment, plan_id=plan_id)
    planning.needs_review = False
    planning.accepted_source_hash = await stable_source_hash(
        database, user_id, workspace_id, planning
    )
    await database.flush()
    response = OutcomeView(
        task=task_view(task),
        block=block_view(block),
        plan=await plan_view(database, user_id, workspace_id),
        result=result,
        undo_request_id=payload.request_id,
        preview_hash=preview_hash,
        replaced_blocks=replaced_blocks,
    )
    if preview_only:
        # Execute the exact planner in the locked transaction, then discard every
        # mutation. Preview cannot record progress, audit, or persist new blocks.
        await database.rollback()
        return response
    await remember(
        database,
        user_id,
        workspace_id,
        payload.request_id,
        "outcome",
        body,
        response,
        undo={
            "task_id": str(task.id),
            "task": prior_task,
            "block": prior_block,
            "future_ids": old_future,
            "new_plan_id": str(plan_id),
            "revision": planning.revision,
            "source_hash": source_hash,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "undone": False,
        },
    )
    schedule.audit(
        database,
        user_id,
        workspace_id,
        "student.study.progress",
        block.id,
        {"outcome": payload.outcome},
    )
    await database.commit()
    return response


async def undo_outcome(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: UndoInput,
    *,
    now: datetime | None = None,
) -> PlanView:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    body = payload.model_dump(mode="json")
    previous = await command(database, user_id, workspace_id, payload.request_id, "undo", body)
    if previous:
        return await plan_view(database, user_id, workspace_id)
    moment = utc(now or await database_now(database))
    original = await database.scalar(
        select(StudentPlanningCommand).where(
            StudentPlanningCommand.user_id == user_id,
            StudentPlanningCommand.workspace_id == workspace_id,
            StudentPlanningCommand.request_id == payload.outcome_request_id,
            StudentPlanningCommand.action == "outcome",
        )
    )
    planning = await state(database, user_id, workspace_id)
    await require_current_plan(database, user_id, workspace_id, planning)
    saved = original.undo_snapshot if original else None
    if (
        original is None
        or saved is None
        or saved.get("undone")
        or planning.revision != payload.revision
        or saved.get("revision") != planning.revision
        or utc(original.created_at) < moment - timedelta(minutes=5)
    ):
        raise schedule.StudentError(
            409, "Only the latest unchanged progress update can be undone within five minutes"
        )
    source_hash, _ = await snapshot(
        database,
        user_id,
        workspace_id,
        planning,
        start_at=datetime.fromisoformat(str(saved["start_at"])),
        end_at=datetime.fromisoformat(str(saved["end_at"])),
        now=moment,
    )
    if source_hash != saved["source_hash"]:
        raise schedule.StudentError(
            409, "Your schedule changed. Preview a fresh plan instead of undoing"
        )
    future_ids = saved["future_ids"]
    assert isinstance(future_ids, list)
    old_ids = [UUID(str(item)) for item in future_ids]
    future = list(
        await database.scalars(
            select(StudentStudyBlock).where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.id.in_(old_ids),
            )
        )
    )
    new = list(
        await database.scalars(
            select(StudentStudyBlock).where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.plan_id == UUID(str(saved["new_plan_id"])),
                StudentStudyBlock.status == "planned",
            )
        )
    )
    if any(utc(row.start_at) < moment for row in [*future, *new]):
        raise schedule.StudentError(
            409, "The next study block has started. Record a new progress update instead"
        )
    task = await get_task(database, user_id, workspace_id, UUID(str(saved["task_id"])))
    prior_task = saved["task"]
    prior_block = saved["block"]
    assert isinstance(prior_task, dict) and isinstance(prior_block, dict)
    if any(
        source["needs_review"] is True
        for source in await current_sources(
            database,
            user_id,
            workspace_id,
            [task_view(task)],
        )
    ):
        raise schedule.StudentError(409, "The task source changed. Review it before undoing")
    task.remaining_minutes = int(prior_task["remaining_minutes"])
    task.actual_minutes = int(prior_task["actual_minutes"])
    task.status = str(prior_task["status"])
    task.revision += 1
    block: StudentStudyBlock = await schedule.get_owned(
        database, StudentStudyBlock, UUID(str(prior_block["id"])), user_id, workspace_id
    )
    block.status, block.actual_minutes, block.outcome_at = (
        str(prior_block["status"]),
        int(prior_block["actual_minutes"]),
        None,
    )
    block.revision += 1
    for row in new:
        row.status = "superseded"
        row.revision += 1
    for row in future:
        row.status = "planned"
        row.revision += 1
    planning.revision += 1
    planning.needs_review = False
    planning.accepted_source_hash = await stable_source_hash(
        database, user_id, workspace_id, planning
    )
    original.undo_snapshot = {**saved, "undone": True}
    await database.flush()
    response = await plan_view(database, user_id, workspace_id)
    await remember(database, user_id, workspace_id, payload.request_id, "undo", body, response)
    await database.commit()
    return response


async def forget_source_tasks(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    item_ids: list[UUID],
) -> None:
    """Feed erasure hook, called under the caller's fresh User lock before orphan deletion.

    No commit: source erasure and every derived copy disappear atomically. Manual
    tasks and tasks still attributed to another retained feed remain untouched.
    """
    if not item_ids:
        return
    if len(item_ids) > 5000:
        raise schedule.StudentError(409, "Too many source items to forget in one operation")
    identifiers = list(
        await database.scalars(
            select(StudentTask.id).where(
                StudentTask.user_id == user_id,
                StudentTask.workspace_id == workspace_id,
                StudentTask.source_item_id.in_(item_ids),
            )
        )
    )
    if not identifiers:
        return
    await database.execute(
        delete(StudentStudyBlock).where(
            StudentStudyBlock.user_id == user_id,
            StudentStudyBlock.workspace_id == workspace_id,
            StudentStudyBlock.task_id.in_(identifiers),
        )
    )
    await database.execute(
        delete(StudentTask).where(
            StudentTask.user_id == user_id,
            StudentTask.workspace_id == workspace_id,
            StudentTask.id.in_(identifiers),
        )
    )
    # Previews and receipts can contain several tasks' titles; discard the
    # bounded derived snapshots rather than retain a forgotten source fragment.
    await invalidate_snapshots(database, user_id, workspace_id)
    planning = await database.scalar(
        select(StudentPlanningState).where(
            StudentPlanningState.user_id == user_id,
            StudentPlanningState.workspace_id == workspace_id,
        )
    )
    if planning:
        planning.revision += 1
        await invalidate_future(database, user_id, workspace_id, planning)
        planning.accepted_source_hash = None


async def invalidate_snapshots(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    from navox.student_recovery.service import invalidate_recovery_previews

    await invalidate_recovery_previews(database, user_id, workspace_id)
    await database.execute(
        delete(StudentPlanningPreview).where(
            StudentPlanningPreview.user_id == user_id,
            StudentPlanningPreview.workspace_id == workspace_id,
        )
    )
    # Keep content-free idempotency tombstones so a delayed retry cannot recreate
    # a forgotten assignment or duplicate an independent manual task.
    await database.execute(
        update(StudentPlanningCommand)
        .where(
            StudentPlanningCommand.user_id == user_id,
            StudentPlanningCommand.workspace_id == workspace_id,
        )
        .values(result={"invalidated": True}, undo_snapshot=None)
        .execution_options(synchronize_session=False)
    )


async def forget_planning_data(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Explicit student-data reset hook; caller holds User lock and commits atomically."""
    from navox.student_recovery.service import invalidate_recovery_previews

    await invalidate_recovery_previews(database, user_id, workspace_id)
    for model in (
        StudentPlanningCommand,
        StudentPlanningPreview,
        StudentStudyBlock,
        StudentTask,
        StudentPlanningState,
    ):
        await database.execute(
            delete(model).where(model.user_id == user_id, model.workspace_id == workspace_id)
        )


async def preference_preview(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, payload: PreferencesUpdate
) -> dict:
    """Bind routine/alarm consent to every study block it would retire, plus class sources."""
    from navox.student_planning.alarm import alarm_schedule

    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    current = await preferences(database, user_id, workspace_id)
    if current.revision != payload.revision:
        raise schedule.StudentError(409, "Your routine changed. Review it again")
    now = await database_now(database)
    blocks = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.start_at >= now,
            )
            .order_by(StudentStudyBlock.start_at, StudentStudyBlock.id)
            .limit(601)
        )
    )
    if len(blocks) > 600:
        raise schedule.StudentError(409, "Too many future blocks to review safely")
    zone = ZoneInfo(user.timezone)
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=14), time.min, zone).astimezone(UTC)
    sources = await schedule.state_for_window(database, user_id, workspace_id, start, end)
    occurrences, _, _, warnings = project(
        *sources, start_at=start, end_at=end, include_conflicts=False
    )
    routine_changed = scheduling_preferences(
        current.model_dump(mode="json", exclude={"revision", "timezone"})
    ) != scheduling_preferences(payload.model_dump(mode="json", exclude={"revision", "request_id"}))
    replaced = (
        [block_view(row).model_dump(mode="json") for row in blocks] if routine_changed else []
    )
    alarm = alarm_schedule(payload, user.timezone, day, occurrences, days=7)
    candidate = payload.model_dump(mode="json", exclude={"request_id", "revision"})
    fingerprint = digest(
        {
            "request": payload.model_dump(mode="json"),
            "timezone": user.timezone,
            "day": day,
            "replaced": replaced,
            "sources": [
                [row.model_dump(mode="json") for row in sorted(group, key=lambda row: str(row.id))]
                for group in sources
            ],
        }
    )
    return {
        "preview_hash": fingerprint,
        "preferences": candidate,
        "replaced_blocks": replaced,
        "timezone": user.timezone,
        "alarm_occurrences": alarm,
        "warnings": warnings,
        "native_ringing_available": False,
        "previous_alarm_occurrences": alarm_schedule(
            current, user.timezone, day, occurrences, days=7
        ),
    }
