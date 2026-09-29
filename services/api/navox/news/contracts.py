"""Immutable source, rights and canonical-item contracts for SPEC-006.

Only trusted source administration may create registry snapshots. Incoming news
never supplies source configuration, rights, retrieval times or verification.
"""

import ipaddress
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)


def _single_line(value: str) -> str:
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("control_characters_not_allowed")
    return unicodedata.normalize("NFC", value)


def _description(value: str) -> str:
    if any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value):
        raise ValueError("control_characters_not_allowed")
    return unicodedata.normalize("NFC", value)


def public_hostname(value: str) -> str:
    """Validate a DNS name syntactically, NOT as permission to make a request.

    A later network adapter must independently resolve and pin public addresses,
    recheck redirects and enforce its reviewed endpoint allowlist.
    """
    if not value or value != value.strip() or any(char in value for char in "/\\:@%"):
        raise ValueError("invalid_public_hostname")
    try:
        host = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("invalid_public_hostname") from None
    if len(host) > 253 or host.endswith("."):
        raise ValueError("invalid_public_hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("ip_literal_not_allowed")
    labels = host.split(".")
    if len(labels) < 2 or any(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in labels
    ):
        raise ValueError("invalid_public_hostname")
    if labels[-1].isdigit() or labels[-1] in {
        "local",
        "localhost",
        "internal",
        "home",
        "invalid",
        "test",
    }:
        raise ValueError("non_public_hostname")
    return host


def canonical_https_url(value: str) -> str:
    """Conservatively normalize a source-supplied HTTPS URL without fetching it."""
    if (
        not value
        or len(value) > 2048
        or "\\" in value
        or any(char.isspace() or unicodedata.category(char) == "Cc" for char in value)
    ):
        raise ValueError("invalid_source_url")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() != "https"
            or not parts.netloc
            or parts.username is not None
            or parts.password is not None
            or parts.port not in {None, 443}
            or parts.hostname is None
        ):
            raise ValueError("invalid_source_url")
        host = public_hostname(parts.hostname)
        query_keys = {
            key.casefold()
            for component in (parts.query, parts.fragment)
            for key, _ in parse_qsl(component)
        }
        if query_keys.intersection(
            {"access_token", "token", "api_key", "apikey", "authorization", "password", "secret"}
        ):
            raise ValueError("credential_bearing_url")
        # Preserve path case, query ordering/values and fragments. Do not invent
        # cross-publisher canonical links or erase potentially meaningful IDs.
        return urlunsplit(("https", host, parts.path or "/", parts.query, parts.fragment))
    except (ValueError, UnicodeError):
        raise ValueError("invalid_source_url") from None


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_single_line),
]
ExternalId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
    AfterValidator(_single_line),
]
Headline = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_single_line),
]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=3000),
    AfterValidator(_description),
]
Domain = Annotated[str, AfterValidator(public_hostname)]
SourceURL = Annotated[str, AfterValidator(canonical_https_url)]
Timestamp = Annotated[AwareDatetime, AfterValidator(_utc)]
Language = Annotated[
    str, StringConstraints(max_length=35, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
]
Region = Annotated[str, StringConstraints(pattern=r"^(?:[A-Z]{2}|GLOBAL)$")]


class NewsModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )


class SourceType(StrEnum):
    PUBLISHER = "PUBLISHER"
    WIRE_SERVICE = "WIRE_SERVICE"
    GOVERNMENT = "GOVERNMENT"
    COMPANY = "COMPANY"
    RESEARCH = "RESEARCH"
    SOCIAL = "SOCIAL"
    SYNDICATION = "SYNDICATION"
    OTHER = "OTHER"


class SourceStatus(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class FeedType(StrEnum):
    RSS = "RSS"
    ATOM = "ATOM"
    API = "API"
    LICENSED_FEED = "LICENSED_FEED"
    WEBHOOK = "WEBHOOK"
    SOCIAL_API = "SOCIAL_API"


class FeedStatus(StrEnum):
    DISABLED = "DISABLED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class NewsCategory(StrEnum):
    WORLD = "WORLD"
    US = "US"
    BUSINESS = "BUSINESS"
    TECHNOLOGY = "TECHNOLOGY"
    SCIENCE = "SCIENCE"
    OTHER = "OTHER"


class Operation(StrEnum):
    STORE_METADATA = "STORE_METADATA"
    PROCESS_METADATA = "PROCESS_METADATA"
    DISPLAY_METADATA = "DISPLAY_METADATA"
    STORE_SNIPPET = "STORE_SNIPPET"
    PROCESS_SNIPPET = "PROCESS_SNIPPET"
    DISPLAY_SNIPPET = "DISPLAY_SNIPPET"
    PROCESS_FULL_TEXT = "PROCESS_FULL_TEXT"
    STORE_FULL_TEXT = "STORE_FULL_TEXT"
    GENERATE_SUMMARY = "GENERATE_SUMMARY"
    DISPLAY_IMAGE = "DISPLAY_IMAGE"


class ContentKind(StrEnum):
    METADATA = "METADATA"
    SNIPPET = "SNIPPET"
    FULL_TEXT = "FULL_TEXT"


class NewsSource(NewsModel):
    id: UUID
    name: ShortText
    domain: Domain
    source_type: SourceType
    language: Language
    region: Region
    identity_verified: StrictBool = False
    status: SourceStatus = SourceStatus.REVIEW_REQUIRED
    additional_article_hosts: tuple[Domain, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_hosts(self) -> Self:
        hosts = (self.domain, *self.additional_article_hosts)
        if len(hosts) != len(set(hosts)):
            raise ValueError("duplicate_article_host")
        return self


class ContentRightsProfile(NewsModel):
    id: UUID
    source_id: UUID
    version: int = Field(ge=1)
    # Opaque reference to a separately reviewed agreement/terms record, never a
    # publisher API key, a source instruction, or proof manufactured by NavoX.
    review_reference: UUID | None = None
    reviewed_at: Timestamp | None = None
    valid_from: Timestamp | None = None
    valid_until: Timestamp | None = None
    retention_days: int = Field(default=0, ge=0, le=3650)
    metadata_storage_allowed: StrictBool = False
    metadata_processing_allowed: StrictBool = False
    metadata_display_allowed: StrictBool = False
    snippet_storage_allowed: StrictBool = False
    snippet_processing_allowed: StrictBool = False
    snippet_display_allowed: StrictBool = False
    full_text_processing_allowed: StrictBool = False
    full_text_storage_allowed: StrictBool = False
    summary_generation_allowed: StrictBool = False
    image_display_allowed: StrictBool = False
    attribution_required: StrictBool = True
    link_required: StrictBool = True

    @model_validator(mode="after")
    def valid_review_window(self) -> Self:
        if self.valid_from is not None and self.valid_until is not None:
            if self.valid_until <= self.valid_from:
                raise ValueError("invalid_rights_window")
        if self.reviewed_at is not None and self.valid_until is not None:
            if self.reviewed_at >= self.valid_until:
                raise ValueError("review_after_expiry")
        return self


class NewsSourceFeed(NewsModel):
    id: UUID
    source_id: UUID
    rights_profile_id: UUID
    feed_type: FeedType
    # Resolve through SPEC-003 in the later transport layer. No credentials or
    # arbitrary caller-selected fetch URL belong in this public-data contract.
    endpoint_reference: UUID
    credential_reference: UUID | None = None
    category: NewsCategory
    region: Region
    language: Language
    poll_interval_seconds: int = Field(default=300, ge=60, le=86400)
    status: FeedStatus = FeedStatus.DISABLED


class NewsRegistry(NewsModel):
    """Trusted, immutable snapshot; not a database or an authorization API."""

    sources: tuple[NewsSource, ...] = Field(default=(), max_length=1000)
    rights_profiles: tuple[ContentRightsProfile, ...] = Field(default=(), max_length=5000)
    feeds: tuple[NewsSourceFeed, ...] = Field(default=(), max_length=5000)

    @model_validator(mode="after")
    def references_are_consistent(self) -> Self:
        sources = {item.id: item for item in self.sources}
        rights = {item.id: item for item in self.rights_profiles}
        feeds = {item.id: item for item in self.feeds}
        if len(sources) != len(self.sources) or len(rights) != len(self.rights_profiles):
            raise ValueError("duplicate_registry_id")
        if len(feeds) != len(self.feeds):
            raise ValueError("duplicate_registry_id")
        revisions = {(item.source_id, item.version) for item in self.rights_profiles}
        if len(revisions) != len(self.rights_profiles):
            raise ValueError("duplicate_rights_revision")
        for profile in self.rights_profiles:
            if profile.source_id not in sources:
                raise ValueError("unknown_rights_source")
        for feed in self.feeds:
            if feed.source_id not in sources or feed.rights_profile_id not in rights:
                raise ValueError("unknown_feed_reference")
            if rights[feed.rights_profile_id].source_id != feed.source_id:
                raise ValueError("cross_source_rights_reference")
        return self


class IncomingNewsMetadata(NewsModel):
    """Already-decoded adapter metadata, not raw HTML, XML or model output.

    The adapter must check source rights before its network read. Datetime inputs
    must be timezone-aware objects; missing dates remain unknown. Unknown fields
    (including full text, images, execution instructions and verification) fail.
    """

    external_id: ExternalId | None = None
    headline: Headline
    canonical_url: SourceURL
    author: ShortText | None = None
    published_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    event_started_at: Timestamp | None = None
    event_ended_at: Timestamp | None = None
    description: Description | None = None
    categories: tuple[NewsCategory, ...] = Field(default=(), max_length=16)
    language: Language | None = None
    region: Region | None = None

    @model_validator(mode="after")
    def timestamps_are_consistent(self) -> Self:
        if self.published_at is not None and self.updated_at is not None:
            if self.updated_at < self.published_at:
                raise ValueError("update_before_publication")
        if self.event_started_at is not None and self.event_ended_at is not None:
            if self.event_ended_at < self.event_started_at:
                raise ValueError("event_end_before_start")
        if len(self.categories) != len(set(self.categories)):
            raise ValueError("duplicate_category")
        return self


class NewsItem(IncomingNewsMetadata):
    id: UUID
    source_id: UUID
    feed_id: UUID
    source_type: SourceType
    language: Language
    region: Region
    # First acquisition time is retained on refresh; retries cannot restart TTL.
    retrieved_at: Timestamp
    expires_at: Timestamp
    rights_profile_id: UUID
    rights_version: int = Field(ge=1)

    @model_validator(mode="after")
    def retention_window_is_positive(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("invalid_retention_window")
        return self


class NewsPresentation(NewsModel):
    """Rights-filtered metadata. Not an AI summary or a verified-story verdict."""

    id: UUID
    headline: Headline
    source_name: ShortText
    original_source_url: SourceURL
    author: ShortText | None = None
    description: Description | None = None
    published_at: Timestamp | None = None
    updated_at: Timestamp | None = None
    categories: tuple[NewsCategory, ...]
    language: Language
    region: Region
