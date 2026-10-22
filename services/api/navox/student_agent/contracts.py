"""Strict proposal contracts. Tool results and review tokens never confer approval."""

import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import Field, JsonValue, field_validator, model_validator

from navox.ai.foundation.contracts import Contract
from navox.student_agent.observations import validate_observation_data

MAX_OBSERVATION_BYTES = 8192
MAX_OBSERVATIONS_BYTES = 32768
MAX_SELECTION_BODY_BYTES = 40960
MAX_SELECTION_COST = Decimal("0.005")
MetadataToolName = Literal[
    "clock.read",
    "classes.lookup",
    "course.info",
    "tasks.read",
    "plan.preview",
    "feeds.read",
    "feed.refresh_review",
]
ToolName = Literal[
    "clock.read",
    "classes.lookup",
    "course.info",
    "tasks.read",
    "plan.preview",
    "feeds.read",
    "feed.refresh_review",
    "course.materials.search",
    "course.materials.page",
]


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


_MANIFEST: dict[str, Any] = json.loads(Path(__file__).with_name("tool_manifest.json").read_text())
MANIFEST_HASH = hashlib.sha256(canonical(_MANIFEST).encode()).hexdigest()
_TOOLS: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in _MANIFEST["tools"]}
_VALIDATORS = {
    name: Draft202012Validator(tool["input_schema"], format_checker=FormatChecker())
    for name, tool in _TOOLS.items()
}


def tool_manifest() -> dict[str, Any]:
    """Return a fresh trusted manifest, never a caller-supplied tool definition."""
    return deepcopy(_MANIFEST)


class Observation(Contract):
    call_id: UUID
    # Material reads terminate at the host. Even an otherwise valid material
    # result must never enter the metadata-only selector context.
    name: MetadataToolName
    effect: Literal["read", "review"]
    data: dict[str, JsonValue] = Field(repr=False)

    @model_validator(mode="after")
    def bounded_untrusted_result(self) -> Self:
        if self.effect != _TOOLS[self.name]["effect"]:
            raise ValueError("Tool observation effect does not match the registry")
        validate_observation_data(self.name, self.data)
        if len(canonical(self.data).encode()) > MAX_OBSERVATION_BYTES:
            raise ValueError("Tool observation exceeds its byte limit")
        return self


class SelectorRequest(Contract):
    session_id: UUID
    turn_id: UUID
    step: int = Field(strict=True, ge=1, le=4)
    question: str = Field(min_length=1, max_length=500, repr=False)
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    observations: tuple[Observation, ...] = Field(default=(), max_length=4, repr=False)
    previous_error: Literal["invalid_selection"] | None = None
    max_cost: Decimal = Field(
        default=MAX_SELECTION_COST, ge=0, le=MAX_SELECTION_COST, allow_inf_nan=False
    )

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("A bounded question without control characters is required")
        return value

    @model_validator(mode="after")
    def bound_manifest_and_results(self) -> Self:
        if self.manifest_hash != MANIFEST_HASH:
            raise ValueError("The student tool manifest changed")
        if len({item.call_id for item in self.observations}) != len(self.observations):
            raise ValueError("Tool observation call identifiers must be distinct")
        if (
            len(canonical([item.model_dump(mode="json") for item in self.observations]).encode())
            > MAX_OBSERVATIONS_BYTES
        ):
            raise ValueError("Tool observations exceed their combined byte limit")
        return self


class ToolSelection(Contract):
    kind: Literal["tool"]
    name: ToolName
    args: dict[str, JsonValue]

    @model_validator(mode="after")
    def registered_arguments(self) -> Self:
        if len(canonical(self.args).encode()) > 2048:
            raise ValueError("Tool arguments exceed their byte limit")
        schema = _TOOLS[self.name]["input_schema"]
        # Strict provider grammars represent optional properties as null. The
        # proposal returned to the host contains only actual arguments.
        required = set(schema.get("required", []))
        normalized = {
            key: value
            for key, value in self.args.items()
            if value is not None or key in required or key not in schema["properties"]
        }
        try:
            _VALIDATORS[self.name].validate(normalized)
        except SchemaValidationError:
            raise ValueError("Tool arguments do not match the registered schema") from None
        if self.name == "classes.lookup":
            selectors = sum(
                normalized.get(key) is not None for key in ("date", "day_offset", "weekday")
            )
            if selectors > 1:
                raise ValueError("Choose one date selector")
            if normalized["mode"] == "at" and "time" not in normalized:
                raise ValueError("A time is required for an at-time lookup")
            if normalized["mode"] == "day" and "time" in normalized:
                raise ValueError("A day lookup does not accept a clock time")
            if normalized["mode"] in {"now", "next"} and (selectors or "time" in normalized):
                raise ValueError("Current class lookup uses the server clock")
        if "date" in normalized:
            value = normalized["date"]
            if not isinstance(value, str):
                raise ValueError("A valid date is required")
            date.fromisoformat(value)
        if self.name == "course.materials.search":
            query = normalized["query"]
            if (
                not isinstance(query, str)
                or not 1 <= len(re.findall(r"\w+", query)) <= 8
                or any(ord(character) < 32 for character in query)
            ):
                raise ValueError("Search for one to eight literal words")
        object.__setattr__(self, "args", normalized)
        return self


class FinishSelection(Contract):
    kind: Literal["finish"]
    evidence_call_ids: tuple[UUID, ...] = Field(min_length=1, max_length=4)

    @field_validator("evidence_call_ids")
    @classmethod
    def distinct_evidence(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Evidence call identifiers must be distinct")
        return value


class ClarifySelection(Contract):
    kind: Literal["clarify"]
    question: str = Field(min_length=1, max_length=240)

    @field_validator("question")
    @classmethod
    def meaningful_clarification(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("A concise clarification is required")
        return value


Selection = Annotated[
    ToolSelection | FinishSelection | ClarifySelection, Field(discriminator="kind")
]


class SelectorOutput(Contract):
    selection: Selection


class SelectorResponse(Contract):
    turn_id: UUID
    step: int = Field(strict=True, ge=1, le=4)
    manifest_hash: str
    selection: Selection
    task_id: UUID
    trace_id: UUID
    estimated_cost: Decimal | None = Field(ge=0, le=MAX_SELECTION_COST)
    actions_executed: Literal[False] = False


class ClockResponse(Contract):
    server_now: datetime
    timezone: str


class SessionAccessResponse(Contract):
    session_id: UUID
    mode: Literal["PERSONAL"] = "PERSONAL"
    status: Literal["active"] = "active"
    actions_executed: Literal[False] = False


def selector_output_schema() -> dict[str, Any]:
    """Concrete tool argument variants for structured-output provider grammars."""
    variants = []
    for name, tool in _TOOLS.items():
        args = deepcopy(tool["input_schema"])
        required = set(args.get("required", []))
        for key, value in tuple(args["properties"].items()):
            if key not in required:
                args["properties"][key] = {"anyOf": [value, {"type": "null"}]}
        args["required"] = list(args["properties"])
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "name", "args"],
                "properties": {
                    "kind": {"type": "string", "const": "tool"},
                    "name": {"type": "string", "const": name},
                    "args": args,
                },
            }
        )
    for model in (FinishSelection, ClarifySelection):
        variants.append(model.model_json_schema())
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["selection"],
        "properties": {"selection": {"anyOf": variants}},
    }
