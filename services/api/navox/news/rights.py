"""Fail-closed, content-free rights decisions against a supplied current snapshot.

These functions do not grant legal rights, query a database, make provider calls,
log content or authorize side effects. The integration layer must supply the
latest authoritative registry, never a source-authored or cached grant snapshot.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError

from navox.news.contracts import (
    ContentKind,
    ContentRightsProfile,
    FeedStatus,
    NewsItem,
    NewsRegistry,
    NewsSource,
    NewsSourceFeed,
    Operation,
    SourceStatus,
)


class DenialCode(StrEnum):
    INVALID_REGISTRY = "invalid_registry"
    INVALID_CLOCK = "invalid_clock"
    INVALID_OPERATION = "invalid_operation"
    UNKNOWN_FEED = "unknown_feed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_IDENTITY_UNVERIFIED = "source_identity_unverified"
    FEED_UNAVAILABLE = "feed_unavailable"
    REVIEW_MISSING = "review_missing"
    REVIEW_NOT_EFFECTIVE = "review_not_effective"
    RIGHTS_NOT_EFFECTIVE = "rights_not_effective"
    RIGHTS_EXPIRED = "rights_expired"
    PERMISSION_DENIED = "permission_denied"
    RETENTION_DENIED = "retention_denied"
    INVALID_ITEM = "invalid_item"
    ITEM_BINDING_MISMATCH = "item_binding_mismatch"
    STALE_RIGHTS_BINDING = "stale_rights_binding"
    ITEM_EXPIRED = "item_expired"
    ITEM_TIME_INVALID = "item_time_invalid"
    INVALID_PAYLOAD = "invalid_payload"
    SOURCE_URL_MISMATCH = "source_url_mismatch"
    REFRESH_ID_MISMATCH = "refresh_id_mismatch"


class NewsBoundaryError(ValueError):
    """An allowlisted code only: never include input values or exception dumps."""

    def __init__(self, code: DenialCode) -> None:
        self.code = code
        super().__init__(f"news_boundary:{code.value}")


@dataclass(frozen=True)
class ResolvedFeed:
    source: NewsSource
    rights: ContentRightsProfile
    feed: NewsSourceFeed


_PERMISSION_FIELDS: dict[Operation, str] = {
    Operation.STORE_METADATA: "metadata_storage_allowed",
    Operation.PROCESS_METADATA: "metadata_processing_allowed",
    Operation.DISPLAY_METADATA: "metadata_display_allowed",
    Operation.STORE_SNIPPET: "snippet_storage_allowed",
    Operation.PROCESS_SNIPPET: "snippet_processing_allowed",
    Operation.DISPLAY_SNIPPET: "snippet_display_allowed",
    Operation.PROCESS_FULL_TEXT: "full_text_processing_allowed",
    Operation.STORE_FULL_TEXT: "full_text_storage_allowed",
    Operation.GENERATE_SUMMARY: "summary_generation_allowed",
    Operation.DISPLAY_IMAGE: "image_display_allowed",
}
_STORAGE_OPERATIONS = {Operation.STORE_METADATA, Operation.STORE_SNIPPET, Operation.STORE_FULL_TEXT}


def checked_clock(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise NewsBoundaryError(DenialCode.INVALID_CLOCK)
    return now.astimezone(UTC)


def resolve_feed(registry: NewsRegistry, feed_id: UUID, *, now: datetime) -> ResolvedFeed:
    current = checked_clock(now)
    try:
        # Revalidate even existing instances: model_copy/model_construct are not
        # validation boundaries. All nested containers are immutable tuples.
        snapshot = NewsRegistry.model_validate(registry)
    except ValidationError:
        raise NewsBoundaryError(DenialCode.INVALID_REGISTRY) from None
    if not isinstance(feed_id, UUID):
        raise NewsBoundaryError(DenialCode.UNKNOWN_FEED)
    feed = next((item for item in snapshot.feeds if item.id == feed_id), None)
    if feed is None:
        raise NewsBoundaryError(DenialCode.UNKNOWN_FEED)
    source = next(item for item in snapshot.sources if item.id == feed.source_id)
    rights = next(item for item in snapshot.rights_profiles if item.id == feed.rights_profile_id)
    if source.status != SourceStatus.ACTIVE:
        raise NewsBoundaryError(DenialCode.SOURCE_UNAVAILABLE)
    if not source.identity_verified:
        raise NewsBoundaryError(DenialCode.SOURCE_IDENTITY_UNVERIFIED)
    if feed.status != FeedStatus.ACTIVE:
        raise NewsBoundaryError(DenialCode.FEED_UNAVAILABLE)
    if (
        rights.review_reference is None
        or rights.reviewed_at is None
        or rights.valid_from is None
        or rights.valid_until is None
    ):
        raise NewsBoundaryError(DenialCode.REVIEW_MISSING)
    if rights.reviewed_at > current:
        raise NewsBoundaryError(DenialCode.REVIEW_NOT_EFFECTIVE)
    if rights.valid_from > current:
        raise NewsBoundaryError(DenialCode.RIGHTS_NOT_EFFECTIVE)
    if current >= rights.valid_until:
        raise NewsBoundaryError(DenialCode.RIGHTS_EXPIRED)
    return ResolvedFeed(source=source, rights=rights, feed=feed)


def require_permission(
    registry: NewsRegistry, feed_id: UUID, operation: Operation, *, now: datetime
) -> ResolvedFeed:
    if not isinstance(operation, Operation):
        raise NewsBoundaryError(DenialCode.INVALID_OPERATION)
    resolved = resolve_feed(registry, feed_id, now=now)
    if getattr(resolved.rights, _PERMISSION_FIELDS[operation]) is not True:
        raise NewsBoundaryError(DenialCode.PERMISSION_DENIED)
    if operation in _STORAGE_OPERATIONS:
        if not resolved.rights.metadata_storage_allowed:
            raise NewsBoundaryError(DenialCode.PERMISSION_DENIED)
        if resolved.rights.retention_days <= 0:
            raise NewsBoundaryError(DenialCode.RETENTION_DENIED)
    if operation == Operation.STORE_FULL_TEXT and not resolved.rights.full_text_processing_allowed:
        raise NewsBoundaryError(DenialCode.PERMISSION_DENIED)
    return resolved


def require_summary_permission(
    registry: NewsRegistry, feed_id: UUID, material: ContentKind, *, now: datetime
) -> ResolvedFeed:
    """Both summary generation AND the selected input's processing must be granted.

    This is only the rights half of the check. NEWS_SYNTHESIS qualification,
    sensitivity, budget and rollout checks remain exclusively in SPEC-005.
    """
    if not isinstance(material, ContentKind):
        raise NewsBoundaryError(DenialCode.INVALID_OPERATION)
    require_permission(registry, feed_id, Operation.GENERATE_SUMMARY, now=now)
    processing = {
        ContentKind.METADATA: Operation.PROCESS_METADATA,
        ContentKind.SNIPPET: Operation.PROCESS_SNIPPET,
        ContentKind.FULL_TEXT: Operation.PROCESS_FULL_TEXT,
    }[material]
    return require_permission(registry, feed_id, processing, now=now)


def retention_deadline(rights: ContentRightsProfile, first_retrieved_at: datetime) -> datetime:
    first = checked_clock(first_retrieved_at)
    if rights.retention_days <= 0 or rights.valid_until is None:
        raise NewsBoundaryError(DenialCode.RETENTION_DENIED)
    duration = timedelta(days=rights.retention_days)
    if rights.valid_until - first <= duration:
        return rights.valid_until
    return first + duration


def require_current_item(
    registry: NewsRegistry, item: NewsItem, operation: Operation, *, now: datetime
) -> ResolvedFeed:
    """Recheck binding, current rights and retention before using a stored item."""
    current = checked_clock(now)
    try:
        checked = NewsItem.model_validate(item)
    except ValidationError:
        raise NewsBoundaryError(DenialCode.INVALID_ITEM) from None
    # A stored item cannot keep being served after its storage grant is removed.
    resolved = require_permission(registry, checked.feed_id, Operation.STORE_METADATA, now=current)
    if operation != Operation.STORE_METADATA:
        resolved = require_permission(registry, checked.feed_id, operation, now=current)
    if (
        checked.source_id != resolved.source.id
        or checked.source_type != resolved.source.source_type
    ):
        raise NewsBoundaryError(DenialCode.ITEM_BINDING_MISMATCH)
    if (
        checked.rights_profile_id != resolved.rights.id
        or checked.rights_version != resolved.rights.version
    ):
        raise NewsBoundaryError(DenialCode.STALE_RIGHTS_BINDING)
    if checked.retrieved_at > current:
        raise NewsBoundaryError(DenialCode.ITEM_TIME_INVALID)
    if checked.expires_at > retention_deadline(resolved.rights, checked.retrieved_at):
        raise NewsBoundaryError(DenialCode.ITEM_TIME_INVALID)
    if current >= checked.expires_at:
        raise NewsBoundaryError(DenialCode.ITEM_EXPIRED)
    # A later source-config change cannot silently make an old URL admissible.
    if urlsplit(checked.canonical_url).hostname not in (
        resolved.source.domain,
        *resolved.source.additional_article_hosts,
    ):
        raise NewsBoundaryError(DenialCode.SOURCE_URL_MISMATCH)
    return resolved
