from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorManifest,
    stable_resource_id,
)


def manifest_payload() -> dict[str, object]:
    return {
        "id": "demo-service",
        "version": "1.0.0",
        "displayName": "Demo Service",
        "category": "productivity",
        "connectorClass": "GENERIC_API",
        "auth": [{"kind": "api_token", "label": "API token", "scopes": []}],
        "resourceTypes": ["demo.task"],
        "capabilities": {
            "read": [
                {
                    "name": "demo.tasks.read",
                    "description": "Read demo tasks",
                    "sensitive": False,
                }
            ],
            "write": [],
            "events": [],
            "incrementalSync": True,
        },
        "requiredSecrets": ["API_TOKEN"],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }


def test_manifest_is_versioned_strict_and_provider_neutral() -> None:
    manifest = ConnectorManifest.model_validate(manifest_payload())

    assert manifest.id == "demo-service"
    assert manifest.capabilities.read[0].name == "demo.tasks.read"
    assert manifest.minimum_navox_connector_api_version == "1"


def test_manifest_rejects_unknown_fields_and_bad_capabilities() -> None:
    payload = manifest_payload()
    payload["password"] = "must-never-exist"

    with pytest.raises(ValidationError):
        ConnectorManifest.model_validate(payload)

    payload = manifest_payload()
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, dict)
    read = capabilities["read"]
    assert isinstance(read, list)
    first = read[0]
    assert isinstance(first, dict)
    first["name"] = "INVALID CAPABILITY"
    with pytest.raises(ValidationError):
        ConnectorManifest.model_validate(payload)


def test_canonical_resource_requires_stable_identity() -> None:
    connection_id = UUID("11111111-1111-4111-8111-111111111111")
    workspace_id = UUID("22222222-2222-4222-8222-222222222222")
    expected = stable_resource_id(connection_id, "demo.task", "task-7")

    resource = CanonicalResource(
        resource_id=expected,
        workspace_id=workspace_id,
        connector_connection_id=connection_id,
        provider="demo",
        resource_type="demo.task",
        external_id="task-7",
        canonical={"title": "Finish report"},
        retrieved_at=datetime.now(UTC),
    )

    assert resource.resource_id == expected
    payload = resource.model_dump()
    payload["resource_id"] = UUID(int=1)
    with pytest.raises(ValidationError, match="stable"):
        CanonicalResource.model_validate(payload)
