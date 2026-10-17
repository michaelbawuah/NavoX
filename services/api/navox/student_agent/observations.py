"""Minimized student result shapes. A tool name cannot smuggle files or hidden context."""

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


def obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def text(maximum: int, minimum: int = 1) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def items(schema: dict[str, Any], maximum: int) -> dict[str, Any]:
    return {"type": "array", "items": schema, "maxItems": maximum}


IDENTIFIER = {"type": "string", "format": "uuid"}
INSTANT = {"type": "string", "format": "date-time"}
DAY = {"type": "string", "format": "date"}
ZONE = text(100)
REVISION = {"type": "integer", "minimum": 0, "maximum": 2_147_483_647}
MINUTES = {"type": "integer", "minimum": 0, "maximum": 10080}
OCCURRENCE = obj(
    {
        "id": {"type": "string", "pattern": r"^[a-fA-F0-9-]{36}:\d{4}-\d{2}-\d{2}$"},
        "course_code": text(40, 0),
        "course_name": text(200),
        "start_at": INSTANT,
        "end_at": INSTANT,
        "building": nullable(text(200)),
        "room": nullable(text(100)),
    }
)
COURSE = obj(
    {
        "id": IDENTIFIER,
        "code": text(40, 0),
        "name": text(200),
        "professor": nullable(text(120)),
        "term_start": DAY,
        "term_end": DAY,
    }
)
TASK = obj(
    {
        "id": IDENTIFIER,
        "title": text(200),
        "course_id": nullable(IDENTIFIER),
        "status": {"enum": ["active", "completed", "archived"]},
        "due_at": nullable(INSTANT),
        "remaining_minutes": MINUTES,
    }
)
FEED = obj(
    {
        "id": IDENTIFIER,
        "name": text(160),
        "domain": {
            "type": "string",
            "minLength": 1,
            "maxLength": 253,
            "pattern": r"^[a-zA-Z0-9.-]+$",
        },
        "state": {"enum": ["active", "paused"]},
        "revision": REVISION,
        "last_synced_at": nullable(INSTANT),
    }
)
PLAN_RESULT = obj(
    {
        "start_at": INSTANT,
        "end_at": INSTANT,
        "blocks": items(
            obj(
                {
                    "task_id": IDENTIFIER,
                    "title": text(200),
                    "start_at": INSTANT,
                    "end_at": INSTANT,
                    "minutes": MINUTES,
                    "focus_window": {"type": "boolean"},
                }
            ),
            600,
        ),
        "unfitted_work": items(
            obj(
                {
                    "task_id": IDENTIFIER,
                    "title": text(200),
                    "minutes": MINUTES,
                    "due_at": nullable(INSTANT),
                    "reason": {
                        "enum": [
                            "deadline_passed",
                            "cannot_meet_deadline",
                            "outside_horizon",
                            "insufficient_time",
                        ]
                    },
                }
            ),
            200,
        ),
        "capacities": items(
            obj(
                {
                    "day": DAY,
                    "free_minutes": MINUTES,
                    "buffer_minutes": MINUTES,
                    "planned_minutes": MINUTES,
                }
            ),
            14,
        ),
        "warnings": items(text(500), 30),
        "planning_only": {"const": True},
    }
)
SCHEMAS = {
    "clock.read": obj({"server_now": INSTANT, "timezone": ZONE}),
    "classes.lookup": obj(
        {
            "mode": {"enum": ["now", "next", "at", "day"]},
            "server_now": INSTANT,
            "timezone": ZONE,
            "date": DAY,
            "at": INSTANT,
            "occurrences": items(OCCURRENCE, 250),
            "next_upcoming": nullable(OCCURRENCE),
            "reason": {"enum": ["matches", "no_classes", "after_last_class", "break"]},
            "conflicts": items(obj({"left_id": text(100), "right_id": text(100)}), 500),
        }
    ),
    "course.info": obj({"courses": items(COURSE, 10), "truncated": {"type": "boolean"}}),
    "tasks.read": obj({"tasks": items(TASK, 12), "truncated": {"type": "boolean"}}),
    "plan.preview": obj(
        {
            "preview_id": IDENTIFIER,
            "preview_hash": {"type": "string", "pattern": r"^[a-f0-9]{64}$"},
            "expires_at": INSTANT,
            "base_revision": REVISION,
            "result": PLAN_RESULT,
            "requires_acceptance": {"const": True},
        }
    ),
    "feeds.read": obj({"feeds": items(FEED, 20)}),
    "feed.refresh_review": obj(
        {
            "review_kind": {"const": "feed_refresh"},
            "feed_id": IDENTIFIER,
            "domain": FEED["properties"]["domain"],
            "revision": REVISION,
            "requires_confirmation": {"const": True},
            "refresh_queued": {"const": False},
        }
    ),
}
VALIDATORS = {
    name: Draft202012Validator(schema, format_checker=FormatChecker())
    for name, schema in SCHEMAS.items()
}


def validate_observation_data(name: str, data: dict[str, Any]) -> None:
    validator = VALIDATORS.get(name)
    if validator is None:
        raise ValueError("Unknown student observation")
    try:
        validator.validate(data)
        if name in {"clock.read", "classes.lookup"}:
            ZoneInfo(data["timezone"])
    except (ValidationError, ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
        raise ValueError("Student observation contains unknown or unverified fields") from None
