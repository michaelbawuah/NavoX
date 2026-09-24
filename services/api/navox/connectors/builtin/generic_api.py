from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

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
from navox.connectors.errors import retry_after_seconds
from navox.connectors.network import (
    join_relative_path,
    require_same_origin,
)
from navox.connectors.outbound import ApprovedHTTPSTransport, approved_origin

MAX_GENERIC_RESPONSE_BYTES = 2_000_000
MAX_GENERIC_ITEMS = 10_000
MAX_GENERIC_PAGES = 50
_SENSITIVE_QUERY_PARTS = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "session",
    "signature",
    "access_key",
    "client_key",
    "auth",
)


def _sensitive_query_key(key: str) -> bool:
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", words.casefold()).strip("_")
    return normalized in {
        "api_key",
        "access_key",
        "client_key",
        "private_key",
    } or any(part in _SENSITIVE_QUERY_PARTS for part in normalized.split("_"))


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
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment or "?" in self.path or "#" in self.path:
            raise ValueError("Endpoint path cannot include query parameters or fragments")
        if (self.cursor_param is None) is not (self.next_cursor_field is None):
            raise ValueError("Cursor request and response fields must be configured together")
        if len(self.static_params) > 32:
            raise ValueError("Too many static endpoint parameters")
        for key, value in self.static_params.items():
            if (
                not key
                or len(key) > 80
                or key.casefold() in {"code", "key"}
                or _sensitive_query_key(key)
                or len(value) > 512
            ):
                raise ValueError("Static endpoint parameters must be bounded and credential-free")
        if any(
            name is not None and _sensitive_query_key(name)
            for name in (self.cursor_param, self.page_size_param)
        ):
            raise ValueError("Pagination parameter names must be credential-free")
        mapped_fields = (
            self.items_field,
            self.id_field,
            self.subject_field,
            self.content_field,
            self.occurred_at_field,
            self.parent_field,
            self.status_field,
            self.source_url_field,
            self.next_cursor_field,
        )
        if any(
            field is not None and any(_sensitive_query_key(part) for part in field.split("."))
            for field in mapped_fields
        ):
            raise ValueError("Resource fields cannot map credential material")
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
        return approved_origin(value)

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
                retry_after_seconds=error.retry_after_seconds,
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
            transport=self.transport or ApprovedHTTPSTransport(self.config.base_url),
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
            trust_env=False,
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
            payload = _json_payload(
                response,
                credential=client.headers.get("Authorization", "").removeprefix("Bearer "),
            )
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
        authorization = client.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            credential = authorization.removeprefix("Bearer ")
            if credential and credential.encode("utf-8") in response.content:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Provider response contains credential material"
                )
        if response.status_code == 401:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Configured API authorization expired")
        if response.status_code == 403:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Configured API permission denied")
        if response.status_code == 404:
            raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Configured API endpoint not found")
        if response.status_code == 429:
            raise ConnectorRuntimeError(
                "RATE_LIMITED",
                "Configured API rate limit reached",
                retry_after_seconds=retry_after_seconds(response.headers.get("Retry-After")),
            )
        if response.status_code >= 500:
            raise ConnectorRuntimeError(
                "PROVIDER_UNAVAILABLE",
                "Configured API is unavailable",
                retry_after_seconds=retry_after_seconds(response.headers.get("Retry-After")),
            )
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
        mapped_values = (identifier, subject_value, content_value, parent_value, status_value)
        if any(isinstance(value, dict | list) for value in mapped_values):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Configured API field must contain a scalar value"
            )
        occurred = _parse_time(occurred_value) or datetime.now(UTC)
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
                "metadata": {"endpoint": endpoint.name},
            },
            provider_metadata={"configured_endpoint": endpoint.name},
            source_url=source_url,
            updated_at=occurred,
            retrieved_at=datetime.now(UTC),
        )


def _json_payload(response: httpx.Response, *, credential: str = "") -> object:
    try:
        payload = response.json()
    except ValueError:
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE",
            "Configured API returned invalid JSON",
        ) from None
    if credential and _contains_credential(payload, credential):
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE", "Provider response contains credential material"
        )
    return payload


def _contains_credential(payload: object, credential: str) -> bool:
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            for _ in range(3):
                if credential in value:
                    return True
                decoded = unquote(value)
                if decoded == value:
                    break
                value = decoded
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


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
    if not isinstance(value, str) or len(value) > 2_000:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            return None
        approved_origin(f"https://{parsed.netloc}")
    except ValueError:
        return None
    return value
