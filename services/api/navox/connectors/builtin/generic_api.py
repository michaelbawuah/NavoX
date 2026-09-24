from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from navox.connectors.contracts import (
    AuthMethod,
    AuthorizationRequest,
    AuthorizationResult,
    CanonicalResource,
    CapabilityDefinition,
    ConnectorActionRequest,
    ConnectorActionResult,
    ConnectorCapabilities,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorHealthState,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SecretAccessor,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)
from navox.connectors.network import (
    join_relative_path,
    require_same_origin,
    validate_public_https_origin,
)

MAX_GENERIC_RESPONSE_BYTES = 2_000_000
MAX_GENERIC_ITEMS = 10_000
MAX_GENERIC_PAGES = 50


class GenericEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=512)
    capability: str = Field(min_length=3, max_length=160)
    resource_type: str = Field(min_length=1, max_length=128)
    items_field: str | None = Field(default=None, max_length=128)
    id_field: str = Field(default="id", min_length=1, max_length=128)
    subject_field: str | None = Field(default="title", max_length=128)
    content_field: str | None = Field(default="description", max_length=128)
    occurred_at_field: str | None = Field(default="updated_at", max_length=128)
    parent_field: str | None = Field(default=None, max_length=128)
    source_url_field: str | None = Field(default=None, max_length=128)
    status_field: str | None = Field(default=None, max_length=128)
    cursor_param: str | None = Field(default=None, max_length=80)
    next_cursor_field: str | None = Field(default=None, max_length=128)
    page_size_param: str | None = Field(default=None, max_length=80)
    page_size: int = Field(default=100, ge=1, le=500)
    static_params: dict[str, str] = Field(default_factory=dict)

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        if re.fullmatch(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$", value) is None:
            raise ValueError("Generic API capability must be a normalized capability name")
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> GenericEndpointConfig:
        join_relative_path("https://example.invalid", self.path)
        if (self.cursor_param is None) is not (self.next_cursor_field is None):
            raise ValueError("Cursor request and response fields must be configured together")
        if len(self.static_params) > 32:
            raise ValueError("Too many static endpoint parameters")
        return self


class GenericAPIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]+$")
    base_url: str
    auth: str = Field(default="bearer", pattern=r"^(bearer|none)$")
    token_secret_name: str = Field(default="API_TOKEN", pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    endpoints: list[GenericEndpointConfig] = Field(min_length=1, max_length=32)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return validate_public_https_origin(value)

    @model_validator(mode="after")
    def validate_unique_endpoints(self) -> GenericAPIConfig:
        names = [endpoint.name for endpoint in self.endpoints]
        resources = [endpoint.resource_type for endpoint in self.endpoints]
        if len(names) != len(set(names)):
            raise ValueError("Generic API endpoint names must be unique")
        if len(resources) != len(set(resources)):
            raise ValueError("Generic API resource types must be unique")
        return self


GENERIC_API_BASE_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": "generic-rest-api",
        "version": "1.0.0",
        "displayName": "Generic REST API",
        "category": "developer",
        "connectorClass": "GENERIC_API",
        "auth": [{"kind": "api_token", "label": "API token", "scopes": []}],
        "resourceTypes": ["generic.resource"],
        "capabilities": {
            "read": [
                {
                    "name": "generic.data.read",
                    "description": "Read configured API resources",
                    "sensitive": True,
                }
            ],
            "write": [],
            "events": [],
            "incrementalSync": False,
        },
        "requiredSecrets": ["API_TOKEN"],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


class GenericAPIConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = GenericAPIConfig.model_validate(config)
        self.secrets = secrets
        self.transport = transport

    def get_manifest(self) -> ConnectorManifest:
        required = [self.config.token_secret_name] if self.config.auth == "bearer" else []
        auth = (
            [AuthMethod(kind="api_token", label="API token", scopes=[])]
            if required
            else [AuthMethod(kind="none", label="No authentication", scopes=[])]
        )
        capabilities = [
            CapabilityDefinition(
                name=endpoint.capability,
                description=f"Read {endpoint.name}",
                sensitive=True,
            )
            for endpoint in self.config.endpoints
        ]
        return ConnectorManifest(
            id=GENERIC_API_BASE_MANIFEST.id,
            version=GENERIC_API_BASE_MANIFEST.version,
            displayName=self.config.display_name,
            category="developer",
            connectorClass="GENERIC_API",
            auth=auth,
            resourceTypes=[endpoint.resource_type for endpoint in self.config.endpoints],
            capabilities=ConnectorCapabilities(
                read=capabilities,
                write=[],
                events=[],
                incrementalSync=False,
            ),
            requiredSecrets=required,
            rateLimitStrategy="provider_headers",
            minimumNavoxConnectorApiVersion="1",
        )

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        return AuthorizationResult(authorized=False)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        del connection
        endpoint = self.config.endpoints[0]
        try:
            async with self._client() as client:
                await self._fetch_endpoint(client, endpoint, max_items=1)
            return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))
        except ConnectorRuntimeError as error:
            states: dict[str, ConnectorHealthState] = {
                "AUTH_EXPIRED": "AUTH_EXPIRED",
                "RATE_LIMITED": "RATE_LIMITED",
                "PROVIDER_UNAVAILABLE": "DEGRADED",
            }
            state = states.get(error.code, "DEGRADED")
            return ConnectorHealth(
                state=state,
                checked_at=datetime.now(UTC),
                reason_code=error.code,
            )

    async def sync(self, request: SyncRequest) -> SyncPage:
        resources: list[CanonicalResource] = []
        async with self._client() as client:
            for endpoint in self.config.endpoints:
                if endpoint.capability not in request.capabilities:
                    continue
                items = await self._fetch_endpoint(client, endpoint)
                resources.extend(self._resource(request, endpoint, item) for item in items)
        resources.sort(key=lambda item: (item.resource_type, item.external_id))
        after = request.cursor or ""
        pending = [
            item for item in resources if f"{item.resource_type}\x00{item.external_id}" > after
        ]
        selected = pending[: request.limit]
        has_more = len(pending) > len(selected)
        next_cursor = (
            f"{selected[-1].resource_type}\x00{selected[-1].external_id}"
            if has_more and selected
            else None
        )
        return SyncPage(resources=selected, next_cursor=next_cursor, has_more=has_more)

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        page = await self.sync(
            SyncRequest(
                connection_id=request.connection_id,
                workspace_id=request.workspace_id,
                limit=1_000,
                capabilities=frozenset(endpoint.capability for endpoint in self.config.endpoints),
            )
        )
        for resource in page.resources:
            if (
                resource.resource_type == request.resource_type
                and resource.external_id == request.external_id
            ):
                return resource
        raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Configured API resource was not found")

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY",
            "Generic REST writes are not enabled in SPEC-003",
        )

    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self.config.auth == "bearer":
            if self.secrets is None or self.config.token_secret_name not in self.secrets.names:
                raise ConnectorRuntimeError("AUTH_EXPIRED", "API credential is unavailable")
            headers["Authorization"] = f"Bearer {self.secrets.get(self.config.token_secret_name)}"
        return httpx.AsyncClient(
            transport=self.transport,
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
        )

    async def _fetch_endpoint(
        self,
        client: httpx.AsyncClient,
        endpoint: GenericEndpointConfig,
        *,
        max_items: int = MAX_GENERIC_ITEMS,
    ) -> list[dict[str, object]]:
        url = join_relative_path(self.config.base_url, endpoint.path)
        params: dict[str, str] = dict(endpoint.static_params)
        if endpoint.page_size_param:
            params[endpoint.page_size_param] = str(endpoint.page_size)
        cursor: str | None = None
        items: list[dict[str, object]] = []
        for _ in range(MAX_GENERIC_PAGES):
            page_params = dict(params)
            if cursor is not None and endpoint.cursor_param:
                page_params[endpoint.cursor_param] = cursor
            response = await self._request(client, url, page_params)
            payload = _json_payload(response)
            page_items = _items(payload, endpoint.items_field)
            items.extend(page_items)
            if len(items) >= max_items:
                return items[:max_items]
            next_cursor = (
                _field(payload, endpoint.next_cursor_field) if endpoint.next_cursor_field else None
            )
            link = response.links.get("next")
            link_url = str(link["url"]) if link and link.get("url") else None
            if next_cursor is not None:
                cursor = str(next_cursor)
                continue
            if link_url:
                require_same_origin(link_url, self.config.base_url)
                url = link_url
                params = {}
                cursor = None
                continue
            return items
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE",
            "Configured API pagination exceeded the bounded page limit",
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: Mapping[str, str],
    ) -> httpx.Response:
        require_same_origin(url, self.config.base_url)
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError:
            raise ConnectorRuntimeError(
                "PROVIDER_UNAVAILABLE",
                "Configured API request failed",
            ) from None
        if len(response.content) > MAX_GENERIC_RESPONSE_BYTES:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                "Configured API response exceeded the bounded size limit",
            )
        if response.status_code == 401:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Configured API authorization expired")
        if response.status_code == 403:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Configured API permission denied")
        if response.status_code == 404:
            raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Configured API endpoint not found")
        if response.status_code == 429:
            raise ConnectorRuntimeError("RATE_LIMITED", "Configured API rate limit reached")
        if response.status_code >= 500:
            raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "Configured API is unavailable")
        if response.status_code >= 400:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                "Configured API returned an unexpected response",
            )
        return response

    def _resource(
        self,
        request: SyncRequest,
        endpoint: GenericEndpointConfig,
        item: dict[str, object],
    ) -> CanonicalResource:
        identifier = _field(item, endpoint.id_field)
        if identifier is None:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                f"Configured API item is missing id field {endpoint.id_field}",
            )
        external_id = f"{endpoint.name}:{identifier}"
        subject_value = _field(item, endpoint.subject_field)
        content_value = _field(item, endpoint.content_field)
        occurred_value = _field(item, endpoint.occurred_at_field)
        parent_value = _field(item, endpoint.parent_field)
        source_url_value = _field(item, endpoint.source_url_field)
        status_value = _field(item, endpoint.status_field)
        occurred = _parse_time(occurred_value) or datetime.now(UTC)
        bounded = _bounded_object(item)
        source_url = _safe_deep_link(source_url_value)
        return CanonicalResource(
            resource_id=stable_resource_id(
                request.connection_id,
                endpoint.resource_type,
                external_id,
            ),
            workspace_id=request.workspace_id,
            connector_connection_id=request.connection_id,
            provider=self.config.provider,
            resource_type=endpoint.resource_type,
            external_id=external_id,
            external_parent_id=str(parent_value)[:512] if parent_value is not None else None,
            canonical={
                "source_type": endpoint.resource_type,
                "subject": str(subject_value)[:2_000]
                if subject_value is not None
                else endpoint.name,
                "content": str(content_value)[:32_000] if content_value is not None else None,
                "occurred_at": occurred.isoformat(),
                "status": str(status_value)[:64] if status_value is not None else "active",
                "metadata": {"fields": bounded, "endpoint": endpoint.name},
            },
            provider_metadata={"configured_endpoint": endpoint.name},
            source_url=source_url,
            updated_at=occurred,
            retrieved_at=datetime.now(UTC),
        )


def _json_payload(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE",
            "Configured API returned invalid JSON",
        ) from None


def _items(payload: object, items_field: str | None) -> list[dict[str, object]]:
    value = _field(payload, items_field) if items_field else payload
    if not isinstance(value, list):
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE",
            "Configured API list mapping did not resolve to a list",
        )
    result = [item for item in value if isinstance(item, dict)]
    if len(result) > MAX_GENERIC_ITEMS:
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE",
            "Configured API response exceeded the bounded item limit",
        )
    return result


def _field(value: object, path: str | None) -> object:
    if path is None:
        return None
    current = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _safe_deep_link(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return value[:2_000]


def _bounded_object(value: dict[str, object]) -> dict[str, JsonValue]:
    encoded = json.loads(json.dumps(value, default=str))
    if not isinstance(encoded, dict):
        return {}
    result: dict[str, JsonValue] = {}
    for key, item in list(encoded.items())[:128]:
        if isinstance(item, str):
            result[str(key)[:128]] = item[:4_000]
        elif item is None or isinstance(item, bool | int | float):
            result[str(key)[:128]] = item
        elif isinstance(item, list):
            result[str(key)[:128]] = [
                str(entry)[:500] if not isinstance(entry, bool | int | float) else entry
                for entry in item[:50]
            ]
        elif isinstance(item, dict):
            result[str(key)[:128]] = {
                str(subkey)[:128]: str(subvalue)[:1_000]
                for subkey, subvalue in list(item.items())[:50]
            }
    return result
