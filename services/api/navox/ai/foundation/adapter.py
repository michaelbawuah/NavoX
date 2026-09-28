"""Provider protocol only. No client, credentials, network, state writes, or tools."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from navox.ai.foundation.contracts import (
    Capability,
    Contract,
    FinishReason,
    Identifier,
    JSONDocument,
    Money,
    Provider,
    Usage,
    VersionedRef,
)
from navox.ai.foundation.registry import ModelDefinition


class ErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    REFUSAL = "refusal"
    UNKNOWN = "unknown"


class ProviderError(Contract):
    """No raw upstream messages, response bodies, request URLs, or credentials."""

    code: ErrorCode
    retry_after_ms: Annotated[int, Field(strict=True, ge=0, le=86_400_000)] | None = None


class ProviderHealth(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"


class ProviderRequest(Contract):
    """Gateway-built request after authorization, minimization, and model selection."""

    task_id: UUID
    model: Identifier
    instructions: Annotated[str, Field(min_length=1, max_length=100_000, repr=False)]
    context: JSONDocument = Field(repr=False)
    output_schema: JSONDocument = Field(repr=False)
    schema_ref: VersionedRef
    prompt_ref: VersionedRef
    max_output_tokens: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]


class ProviderResponse(Contract):
    model: Identifier
    output: JSONDocument = Field(repr=False)
    finish_reason: FinishReason
    usage: Usage = Field(default_factory=Usage)


@runtime_checkable
class AIProviderAdapter(Protocol):
    @property
    def provider(self) -> Provider: ...

    async def list_models(self) -> tuple[ModelDefinition, ...]: ...

    def capabilities(self, model: str) -> frozenset[Capability]: ...

    async def execute(self, request: ProviderRequest) -> ProviderResponse: ...

    def normalize_response(self, response: Mapping[str, object]) -> ProviderResponse: ...

    def estimate_cost(self, model: str, usage: Usage) -> Money | None: ...

    def classify_error(self, error: Exception) -> ProviderError: ...

    async def health_probe(self) -> ProviderHealth: ...
