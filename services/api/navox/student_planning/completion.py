"""Explicit task completion removes only that task's remaining blocks."""

from typing import Any
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.db.student_planning import StudentStudyBlock
from navox.student import service as schedule
from navox.student.schemas import Input
from navox.student_calendars import claims as calendar_claims
from navox.student_planning import service


class CompleteInput(Input):
    request_id: UUID
    revision: int = Field(ge=1)


class CompleteConfirm(CompleteInput):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


async def complete(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    task_id: UUID,
    payload: CompleteInput,
    *,
    expected_preview: str | None = None,
) -> dict[str, Any]:
    await schedule.owner(database, user_id, workspace_id, lock=True)
    if expected_preview is not None:
        await calendar_claims.require_mutable(database, user_id, workspace_id)
    body = {"task_id": str(task_id), **payload.model_dump(mode="json")}
    if expected_preview:
        previous = await service.command(
            database, user_id, workspace_id, payload.request_id, "complete_task", body
        )
        if previous:
            if previous.result["preview_hash"] != expected_preview:
                raise schedule.StudentError(409, "Review the current task before approving")
            return previous.result
    task = await service.get_task(database, user_id, workspace_id, task_id)
    if task.revision != payload.revision or task.status != "active":
        raise schedule.StudentError(409, "This task changed. Reopen it and review again")
    planning = await service.state(database, user_id, workspace_id)
    now = utc(await service.database_now(database))
    rows = list(
        await database.scalars(
            select(StudentStudyBlock)
            .where(
                StudentStudyBlock.user_id == user_id,
                StudentStudyBlock.workspace_id == workspace_id,
                StudentStudyBlock.task_id == task_id,
                StudentStudyBlock.status == "planned",
                StudentStudyBlock.end_at > now,
            )
            .order_by(StudentStudyBlock.start_at, StudentStudyBlock.id)
            .limit(5001)
        )
    )
    if len(rows) > 5000:
        raise schedule.StudentError(409, "Too many task blocks to review safely")
    before = service.task_view(task).model_dump(mode="json")
    removed = [service.block_view(row).model_dump(mode="json") for row in rows]
    fingerprint = service.digest(
        {
            "owner": [str(user_id), str(workspace_id)],
            "task": before,
            "revision": planning.revision,
            "removed": removed,
        }
    )
    response = {
        "preview_hash": fingerprint,
        "task": before,
        "removed_blocks": removed,
        "credited_minutes": 0,
        "other_blocks_change": False,
        "alarms_change": False,
        "calendar_events_change": False,
    }
    if expected_preview is None:
        await database.rollback()
        return response
    if fingerprint != expected_preview:
        raise schedule.StudentError(409, "The affected task or blocks changed. Review again")
    task.status, task.remaining_minutes = "completed", 0
    task.revision += 1
    for row in rows:
        row.status = "superseded"
        row.revision += 1
    planning.revision += 1
    await database.flush()
    if not planning.needs_review:
        planning.accepted_source_hash = await service.stable_source_hash(
            database, user_id, workspace_id, planning
        )
    response["task"] = service.task_view(task).model_dump(mode="json")
    await service.remember(
        database, user_id, workspace_id, payload.request_id, "complete_task", body, response
    )
    schedule.audit(database, user_id, workspace_id, "student.task.completed", task_id)
    await database.commit()
    return response
