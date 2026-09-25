from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from navox.connectors.contracts import CanonicalResource
from navox.intelligence.contracts import SourceDocument, SourceIdentity


def canonical_resource_to_source_document(
    resource: CanonicalResource,
    *,
    provenance_connection_id: UUID,
) -> SourceDocument:
    """Normalize a connector resource into the sole SPEC-002 input contract."""

    canonical = resource.canonical
    if "source_document" in canonical:
        envelope = canonical["source_document"]
        if not isinstance(envelope, dict):
            raise ValueError("Invalid SourceDocument envelope")
        document = SourceDocument.model_validate(
            {**envelope, "retrieved_at": resource.retrieved_at}
        )
        if (
            document.workspace_id != resource.workspace_id
            or document.provider != resource.provider
            or document.external_id != resource.external_id
            or document.external_parent_id != resource.external_parent_id
            or document.metadata.get("status", "active") != canonical.get("status", "active")
        ):
            raise ValueError("SourceDocument envelope changed resource ownership")
        return document
    occurred_at = _parse_time(canonical.get("occurred_at"))
    author = _identity(canonical.get("author"), resource.provider)
    recipients = _recipients(canonical.get("recipients"), resource.provider)
    subject = _optional_text(canonical.get("subject"), 2_000)
    content = _optional_text(canonical.get("content"), 32_000)
    source_type = _optional_text(canonical.get("source_type"), 64) or resource.resource_type
    metadata_value = canonical.get("metadata")
    metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
    metadata.update(
        {
            "connector_connection_id": str(resource.connector_connection_id),
            "canonical_resource_id": str(resource.resource_id),
            "source_url": resource.source_url,
            "status": canonical.get("status", "active"),
        }
    )
    return SourceDocument(
        id=resource.resource_id,
        workspace_id=resource.workspace_id,
        provider=resource.provider,
        source_type=source_type,
        external_id=resource.external_id,
        external_parent_id=resource.external_parent_id,
        author=author,
        recipients=recipients,
        subject=subject,
        content=content,
        occurred_at=(
            occurred_at or resource.updated_at or resource.created_at or resource.retrieved_at
        ),
        retrieved_at=resource.retrieved_at,
        metadata=metadata,
    )


def connector_receipt_source(resource_type: str) -> str:
    """Bound resource type into the legacy 32-character receipt source column."""

    import hashlib

    digest = hashlib.sha256(resource_type.encode("utf-8")).hexdigest()[:20]
    return f"connector:{digest}"


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _optional_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None


def _identity(value: object, provider: str) -> SourceIdentity | None:
    if not isinstance(value, dict):
        return None
    payload = dict(value)
    payload.setdefault("provider", provider)
    try:
        return SourceIdentity.model_validate(payload)
    except ValidationError:
        return None


def _recipients(value: object, provider: str) -> list[SourceIdentity]:
    if not isinstance(value, list):
        return []
    result: list[SourceIdentity] = []
    for item in value[:256]:
        identity = _identity(item, provider)
        if identity is not None:
            result.append(identity)
    return result
