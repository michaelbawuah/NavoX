"""File snapshots exposed through the same read-only connector runtime."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from navox.connectors.builtin.imports import IMPORT_MANIFEST
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
    SecretAccessor,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)
from navox.connectors.import_parser import IMPORT_CAPABILITIES, ImportFormat
from navox.connectors.import_storage import SnapshotEnvelope


class SnapshotLoader(Protocol):
    async def load(
        self, connection_id: UUID, workspace_id: UUID, user_id: UUID, digest: str
    ) -> SnapshotEnvelope: ...


class StoredImportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: ImportFormat
    snapshot_digest: str
    owner_id: UUID


class StoredImportConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        reader: SnapshotLoader,
    ) -> None:
        del secrets
        self.config = StoredImportConfig.model_validate(config)
        self.reader = reader

    def get_manifest(self) -> ConnectorManifest:
        return IMPORT_MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        return AuthorizationResult(authorized=False)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        if connection.user_id != self.config.owner_id:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Import access denied")
        await self.reader.load(
            connection.id, connection.workspace_id, connection.user_id, self.config.snapshot_digest
        )
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        if IMPORT_CAPABILITIES[self.config.format] not in request.capabilities:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Import read permission is required")
        snapshot = await self.reader.load(
            request.connection_id,
            request.workspace_id,
            self.config.owner_id,
            self.config.snapshot_digest,
        )
        try:
            offset = int(request.cursor or "0")
        except ValueError:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Invalid import cursor"
            ) from None
        if not 0 <= offset <= len(snapshot.records):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid import cursor")
        selected = snapshot.records[offset : offset + request.limit]
        next_offset = offset + len(selected)
        resources = [
            CanonicalResource(
                resource_id=stable_resource_id(
                    request.connection_id, record.resource_type, record.external_id
                ),
                workspace_id=request.workspace_id,
                connector_connection_id=request.connection_id,
                provider="import",
                resource_type=record.resource_type,
                external_id=record.external_id,
                version=snapshot.digest,
                canonical={
                    "source_type": record.source_type,
                    "subject": record.title,
                    "content": record.content,
                    "occurred_at": snapshot.imported_at.isoformat(),
                    "status": record.status,
                },
                source_url=record.source_url,
                updated_at=snapshot.imported_at,
                retrieved_at=datetime.now(UTC),
            )
            for record in selected
        ]
        return SyncPage(
            resources=resources,
            next_cursor=str(next_offset) if next_offset < len(snapshot.records) else None,
            has_more=next_offset < len(snapshot.records),
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        page = await self.sync(
            SyncRequest(
                connection_id=request.connection_id,
                workspace_id=request.workspace_id,
                limit=1_000,
                capabilities=frozenset({IMPORT_CAPABILITIES[self.config.format]}),
            )
        )
        for resource in page.resources:
            if (
                resource.resource_type == request.resource_type
                and resource.external_id == request.external_id
            ):
                return resource
        raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "The imported record was not found")

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "File imports cannot execute actions")
