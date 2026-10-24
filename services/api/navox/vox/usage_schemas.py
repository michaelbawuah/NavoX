"""Content-free internal usage contracts; never browser-submitted metering."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelPricing(Contract):
    provider: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    model: str = Field(pattern=r"^[A-Za-z0-9_./:-]{1,128}$")
    version: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    max_input_tokens: int | None = Field(default=None, ge=1, strict=True)
    audio_usd_per_minute: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_text: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_text: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_text: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_audio: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cached_audio: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_audio: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class UsagePolicy(Contract):
    daily_voice_minutes: int = Field(default=60, ge=1, le=1440, strict=True)
    monthly_voice_minutes: int = Field(default=600, ge=1, le=44640, strict=True)
    daily_tokens: int = Field(default=300_000, ge=1, le=100_000_000, strict=True)
    monthly_tokens: int = Field(default=3_000_000, ge=1, le=3_100_000_000, strict=True)
    notice_fraction: Decimal = Field(default=Decimal("0.8"), gt=0, lt=1, allow_inf_nan=False)
    model_pricing: list[ModelPricing] = Field(default_factory=list, max_length=64)
    transport_usd_per_minute: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    transport_pricing_version: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")

    @model_validator(mode="after")
    def coherent(self) -> Self:
        keys = [(p.provider, p.model) for p in self.model_pricing]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate pricing entry")
        if self.transport_usd_per_minute is not None and self.transport_pricing_version is None:
            raise ValueError("Transport pricing requires a version")
        return self


class UsageCounters(Contract):
    billed_audio_ms: int = Field(default=0, ge=0, le=2**53, strict=True)
    speaking_ms: int = Field(default=0, ge=0, le=2**53, strict=True)
    idle_ms: int = Field(default=0, ge=0, le=2**53, strict=True)
    connected_ms: int = Field(default=0, ge=0, le=2**53, strict=True)
    unclassified_input: int = Field(default=0, ge=0, le=2**53, strict=True)
    unclassified_output: int = Field(default=0, ge=0, le=2**53, strict=True)
    input_text: int = Field(default=0, ge=0, le=2**53, strict=True)
    cached_text: int = Field(default=0, ge=0, le=2**53, strict=True)
    output_text: int = Field(default=0, ge=0, le=2**53, strict=True)
    input_audio: int = Field(default=0, ge=0, le=2**53, strict=True)
    cached_audio: int = Field(default=0, ge=0, le=2**53, strict=True)
    output_audio: int = Field(default=0, ge=0, le=2**53, strict=True)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.speaking_ms + self.idle_ms != self.connected_ms:
            raise ValueError("Speaking and idle must partition connected wall time")
        if self.cached_text > self.input_text or self.cached_audio > self.input_audio:
            raise ValueError("Cached tokens are a subset of input tokens")
        return self

    @property
    def total_tokens(self) -> int:
        return (
            self.input_text
            + self.output_text
            + self.input_audio
            + self.output_audio
            + self.unclassified_input
            + self.unclassified_output
        )


class UsageUpdate(Contract):
    event_id: UUID
    sequence: int = Field(ge=1, le=2**31 - 1, strict=True)
    cumulative: UsageCounters


class UsageStreamView(Contract):
    id: UUID
    call_id: UUID
    kind: Literal["transport", "provider"]
    status: Literal["open", "complete", "uncertain"]
    sequence: int
    reserved_tokens: int
    cumulative: UsageCounters
    pricing_version: str | None


class UsageTotals(Contract):
    speaking_ms: int = 0
    idle_ms: int = 0
    connected_ms: int = 0
    tokens: int = 0
    reserved_tokens: int = 0
    reserved_voice_ms: int = 0
    uncertain_voice_ms: int = 0
    estimated_cost_usd: Decimal | None = None
    known_estimated_cost_usd: Decimal = Decimal(0)
    unknown_cost_components: int = 0
    reconciled_cost_usd: Decimal | None = None
    cost_basis: Literal["estimate", "unknown", "reconciled"] = "unknown"
    transport_billing_notice: str = (
        "Connected room time includes idle time; transport providers may bill for both."
    )


class BudgetStatus(Contract):
    timezone: str
    daily: UsageTotals
    monthly: UsageTotals
    remaining_voice_ms: int
    remaining_tokens: int
    blocked: bool
    notices: list[str]
    policy_revision: int


class VoiceCreditGrant(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    stream_id: UUID
    user_id: UUID
    workspace_id: UUID
    call_id: UUID
    connection_id: UUID
    generation: int = Field(ge=0, strict=True)
    worker_nonce: UUID
    base_sequence: int = Field(ge=0, strict=True)
    base_cumulative: UsageCounters
    policy_revision: int = Field(ge=0, strict=True)
    granted_ms: int = Field(ge=1, le=1000, strict=True)
    granted_at: datetime
    expires_at: datetime
    day_end: datetime
    month_end: datetime
