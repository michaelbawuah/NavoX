"""Operator-reviewed MCP 2026-07-28 read adapter.

Server metadata is discovery data, never an authority grant. An operator pins
the HTTPS endpoint, resource prefixes, and exact read-only tool calls; the
ConnectorRuntime applies provider, user, and policy capability intersection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal
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
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SecretAccessor,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)
from navox.connectors.errors import retry_after_seconds
from navox.connectors.network import join_relative_path
from navox.connectors.outbound import MAX_RESPONSE_BYTES, ApprovedHTTPSTransport, approved_origin
from navox.connectors.registry import ConnectorRegistry

MCP_VERSION = "2026-07-28"
MAX_MCP_LIST_PAGES = 10
MAX_MCP_DISCOVERED = 200
MAX_MCP_TEXT = 32_000
_READ_CAPABILITY = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.read$"
_SENSITIVE = re.compile(r"(?:token|secret|password|credential|api[_-]?key|authorization)", re.I)
_FORBIDDEN_URI_SCHEMES = frozenset(
    {"file", "data", "javascript", "http", "ftp", "ssh", "cmd", "exec", "about", "chrome"}
)


def _clean_resource_uri(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        decoded = unquote(value)
        value.encode("utf-8")
        return (
            0 < len(value) <= 512
            and bool(parsed.scheme)
            and parsed.scheme not in _FORBIDDEN_URI_SCHEMES
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and "\\" not in decoded
            and not any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            and not any(part in {".", ".."} for part in urlsplit(decoded).path.split("/"))
            and not _SENSITIVE.search(decoded)
        )
    except (ValueError, UnicodeError):
        return False


class MCPResourceGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uri_prefix: str = Field(min_length=4, max_length=512)
    capability: str = Field(pattern=_READ_CAPABILITY)
    resource_type: str = Field(min_length=1, max_length=128)

    @field_validator("uri_prefix")
    @classmethod
    def require_uri(cls, value: str) -> str:
        if not _clean_resource_uri(value):
            raise ValueError("MCP resource prefix must be a credential-free URI")
        return value


class MCPReadToolGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    capability: str = Field(pattern=_READ_CAPABILITY)
    resource_type: str = Field(min_length=1, max_length=128)
    # Arguments are immutable operator-reviewed values. The model never chooses
    # a tool or supplies arguments through this ingestion adapter.
    arguments: dict[str, str | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_arguments(self) -> MCPReadToolGrant:
        if (
            len(self.arguments) > 12
            or any(
                not key
                or len(key) > 80
                or _SENSITIVE.search(key)
                or (isinstance(value, str) and (len(value) > 256 or _SENSITIVE.search(value)))
                for key, value in self.arguments.items()
            )
            or any(ord(char) < 32 or ord(char) == 127 for char in self.name)
        ):
            raise ValueError("MCP read tool arguments must be bounded and credential-free")
        return self


class MCPServerPolicy(BaseModel):
    """Deployment-managed policy, never accepted from a connection request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
    provider: str = Field(max_length=32, pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    display_name: str = Field(min_length=1, max_length=120)
    origin: str
    path: str = "/mcp"
    token_secret_name: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    resources: list[MCPResourceGrant] = Field(default_factory=list, max_length=32)
    read_tools: list[MCPReadToolGrant] = Field(default_factory=list, max_length=32)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return approved_origin(value)

    @model_validator(mode="after")
    def validate_policy(self) -> MCPServerPolicy:
        if not self.resources and not self.read_tools:
            raise ValueError("MCP policy must explicitly approve read access")
        join_relative_path(self.origin, self.path)
        if (
            urlsplit(self.path).query
            or urlsplit(self.path).fragment
            or "?" in self.path
            or "#" in self.path
            or "%" in self.path
        ):
            raise ValueError("MCP endpoint path must be fixed and credential-free")
        if len({r.uri_prefix for r in self.resources}) != len(self.resources):
            raise ValueError("MCP resource grants must have unique prefixes")
        if len({t.name for t in self.read_tools}) != len(self.read_tools):
            raise ValueError("MCP read tools must be unique")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def connection_config(self) -> dict[str, str]:
        return {"server_id": self.server_id, "policy_digest": self.digest}

    @property
    def connector_key(self) -> str:
        # Rotating any approved field creates a new registry identity. An old
        # connection cannot silently inherit the new server/tool approval.
        return f"mcp-{self.server_id}-{self.digest[:12]}"


class MCPConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        policy: MCPServerPolicy,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if dict(config) != policy.connection_config():
            raise ValueError("MCP connection differs from the reviewed server policy")
        self.policy = policy
        self.secrets = secrets
        self.transport = transport

    def get_manifest(self) -> ConnectorManifest:
        grants: list[MCPResourceGrant | MCPReadToolGrant] = [
            *self.policy.resources,
            *self.policy.read_tools,
        ]
        names = sorted({grant.capability for grant in grants})
        return ConnectorManifest(
            id=self.policy.connector_key,
            version="1.0.0",
            displayName=self.policy.display_name,
            category="developer",
            connectorClass="MCP",
            auth=[
                AuthMethod(kind="mcp", label="Server token", scopes=[])
                if self.policy.token_secret_name
                else AuthMethod(kind="none", label="No authentication", scopes=[])
            ],
            resourceTypes=sorted({grant.resource_type for grant in grants}),
            capabilities=ConnectorCapabilities(
                read=[
                    CapabilityDefinition(name=name, description=f"Read {name}", sensitive=True)
                    for name in names
                ],
                write=[],
                events=[],
                incrementalSync=False,
            ),
            requiredSecrets=[self.policy.token_secret_name]
            if self.policy.token_secret_name
            else [],
            rateLimitStrategy="provider_headers",
            minimumNavoxConnectorApiVersion="1",
        )

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        return AuthorizationResult(authorized=False)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        del connection
        state: Literal["CONNECTED", "AUTH_EXPIRED"] = "CONNECTED"
        if self.policy.token_secret_name and (
            self.secrets is None or self.policy.token_secret_name not in self.secrets.names
        ):
            state = "AUTH_EXPIRED"
        return ConnectorHealth(
            state=state,
            checked_at=datetime.now(UTC),
            reason_code="AUTH_EXPIRED" if state == "AUTH_EXPIRED" else None,
        )

    async def discover(
        self, capabilities: frozenset[str]
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Return approved resource descriptors and read tool names only."""
        async with self._client() as client:
            return await self._discover(client, capabilities)

    async def _discover(
        self, client: httpx.AsyncClient, capabilities: frozenset[str]
    ) -> tuple[list[dict[str, str]], list[str]]:
        resources: list[dict[str, str]] = []
        tools: list[str] = []
        approved_resources = [r for r in self.policy.resources if r.capability in capabilities]
        approved_tools = {t.name for t in self.policy.read_tools if t.capability in capabilities}
        if approved_resources:
            for entry in await self._list(client, "resources/list", "resources"):
                uri = entry.get("uri")
                if not isinstance(uri, str) or not any(
                    uri.startswith(grant.uri_prefix) for grant in approved_resources
                ):
                    continue
                # Resource URIs are sent to their own server, never persisted or
                # surfaced to the model. Reject credentials and oversized URIs.
                if not _clean_resource_uri(uri):
                    continue
                name = entry.get("name")
                resources.append(
                    {"uri": uri, "name": name[:200] if isinstance(name, str) else "MCP resource"}
                )
        if approved_tools:
            for entry in await self._list(client, "tools/list", "tools"):
                name = entry.get("name")
                if isinstance(name, str) and name in approved_tools:
                    # Custom header extensions need an explicitly implemented
                    # request contract; do not silently omit security headers.
                    if '"x-mcp-header"' in json.dumps(entry.get("inputSchema", {})):
                        continue
                    annotations = entry.get("annotations")
                    if (
                        not isinstance(annotations, dict)
                        or annotations.get("readOnlyHint") is not True
                        or annotations.get("destructiveHint") is True
                    ):
                        continue
                    tools.append(name)
        if len({r["uri"] for r in resources}) != len(resources) or len(set(tools)) != len(tools):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Duplicate MCP discovery item")
        return resources, tools

    async def sync(self, request: SyncRequest) -> SyncPage:
        allowed = {item.name for item in self.get_manifest().capabilities.read}
        if not request.capabilities or not request.capabilities <= allowed:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "MCP read capability is not approved")
        async with self._client() as client:
            descriptors, tool_names = await self._discover(client, request.capabilities)
            grants = {item.name: item for item in self.policy.read_tools}
            items: list[tuple[str, str, MCPResourceGrant | MCPReadToolGrant]] = []
            for descriptor in descriptors:
                uri = descriptor["uri"]
                resource_grant = next(
                    candidate
                    for candidate in self.policy.resources
                    if candidate.capability in request.capabilities
                    and uri.startswith(candidate.uri_prefix)
                )
                items.append(
                    (f"resource:{hashlib.sha256(uri.encode()).hexdigest()}", uri, resource_grant)
                )
            for name in tool_names:
                items.append((f"tool:{name}", name, grants[name]))
            items.sort(key=lambda item: item[0])
            pending = [item for item in items if item[0] > (request.cursor or "")]
            selected = pending[: request.limit]
            resources = []
            for external_id, identifier, grant in selected:
                if isinstance(grant, MCPResourceGrant):
                    result = await self._rpc(client, "resources/read", {"uri": identifier})
                    content = self._resource_text(result, identifier)
                    title = next(d["name"] for d in descriptors if d["uri"] == identifier)
                else:
                    result = await self._rpc(
                        client, "tools/call", {"name": identifier, "arguments": grant.arguments}
                    )
                    content = self._tool_text(result)
                    title = identifier
                now = datetime.now(UTC)
                resources.append(
                    CanonicalResource(
                        resource_id=stable_resource_id(
                            request.connection_id, grant.resource_type, external_id
                        ),
                        workspace_id=request.workspace_id,
                        connector_connection_id=request.connection_id,
                        provider=self.policy.provider,
                        resource_type=grant.resource_type,
                        external_id=external_id,
                        canonical={
                            "source_type": grant.resource_type,
                            "subject": title,
                            "content": content,
                            "occurred_at": now.isoformat(),
                            "status": "active",
                            "metadata": {"capability": grant.capability},
                        },
                        provider_metadata={"capability": grant.capability},
                        retrieved_at=now,
                    )
                )
            has_more = len(pending) > len(selected)
            return SyncPage(
                resources=resources,
                next_cursor=selected[-1][0] if has_more and selected else None,
                has_more=has_more,
            )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY", "MCP reads require a policy-scoped sync request"
        )

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "MCP writes are not enabled")

    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.policy.token_secret_name:
            if self.secrets is None or self.policy.token_secret_name not in self.secrets.names:
                raise ConnectorRuntimeError("AUTH_EXPIRED", "MCP credential is unavailable")
            headers["Authorization"] = f"Bearer {self.secrets.get(self.policy.token_secret_name)}"
        return httpx.AsyncClient(
            transport=self.transport
            or ApprovedHTTPSTransport(
                self.policy.origin, allowed_post_paths=frozenset({self.policy.path})
            ),
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
            trust_env=False,
        )

    async def _list(
        self, client: httpx.AsyncClient, method: str, key: str
    ) -> list[dict[str, object]]:
        cursor: str | None = None
        seen: set[str] = set()
        collected: list[dict[str, object]] = []
        for _ in range(MAX_MCP_LIST_PAGES):
            result = await self._rpc(client, method, {"cursor": cursor} if cursor else {})
            raw = result.get(key)
            if not isinstance(raw, list) or not all(isinstance(entry, dict) for entry in raw):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Invalid MCP discovery list"
                )
            collected.extend(raw)
            if len(collected) > MAX_MCP_DISCOVERED:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "MCP discovery limit exceeded"
                )
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return collected
            if (
                not isinstance(next_cursor, str)
                or not 0 < len(next_cursor) <= 512
                or next_cursor in seen
            ):
                raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid MCP cursor")
            seen.add(next_cursor)
            cursor = next_cursor
        raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "MCP pagination limit exceeded")

    async def _rpc(
        self, client: httpx.AsyncClient, method: str, params: dict[str, object]
    ) -> dict[str, object]:
        parameters = {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": MCP_VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "NavoX", "version": "1.0.0"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        }
        headers = {"MCP-Protocol-Version": MCP_VERSION, "Mcp-Method": method}
        name = params.get("name", params.get("uri"))
        if isinstance(name, str):
            encoded = name.encode()
            if len(encoded) > 768:
                raise ConnectorRuntimeError("PERMISSION_DENIED", "MCP name header is too long")
            headers["Mcp-Name"] = (
                name
                if all(32 <= byte < 127 for byte in encoded)
                else f"=?base64?{base64.b64encode(encoded).decode()}?="
            )
        try:
            response = await client.post(
                join_relative_path(self.policy.origin, self.policy.path),
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": parameters},
                headers=headers,
            )
        except httpx.HTTPError:
            raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "MCP request failed") from None
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "MCP response too large")
        token = client.headers.get("Authorization", "").removeprefix("Bearer ")
        if token and token.encode() in response.content:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "MCP response contains credential material"
            )
        if response.status_code in {401, 403}:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "MCP access expired")
        if response.status_code == 429:
            raise ConnectorRuntimeError(
                "RATE_LIMITED",
                "MCP rate limit reached",
                retry_after_seconds=retry_after_seconds(response.headers.get("Retry-After")),
            )
        if response.status_code >= 500:
            raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "MCP server unavailable")
        if response.status_code != 200 or "application/json" not in response.headers.get(
            "content-type", ""
        ):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Unsupported MCP response")
        try:
            payload = response.json()
        except ValueError:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid MCP JSON") from None
        if token and self._contains_credential(payload, token):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "MCP response contains credential material"
            )
        if (
            not isinstance(payload, dict)
            or payload.get("jsonrpc") != "2.0"
            or payload.get("id") != 1
            or "error" in payload
            or not isinstance(payload.get("result"), dict)
        ):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid MCP RPC result")
        result: dict[str, object] = payload["result"]
        return result

    @staticmethod
    def _contains_credential(payload: object, token: str) -> bool:
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                for _ in range(3):
                    if token in value:
                        return True
                    decoded = unquote(value)
                    if decoded == value:
                        break
                    value = decoded
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return False

    @staticmethod
    def _resource_text(result: dict[str, object], uri: str) -> str:
        contents = result.get("contents")
        if not isinstance(contents, list) or not 1 <= len(contents) <= 16:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid MCP resource content")
        text = []
        for item in contents:
            if (
                not isinstance(item, dict)
                or item.get("uri") != uri
                or item.get("mimeType", "text/plain") not in {"text/plain", "application/json"}
                or not isinstance(item.get("text"), str)
            ):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "MCP resource must be approved text"
                )
            text.append(item["text"])
        output = "\n".join(text)
        if len(output) > MAX_MCP_TEXT:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "MCP content limit exceeded")
        return output

    @staticmethod
    def _tool_text(result: dict[str, object]) -> str:
        if result.get("isError") is True:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "MCP read tool failed")
        content = result.get("content")
        if not isinstance(content, list) or not 1 <= len(content) <= 16:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid MCP tool content")
        parts: list[str] = []
        for item in content:
            if (
                not isinstance(item, dict)
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)
            ):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "MCP read tool must return text"
                )
            parts.append(item["text"])
        output = "\n".join(parts)
        if len(output) > MAX_MCP_TEXT:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "MCP content limit exceeded")
        return output


def register_mcp_server(registry: ConnectorRegistry, policy: MCPServerPolicy) -> None:
    """Trusted deployment code registers a reviewed server, never remote plugin code."""

    def factory(config: Mapping[str, JsonValue], secrets: SecretAccessor | None) -> MCPConnector:
        return MCPConnector(config, secrets, policy=policy)

    registry.register(factory(policy.connection_config(), None).get_manifest(), factory)
