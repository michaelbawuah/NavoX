"""Reauthorize minimized observations against current owned records before model context."""

from datetime import datetime
from typing import Any, Never
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.context import ContextDenied
from navox.connectors.sync_state import database_now, utc
from navox.db.lms_feeds import LmsFeed
from navox.db.student import StudentCourse
from navox.db.student_planning import StudentPlanningPreview, StudentPlanningState, StudentTask
from navox.student import service as schedule
from navox.student_agent.contracts import Observation, canonical
from navox.student_planning import service as planning


def denied() -> Never:
    raise ContextDenied("Student tool observations are unavailable or changed")


async def owned(
    database: AsyncSession, model: Any, identifier: object, user_id: UUID, workspace_id: UUID
) -> Any:
    try:
        selected = UUID(str(identifier))
    except (TypeError, ValueError):
        denied()
    row = await database.scalar(
        select(model)
        .where(model.id == selected, model.user_id == user_id, model.workspace_id == workspace_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        denied()
    return row


def equal(actual: object, supplied: object) -> None:
    if canonical(actual) != canonical(supplied):
        denied()


def occurrence(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in ("id", "course_code", "course_name", "start_at", "end_at", "building", "room")
    }


async def authorize_observations(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, observations: tuple[Observation, ...]
) -> None:
    if not observations:
        return
    try:
        user = await schedule.owner(database, user_id, workspace_id)
        now = await database_now(database)
        for observation in observations:
            data: dict[str, Any] = observation.data
            if observation.name == "clock.read":
                if (
                    data["timezone"] != user.timezone
                    or abs((now - datetime.fromisoformat(data["server_now"])).total_seconds()) > 30
                ):
                    denied()
            elif observation.name == "classes.lookup":
                mode = data["mode"]
                when = datetime.fromisoformat(data["at"])
                captured = datetime.fromisoformat(data["server_now"])
                if abs((now - captured).total_seconds()) > 30 or (
                    mode in {"now", "next"} and when != captured
                ):
                    denied()
                view = await schedule.lookup(
                    database,
                    user_id,
                    workspace_id,
                    mode=mode,
                    day=None
                    if mode in {"now", "next"}
                    else datetime.fromisoformat(data["date"]).date(),
                    clock=when.astimezone(ZoneInfo(user.timezone)).strftime("%H:%M")
                    if mode == "at"
                    else None,
                )
                raw = view.model_dump(mode="json")
                if mode in {"at", "day"} and datetime.fromisoformat(raw["at"]) != when:
                    denied()
                current = {key: raw[key] for key in ("mode", "timezone", "date", "reason")}
                current["occurrences"] = [occurrence(row) for row in raw["occurrences"]]
                current["next_upcoming"] = (
                    occurrence(raw["next_upcoming"]) if raw["next_upcoming"] else None
                )
                current["conflicts"] = [
                    {"left_id": row["left_id"], "right_id": row["right_id"]}
                    for row in raw["conflicts"]
                ]
                equal(
                    current,
                    {key: value for key, value in data.items() if key not in {"at", "server_now"}},
                )
            elif observation.name == "course.info":
                for record in data["courses"]:
                    course = await owned(
                        database, StudentCourse, record["id"], user_id, workspace_id
                    )
                    if course.archived:
                        denied()
                    equal(
                        {
                            "id": str(course.id),
                            "code": course.code,
                            "name": course.name,
                            "professor": course.professor,
                            "term_start": course.term_start.isoformat(),
                            "term_end": course.term_end.isoformat(),
                        },
                        record,
                    )
            elif observation.name == "tasks.read":
                for record in data["tasks"]:
                    task = await owned(database, StudentTask, record["id"], user_id, workspace_id)
                    fresh = planning.task_view(task).model_dump(mode="json")
                    equal({key: fresh[key] for key in record}, record)
            elif observation.name == "plan.preview":
                preview = await owned(
                    database, StudentPlanningPreview, data["preview_id"], user_id, workspace_id
                )
                state = await database.scalar(
                    select(StudentPlanningState)
                    .where(
                        StudentPlanningState.user_id == user_id,
                        StudentPlanningState.workspace_id == workspace_id,
                    )
                    .execution_options(populate_existing=True)
                )
                if (
                    state is None
                    or preview.accepted_at is not None
                    or utc(preview.expires_at) <= now
                    or preview.preview_hash != data["preview_hash"]
                    or utc(preview.expires_at) != datetime.fromisoformat(data["expires_at"])
                    or preview.base_revision != data["base_revision"]
                    or state.revision != preview.base_revision
                ):
                    denied()
                equal(preview.result, data["result"])
                source, _ = await planning.snapshot(
                    database,
                    user_id,
                    workspace_id,
                    state,
                    start_at=datetime.fromisoformat(data["result"]["start_at"]),
                    end_at=datetime.fromisoformat(data["result"]["end_at"]),
                    now=now,
                )
                if source != preview.source_hash:
                    denied()
            else:
                rows = (
                    data["feeds"] if observation.name == "feeds.read" else [{"id": data["feed_id"]}]
                )
                for record in rows:
                    feed = await owned(database, LmsFeed, record["id"], user_id, workspace_id)
                    if feed.state not in {"active", "paused"}:
                        denied()
                    if observation.name == "feeds.read":
                        equal(
                            {
                                "id": str(feed.id),
                                "name": feed.name,
                                "domain": feed.domain,
                                "state": feed.state,
                                "revision": feed.revision,
                                "last_synced_at": utc(feed.last_synced_at).isoformat()
                                if feed.last_synced_at
                                else None,
                            },
                            record,
                        )
                    elif feed.domain != data["domain"] or feed.revision != data["revision"]:
                        denied()
    except (schedule.StudentError, ValueError, TypeError, KeyError, AttributeError):
        denied()
