"""Reusable SPEC-003 contract probes across unrelated integration paths.

Provider responses and credentials are fixtures. These probes certify the
canonical and permission boundaries, not live provider availability.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from navox.connectors.builtin.canvas import CANVAS_MANIFEST, CanvasConnector
from navox.connectors.builtin.generic_api import GenericAPIConnector
from navox.connectors.builtin.google import GOOGLE_MANIFEST
from navox.connectors.builtin.google_calendar import CALENDAR_MANIFEST, calendar_resource
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST
from navox.connectors.builtin.imports import IMPORT_MANIFEST, ImportConnector
from navox.connectors.builtin.oauth_canvas import OAUTH_CANVAS_MANIFEST
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorActionRequest,
    ConnectorManifest,
    ConnectorRuntimeError,
    SyncRequest,
    stable_resource_id,
)
from navox.connectors.normalization import canonical_resource_to_source_document
from navox.providers.google_sources import calendar_document


class CanvasSecrets:
    @property
    def names(self) -> frozenset[str]:
        return frozenset({"CANVAS_API_TOKEN"})

    def get(self, name: str) -> str:
        assert name == "CANVAS_API_TOKEN"
        return "fixture-token"


MANIFESTS = [
    GOOGLE_MANIFEST,
    CALENDAR_MANIFEST,
    GMAIL_MANIFEST,
    CANVAS_MANIFEST,
    OAUTH_CANVAS_MANIFEST,
    IMPORT_MANIFEST,
    GenericAPIConnector(
        {
            "display_name": "Unknown read service",
            "provider": "external_fixture",
            "base_url": "https://fixture.example.invalid",
            "auth": "none",
            "endpoints": [
                {
                    "name": "items",
                    "path": "/v1/items",
                    "capability": "external.items.read",
                    "resource_type": "external.item",
                }
            ],
        },
        None,
    ).get_manifest(),
]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda value: value.id)
def test_each_reference_manifest_roundtrips_and_cannot_add_authority(manifest):
    payload = manifest.model_dump(mode="json", by_alias=True)
    assert (
        ConnectorManifest.model_validate_json(manifest.model_dump_json(by_alias=True)) == manifest
    )
    assert payload["minimumNavoxConnectorApiVersion"] == "1"
    assert set(manifest.required_secrets).isdisjoint(payload.keys())
    assert all(name not in payload for name in ("password", "access_token", "private_key"))
    poisoned = {**payload, "grantedPermissions": ["communication.messages.send"]}
    with pytest.raises(ValidationError):
        ConnectorManifest.model_validate(poisoned)
    asserted = {**payload, "minimumNavoxConnectorApiVersion": "999"}
    with pytest.raises(ValidationError):
        ConnectorManifest.model_validate(asserted)


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda value: value.id)
def test_each_reference_manifest_needs_all_permission_layers(manifest):
    declared = {
        *(item.name for item in manifest.capabilities.read),
        *(item.name for item in manifest.capabilities.write),
        *manifest.capabilities.events,
    }
    if not declared:
        pytest.fail("A reference connector must declare at least one capability")
    gateway = CapabilityGateway()
    common = dict(manifest=manifest, health_state="CONNECTED")
    for missing in ("provider_capabilities", "user_authorized", "policy_allowed"):
        grants = {
            "provider_capabilities": declared,
            "user_authorized": declared,
            "policy_allowed": declared,
        }
        grants[missing] = set()
        assert not gateway.evaluate(**common, **grants).all
    grants[missing] = declared | {"injected.permissions.write"}
    assert "injected.permissions.write" not in gateway.evaluate(**common, **grants).all
    assert not gateway.evaluate(
        **{**common, "health_state": "DISCONNECTED"},
        provider_capabilities=declared,
        user_authorized=declared,
        policy_allowed=declared,
    ).all


async def _resources(kind: str, connection: UUID, workspace: UUID) -> list[CanonicalResource]:
    if kind == "google":
        document = calendar_document(
            {
                "id": "event-7",
                "summary": "Review the project",
                "description": "Review by Friday",
                "updated": "2026-09-24T12:00:00Z",
            },
            workspace_id=workspace,
            connection_id=connection,
            now=datetime(2026, 9, 24, tzinfo=UTC),
        )
        return [calendar_resource(document, connection)]

    if kind.startswith("import-"):
        format_name = kind.removeprefix("import-")
        content = {
            "ics": (
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-7\nSUMMARY:Review\n"
                "DTSTART:20260925T120000Z\nEND:VEVENT\nEND:VCALENDAR"
            ),
            "csv": "id,title\n7,Review\n",
            "json": '[{"id":"7","title":"Review"}]',
        }[format_name]
        connector = ImportConnector({"format": format_name, "content": content}, None)
        grants = {item.name for item in IMPORT_MANIFEST.capabilities.read}
    elif kind == "canvas":

        async def canvas_http(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer fixture-token"
            assert request.url.path == "/api/v1/courses"
            return httpx.Response(
                200, json=[{"id": 7, "name": "Review", "workflow_state": "available"}]
            )

        connector = CanvasConnector(
            {"base_url": "https://canvas.example.invalid"},
            CanvasSecrets(),
            transport=httpx.MockTransport(canvas_http),
        )
        grants = {"academic.courses.read"}
    elif kind == "generic":

        async def generic_http(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/items"
            return httpx.Response(200, json={"items": [{"id": "7", "title": "Review"}]})

        connector = GenericAPIConnector(
            {
                "display_name": "Unknown service",
                "provider": "external_fixture",
                "base_url": "https://fixture.example.invalid",
                "auth": "none",
                "endpoints": [
                    {
                        "name": "items",
                        "path": "/v1/items",
                        "capability": "external.items.read",
                        "resource_type": "external.item",
                        "items_field": "items",
                        "id_field": "id",
                        "subject_field": "title",
                    }
                ],
            },
            None,
            transport=httpx.MockTransport(generic_http),
        )
        grants = {"external.items.read"}
    else:
        raise AssertionError(kind)

    page = await connector.sync(
        SyncRequest(
            connection_id=connection, workspace_id=workspace, capabilities=frozenset(grants)
        )
    )
    assert not page.has_more
    return page.resources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["google", "canvas", "import-ics", "import-csv", "import-json", "generic"]
)
async def test_cross_provider_canonical_identity_and_spec_002_boundary(kind):
    connection, workspace = uuid4(), uuid4()
    first = await _resources(kind, connection, workspace)
    replay = await _resources(kind, connection, workspace)
    other = await _resources(kind, uuid4(), workspace)
    assert first and len(first) == len(replay) == len(other)
    for resource, repeat, separate in zip(first, replay, other, strict=True):
        validated = CanonicalResource.model_validate_json(resource.model_dump_json())
        assert validated == resource
        assert resource.resource_id == repeat.resource_id
        assert resource.resource_id != separate.resource_id
        assert resource.resource_id == stable_resource_id(
            connection, resource.resource_type, resource.external_id
        )
        document = canonical_resource_to_source_document(
            resource, provenance_connection_id=connection
        )
        assert document.workspace_id == workspace
        assert document.provider == resource.provider
        assert document.external_id == resource.external_id
        assert document.retrieved_at == resource.retrieved_at
        forged = resource.model_copy(update={"workspace_id": uuid4()})
        if "source_document" in resource.canonical:
            with pytest.raises(ValueError, match="ownership"):
                canonical_resource_to_source_document(forged, provenance_connection_id=connection)
        action = ConnectorActionRequest(
            connection_id=connection,
            workspace_id=workspace,
            capability="communication.messages.send",
            payload={},
        )
        if kind == "canvas":
            adapter = CanvasConnector(
                {"base_url": "https://canvas.example.invalid"}, CanvasSecrets()
            )
        elif kind.startswith("import-"):
            adapter = ImportConnector({"format": "json", "content": "[]"}, None)
        elif kind == "generic":
            adapter = GenericAPIConnector(
                {
                    "display_name": "Fixture",
                    "provider": "external_fixture",
                    "base_url": "https://fixture.example.invalid",
                    "auth": "none",
                    "endpoints": [
                        {
                            "name": "items",
                            "path": "/v1/items",
                            "capability": "external.items.read",
                            "resource_type": "external.item",
                        }
                    ],
                },
                None,
            )
        else:
            continue  # Google writes use SPEC-001's separate approval boundary.
        with pytest.raises(ConnectorRuntimeError) as denied:
            await adapter.execute(action)
        assert denied.value.code == "UNSUPPORTED_CAPABILITY"
