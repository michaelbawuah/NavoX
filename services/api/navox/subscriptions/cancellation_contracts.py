"""Credential-free contracts crossing the SPEC-004/SPEC-003 boundary."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CancellationMethod = Literal[
    "PROVIDER_API",
    "CONNECTED_PROVIDER_ACTION",
    "BROWSER_ASSISTED",
    "VERIFIED_MANAGEMENT_URL",
    "GUIDED_MANUAL",
    "UNSUPPORTED",
]


class CancellationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    obligation_id: UUID
    workspace_id: UUID
    user_id: UUID
    connection_id: UUID | None = None
    external_resource_id: str | None = None
    merchant_id: UUID
    merchant_domain: str | None = None
    name: str
    plan_name: str | None = None
    billing_amount: Decimal | None = None
    billing_currency: str | None = None
    billing_interval: str | None = None
    billing_interval_count: int = 1
    next_renewal_at: datetime | None = None
    auto_renew: bool | None = None
    revision: str


class CancellationEconomics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_name: str | None = None
    billing_amount: Decimal | None = None
    billing_currency: str | None = None
    billing_interval: str | None = None
    billing_interval_count: int = 1
    next_renewal_at: datetime | None = None
    auto_renew: bool | None = None


class CancellationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: CancellationTarget
    method: CancellationMethod
    provider_revision: str
    provider_state: str
    economics: CancellationEconomics | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    expected_effect: str
    access_ends_at: datetime | None = None
    fee: Decimal | None = None
    refund: str | None = None
    management_url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    inspected_at: datetime
    expires_at: datetime


class CancellationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["SUBMITTED", "AWAITING_USER", "UNCERTAIN", "FAILED"]
    external_request_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class CancellationVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["VERIFIED_CANCELLED", "VERIFIED_ACTIVE", "VERIFICATION_PENDING", "CONTRADICTED"]
    source_type: Literal[
        "provider_state", "provider_page", "confirmation_email", "user_confirmation"
    ]
    external_resource_id: str | None = None
    provider_revision: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)
    observed_at: datetime


class CancelSubscriptionCapability(Protocol):
    async def inspect(self, target: CancellationTarget) -> CancellationPreview: ...
    async def execute(
        self, preview: CancellationPreview, idempotency_key: str
    ) -> CancellationSubmission: ...
    async def verify(
        self, preview: CancellationPreview, submission: CancellationSubmission
    ) -> CancellationVerification: ...


class PrepareCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class ConfirmCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class CancellationAttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    obligation_id: UUID
    status: str
    method: str
    verification_status: str
    payload_hash: str
    preview: CancellationPreview
    expires_at: datetime | None
    failure_code: str | None
