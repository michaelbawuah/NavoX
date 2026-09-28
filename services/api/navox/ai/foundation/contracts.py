"""SPEC-005 value contracts. These types confer no authorization or execution rights."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
]
Version = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^v[1-9][0-9]*(?:\.[0-9]+)*$")]
TokenCount = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]
Money = Annotated[Decimal, Field(ge=0, le=1_000_000, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )


class Provider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    XAI = "xai"


class Capability(StrEnum):
    TEXT = "text"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_PROPOSALS = "tool_proposals"
    VISION = "vision"
    EMBEDDINGS = "embeddings"


class TaskType(StrEnum):
    EXTRACT = "extract"
    CLASSIFY = "classify"
    REASON = "reason"
    PLAN = "plan"
    SUMMARIZE = "summarize"
    RANK = "rank"
    EMBED = "embed"
    DRAFT_COMMUNICATION = "draft_communication"


class Profile(StrEnum):
    EXTRACTION_FAST = "EXTRACTION_FAST"
    EXTRACTION_HIGH_ACCURACY = "EXTRACTION_HIGH_ACCURACY"
    REASONING_STANDARD = "REASONING_STANDARD"
    REASONING_HIGH = "REASONING_HIGH"
    PLANNING_HIGH = "PLANNING_HIGH"
    SUMMARIZATION_FAST = "SUMMARIZATION_FAST"
    MULTIMODAL_STANDARD = "MULTIMODAL_STANDARD"
    NEWS_SYNTHESIS = "NEWS_SYNTHESIS"
    CODE_REASONING = "CODE_REASONING"
    ASSISTANT_INTERACTIVE = "ASSISTANT_INTERACTIVE"
    ASSISTANT_HIGH_REASONING = "ASSISTANT_HIGH_REASONING"


class Sensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    RESTRICTED = "RESTRICTED"

    @property
    def rank(self) -> int:
        return tuple(Sensitivity).index(self)


class LatencyClass(StrEnum):
    INTERACTIVE = "INTERACTIVE"
    BACKGROUND = "BACKGROUND"
    BATCH = "BATCH"


class QualityClass(StrEnum):
    STANDARD = "STANDARD"
    HIGH = "HIGH"


class VersionedRef(Contract):
    name: Identifier
    version: Version


class ContextReference(Contract):
    """Opaque references, never source bodies. The context builder must resolve consent."""

    workspace_id: UUID
    user_id: UUID
    source_id: UUID
    connection_id: UUID
    sensitivity: Sensitivity


class ProviderGrant(Contract):
    provider: Provider
    sensitivities: frozenset[Sensitivity] = Field(default_factory=frozenset)


class ProviderPolicy(Contract):
    """Server-created snapshot; never deserialize model/source content into this type."""

    workspace_id: UUID
    user_id: UUID
    revision: Annotated[int, Field(strict=True, ge=1)]
    grants: tuple[ProviderGrant, ...] = Field(default=(), max_length=4)
    allow_fallback: Annotated[bool, Field(strict=True)] = False
    max_fallbacks: Annotated[int, Field(strict=True, ge=0, le=3)] = 0

    @model_validator(mode="after")
    def check_policy(self) -> Self:
        if len({grant.provider for grant in self.grants}) != len(self.grants):
            raise ValueError("Duplicate provider grant")
        if not self.allow_fallback and self.max_fallbacks:
            raise ValueError("Fallback count requires explicit fallback permission")
        return self

    def permits(self, provider: Provider, sensitivity: Sensitivity) -> bool:
        return any(
            grant.provider == provider and sensitivity in grant.sensitivities
            for grant in self.grants
        )


class AITask(Contract):
    """Feature-facing request. No provider model ID, credentials, or action authority."""

    contract_version: str = Field(default="ai-task.v1", pattern=r"^ai-task\.v1$")
    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    user_id: UUID
    trace_id: UUID = Field(default_factory=uuid4)
    task_type: TaskType
    profile: Profile
    capability_requirements: frozenset[Capability] = Field(min_length=1)
    context_references: tuple[ContextReference, ...] = Field(default=(), max_length=32)
    output_schema: VersionedRef
    prompt: VersionedRef
    sensitivity: Sensitivity
    latency_class: LatencyClass
    quality_class: QualityClass
    max_cost: Money
    cost_currency: str = Field(default="USD", pattern=r"^USD$")
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
    provider_policy: ProviderPolicy
    preferred_provider: Provider | None = None

    @model_validator(mode="after")
    def check_scope(self) -> Self:
        scope = (self.workspace_id, self.user_id)
        if (self.provider_policy.workspace_id, self.provider_policy.user_id) != scope:
            raise ValueError("Provider policy scope mismatch")
        seen: set[UUID] = set()
        for reference in self.context_references:
            if (reference.workspace_id, reference.user_id) != scope:
                raise ValueError("Context reference scope mismatch")
            if reference.sensitivity.rank > self.sensitivity.rank:
                raise ValueError("Task sensitivity cannot downgrade referenced context")
            if reference.source_id in seen:
                raise ValueError("Duplicate context reference")
            seen.add(reference.source_id)
        return self


def _reject_constant(_: str) -> object:
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _check_json_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > 64:
                raise ValueError("JSON nesting exceeds the depth limit")
        elif character in "]}":
            depth -= 1


class JSONDocument(Contract):
    """Immutable, bounded JSON transport; not schema or semantic validation."""

    text: Annotated[str, Field(min_length=1, max_length=262_144, repr=False)]

    @field_validator("text")
    @classmethod
    def check_json(cls, text: str) -> str:
        try:
            _check_json_depth(text)
            value = json.loads(
                text, parse_constant=_reject_constant, object_pairs_hook=_unique_object
            )
            normalized = json.dumps(
                value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (ValueError, RecursionError, OverflowError) as exc:
            raise ValueError("Invalid JSON document") from exc
        if len(normalized) > 262_144:
            raise ValueError("JSON document exceeds normalized size limit")
        return normalized


class Usage(Contract):
    """Unknown usage stays unknown; it is not recorded as a measured zero."""

    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_PROPOSALS = "tool_proposals"
    REFUSAL = "refusal"


class AIResult(Contract):
    """Untrusted result envelope. Validation flags are assertions, not permissions."""

    contract_version: str = Field(default="ai-result.v1", pattern=r"^ai-result\.v1$")
    task_id: UUID
    workspace_id: UUID
    user_id: UUID
    trace_id: UUID
    provider: Provider
    model: Identifier
    output: JSONDocument = Field(repr=False)
    finish_reason: FinishReason
    usage: Usage = Field(default_factory=Usage)
    latency_ms: Annotated[int, Field(strict=True, ge=0)]
    estimated_cost: Money | None = None
    cost_currency: str = Field(default="USD", pattern=r"^USD$")
    output_schema: VersionedRef
    prompt: VersionedRef
    fallback_count: Annotated[int, Field(strict=True, ge=0, le=3)] = 0
    schema_validated: Annotated[bool, Field(strict=True)] = False
    semantic_validated: Annotated[bool, Field(strict=True)] = False
    policy_validated: Annotated[bool, Field(strict=True)] = False


def validate_result_binding(task: AITask, result: AIResult) -> None:
    """Check attribution only; this does not validate content or authorize any action."""

    expected = (
        task.id,
        task.workspace_id,
        task.user_id,
        task.trace_id,
        task.output_schema,
        task.prompt,
    )
    actual = (
        result.task_id,
        result.workspace_id,
        result.user_id,
        result.trace_id,
        result.output_schema,
        result.prompt,
    )
    if actual != expected:
        raise ValueError("AI result attribution mismatch")
    if not task.provider_policy.permits(result.provider, task.sensitivity):
        raise ValueError("AI result provider not permitted by task policy")
    if result.fallback_count > task.provider_policy.max_fallbacks:
        raise ValueError("AI result exceeds task fallback limit")
