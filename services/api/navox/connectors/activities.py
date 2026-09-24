from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from navox.ai.factory import AIProviderNotConfigured, build_ai_gateway
from navox.connectors.catalog import build_connector_registry
from navox.connectors.contracts import ConnectorConnectionContext, ConnectorRuntimeError
from navox.connectors.intelligence import ingest_connector_resource
from navox.connectors.jobs import (
    ConnectorDisconnectWork,
    ConnectorHealthWork,
    ConnectorSyncWork,
)
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.runtime import ConnectorRuntime
from navox.core.settings import get_settings
from navox.db.models import (
    ConnectorConnection,
    ConnectorResource,
    ConnectorSubscription,
    ConnectorSyncRun,
    User,
    WorkspaceMembership,
)
from navox.db.session import get_session_factory
from navox.intelligence.extraction import OperationalExtractor


def _failure(code: str, *, retry_after_seconds: int | None = None) -> ApplicationError:
    detail: dict[str, str | int] = {"code": code}
    if retry_after_seconds is not None:
        detail["retry_after_seconds"] = retry_after_seconds
    retryable = code in {
        "RATE_LIMITED",
        "PROVIDER_UNAVAILABLE",
        "TEMPORARY_FAILURE",
        "SYNC_FAILED",
    }
    delay = (
        timedelta(seconds=retry_after_seconds)
        if retryable and retry_after_seconds is not None
        else None
    )
    return ApplicationError(
        "Connector operation failed",
        detail,
        type="ConnectorRuntimeFailure",
        non_retryable=not retryable,
        next_retry_delay=delay,
    )


async def _authorized_connection(
    connection_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
) -> ConnectorConnection | None:
    async with get_session_factory()() as database:
        connection = await database.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
        )
        if connection is None:
            return None
        membership = await database.get(WorkspaceMembership, (workspace_id, user_id))
        user = await database.get(User, user_id)
        if membership is None or user is None or user.agent_paused:
            return None
        return connection


@activity.defn
async def connector_sync_activity(payload: ConnectorSyncWork) -> int:
    settings = get_settings()
    try:
        gateway = build_ai_gateway(settings)
    except AIProviderNotConfigured:
        raise _failure("TEMPORARY_FAILURE") from None

    connection_id = UUID(payload.connection_id)
    user_id = UUID(payload.user_id)
    workspace_id = UUID(payload.workspace_id)
    authorized = await _authorized_connection(connection_id, user_id, workspace_id)
    if authorized is None:
        return 0

    async with get_session_factory()() as database:
        connection = await database.scalar(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        user = await database.get(User, user_id)
        if connection is None or user is None:
            return 0
        if connection.status in {"PAUSED", "DISCONNECTED"}:
            return 0

        registry = build_connector_registry(settings)
        runtime = ConnectorRuntime(registry)
        extractor = OperationalExtractor(gateway)
        affected: set[UUID] = set()

        async def consume(resource) -> None:
            identifiers = await ingest_connector_resource(
                database,
                connector_connection=connection,
                resource=resource,
                extractor=extractor,
                timezone_name=user.timezone,
            )
            affected.update(identifiers)

        try:
            await runtime.sync(
                database,
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                request_id=UUID(payload.request_id),
                policy_allowed=set(connection.authorized_capabilities),
                consume=consume,
                trigger=payload.trigger,
            )
            if affected:
                from navox.intelligence.attention import evaluate_workspace_attention

                await evaluate_workspace_attention(
                    database,
                    user_id=user.id,
                    workspace_id=workspace_id,
                )
                await database.commit()
            return len(affected)
        except ConnectorRuntimeError as error:
            raise _failure(error.code) from None
        except ApplicationError:
            raise
        except Exception:
            raise _failure("TEMPORARY_FAILURE") from None


@activity.defn
async def connector_health_activity(payload: ConnectorHealthWork) -> str:
    settings = get_settings()
    connection_id = UUID(payload.connection_id)
    user_id = UUID(payload.user_id)
    workspace_id = UUID(payload.workspace_id)
    async with get_session_factory()() as database:
        connection = await database.scalar(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if connection is None:
            return "DISCONNECTED"
        if connection.status == "DISCONNECTED":
            return "DISCONNECTED"
        if connection.status == "PAUSED":
            connection.health_state = "PAUSED"
            await ensure_provenance_connection(database, connection)
            await database.commit()
            return "PAUSED"

        try:
            registry = build_connector_registry(settings)
            definition = await database.get(
                __import__("navox.db.models", fromlist=["ConnectorDefinition"]).ConnectorDefinition,
                connection.connector_definition_id,
            )
            if definition is None:
                raise ConnectorRuntimeError("PERMANENT_FAILURE")
            connector = registry.build(definition.connector_key, connection.config)
            result = await connector.health(
                ConnectorConnectionContext(
                    id=connection.id,
                    workspace_id=connection.workspace_id,
                    user_id=connection.user_id,
                    connector_id=definition.connector_key,
                    provider=connection.provider,
                    external_account_id=connection.external_account_id,
                    status=connection.health_state,
                    authorized_capabilities=frozenset(connection.authorized_capabilities),
                    config=connection.config,
                )
            )
            connection.health_state = result.state
            connection.last_error_code = result.reason_code
            if result.state == "CONNECTED":
                connection.last_healthy_at = result.checked_at
            await ensure_provenance_connection(database, connection)
            await database.commit()
            return result.state
        except ConnectorRuntimeError as error:
            connection.health_state = "SYNC_FAILED"
            connection.last_error_code = error.code
            await database.commit()
            raise _failure(error.code) from None
        except Exception:
            connection.health_state = "DEGRADED"
            connection.last_error_code = "TEMPORARY_FAILURE"
            await database.commit()
            raise _failure("TEMPORARY_FAILURE") from None


@activity.defn
async def connector_reconciliation_activity() -> list[ConnectorSyncWork]:
    now = datetime.now(UTC)
    async with get_session_factory()() as database:
        rows = list(
            await database.scalars(
                select(ConnectorConnection)
                .join(User, User.id == ConnectorConnection.user_id)
                .where(
                    ConnectorConnection.status == "CONNECTED",
                    User.agent_paused.is_(False),
                )
                .order_by(ConnectorConnection.last_synced_at.asc().nullsfirst())
                .limit(100)
            )
        )
        result: list[ConnectorSyncWork] = []
        for connection in rows:
            last = connection.last_synced_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if last is not None and last > now - timedelta(minutes=30):
                continue
            result.append(
                ConnectorSyncWork(
                    connection_id=str(connection.id),
                    user_id=str(connection.user_id),
                    workspace_id=str(connection.workspace_id),
                    request_id=str(
                        UUID(
                            bytes=__import__("hashlib").sha256(
                                f"{connection.id}:{now:%Y%m%d%H%M}".encode()
                            ).digest()[:16]
                        )
                    ),
                    trigger="reconciliation",
                )
            )
        return result


@activity.defn
async def connector_disconnect_activity(payload: ConnectorDisconnectWork) -> str:
    connection_id = UUID(payload.connection_id)
    user_id = UUID(payload.user_id)
    workspace_id = UUID(payload.workspace_id)
    async with get_session_factory()() as database:
        connection = await database.scalar(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
            .with_for_update()
        )
        if connection is None:
            return "DISCONNECTED"
        connection.status = "DISCONNECTED"
        connection.health_state = "DISCONNECTED"
        connection.paused_at = None
        await ensure_provenance_connection(database, connection)
        await database.execute(
            delete(ConnectorSubscription).where(
                ConnectorSubscription.connector_connection_id == connection.id
            )
        )
        if payload.delete_data:
            await database.execute(
                delete(ConnectorResource).where(
                    ConnectorResource.connector_connection_id == connection.id
                )
            )
            await database.execute(
                delete(ConnectorSyncRun).where(
                    ConnectorSyncRun.connector_connection_id == connection.id
                )
            )
        await database.commit()
        return "DISCONNECTED"
