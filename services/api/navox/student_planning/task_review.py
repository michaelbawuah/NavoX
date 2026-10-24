"""Bind task writes to the complete schedule impact shown to the student."""

from typing import Any
from uuid import UUID

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.student import StudentCourse
from navox.student import service as schedule
from navox.student_planning import service
from navox.student_planning.schemas import TaskCreate, TaskUpdate


class TaskCreateConfirm(TaskCreate):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class TaskUpdateConfirm(TaskUpdate):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


async def review(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    payload: TaskCreate | TaskUpdate,
    task_id: UUID | None = None,
) -> dict[str, Any]:
    user = await schedule.owner(database, user_id, workspace_id, lock=True)
    await service._course(database, user_id, workspace_id, payload.course_id)
    course = await database.get(StudentCourse, payload.course_id) if payload.course_id else None
    remaining = payload.remaining_minutes
    if remaining is None:
        remaining = payload.estimated_minutes
    if (
        payload.status == "active"
        and remaining == 0
        or payload.status == "completed"
        and remaining != 0
    ):
        raise schedule.StudentError(422, "Completed tasks need zero remaining minutes")
    before = None
    if task_id:
        row = await service.get_task(database, user_id, workspace_id, task_id)
        if not isinstance(payload, TaskUpdate) or row.revision != payload.revision:
            raise schedule.StudentError(409, "This task changed. Reopen it and review again")
        before = service.task_view(row).model_dump(mode="json")
    planning = await service.state(database, user_id, workspace_id)
    blocks = []
    if planning.window_end is not None or planning.accepted_source_hash is not None:
        blocks = [
            row.model_dump(mode="json")
            for row in await service.future_blocks(
                database, user_id, workspace_id, await service.database_now(database)
            )
        ]
    candidate = payload.model_dump(mode="json")
    fingerprint = service.digest(
        {
            "owner": [str(user_id), str(workspace_id)],
            "task_id": str(task_id),
            "before": before,
            "candidate": candidate,
            "course": {
                "id": str(course.id),
                "revision": course.revision,
                "code": course.code,
                "name": course.name,
            }
            if course
            else None,
            "planning_revision": planning.revision,
            "replaced_blocks": blocks,
            "timezone": user.timezone,
        }
    )
    return {
        "preview_hash": fingerprint,
        "course_label": (course.code or course.name) if course else None,
        "before": before,
        "proposed": candidate,
        "replaced_blocks": blocks,
        "timezone": user.timezone,
        "alarms_change": False,
        "calendar_events_change": False,
    }
