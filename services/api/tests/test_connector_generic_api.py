from uuid import UUID

import httpx
import pytest

from navox.connectors.builtin.generic_api import GenericAPIConnector
from navox.connectors.contracts import ConnectorRuntimeError, SyncRequest


class Secrets:
    @property
    def names(self) -> frozenset[str]:
        return frozenset({"DEMO_TOKEN"})

    def get(self, name: str) -> str:
        assert name == "DEMO_TOKEN"
        return "secret-token"


def config() -> dict[str, object]:
    return {
        "display_name": "Unknown Tasks API",
        "provider": "unknown_tasks",
        "base_url": "https://api.unknown.example",
        "auth": "bearer",
        "token_secret_name": "DEMO_TOKEN",
        "endpoints": [
            {
                "name": "tasks",
                "path": "/v1/tasks",
                "capability": "tasks.items.read",
                "resource_type": "tasks.item",
                "items_field": "items",
                "id_field": "id",
                "subject_field": "title",
                "content_field": "description",
                "occurred_at_field": "updated_at",
                "source_url_field": "url",
                "cursor_param": "cursor",
                "next_cursor_field": "next_cursor",
                "page_size_param": "limit",
                "page_size": 100,
            }
        ],
    }


@pytest.mark.asyncio
async def test_generic_api_configuration_drives_unknown_service_without_core_code() -> None:
    calls: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-token"
        cursor = request.url.params.get("cursor")
        calls.append(cursor)
        if cursor is None:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "a",
                            "title": "Review launch plan",
                            "description": "Review before Friday",
                            "updated_at": "2026-09-24T12:00:00Z",
                            "url": "https://app.unknown.example/tasks/a",
                        }
                    ],
                    "next_cursor": "page-2",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "b",
                        "title": "Confirm vendor",
                        "updated_at": "2026-09-24T13:00:00Z",
                    }
                ],
                "next_cursor": None,
            },
        )

    connector = GenericAPIConnector(
        config(),
        Secrets(),
        transport=httpx.MockTransport(handler),
    )
    manifest = connector.get_manifest()
    assert manifest.required_secrets == ["DEMO_TOKEN"]
    assert manifest.capabilities.read[0].name == "tasks.items.read"

    page = await connector.sync(
        SyncRequest(
            connection_id=UUID("11111111-1111-4111-8111-111111111111"),
            workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
            capabilities=frozenset({"tasks.items.read"}),
        )
    )

    assert calls == [None, "page-2"]
    assert [item.external_id for item in page.resources] == ["tasks:a", "tasks:b"]
    assert page.resources[0].provider == "unknown_tasks"
    assert page.resources[0].canonical["subject"] == "Review launch plan"


def test_generic_api_rejects_undeclared_domains_and_unsafe_paths() -> None:
    bad = config()
    bad["base_url"] = "http://127.0.0.1"
    with pytest.raises(ValueError):
        GenericAPIConnector(bad, Secrets())

    bad = config()
    endpoints = bad["endpoints"]
    assert isinstance(endpoints, list)
    endpoint = dict(endpoints[0])
    endpoint["path"] = "https://evil.example/data"
    bad["endpoints"] = [endpoint]
    with pytest.raises(ValueError):
        GenericAPIConnector(bad, Secrets())


@pytest.mark.asyncio
async def test_generic_api_rejects_cross_origin_link_pagination() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "next_cursor": None},
            headers={"Link": '<https://evil.example/page2>; rel="next"'},
        )

    no_cursor = config()
    endpoints = no_cursor["endpoints"]
    assert isinstance(endpoints, list)
    endpoint = dict(endpoints[0])
    endpoint["cursor_param"] = None
    endpoint["next_cursor_field"] = None
    no_cursor["endpoints"] = [endpoint]
    connector = GenericAPIConnector(
        no_cursor,
        Secrets(),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ConnectorRuntimeError, match="configured origin"):
        await connector.sync(
            SyncRequest(
                connection_id=UUID("11111111-1111-4111-8111-111111111111"),
                workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
                capabilities=frozenset({"tasks.items.read"}),
            )
        )
