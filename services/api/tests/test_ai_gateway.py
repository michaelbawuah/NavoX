import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from navox.ai.gateway import (
    AIGateway,
    StructuredOutputResponse,
    minimized_source_payload,
    operational_extraction_json_schema,
)
from navox.intelligence.contracts import SourceDocument, SourceIdentity


def document() -> SourceDocument:
    return SourceDocument(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
        provider="google",
        source_type="gmail_message",
        external_id="message-123",
        author=SourceIdentity(
            provider="google",
            identity_type="email",
            identity_value="maya@example.com",
            display_name="Maya",
        ),
        recipients=[SourceIdentity(identity_type="email", identity_value="owner@example.com")],
        subject="Budget review",
        content="Please send the budget.",
        occurred_at=datetime(2026, 9, 22, 13, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 9, 22, 13, 1, tzinfo=UTC),
        metadata={"history_id": "secretly-not-needed-for-extraction"},
    )


class StubProvider:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    async def generate_json(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        instructions: str,
        input_text: str,
    ) -> StructuredOutputResponse:
        self.request = {
            "schema_name": schema_name,
            "schema": schema,
            "instructions": instructions,
            "input_text": input_text,
        }
        return StructuredOutputResponse(
            data={
                "schema_version": "operational-extraction.v1",
                "observations": [],
                "people": [],
                "temporals": [],
                "relationships": [],
            },
            provider="stub",
            model="stub-v1",
        )


@pytest.mark.asyncio
async def test_ai_gateway_is_provider_neutral_and_uses_strict_extraction_contract() -> None:
    provider = StubProvider()

    response = await AIGateway(provider).extract_operational(document())

    assert response.provider == "stub"
    assert response.model == "stub-v1"
    assert provider.request is not None
    assert provider.request["schema_name"] == "navox_operational_extraction_v1"
    schema = provider.request["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "observations",
        "people",
        "temporals",
        "relationships",
    }
    assert "untrusted data" in str(provider.request["instructions"])


def test_minimized_source_payload_omits_workspace_and_connector_metadata() -> None:
    payload = json.loads(minimized_source_payload(document()))

    assert payload["external_id"] == "message-123"
    assert payload["author"]["identity_value"] == "maya@example.com"
    assert "workspace_id" not in payload
    assert "metadata" not in payload
    assert "retrieved_at" not in payload


def test_operational_schema_requires_no_authority_or_action_fields() -> None:
    schema = operational_extraction_json_schema()

    root_properties = schema["properties"]
    assert "granted_permissions" not in root_properties
    observation_properties = root_properties["observations"]["items"]["properties"]
    assert "action" not in observation_properties
    assert "tool" not in observation_properties
