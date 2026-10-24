"""Capture student-provided work as an editable, explicitly approved task proposal."""

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from navox.core.settings import Settings
from navox.db.student import StudentCourse
from navox.student import service as schedule
from navox.student_planning import service as planning
from navox.student_planning.schemas import TaskCreate, TaskUpdate
from navox.student_planning.task_review import review
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost


class TaskArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    course: str | None = Field(default=None, max_length=200)
    due_local: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    estimated_minutes: int = Field(ge=1, le=10080)


class RemainingArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    remaining_minutes: int = Field(ge=1, le=10080)


class ClassArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    transcript: str = Field(min_length=1, max_length=500)


def deadline(local: str, timezone: str) -> datetime:
    wall = datetime.strptime(local, "%Y-%m-%dT%H:%M")
    zone = ZoneInfo(timezone)
    choices = {
        wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        for fold in (0, 1)
        if wall.replace(tzinfo=zone, fold=fold)
        .astimezone(UTC)
        .astimezone(zone)
        .replace(tzinfo=None)
        == wall
    }
    if len(choices) != 1:
        raise ValueError("Choose a deadline that is not skipped or repeated by the clock change.")
    return choices.pop()


class CaptureTools:
    def __init__(
        self,
        check: DatabaseAuthority,
        settings: Settings,
        publish: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        self.check, self.settings, self.publish = check, settings, publish

    def definitions(self) -> list[dict[str, Any]]:
        if not self.settings.student_planning_enabled:
            return []
        return [
            {
                "type": "function",
                "name": "prepare_student_task",
                "description": (
                    "Capture a task using only details supplied by the student. Ask for missing "
                    "deadline (date AND time) and effort before calling. Resolve relative dates "
                    "using get_student_schedule's server date in the saved timezone. due_local "
                    "is YYYY-MM-DDTHH:MM in that timezone; do not convert offsets yourself. "
                    "course is the saved code/name or null for a personal task. No task or plan "
                    "is saved: the student must review the exact details and any removed blocks "
                    "on screen and approve. Do not invent personal facts or assume approval."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "course": {"type": ["string", "null"]},
                        "due_local": {"type": "string"},
                        "estimated_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
                    },
                    "required": ["title", "course", "due_local", "estimated_minutes"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "prepare_task_remaining",
                "description": (
                    "When the student says they did not finish saved work, prepare an update "
                    "to the remaining minutes. Ask which exact task and how many minutes remain "
                    "before calling. This does not claim focus time or completion and does not "
                    "save anything. The exact task and affected study blocks must be reviewed "
                    "and approved on screen; only then offer prepare_study_plan. Titles must "
                    "match one saved active task; do not guess between similar tasks."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "remaining_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
                    },
                    "required": ["title", "remaining_minutes"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "prepare_student_class",
                "description": (
                    "Carry a student's spoken class request into the editable Classes review. "
                    "Use their own course, days, times and room details in a sentence beginning "
                    "'Add'; never invent missing details. Preserve ambiguous times without "
                    "guessing AM/PM. The student opens the class review, checks term dates and "
                    "timezone, and explicitly saves the course and its meeting. Nothing is "
                    "added by this tool. Explain that both review steps are required."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"transcript": {"type": "string", "maxLength": 500}},
                    "required": ["transcript"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        authority = await self.check()
        if not self.settings.student_planning_enabled:
            return {"status": "unavailable", "message": "Study planning is unavailable."}
        try:
            if name == "prepare_student_class":
                query_class = ClassArguments.model_validate(arguments)
                transcript = query_class.transcript.strip()
                if not re.match(r"^(?:please\s+)?(?:add|create)\s+", transcript, re.I) or any(
                    ord(char) < 32 or ord(char) == 127 for char in transcript
                ):
                    raise ValueError
                async with self.check.factory() as database:
                    user = await schedule.owner(database, authority.user_id, authority.workspace_id)
                    timezone = user.timezone
                if await self.check() != authority:
                    raise WorkerAccessLost("The voice tool's owner changed")
                await self.publish(
                    {
                        "kind": "class",
                        "preview_id": str(uuid4()),
                        "preview_hash": planning.digest(transcript),
                        "timezone": timezone,
                        "transcript": transcript,
                    }
                )
                return {
                    "status": "review_required",
                    "applied": False,
                    "timezone": timezone,
                    "message": (
                        "Open the class review on screen. Check the course and term dates, "
                        "then review its days, times and location before saving the meeting. "
                        "Nothing has been added yet."
                    ),
                }
            if name not in {"prepare_student_task", "prepare_task_remaining"}:
                raise ValueError
            query = (
                TaskArguments.model_validate(arguments) if name == "prepare_student_task" else None
            )
            remaining_query = (
                RemainingArguments.model_validate(arguments)
                if name == "prepare_task_remaining"
                else None
            )
            async with self.check.factory() as database:
                user = await schedule.owner(database, authority.user_id, authority.workspace_id)
                course_id = None
                task_id = None
                payload: TaskCreate | TaskUpdate
                if remaining_query:
                    task_matches = [
                        row
                        for row in await planning.tasks(
                            database, authority.user_id, authority.workspace_id
                        )
                        if row.status == "active"
                        and " ".join(row.title.split()).casefold()
                        == " ".join(remaining_query.title.split()).casefold()
                    ]
                    if len(task_matches) != 1:
                        raise schedule.StudentError(
                            422, "Choose one exact active task in Planner, then ask again."
                        )
                    row = task_matches[0]
                    task_id = row.id
                    payload = TaskUpdate(
                        request_id=uuid4(),
                        revision=row.revision,
                        **row.model_dump(
                            include={
                                "title",
                                "course_id",
                                "due_at",
                                "estimated_minutes",
                                "difficulty",
                                "priority",
                                "course_weight",
                                "status",
                            }
                        ),
                        remaining_minutes=remaining_query.remaining_minutes,
                    )
                elif query and query.course:
                    courses = list(
                        await database.scalars(
                            select(StudentCourse).where(
                                StudentCourse.user_id == authority.user_id,
                                StudentCourse.workspace_id == authority.workspace_id,
                                StudentCourse.archived.is_(False),
                            )
                        )
                    )

                    def normalize(value: str) -> str:
                        return re.sub(r"\s+", "", value).casefold()

                    course_matches = [
                        row
                        for row in courses
                        if normalize(query.course) in {normalize(row.code), normalize(row.name)}
                    ]
                    if len(course_matches) != 1:
                        raise schedule.StudentError(
                            422,
                            "Ask the student to choose an exact saved course or a personal task.",
                        )
                    course_id = course_matches[0].id
                if query:
                    payload = TaskCreate(
                        request_id=uuid4(),
                        title=query.title.strip(),
                        course_id=course_id,
                        due_at=deadline(query.due_local, user.timezone),
                        estimated_minutes=query.estimated_minutes,
                        remaining_minutes=query.estimated_minutes,
                    )
                proposal = await review(
                    database, authority.user_id, authority.workspace_id, payload, task_id
                )
                timezone = user.timezone
            if await self.check() != authority:
                raise WorkerAccessLost("The voice tool's owner changed")
            await self.publish(
                {
                    "kind": "task",
                    "preview_id": str(payload.request_id),
                    "preview_hash": proposal["preview_hash"],
                    "timezone": timezone,
                    "task": payload.model_dump(mode="json", exclude={"request_id"}),
                    **(
                        {"task_id": str(task_id), "revision": payload.revision}
                        if isinstance(payload, TaskUpdate)
                        else {}
                    ),
                }
            )
            return {
                "status": "review_required",
                "applied": False,
                "title": payload.title,
                "course": proposal["course_label"],
                "due_local": payload.due_at.astimezone(ZoneInfo(timezone)).isoformat()
                if payload.due_at
                else None,
                "timezone": timezone,
                "estimated_minutes": payload.estimated_minutes,
                "remaining_minutes": payload.remaining_minutes,
                "replaced_count": len(proposal["replaced_blocks"]),
                "replaced_blocks": proposal["replaced_blocks"]
                if len(proposal["replaced_blocks"]) <= 12
                else [],
                "full_readback_on_screen": len(proposal["replaced_blocks"]) > 12,
                "message": (
                    "The exact task and affected blocks are on screen. Review and approve "
                    "there, then request a new study plan. No focus time is credited."
                ),
            }
        except (ValidationError, ValueError):
            result = {
                "status": "needs_details",
                "message": (
                    "Ask for the missing or ambiguous details. New tasks need a local deadline "
                    "and effort; unfinished tasks need an exact title and remaining minutes."
                ),
            }
        except schedule.StudentError as error:
            result = {"status": "needs_details", "message": str(error)}
        if await self.check() != authority:
            raise WorkerAccessLost("The voice tool's owner changed")
        return result
