from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

NAVOX_CONNECTOR_API_VERSION = "1"

ConnectorClass = Literal[
    "OAUTH_API",
    "TOKEN_API",
    "WEBHOOK",
    "POLLING",
    "MCP",
    "GENERIC_API",
    "BROWSER_ASSISTED",
    "IMPORT",
]
TrustLevel = Literal[
    "NAVOX_FIRST_PARTY",
    "NAVOX_VERIFIED",
    "WORKSPACE_PRIVATE",
    "USER_PRIVATE",
    "THIRD_PARTY_VERIFIED",
    "UNVERIFIED",
]
ConnectorHealthState = Literal[
    "CONNECTED",
    "DEGRADED",
    "AUTH_EXPIRED",
    "RATE_LIMITED",
    "SYNC_FAILED",
    "PAUSED",
    "DISCONNECTED",
]
ConnectorErrorCode = Literal[
    "AUTH_EXPIRED",
    "AUTH_REVOKED",
    "RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "RESOURCE_NOT_FOUND",
    "PERMISSION_DENIED",
    "INVALID_PROVIDER_RESPONSE",
    "UNSUPPORTED_CAPABILITY",
    "TEMPORARY_FAILURE",
    "PERMANENT_FAILURE",
]
AuthKind = Literal["oauth2", "api_token", "webhook_secret", "mcp", "none"]

_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_CONNECTOR_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")


class AuthMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AuthKind
    label: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=list, max_length=128)


class CapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=1, max_length=512)
    sensitive: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _CAPABILITY_RE.fullmatch(value) is None:
            raise ValueError("Capability names must be dot-separated lowercase identifiers")
        return value


class ConnectorCapabilities(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    read: list[CapabilityDefinition] = Field(default_factory=list, max_length=128)
    write: list[CapabilityDefinition] = Field(default_factory=list, max_length=128)
    events: list[str] = Field(default_factory=list, max_length=128)
    incremental_sync: bool = Field(default=False, alias="incrementalSync")

    @model_validator(mode="after")
    def validate_unique_capabilities(self) -> ConnectorCapabilities:
        read_names = [item.name for item in self.read]
        write_names = [item.name for item in self.write]
        if len(read_names) != len(set(read_names)):
            raise ValueError("Duplicate read capability")
        if len(write_names) != len(set(write_names)):
            raise ValueError("Duplicate write capability")
        if len(self.events) != len(set(self.events)):
            raise ValueError("Duplicate connector event")
        for event in self.events:
            if _CAPABILITY_RE.fullmatch(event) is None:
                raise ValueError("Event names must be dot-separated lowercase identifiers")
        return self


class ConnectorManifest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    id: str = Field(min_length=3, max_length=64)
    version: str = Field(min_length=5, max_length=64)
    display_name: str = Field(alias="displayName", min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    connector_class: ConnectorClass = Field(alias="connectorClass")
    auth: list[AuthMethod] = Field(default_factory=list, max_length=16)
    resource_types: list[str] = Field(alias="resourceTypes", min_length=1, max_length=128)
    capabilities: ConnectorCapabilities
    required_secrets: list[str] = Field(
        alias="requiredSecrets", default_factory=list, max_length=32
    )
    rate_limit_strategy: Literal["provider_headers", "fixed_backoff", "none"] = Field(
        alias="rateLimitStrategy"
    )
    minimum_navox_connector_api_version: str = Field(alias="minimumNavoxConnectorApiVersion")

    @model_validator(mode="after")
    def validate_manifest(self) -> ConnectorManifest:
        if _CONNECTOR_ID_RE.fullmatch(self.id) is None:
            raise ValueError("Connector id must be a lowercase slug")
        if _SEMVER_RE.fullmatch(self.version) is None:
            raise ValueError("Connector version must use semantic versioning")
        if self.minimum_navox_connector_api_version != NAVOX_CONNECTOR_API_VERSION:
            raise ValueError("Connector requires an unsupported NavoX connector API version")
        if len(self.resource_types) != len(set(self.resource_types)):
            raise ValueError("Duplicate resource type")
        if len(self.required_secrets) != len(set(self.required_secrets)):
            raise ValueError("Duplicate required secret")
        for name in self.required_secrets:
            if re.fullmatch(r"^[A-Z][A-Z0-9_]{1,63}$", name) is None:
                raise ValueError("Required secrets must be uppercase identifiers")
        if self.connector_class == "IMPORT" and self.required_secrets:
            raise ValueError("Import connectors cannot require secrets")
        return self


class ConnectorConnectionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID
    connector_id: str
    provider: str
    external_account_id: str
    status: ConnectorHealthState
    authorized_capabilities: frozenset[str] = frozenset()
    config: dict[str, JsonValue] = Field(default_factory=dict)


class ConnectorHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ConnectorHealthState
    checked_at: datetime
    reason_code: ConnectorErrorCode | None = None
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)

    @field_validator("checked_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Connector health timestamps must include a timezone")
        return value.astimezone(UTC)


class CanonicalResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: UUID
    workspace_id: UUID
    connector_connection_id: UUID
    provider: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=128)
    external_id: str = Field(min_length=1, max_length=512)
    external_parent_id: str | None = Field(default=None, max_length=512)
    version: str | None = Field(default=None, max_length=256)
    canonical: dict[str, JsonValue]
    provider_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    source_url: str | None = Field(default=None, max_length=2_000)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    retrieved_at: datetime

    @field_validator("created_at", "updated_at", "retrieved_at")
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Canonical resource timestamps must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_stable_identity(self) -> CanonicalResource:
        expected = stable_resource_id(
            self.connector_connection_id,
            self.resource_type,
            self.external_id,
        )
        if self.resource_id != expected:
            raise ValueError("Canonical resource id is not stable for this provider resource")
        return self


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: UUID
    user_id: UUID
    requested_capabilities: frozenset[str]


class AuthorizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized: bool
    external_account_id: str | None = None
    granted_capabilities: frozenset[str] = frozenset()
    redirect_url: str | None = None


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: UUID
    workspace_id: UUID
    cursor: str | None = None
    limit: int = Field(default=250, ge=1, le=1_000)
    capabilities: frozenset[str]


class SyncPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resources: list[CanonicalResource] = Field(default_factory=list, max_length=1_000)
    next_cursor: str | None = None
    has_more: bool = False


class FetchResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: UUID
    workspace_id: UUID
    resource_type: str
    external_id: str


class ConnectorActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: UUID
    workspace_id: UUID
    capability: str
    payload: dict[str, JsonValue]
    approval_id: UUID | None = None


class ConnectorActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    external_id: str | None = None
    result: dict[str, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class NavoXConnector(Protocol):
    def get_manifest(self) -> ConnectorManifest: ...

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult: ...

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth: ...

    async def sync(self, request: SyncRequest) -> SyncPage: ...

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource: ...

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        raise NotImplementedError


class ConnectorRuntimeError(RuntimeError):
    def __init__(
        self, code: ConnectorErrorCode, message: str = "Connector operation failed"
    ) -> None:
        super().__init__(message)
        self.code = code


def stable_resource_id(
    connection_id: UUID,
    resource_type: str,
    external_id: str,
) -> UUID:
    return uuid5(NAMESPACE_URL, f"navox:{connection_id}:{resource_type}:{external_id}")
