from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorConnectionContext,
    ConnectorRuntimeError,
    SyncRequest,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.secrets import SecretBroker
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncRun,
)

ResourceConsumer = Callable[[CanonicalResource], Awaitable[None]]


class ConnectorRuntime:
    """Privileged deterministic runtime; connector code never receives direct DB access."""

    def __init__(
        self,
        registry: ConnectorRegistry,
        capability_gateway: CapabilityGateway | None = None,
        secret_broker: SecretBroker | None = None,
    ) -> None:
        self.registry = registry
        self.capability_gateway = capability_gateway or CapabilityGateway()
        self.secret_broker = secret_broker

    async def sync(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        request_id: UUID,
        policy_allowed: set[str] | frozenset[str],
        consume: ResourceConsumer,
        trigger: str = "manual",
    ) -> ConnectorSyncRun:
        connection = await database.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
        )
        if connection is None:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector connection not found")
        existing_run = await database.scalar(
            select(ConnectorSyncRun).where(
                ConnectorSyncRun.connector_connection_id == connection.id,
                ConnectorSyncRun.request_id == request_id,
            )
        )
        if existing_run is not None:
            return existing_run
        if connection.status in {"PAUSED", "DISCONNECTED"}:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector is not active")

        definition = await database.get(ConnectorDefinition, connection.connector_definition_id)
        if definition is None or not definition.active:
            raise ConnectorRuntimeError("PERMANENT_FAILURE", "Connector definition unavailable")

        context = ConnectorConnectionContext.model_validate(
            {
                "id": connection.id,
                "workspace_id": connection.workspace_id,
                "user_id": connection.user_id,
                "connector_id": definition.connector_key,
                "provider": connection.provider,
                "external_account_id": connection.external_account_id,
                "status": connection.health_state,
                "authorized_capabilities": connection.authorized_capabilities,
                "config": connection.config,
            }
        )
        registered = self.registry.get(definition.connector_key)
        preview = registered.factory(context.config, None)
        manifest = preview.get_manifest()
        if manifest.id != registered.manifest.id or manifest.version != registered.manifest.version:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                "Connector config changed its registered identity",
            )
        secret_lease = None
        if manifest.required_secrets:
            if self.secret_broker is None:
                raise ConnectorRuntimeError(
                    "PERMISSION_DENIED",
                    "Connector credentials are unavailable",
                )
            secret_lease = await self.secret_broker.lease(
                database,
                connection_id=connection.id,
                purpose="sync.read",
                names=frozenset(manifest.required_secrets),
            )
        connector = self.registry.build(
            definition.connector_key,
            context.config,
            secret_lease,
        )
        health = await connector.health(context)
        connection.health_state = health.state
        connection.last_error_code = health.reason_code
        if health.state == "CONNECTED":
            connection.last_healthy_at = health.checked_at

        effective = self.capability_gateway.evaluate(
            manifest=manifest,
            provider_capabilities=frozenset(connection.provider_capabilities),
            user_authorized=frozenset(connection.authorized_capabilities),
            policy_allowed=policy_allowed,
            health_state=health.state,
        )
        if not effective.read:
            raise ConnectorRuntimeError(
                "UNSUPPORTED_CAPABILITY",
                "No effective read capability is available",
            )

        run = ConnectorSyncRun(
            connector_connection_id=connection.id,
            workspace_id=connection.workspace_id,
            request_id=request_id,
            trigger=trigger,
            status="running",
            cursor_before=connection.sync_cursor,
        )
        database.add(run)
        await database.commit()

        # Keep scalar identifiers across rollback, which expires ORM instances.
        run_id = run.id
        cursor = connection.sync_cursor
        resource_count = processed_count = duplicate_count = 0
        try:
            for _page_number in range(50):
                page = await connector.sync(
                    SyncRequest(
                        connection_id=connection.id,
                        workspace_id=connection.workspace_id,
                        cursor=cursor,
                        capabilities=effective.read,
                    )
                )
                for resource in page.resources:
                    self._validate_resource(connection, manifest.id, resource)
                    duplicate = await self._persist_resource(database, resource)
                    await consume(resource)
                    resource_count += 1
                    duplicate_count += int(duplicate)
                    processed_count += int(not duplicate)
                    await database.commit()
                cursor = page.next_cursor
                if not page.has_more:
                    break
            else:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE",
                    "Connector exceeded the bounded page limit",
                )

            connection = await database.get(ConnectorConnection, connection_id)
            completed_run = await database.get(ConnectorSyncRun, run_id)
            if connection is None or completed_run is None:
                raise ConnectorRuntimeError("PERMANENT_FAILURE", "Connector state disappeared")
            run = completed_run
            connection.sync_cursor = cursor
            connection.last_synced_at = datetime.now(UTC)
            connection.health_state = "CONNECTED"
            run.status = "completed"
            run.cursor_after = cursor
            run.resource_count = resource_count
            run.processed_count = processed_count
            run.duplicate_count = duplicate_count
            run.completed_at = datetime.now(UTC)
            await database.commit()
            return run
        except Exception as error:
            await database.rollback()
            failed_run = await database.get(ConnectorSyncRun, run_id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.error_code = (
                    error.code if isinstance(error, ConnectorRuntimeError) else "TEMPORARY_FAILURE"
                )
                failed_run.completed_at = datetime.now(UTC)
                await database.commit()
            raise

    @staticmethod
    def _validate_resource(
        connection: ConnectorConnection,
        connector_id: str,
        resource: CanonicalResource,
    ) -> None:
        if resource.workspace_id != connection.workspace_id:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Cross-workspace resource rejected")
        if resource.connector_connection_id != connection.id:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Cross-connection resource rejected")
        if not resource.provider or resource.provider == "navox":
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid provider identity")
        if connector_id == "browser-assisted" and not bool(
            connection.config.get("explicit_capture_authorized", False)
        ):
            raise ConnectorRuntimeError(
                "PERMISSION_DENIED", "Browser capture requires authorization"
            )

    @staticmethod
    async def _persist_resource(
        database: AsyncSession,
        resource: CanonicalResource,
    ) -> bool:
        content_hash = hashlib.sha256(
            json.dumps(
                resource.model_dump(mode="json", exclude={"retrieved_at"}),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = await database.scalar(
            select(ConnectorResource).where(
                ConnectorResource.connector_connection_id == resource.connector_connection_id,
                ConnectorResource.resource_type == resource.resource_type,
                ConnectorResource.external_id == resource.external_id,
            )
        )
        if existing is None:
            database.add(
                ConnectorResource(
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
                    content_hash=content_hash,
                )
            )
            await database.flush()
            return False
        duplicate = existing.content_hash == content_hash
        existing.version = resource.version
        existing.canonical = dict(resource.canonical)
        existing.provider_metadata = dict(resource.provider_metadata)
        existing.source_url = resource.source_url
        existing.source_created_at = resource.created_at
        existing.source_updated_at = resource.updated_at
        existing.retrieved_at = resource.retrieved_at
        existing.content_hash = content_hash
        existing.deleted = False
        await database.flush()
        return duplicate
