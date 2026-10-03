"""News contracts. Source content is data and cannot grant rights or verification."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from navox.connectors.builtin.generic_api import _sensitive_query_key
from navox.connectors.network import validate_https_endpoint, validate_public_https_origin


class SourceType(StrEnum):
    PUBLISHER = "PUBLISHER"
    WIRE_SERVICE = "WIRE_SERVICE"
    GOVERNMENT = "GOVERNMENT"
    COMPANY = "COMPANY"
    RESEARCH = "RESEARCH"
    SOCIAL = "SOCIAL"
    SYNDICATION = "SYNDICATION"
    OTHER = "OTHER"


class FeedType(StrEnum):
    RSS = "RSS"
    ATOM = "ATOM"
    API = "API"
    LICENSED_FEED = "LICENSED_FEED"
    WEBHOOK = "WEBHOOK"
    SOCIAL_API = "SOCIAL_API"


class Category(StrEnum):
    WORLD = "world"
    US = "us"
    BUSINESS = "business"
    TECHNOLOGY = "technology"
    SCIENCE = "science"


class Verification(StrEnum):
    VERIFIED = "VERIFIED"
    CORROBORATED = "CORROBORATED"
    ATTRIBUTED = "ATTRIBUTED"
    DEVELOPING = "DEVELOPING"
    UNCONFIRMED = "UNCONFIRMED"
    DISPUTED = "DISPUTED"
    CONTRADICTED = "CONTRADICTED"
    RETRACTED = "RETRACTED"


class NewsError(ValueError):
    """Fixed codes only; never put source bodies or provider exceptions in errors."""

    def __init__(self, code: str) -> None:
        self.code = code if code in ERROR_CODES else "news_unavailable"
        super().__init__(self.code)


ERROR_CODES = frozenset(
    {
        "news_unavailable",
        "news_disabled",
        "source_unavailable",
        "source_changed",
        "rights_denied",
        "rights_expired",
        "invalid_source",
        "invalid_feed",
        "feed_unavailable",
        "feed_too_large",
        "invalid_item",
        "item_unavailable",
        "story_unavailable",
        "refresh_too_soon",
        "invalid_evidence",
        "invalid_reference",
        "unsupported_claim",
        "stale_evidence",
        "conversation_unavailable",
        "rate_limited",
        "ai_unavailable",
    }
)


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone is required")
    return value.astimezone(UTC)


def stored_utc(value: datetime) -> datetime:
    # SQLite strips timezone information; application writes are always aware UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def source_url(value: str) -> str:
    parsed = urlsplit(value)
    origin = validate_public_https_origin(f"{parsed.scheme}://{parsed.netloc}", label="source_url")
    if parsed.username or parsed.password or parsed.fragment or "\\" in value:
        raise ValueError("Invalid source URL")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid source URL")
    return origin + (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")


class ContentRights(Contract):
    metadata_storage_allowed: bool = Field(default=False, strict=True)
    snippet_storage_allowed: bool = Field(default=False, strict=True)
    full_text_processing_allowed: bool = Field(default=False, strict=True)
    full_text_storage_allowed: bool = Field(default=False, strict=True)
    summary_generation_allowed: bool = Field(default=False, strict=True)
    image_display_allowed: bool = Field(default=False, strict=True)
    attribution_required: bool = Field(default=True, strict=True)
    link_required: bool = Field(default=True, strict=True)
    retention_days: int = Field(ge=1, le=3650, strict=True)
    permission_reference: str = Field(min_length=1, max_length=2048)
    reviewed_at: datetime
    expires_at: datetime

    _url = field_validator("permission_reference")(source_url)
    _time = field_validator("reviewed_at", "expires_at")(aware_utc)

    @model_validator(mode="after")
    def valid_period(self) -> ContentRights:
        if self.expires_at <= self.reviewed_at:
            raise ValueError("Rights expiry must follow review")
        if self.full_text_storage_allowed and not self.full_text_processing_allowed:
            raise ValueError("Full-text storage requires processing permission")
        return self


class SourceEvidencePolicy(Contract):
    """Operator-reviewed feed role; absent by default, never provided by source text."""

    role: Literal["ORIGINAL_REPORT", "INTERESTED_PARTY", "SYNDICATED", "SOCIAL"]
    strong_evidence_allowed: bool = Field(default=False, strict=True)
    origin_groups: frozenset[str] = Field(default=frozenset(), max_length=20)
    review_reference: str = Field(min_length=8, max_length=80)
    reviewed_at: datetime
    expires_at: datetime

    _reviewed = field_validator("reviewed_at")(aware_utc)
    _expires = field_validator("expires_at")(aware_utc)

    @model_validator(mode="after")
    def reviewed_scope(self) -> SourceEvidencePolicy:
        if self.expires_at <= self.reviewed_at:
            raise ValueError("Evidence policy requires a bounded review window")
        if self.strong_evidence_allowed and self.role != "ORIGINAL_REPORT":
            raise ValueError("Only reviewed original reporting can supply strong evidence")
        if self.role == "SYNDICATED" and not self.origin_groups:
            raise ValueError("Syndication requires reviewed origins")
        if any(not group.strip() or len(group) > 128 for group in self.origin_groups):
            raise ValueError("Invalid reviewed origin identity")
        return self


class SourceDefinition(Contract):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=3, max_length=253)
    source_type: SourceType
    region: str = Field(default="world", min_length=2, max_length=64)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    identity_verified: bool = Field(default=False, strict=True)
    feed_type: FeedType
    endpoint: str = Field(max_length=2048)
    article_domains: tuple[str, ...] = Field(min_length=1, max_length=10)
    image_domains: tuple[str, ...] = Field(default=(), max_length=20)
    category: Category
    poll_interval_seconds: int = Field(default=900, ge=60, le=86400)
    # Source/copy independence is operator reviewed, never assigned by an article or model.
    independence_group: str = Field(min_length=1, max_length=128)
    rights: ContentRights
    evidence_policy: SourceEvidencePolicy | None = None
    api_connection_id: UUID | None = None
    api_endpoint_name: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def evidence_authority(self) -> SourceDefinition:
        if self.feed_type == FeedType.API:
            if self.api_connection_id is None or self.api_endpoint_name is None:
                raise ValueError("API feeds require an owned approved credential connection")
        elif self.api_connection_id is not None or self.api_endpoint_name is not None:
            raise ValueError("Only API feeds can reference API credentials")
        policy = self.evidence_policy
        if policy is not None:
            if not self.identity_verified:
                raise ValueError("Automatic evidence requires a reviewed source identity")
            if policy.role == "ORIGINAL_REPORT" and self.source_type not in {
                SourceType.PUBLISHER,
                SourceType.WIRE_SERVICE,
            }:
                raise ValueError("This source cannot acquire original-report authority")
            if self.source_type == SourceType.SOCIAL and policy.role != "SOCIAL":
                raise ValueError("Social sources remain social evidence")
            if self.source_type == SourceType.COMPANY and policy.role != "INTERESTED_PARTY":
                raise ValueError("Company sources remain interested-party evidence")
        return self

    @field_validator("endpoint")
    @classmethod
    def endpoint_url(cls, value: str) -> str:
        return validate_https_endpoint(value)

    @field_validator("domain")
    @classmethod
    def domain_name(cls, value: str) -> str:
        origin = validate_public_https_origin(f"https://{value}")
        if urlsplit(origin).port or "." not in value:
            raise ValueError("A public domain is required")
        return value.casefold()

    @field_validator("article_domains", "image_domains")
    @classmethod
    def domains(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(cls.domain_name(value) for value in values)

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        if not self.image_domains:
            payload.pop("image_domains")
        if self.api_connection_id is None:
            payload.pop("api_connection_id")
            payload.pop("api_endpoint_name")
        if self.evidence_policy is None:
            payload.pop("evidence_policy")
        else:
            payload["evidence_policy"]["origin_groups"] = sorted(self.evidence_policy.origin_groups)
        value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()


class NewsImage(Contract):
    """A permitted publisher image, with its supplied description and credit."""

    url: str = Field(min_length=1, max_length=2048)
    alt: str = Field(min_length=1, max_length=500)
    credit: str = Field(min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def public_image_url(cls, value: str) -> str:
        value = source_url(value)
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.port not in {None, 443}
            or "." not in host
            or host.rsplit(".", 1)[-1] in {"localhost", "local", "internal", "test", "invalid"}
        ):
            raise ValueError("Invalid image URL")
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,40}", key):
                raise ValueError("Invalid image URL")
            if _sensitive_query_key(key):
                raise ValueError("Invalid image URL")
        return value

    @field_validator("alt", "credit")
    @classmethod
    def visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Image description and credit are required")
        return value.strip()


class NewsItemInput(Contract):
    external_id: str = Field(min_length=1, max_length=512)
    headline: str = Field(min_length=1, max_length=500)
    canonical_url: str = Field(max_length=2048)
    author: str | None = Field(default=None, max_length=200)
    published_at: datetime
    updated_at: datetime | None = None
    event_started_at: datetime | None = None
    event_ended_at: datetime | None = None
    description: str | None = Field(default=None, max_length=4000)
    image: NewsImage | None = None
    categories: tuple[Category, ...] = Field(default=(), max_length=5)
    language: str = Field(default="en", max_length=32)
    region: str = Field(default="world", max_length=64)

    _url = field_validator("canonical_url")(source_url)
    _published = field_validator("published_at")(aware_utc)

    @field_validator("updated_at", "event_started_at", "event_ended_at")
    @classmethod
    def optional_time(cls, value: datetime | None) -> datetime | None:
        return aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def times(self) -> NewsItemInput:
        if self.event_ended_at is not None and (
            self.event_started_at is None or self.event_ended_at < self.event_started_at
        ):
            raise ValueError("Invalid event interval")
        return self


class NewsItemRead(NewsItemInput):
    id: UUID
    source_id: UUID
    source_name: str
    source_type: SourceType
    rights_profile_id: UUID
    retrieved_at: datetime
    last_observed_at: datetime
    expires_at: datetime
    revision: int
