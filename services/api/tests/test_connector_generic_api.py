from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest

from navox.connectors.builtin.generic_api import GenericAPIConnector
from navox.connectors.contracts import ConnectorRuntimeError, SyncRequest
from navox.connectors.normalization import canonical_resource_to_source_document
from navox.connectors.outbound import MAX_RESPONSE_BYTES, ApprovedHTTPSTransport


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


def sync_request() -> SyncRequest:
    return SyncRequest(
        connection_id=UUID("11111111-1111-4111-8111-111111111111"),
        workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
        capabilities=frozenset({"tasks.items.read"}),
    )


def example_item(**changes: object) -> dict[str, object]:
    return {
        "id": "a",
        "title": "Review launch plan",
        "description": "Review before Friday",
        "updated_at": "2026-09-24T12:00:00Z",
        **changes,
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
    bad["base_url"] = "https://api.unknown.example:8443"
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


@pytest.mark.parametrize(
    "parameter",
    ["token", "api_key", "access_token", "authorization", "client_secret", "password"],
)
def test_generic_api_rejects_credentials_in_configured_query_parameters(parameter: str) -> None:
    bad = config()
    endpoints = bad["endpoints"]
    assert isinstance(endpoints, list)
    endpoint = dict(endpoints[0])
    endpoint["static_params"] = {parameter: "a-secret-that-must-not-enter-a-url"}
    bad["endpoints"] = [endpoint]
    with pytest.raises(ValueError):
        GenericAPIConnector(bad, Secrets())


def test_generic_api_accepts_benign_author_and_course_code_field_names() -> None:
    good = config()
    endpoints = good["endpoints"]
    assert isinstance(endpoints, list)
    endpoint = dict(endpoints[0])
    endpoint["subject_field"] = "author.name"
    endpoint["id_field"] = "course_code"
    endpoint["static_params"] = {"course_code": "CS2110", "zipcode": "14853"}
    good["endpoints"] = [endpoint]

    connector = GenericAPIConnector(good, Secrets())
    assert connector.config.endpoints[0].subject_field == "author.name"
    assert connector.config.endpoints[0].id_field == "course_code"
    assert connector.config.endpoints[0].static_params["zipcode"] == "14853"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_field", "private.access_token"),
        ("content_field", "password"),
        ("cursor_param", "api_key"),
        ("path", "/v1/tasks?token=unsafe"),
        ("static_params", {"page": "x" * 513}),
    ],
)
def test_generic_api_rejects_credential_mappings_and_unbounded_config(
    field: str, value: object
) -> None:
    bad = config()
    endpoints = bad["endpoints"]
    assert isinstance(endpoints, list)
    endpoint = dict(endpoints[0])
    endpoint[field] = value
    bad["endpoints"] = [endpoint]
    with pytest.raises(ValueError):
        GenericAPIConnector(bad, Secrets())


@pytest.mark.asyncio
async def test_generic_api_default_client_uses_approved_transport_without_ambient_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    connector = GenericAPIConnector(config(), Secrets())
    client = connector._client()
    try:
        assert isinstance(client._transport, ApprovedHTTPSTransport)
        assert client._transport.origin == "https://api.unknown.example"
        assert client._mounts == {}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_generic_api_rejects_mixed_private_dns_before_provider_io() -> None:
    calls = 0

    async def resolve(host: str) -> list[str]:
        assert host == "api.unknown.example"
        return ["93.184.216.34", "127.0.0.1"]

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"items": []})

    transport = ApprovedHTTPSTransport(
        "https://api.unknown.example",
        resolver=resolve,
        transport=httpx.MockTransport(handler),
    )
    connector = GenericAPIConnector(config(), Secrets(), transport=transport)
    with pytest.raises(ConnectorRuntimeError, match="address is not approved"):
        await connector.sync(sync_request())
    assert calls == 0


@pytest.mark.asyncio
async def test_generic_api_pins_public_peer_and_rejects_redirects() -> None:
    calls = 0

    async def resolve(host: str) -> list[str]:
        assert host == "api.unknown.example"
        return ["93.184.216.34"]

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "api.unknown.example"
        assert request.extensions["sni_hostname"] == "api.unknown.example"
        return httpx.Response(302, headers={"Location": "https://evil.example/private"})

    transport = ApprovedHTTPSTransport(
        "https://api.unknown.example",
        resolver=resolve,
        transport=httpx.MockTransport(handler),
    )
    connector = GenericAPIConnector(config(), Secrets(), transport=transport)
    with pytest.raises(ConnectorRuntimeError, match="redirects are not allowed"):
        await connector.sync(sync_request())
    assert calls == 1


@pytest.mark.asyncio
async def test_generic_api_bounds_streamed_response_before_json_parsing() -> None:
    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * MAX_RESPONSE_BYTES
            yield b"y"

    async def resolve(host: str) -> list[str]:
        assert host == "api.unknown.example"
        return ["93.184.216.34"]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=LargeStream())

    transport = ApprovedHTTPSTransport(
        "https://api.unknown.example",
        resolver=resolve,
        transport=httpx.MockTransport(handler),
    )
    connector = GenericAPIConnector(config(), Secrets(), transport=transport)
    with pytest.raises(ConnectorRuntimeError, match="response is too large"):
        await connector.sync(sync_request())


@pytest.mark.asyncio
async def test_generic_api_does_not_forward_unmapped_provider_secrets() -> None:
    secret = "provider-private-token-that-must-not-enter-canonical-data"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [example_item(access_token=secret, private={"password": secret})]},
        )

    connector = GenericAPIConnector(config(), Secrets(), transport=httpx.MockTransport(handler))
    try:
        page = await connector.sync(sync_request())
    except ConnectorRuntimeError as error:
        assert secret not in str(error)
    else:
        assert secret not in page.model_dump_json()
        assert (
            secret
            not in canonical_resource_to_source_document(
                page.resources[0],
                provenance_connection_id=page.resources[0].connector_connection_id,
            ).model_dump_json()
        )


@pytest.mark.asyncio
async def test_generic_api_rejects_reflected_bearer_token_before_model_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [example_item(description="Bearer secret-token was reflected")]},
        )

    connector = GenericAPIConnector(config(), Secrets(), transport=httpx.MockTransport(handler))
    with pytest.raises(ConnectorRuntimeError) as failure:
        await connector.sync(sync_request())
    assert "secret-token" not in str(failure.value)


@pytest.mark.asyncio
async def test_generic_api_rejects_percent_encoded_token_in_deep_link() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [example_item(url="https://app.unknown.example/tasks/secret%2Dtoken")]},
        )

    connector = GenericAPIConnector(config(), Secrets(), transport=httpx.MockTransport(handler))
    with pytest.raises(ConnectorRuntimeError, match="credential material") as failure:
        await connector.sync(sync_request())
    assert "secret-token" not in str(failure.value)


@pytest.mark.asyncio
async def test_generic_api_rejects_bearer_token_hidden_by_json_escapes() -> None:
    body = (
        b'{"items":[{"id":"a","title":"Review launch plan",'
        b'"description":"Bearer secret\\u002dtoken was reflected",'
        b'"updated_at":"2026-09-24T12:00:00Z"}]}'
    )
    assert b"secret-token" not in body

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    connector = GenericAPIConnector(config(), Secrets(), transport=httpx.MockTransport(handler))
    with pytest.raises(ConnectorRuntimeError) as failure:
        await connector.sync(sync_request())
    assert "secret-token" not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "link",
    [
        "http://app.unknown.example/tasks/a",
        "https://user:password@app.unknown.example/tasks/a",
        "https://app.unknown.example/tasks/a?access_token=secret-token",
        "https://app.unknown.example/tasks/a?page=2",
    ],
)
async def test_generic_api_never_exposes_credential_bearing_deep_links(link: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [example_item(url=link)]})

    connector = GenericAPIConnector(config(), Secrets(), transport=httpx.MockTransport(handler))
    try:
        page = await connector.sync(sync_request())
    except ConnectorRuntimeError as error:
        assert "secret-token" not in str(error)
        assert "password" not in str(error)
    else:
        source_url = page.resources[0].source_url
        if source_url is not None:
            parsed = urlsplit(source_url)
            assert parsed.scheme == "https"
            assert parsed.username is None and parsed.password is None
            assert not parsed.query and not parsed.fragment
            assert "secret-token" not in source_url


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
