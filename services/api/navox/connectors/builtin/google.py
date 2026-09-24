from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.contracts import (
    AuthorizationRequest,
    AuthorizationResult,
    CanonicalResource,
    ConnectorActionRequest,
    ConnectorActionResult,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)
from navox.db.models import (
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
)
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import source_document_hash
from navox.providers.google_sources import CALENDAR_READ_SCOPE, GMAIL_READ_SCOPE

GOOGLE_CONNECTOR_KEY = "google-workspace"
GOOGLE_CONNECTOR_VERSION = "1.0.0"
GOOGLE_GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"

GOOGLE_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": GOOGLE_CONNECTOR_KEY,
        "version": GOOGLE_CONNECTOR_VERSION,
        "displayName": "Google Workspace",
        "category": "productivity",
        "connectorClass": "OAUTH_API",
        "auth": [
            {
                "kind": "oauth2",
                "label": "Google OAuth",
                "scopes": [
                    GMAIL_READ_SCOPE,
                    CALENDAR_READ_SCOPE,
                    GOOGLE_GMAIL_SEND_SCOPE,
                ],
            }
        ],
        "resourceTypes": ["communication.message", "calendar.event"],
        "capabilities": {
            "read": [
                {
                    "name": "communication.messages.read",
                    "description": "Read authorized Gmail messages",
                    "sensitive": True,
                },
                {
                    "name": "calendar.events.read",
                    "description": "Read authorized Google Calendar events",
                    "sensitive": True,
                },
            ],
            "write": [
                {
                    "name": "communication.messages.send",
                    "description": "Send Gmail messages through SPEC-001 approval",
                    "sensitive": True,
                }
            ],
            "events": [
                "communication.messages.changed",
                "calendar.events.changed",
            ],
            "incrementalSync": True,
        },
        "requiredSecrets": ["GOOGLE_REFRESH_TOKEN"],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


def google_capabilities(scopes: list[str]) -> frozenset[str]:
    result: set[str] = set()
    granted = set(scopes)
    if GMAIL_READ_SCOPE in granted:
        result.update(
            {
                "communication.messages.read",
                "communication.messages.changed",
            }
        )
    if CALENDAR_READ_SCOPE in granted:
        result.update({"calendar.events.read", "calendar.events.changed"})
    if GOOGLE_GMAIL_SEND_SCOPE in granted:
        result.add("communication.messages.send")
    return frozenset(result)


def google_health_state(connection: Connection) -> str:
    status = connection.status.casefold()
    if status == "active":
        return "CONNECTED"
    if status in {"needs_reauthorization", "auth_expired"}:
        return "AUTH_EXPIRED"
    if status == "paused":
        return "PAUSED"
    if status in {"disconnected", "revoked"}:
        return "DISCONNECTED"
    return "DEGRADED"


async def ensure_google_connector_definition(database: AsyncSession) -> ConnectorDefinition:
    definition = await database.scalar(
        select(ConnectorDefinition).where(
            ConnectorDefinition.connector_key == GOOGLE_CONNECTOR_KEY,
            ConnectorDefinition.version == GOOGLE_CONNECTOR_VERSION,
        )
    )
    if definition is not None:
        return definition
    definition = ConnectorDefinition(
        connector_key=GOOGLE_CONNECTOR_KEY,
        version=GOOGLE_CONNECTOR_VERSION,
        display_name=GOOGLE_MANIFEST.display_name,
        connector_class=GOOGLE_MANIFEST.connector_class,
        trust_level="NAVOX_FIRST_PARTY",
        manifest=GOOGLE_MANIFEST.model_dump(mode="json", by_alias=True),
        active=True,
    )
    database.add(definition)
    await database.flush()
    return definition


async def ensure_google_connector_connection(
    database: AsyncSession,
    legacy: Connection,
) -> ConnectorConnection:
    definition = await ensure_google_connector_definition(database)
    mirror = await database.scalar(
        select(ConnectorConnection).where(
            ConnectorConnection.legacy_connection_id == legacy.id,
        )
    )
    capabilities = sorted(google_capabilities(legacy.granted_scopes))
    health = google_health_state(legacy)
    if mirror is None:
        mirror = ConnectorConnection(
            connector_definition_id=definition.id,
            legacy_connection_id=legacy.id,
            user_id=legacy.user_id,
            workspace_id=legacy.workspace_id,
            provider="google",
            external_account_id=legacy.external_account_id,
            display_name=legacy.external_email,
            status=health,
            health_state=health,
            authorized_capabilities=capabilities,
            provider_capabilities=capabilities,
            config={"legacy_bridge": True},
            credential_reference=legacy.credential_reference,
            last_healthy_at=datetime.now(UTC) if health == "CONNECTED" else None,
            last_error_code=_legacy_error_code(legacy),
        )
        database.add(mirror)
        await database.flush()
        return mirror

    if mirror.workspace_id != legacy.workspace_id or mirror.user_id != legacy.user_id:
        raise ValueError("Legacy Google connection mirror changed workspace ownership")
    mirror.connector_definition_id = definition.id
    mirror.external_account_id = legacy.external_account_id
    mirror.display_name = legacy.external_email
    mirror.status = health
    mirror.health_state = health
    mirror.authorized_capabilities = capabilities
    mirror.provider_capabilities = capabilities
    mirror.credential_reference = legacy.credential_reference
    mirror.last_error_code = _legacy_error_code(legacy)
    if health == "CONNECTED":
        mirror.last_healthy_at = datetime.now(UTC)
    await database.flush()
    return mirror


def google_canonical_resource(
    document: SourceDocument,
    *,
    connector_connection_id: UUID,
) -> CanonicalResource:
    if document.provider != "google":
        raise ValueError("Only Google SourceDocuments can use the Google connector bridge")
    if document.source_type == "gmail_message":
        resource_type = "communication.message"
        source_url = f"https://mail.google.com/mail/u/0/#all/{document.external_id}"
    elif document.source_type == "calendar_event":
        resource_type = "calendar.event"
        source_url = None
    else:
        raise ValueError("Unsupported Google source type")

    status = document.metadata.get("status")
    canonical = {
        "source_type": document.source_type,
        "occurred_at": document.occurred_at.isoformat(),
        "status": status if isinstance(status, str) else "active",
        "has_subject": bool(document.subject),
        "has_content": bool(document.content),
    }
    provider_metadata = {
        "source_hash": source_document_hash(document),
        "content_persisted": False,
    }
    return CanonicalResource(
        resource_id=stable_resource_id(
            connector_connection_id,
            resource_type,
            document.external_id,
        ),
        workspace_id=document.workspace_id,
        connector_connection_id=connector_connection_id,
        provider="google",
        resource_type=resource_type,
        external_id=document.external_id,
        external_parent_id=document.external_parent_id,
        canonical=canonical,
        provider_metadata=provider_metadata,
        source_url=source_url,
        updated_at=document.occurred_at,
        retrieved_at=document.retrieved_at,
    )


async def mirror_google_document(
    database: AsyncSession,
    *,
    legacy_connection: Connection,
    document: SourceDocument,
) -> ConnectorResource:
    mirror = await ensure_google_connector_connection(database, legacy_connection)
    resource = google_canonical_resource(
        document,
        connector_connection_id=mirror.id,
    )
    source_hash = str(resource.provider_metadata["source_hash"])
    existing = await database.scalar(
        select(ConnectorResource).where(
            ConnectorResource.connector_connection_id == mirror.id,
            ConnectorResource.resource_type == resource.resource_type,
            ConnectorResource.external_id == resource.external_id,
        )
    )
    deleted = resource.canonical.get("status") in {"deleted", "cancelled"}
    if existing is None:
        existing = ConnectorResource(
            id=resource.resource_id,
            workspace_id=resource.workspace_id,
            connector_connection_id=resource.connector_connection_id,
            provider=resource.provider,
            resource_type=resource.resource_type,
            external_id=resource.external_id,
            external_parent_id=resource.external_parent_id,
            version=resource.version,
            canonical=resource.canonical,
            provider_metadata=resource.provider_metadata,
            source_url=resource.source_url,
            source_created_at=resource.created_at,
            source_updated_at=resource.updated_at,
            retrieved_at=resource.retrieved_at,
            content_hash=source_hash,
            deleted=deleted,
        )
        database.add(existing)
    else:
        if existing.workspace_id != legacy_connection.workspace_id:
            raise ValueError("Google resource mirror crossed a workspace boundary")
        existing.external_parent_id = resource.external_parent_id
        existing.canonical = resource.canonical
        existing.provider_metadata = resource.provider_metadata
        existing.source_url = resource.source_url
        existing.source_updated_at = resource.updated_at
        existing.retrieved_at = resource.retrieved_at
        existing.content_hash = source_hash
        existing.deleted = deleted
    await database.flush()
    return existing


def _legacy_error_code(connection: Connection) -> str | None:
    if connection.status in {"needs_reauthorization", "auth_expired"}:
        return "AUTH_EXPIRED"
    if connection.status in {"disconnected", "revoked"}:
        return "AUTH_REVOKED"
    if connection.last_error:
        return "TEMPORARY_FAILURE"
    return None



class GoogleCompatibilityConnector:
    """Catalog/runtime facade over the hardened existing Google source workflows."""

    def __init__(self, config: Mapping[str, JsonValue]) -> None:
        self.config = dict(config)

    def get_manifest(self) -> ConnectorManifest:
        return GOOGLE_MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        return AuthorizationResult(authorized=False)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        state = connection.status
        return ConnectorHealth(state=state, checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY",
            "Google sync is delegated to the hardened source-specific workflow",
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY",
            "Google resources are fetched by the hardened source-specific workflow",
        )

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY",
            "Google writes remain behind SPEC-001 approval and the Tool Gateway",
        )
