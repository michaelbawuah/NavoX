from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from navox.intelligence.contracts import (
    SOURCE_DOCUMENT_SCHEMA_VERSION,
    SourceDocument,
    SourceIdentity,
)


def source_document_payload() -> dict[str, object]:
    return {
        "id": UUID("11111111-1111-4111-8111-111111111111"),
        "workspace_id": UUID("22222222-2222-4222-8222-222222222222"),
        "provider": "google",
        "source_type": "gmail_message",
        "external_id": "message-123",
        "external_parent_id": "thread-456",
        "author": {
            "provider": "google",
            "identity_type": "email",
            "identity_value": "owner@example.com",
            "display_name": "Owner",
        },
        "recipients": [
            {
                "identity_type": "email",
                "identity_value": "recipient@example.com",
            }
        ],
        "subject": "Budget review",
        "content": "Please send the budget before tomorrow's review.",
        "occurred_at": datetime(2026, 9, 22, 9, 0, tzinfo=UTC),
        "retrieved_at": datetime(2026, 9, 22, 9, 1, tzinfo=UTC),
        "metadata": {"label_ids": ["INBOX"], "history_id": "42"},
    }


def test_source_document_is_versioned_provider_neutral_and_json_safe() -> None:
    document = SourceDocument.model_validate(source_document_payload())

    assert document.schema_version == SOURCE_DOCUMENT_SCHEMA_VERSION
    assert document.provider == "google"
    assert document.source_type == "gmail_message"
    assert document.author == SourceIdentity(
        provider="google",
        identity_type="email",
        identity_value="owner@example.com",
        display_name="Owner",
    )
    assert document.metadata["history_id"] == "42"


def test_source_document_normalizes_aware_timestamps_to_utc() -> None:
    payload = source_document_payload()
    payload["occurred_at"] = datetime.fromisoformat("2026-09-22T05:00:00-04:00")

    document = SourceDocument.model_validate(payload)

    assert document.occurred_at == datetime(2026, 9, 22, 9, 0, tzinfo=UTC)
    assert document.occurred_at.tzinfo is UTC


@pytest.mark.parametrize("field", ["occurred_at", "retrieved_at"])
def test_source_document_rejects_naive_timestamps(field: str) -> None:
    payload = source_document_payload()
    payload[field] = datetime(2026, 9, 22, 9, 0)

    with pytest.raises(ValidationError, match="timestamps must include a timezone"):
        SourceDocument.model_validate(payload)


def test_source_document_rejects_authority_fields_and_unknown_connector_data() -> None:
    payload = source_document_payload()
    payload["granted_permissions"] = ["gmail.send"]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceDocument.model_validate(payload)


def test_source_document_metadata_must_be_json_serializable() -> None:
    payload = source_document_payload()
    payload["metadata"] = {"secret_object": object()}

    with pytest.raises(ValidationError):
        SourceDocument.model_validate(payload)


def test_source_identity_requires_a_stable_identity_value() -> None:
    with pytest.raises(ValidationError):
        SourceIdentity(identity_type="email", identity_value=" ")
