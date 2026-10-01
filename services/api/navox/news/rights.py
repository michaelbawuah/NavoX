"""Enforce both ingestion-time and current permissions on every content operation."""

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum

from navox.db.news import NewsContentRights, NewsItem
from navox.news.contracts import ContentRights, NewsError, stored_utc


class Operation(StrEnum):
    METADATA = "metadata_storage_allowed"
    SNIPPET = "snippet_storage_allowed"
    PROCESS_TEXT = "full_text_processing_allowed"
    STORE_TEXT = "full_text_storage_allowed"
    SUMMARY = "summary_generation_allowed"
    IMAGE = "image_display_allowed"


def policy_digest(policy: ContentRights) -> str:
    value = json.dumps(policy.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def policy_for(row: NewsContentRights, *, now: datetime) -> ContentRights:
    if row.revoked_at is not None:
        raise NewsError("rights_denied")
    try:
        policy = ContentRights.model_validate(row.policy)
    except ValueError:
        raise NewsError("rights_denied") from None
    if policy_digest(policy) != row.policy_digest:
        raise NewsError("rights_denied")
    if not policy.reviewed_at <= now < policy.expires_at:
        raise NewsError("rights_expired")
    return policy


def require_operation(operation: Operation, *policies: ContentRights) -> None:
    if not policies or not all(getattr(policy, operation.value) is True for policy in policies):
        raise NewsError("rights_denied")


def retention_deadline(retrieved_at: datetime, *policies: ContentRights) -> datetime:
    if not policies:
        raise NewsError("rights_denied")
    return min(min(retrieved_at + timedelta(days=p.retention_days), p.expires_at) for p in policies)


def require_item_retention(item: NewsItem, *policies: ContentRights, now: datetime) -> None:
    deadline = min(
        stored_utc(item.expires_at), retention_deadline(stored_utc(item.retrieved_at), *policies)
    )
    if now >= deadline:
        raise NewsError("rights_expired")
