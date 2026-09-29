"""Rights-first normalization and display projection; no network or AI calls."""

import json
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from pydantic import ValidationError

from navox.news.contracts import (
    IncomingNewsMetadata,
    NewsItem,
    NewsPresentation,
    NewsRegistry,
    Operation,
)
from navox.news.rights import (
    DenialCode,
    NewsBoundaryError,
    checked_clock,
    require_current_item,
    require_permission,
    retention_deadline,
)

_ITEM_NAMESPACE = UUID("3fd09074-7e3c-469f-95a1-81b47756c769")


def stable_item_id(source_id: UUID, feed_id: UUID, incoming: IncomingNewsMetadata) -> UUID:
    """Idempotent within a feed, not a clustering/corroboration determination."""
    identity = (
        ["external_id", incoming.external_id]
        if incoming.external_id is not None
        else ["url", incoming.canonical_url]
    )
    key = json.dumps([str(source_id), str(feed_id), *identity], ensure_ascii=False)
    return uuid5(_ITEM_NAMESPACE, key)


def normalize_metadata(
    registry: NewsRegistry,
    feed_id: UUID,
    payload: dict[str, object],
    *,
    now: datetime,
    previous: NewsItem | None = None,
) -> NewsItem:
    """Normalize an already decoded, authorized adapter item.

    Restricted snippets are rejected, not silently copied or summarized. Unknown
    fields such as full_text, image_url, verification_status and source_id fail.
    Persistence must pass an existing row as `previous` on repeat acquisition;
    this function has no database and cannot itself enforce concurrent upserts.
    """
    current = checked_clock(now)
    resolved = require_permission(registry, feed_id, Operation.STORE_METADATA, now=current)
    if not isinstance(payload, dict):
        raise NewsBoundaryError(DenialCode.INVALID_PAYLOAD)
    if payload.get("description") is not None:
        require_permission(registry, feed_id, Operation.STORE_SNIPPET, now=current)
    try:
        incoming = IncomingNewsMetadata.model_validate(payload)
    except ValidationError:
        raise NewsBoundaryError(DenialCode.INVALID_PAYLOAD) from None
    if urlsplit(incoming.canonical_url).hostname not in (
        resolved.source.domain,
        *resolved.source.additional_article_hosts,
    ):
        raise NewsBoundaryError(DenialCode.SOURCE_URL_MISMATCH)
    identifier = stable_item_id(resolved.source.id, feed_id, incoming)
    first = current
    deadline = retention_deadline(resolved.rights, first)
    if previous is not None:
        require_current_item(registry, previous, Operation.STORE_METADATA, now=current)
        if previous.id != identifier or previous.feed_id != feed_id:
            raise NewsBoundaryError(DenialCode.REFRESH_ID_MISMATCH)
        first = previous.retrieved_at
        deadline = min(previous.expires_at, retention_deadline(resolved.rights, first))
    values = incoming.model_dump(mode="python")
    values.update(
        id=identifier,
        source_id=resolved.source.id,
        feed_id=feed_id,
        source_type=resolved.source.source_type,
        language=incoming.language or resolved.feed.language,
        region=incoming.region or resolved.feed.region,
        categories=incoming.categories or (resolved.feed.category,),
        retrieved_at=first,
        expires_at=deadline,
        rights_profile_id=resolved.rights.id,
        rights_version=resolved.rights.version,
    )
    try:
        return NewsItem.model_validate(values)
    except ValidationError:
        raise NewsBoundaryError(DenialCode.INVALID_ITEM) from None


def present_metadata(registry: NewsRegistry, item: NewsItem, *, now: datetime) -> NewsPresentation:
    """Project currently permitted fields; always keep original attribution/link.

    There is intentionally no 'Verified' label: a registered publisher identity
    is not verification of any material claim. Render all strings as text, never
    raw HTML. No image or full-text field can leak through this projection.
    """
    resolved = require_current_item(registry, item, Operation.DISPLAY_METADATA, now=now)
    # Revalidate again so projection uses the validated, normalized values rather
    # than an unchecked model_copy passed by a caller.
    checked = NewsItem.model_validate(item)
    description = None
    if (
        checked.description is not None
        and resolved.rights.snippet_storage_allowed
        and resolved.rights.snippet_display_allowed
    ):
        description = checked.description
    return NewsPresentation(
        id=checked.id,
        headline=checked.headline,
        source_name=resolved.source.name,
        original_source_url=checked.canonical_url,
        author=checked.author,
        description=description,
        published_at=checked.published_at,
        updated_at=checked.updated_at,
        categories=checked.categories,
        language=checked.language,
        region=checked.region,
    )
