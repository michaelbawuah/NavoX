"""Explicit, bounded subscription commands; evidence cannot confer action authority."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ObligationType = Literal[
    "SUBSCRIPTION",
    "MEMBERSHIP",
    "FREE_TRIAL",
    "SOFTWARE_LICENSE",
    "DOMAIN_RENEWAL",
    "SERVICE_PLAN",
    "RECURRING_BILL",
    "OTHER_RECURRING",
]
BillingInterval = Literal["DAY", "WEEK", "MONTH", "QUARTER", "YEAR", "UNKNOWN"]
ManualStatus = Literal["CANDIDATE", "TRIAL", "ACTIVE", "PAUSED", "EXPIRED", "UNKNOWN"]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Name = Annotated[str, Field(min_length=1, max_length=256)]


class SubscriptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID | None = None
    name: Name
    merchant_name: Name | None = None
    obligation_type: ObligationType = "SUBSCRIPTION"
    plan_name: Name | None = None
    status: ManualStatus = "ACTIVE"
    billing_amount: Money | None = None
    billing_currency: Currency | None = None
    billing_interval: BillingInterval = "UNKNOWN"
    interval_count: int = Field(default=1, ge=1, le=1200)
    next_renewal_at: datetime | None = None
    trial_ends_at: datetime | None = None
    trial_conversion_amount: Money | None = None
    trial_conversion_interval: BillingInterval | None = None
    trial_conversion_interval_count: int = Field(default=1, ge=1, le=1200)
    auto_renew: bool | None = None
    started_at: datetime | None = None
    cancellation_connection_id: UUID | None = None
    cancellation_external_resource_id: str | None = Field(
        default=None, min_length=1, max_length=512
    )

    @field_validator("next_renewal_at", "trial_ends_at", "started_at")
    @classmethod
    def aware_date(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Dates must include a timezone")
        return value

    @model_validator(mode="after")
    def coherent_money_and_target(self) -> Self:
        if (
            self.billing_amount is not None or self.trial_conversion_amount is not None
        ) and self.billing_currency is None:
            raise ValueError("An amount requires its billing currency")
        if bool(self.cancellation_connection_id) != bool(self.cancellation_external_resource_id):
            raise ValueError("Cancellation connection and resource must be supplied together")
        return self


class SubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Name | None = None
    obligation_type: ObligationType | None = None
    plan_name: Name | None = None
    status: ManualStatus | None = None
    billing_amount: Money | None = None
    billing_currency: Currency | None = None
    billing_interval: BillingInterval | None = None
    interval_count: int | None = Field(default=None, ge=1, le=1200)
    next_renewal_at: datetime | None = None
    trial_ends_at: datetime | None = None
    trial_conversion_amount: Money | None = None
    trial_conversion_interval: BillingInterval | None = None
    trial_conversion_interval_count: int | None = Field(default=None, ge=1, le=1200)
    auto_renew: bool | None = None
    started_at: datetime | None = None
    cancellation_connection_id: UUID | None = None
    cancellation_external_resource_id: str | None = Field(
        default=None, min_length=1, max_length=512
    )

    @field_validator("next_renewal_at", "trial_ends_at", "started_at")
    @classmethod
    def aware_date(cls, value: datetime | None) -> datetime | None:
        return SubscriptionCreate.aware_date(value)

    @model_validator(mode="after")
    def no_null_required_fields(self) -> Self:
        required = {
            "name",
            "obligation_type",
            "status",
            "billing_interval",
            "interval_count",
            "trial_conversion_interval_count",
        }
        if any(getattr(self, field) is None for field in self.model_fields_set & required):
            raise ValueError("Required fields cannot be cleared")
        return self


class SubscriptionKeep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID | None = None
    review_after: datetime | None = None

    @field_validator("review_after")
    @classmethod
    def aware_date(cls, value: datetime | None) -> datetime | None:
        return SubscriptionCreate.aware_date(value)


class SubscriptionReview(SubscriptionKeep):
    decision: Literal["CONFIRM", "NOT_MINE", "SNOOZE"]


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    merchant_id: UUID
    name: str
    obligation_type: str
    plan_name: str | None
    status: str
    confidence: Decimal
    billing_amount: Decimal | None
    billing_currency: str | None
    billing_interval: str
    interval_count: int
    monthly_equivalent: Decimal | None = None
    yearly_equivalent: Decimal | None = None
    next_renewal_at: datetime | None
    trial_ends_at: datetime | None
    trial_conversion_amount: Decimal | None
    trial_conversion_interval: str | None
    trial_conversion_interval_count: int
    auto_renew: bool | None
    started_at: datetime | None
    last_verified_at: datetime | None
    revision: int
    attention_reasons: list[str] = Field(default_factory=list)
    verification_status: str | None = None
    review_state: str
    review_after: datetime | None
    cancellation_connection_id: UUID | None
    cancellation_external_resource_id: str | None
    created_at: datetime
    updated_at: datetime


class CurrencyTotal(BaseModel):
    currency: str
    monthly_equivalent: Decimal
    yearly_equivalent: Decimal
    obligation_count: int


class SubscriptionSummary(BaseModel):
    currency_totals: list[CurrencyTotal]
    known_count: int
    unknown_cost_count: int
    coverage: Literal["INCOMPLETE"] = "INCOMPLETE"
    label: str = "Known recurring subscriptions"


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    obligation_id: UUID
    evidence_type: str
    source_type: str
    connection_id: UUID | None
    external_resource_id: str | None
    merchant_text: str | None
    plan_text: str | None
    amount: Decimal | None
    currency: str | None
    billing_interval: str | None
    interval_count: int
    effective_at: datetime | None
    renewal_at: datetime | None
    confidence: Decimal
    observed_at: datetime


class PriceHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    amount: Decimal
    currency: str
    billing_interval: str
    interval_count: int
    effective_from: datetime
    effective_until: datetime | None
    evidence_id: UUID
