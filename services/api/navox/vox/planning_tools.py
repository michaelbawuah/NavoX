"""Voice planning proposals. Only the student's reviewed browser approval can apply."""

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from navox.connectors.sync_state import utc
from navox.core.settings import Settings
from navox.student import service as schedule
from navox.student_planning import service as planning
from navox.student_planning.openings import openings
from navox.student_planning.schemas import PreferencesUpdate, PreviewInput
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost

REVIEW_TOPIC = "navox.vox.plan-review.v1"


class PlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    date: str | None = None
    days: int = Field(ge=1, le=14)


class OpeningArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    days: int = Field(ge=1, le=7)


class AlarmArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal[
        "set_wake_time",
        "enable",
        "disable",
        "enable_days",
        "disable_days",
        "smart_on",
        "smart_off",
        "skip_tomorrow",
    ]
    time: str | None = None
    weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7)


class PlanningTools:
    def __init__(
        self,
        check: DatabaseAuthority,
        settings: Settings,
        publish: Callable[[dict[str, object]], Awaitable[None]],
    ):
        self.check, self.settings, self.publish = check, settings, publish

    def definitions(self) -> list[dict[str, Any]]:
        if not self.settings.student_planning_enabled:
            return []
        return [
            {
                "type": "function",
                "name": "prepare_alarm_change",
                "description": (
                    "Prepare the requested wake/alarm change for exact on-screen approval. "
                    "set_wake_time requires HH:MM and weekdays (Monday 0 through Sunday 6); "
                    "enable_days/disable_days require weekdays and null time (for example, "
                    "no alarm Saturday means disable_days with [5]). Other actions require "
                    "null time and weekdays. "
                    "skip_tomorrow preserves other days. "
                    "Read back every affected alarm and study block. Never claim an alarm is set "
                    "until it is approved. The web saves alarm setup only; ringing and "
                    "snooze require the phone app. "
                    "If wake time is ambiguous (seven without morning/evening), ask first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "set_wake_time",
                                "enable",
                                "disable",
                                "enable_days",
                                "disable_days",
                                "smart_on",
                                "smart_off",
                                "skip_tomorrow",
                            ],
                        },
                        "time": {"type": ["string", "null"]},
                        "weekdays": {
                            "type": ["array", "null"],
                            "items": {"type": "integer", "minimum": 0, "maximum": 6},
                        },
                    },
                    "required": ["action", "time", "weekdays"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "prepare_study_plan",
                "description": (
                    "When the student asks to plan or replan their saved work, prepare a proposal "
                    "around actual classes, routine and connected calendar. Date is their local "
                    "YYYY-MM-DD or null for today; days is 1 to 14. Nothing is applied. "
                    "Read every listed replacement and proposed time, then ask the student to "
                    "review and approve on screen. If readback is too long, direct them to the "
                    "complete on-screen list. Never claim a plan was saved or infer approval. "
                    "This cannot create tasks, change alarms or send calendar invitations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": ["string", "null"]},
                        "days": {"type": "integer", "minimum": 1, "maximum": 14},
                    },
                    "required": ["date", "days"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "get_student_free_time",
                "description": (
                    "Check actual free windows over the next 1 to 7 days, considering classes, "
                    "accepted study, travel, breaks, sleep and connected calendar. "
                    "If calendar_checked or routine_confirmed is false, explain what is missing; "
                    "do not call a window verified. This cannot reserve time or change anything."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 7}},
                    "required": ["days"],
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
            if name == "prepare_alarm_change":
                alarm_query = AlarmArguments.model_validate(arguments)
                if alarm_query.action in {"set_wake_time", "enable_days", "disable_days"}:
                    if not alarm_query.weekdays or any(
                        type(day) is not int or not 0 <= day <= 6 for day in alarm_query.weekdays
                    ):
                        raise ValueError
                    if alarm_query.action == "set_wake_time":
                        if not alarm_query.time:
                            raise ValueError
                    elif alarm_query.time is not None:
                        raise ValueError
                elif alarm_query.time is not None or alarm_query.weekdays is not None:
                    raise ValueError
                async with self.check.factory() as database:
                    current = await planning.preferences(
                        database, authority.user_id, authority.workspace_id
                    )
                    candidate = current.model_dump(mode="json", exclude={"timezone"})
                    candidate["request_id"] = str(uuid4())
                    if alarm_query.action == "set_wake_time":
                        assert alarm_query.weekdays is not None
                        wakes = candidate["wake_times"] or [candidate["wake_time"]] * 7
                        for weekday in alarm_query.weekdays:
                            wakes[weekday] = alarm_query.time
                        candidate["wake_times"] = wakes
                        candidate["alarm"]["days"] = sorted(
                            set(
                                (
                                    candidate["alarm"]["days"]
                                    if candidate["alarm"]["enabled"]
                                    else []
                                )
                                + alarm_query.weekdays
                            )
                        )
                        candidate["alarm"]["enabled"] = True
                        candidate["alarm"]["smart"] = False
                    elif alarm_query.action in {"enable", "disable"}:
                        candidate["alarm"]["enabled"] = alarm_query.action == "enable"
                    elif alarm_query.action in {"enable_days", "disable_days"}:
                        selected_days = set(alarm_query.weekdays or [])
                        existing_days = set(candidate["alarm"]["days"])
                        if alarm_query.action == "enable_days":
                            if not candidate["alarm"]["enabled"]:
                                existing_days = set()
                            candidate["alarm"]["days"] = sorted(existing_days | selected_days)
                            candidate["alarm"]["enabled"] = True
                        else:
                            candidate["alarm"]["days"] = sorted(existing_days - selected_days)
                            if not candidate["alarm"]["days"]:
                                candidate["alarm"]["enabled"] = False
                    elif alarm_query.action in {"smart_on", "smart_off"}:
                        candidate["alarm"]["smart"] = alarm_query.action == "smart_on"
                    else:
                        tomorrow = utc(await planning.database_now(database)).astimezone(
                            ZoneInfo(current.timezone)
                        ).date() + timedelta(days=1)
                        candidate["alarm"]["skip_date"] = tomorrow.isoformat()
                    payload = PreferencesUpdate.model_validate(candidate)
                    review = await planning.preference_preview(
                        database, authority.user_id, authority.workspace_id, payload
                    )
                if await self.check() != authority:
                    raise WorkerAccessLost("The voice tool's owner changed")
                await self.publish(
                    {
                        "kind": "routine",
                        "preview_id": str(payload.request_id),
                        "preview_hash": review["preview_hash"],
                        "timezone": current.timezone,
                        "preferences": {
                            **payload.model_dump(mode="json", exclude={"request_id"}),
                            "timezone": current.timezone,
                        },
                    }
                )
                return {
                    "status": "review_required",
                    "applied": False,
                    "timezone": current.timezone,
                    "native_ringing_available": False,
                    "previous_alarms": review["previous_alarm_occurrences"],
                    "proposed_alarms": review["alarm_occurrences"],
                    "replaced_count": len(review["replaced_blocks"]),
                    "replaced_blocks": review["replaced_blocks"]
                    if len(review["replaced_blocks"]) <= 12
                    else [],
                    "full_readback_on_screen": len(review["replaced_blocks"]) > 12,
                    "warnings": review["warnings"],
                    "message": (
                        "Review and approve the saved alarm setup on screen. Ringing and "
                        "snooze require the phone app; this web setup cannot ring your phone."
                    ),
                }
            if name == "prepare_study_plan":
                plan_query = PlanArguments.model_validate(arguments)
                selected = date.fromisoformat(plan_query.date) if plan_query.date else None
                if selected and selected.isoformat() != plan_query.date:
                    raise ValueError
                async with self.check.factory() as database:
                    user = await schedule.owner(database, authority.user_id, authority.workspace_id)
                    timezone = user.timezone
                    preview = await planning.preview(
                        database,
                        authority.user_id,
                        authority.workspace_id,
                        PreviewInput(day=selected, days=plan_query.days),
                    )
                if await self.check() != authority:
                    raise WorkerAccessLost("The voice tool's owner changed")
                await self.publish(
                    {
                        "kind": "study-plan",
                        "preview_id": str(preview.preview_id),
                        "preview_hash": preview.preview_hash,
                        "timezone": timezone,
                    }
                )
                short = len(preview.replaced_blocks) + len(preview.result.blocks) <= 12
                return {
                    "status": "review_required",
                    "applied": False,
                    "timezone": timezone,
                    "expires_at": preview.expires_at.isoformat(),
                    "replaced_count": len(preview.replaced_blocks),
                    "proposed_count": len(preview.result.blocks),
                    "replaced_blocks": [
                        row.model_dump(
                            mode="json", include={"title", "start_at", "end_at", "minutes"}
                        )
                        for row in preview.replaced_blocks
                    ]
                    if short
                    else [],
                    "proposed_blocks": [
                        row.model_dump(
                            mode="json", include={"title", "start_at", "end_at", "minutes"}
                        )
                        for row in preview.result.blocks
                    ]
                    if short
                    else [],
                    "full_readback_on_screen": not short,
                    "warnings": preview.result.warnings,
                    "unfitted_minutes": sum(row.minutes for row in preview.result.unfitted_work),
                    "unchanged": ["classes", "calendar events", "alarms"],
                    "message": (
                        "The full proposal is on screen. "
                        "Nothing changes until the student approves there."
                    ),
                }
            if name == "get_student_free_time":
                opening_query = OpeningArguments.model_validate(arguments)
                async with self.check.factory() as database:
                    result = await openings(
                        database, authority.user_id, authority.workspace_id, opening_query.days
                    )
                if await self.check() != authority:
                    raise WorkerAccessLost("The voice tool's owner changed")
                return {"status": "ok", **result}
            raise ValueError
        except (ValidationError, ValueError):
            result = {
                "status": "invalid_arguments",
                "message": "Choose a supported local date and number of days.",
            }
        except schedule.StudentError as error:
            result = {"status": "unavailable", "message": str(error)}
        if await self.check() != authority:
            raise WorkerAccessLost("The voice tool's owner changed")
        return result
