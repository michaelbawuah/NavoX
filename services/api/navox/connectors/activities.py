from __future__ import annotations

from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, or_, select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from navox.ai.factory import AIProviderNotConfigured, build_ai_gateway
from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.catalog import build_connector_registry
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorConnectionContext,
    ConnectorRuntimeError,
)
from navox.connectors.intelligence import ingest_connector_resource
from navox.connectors.jobs import (
    ConnectorDisconnectWork,
    ConnectorHealthWork,
    ConnectorSyncWork,
)
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.runtime import ConnectorRuntime
from navox.connectors.secrets import ScopedSecretLease, SecretBroker, SecretBrokerError
from navox.connectors.sync_state import RETRYABLE_CODES, database_now, utc
from navox.core.settings import get_settings
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSubscription,
    ConnectorSyncRun,
    User,
    WorkspaceMembership,
)
from navox.db.session import get_session_factory
from navox.intelligence.activities import _heartbeat_activity
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
    async with _heartbeat_activity(
        connection_id=payload.connection_id,
        user_id=payload.user_id,
        workspace_id=payload.workspace_id,
    ):
        return await _connector_sync(payload)


async def _connector_sync(payload: ConnectorSyncWork) -> int:
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
        raise _failure("PERMISSION_DENIED")

    async with get_session_factory()() as database:
        connection = await database.scalar(
            select(ConnectorConnection).where(
                ConnectorConnection.id == connection_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
        )
        user = await database.get(User, user_id)
        if connection is None or user is None:
            return 0
        if connection.status in {"PAUSED", "DISCONNECTED"}:
            return 0

        registry = build_connector_registry(settings)
        try:
            secret_broker = SecretBroker(settings)
        except SecretBrokerError:
            secret_broker = None
        runtime = ConnectorRuntime(
            registry,
            secret_broker=secret_broker,
            retain_canonical_content=connection.provider not in {"import", "canvas"},
            page_budget=1000 if connection.provider == "canvas" else 50,
        )
        extractor = OperationalExtractor(gateway)

        # Bind non-optional values before the nested consumer is constructed.
        owned_connection, owner = connection, user

        async def consume(resource: CanonicalResource) -> list[UUID]:
            return await ingest_connector_resource(
                database,
                connector_connection=owned_connection,
                resource=resource,
                extractor=extractor,
                timezone_name=owner.timezone,
            )

        try:
            run = await runtime.sync(
                database,
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=user_id,
                request_id=UUID(payload.request_id),
                policy_allowed=set(connection.authorized_capabilities),
                consume=consume,
                trigger=payload.trigger,
                consumer_version=extractor.extractor_version,
            )
            result_count = len(run.result_ids)
            if result_count:
                try:
                    await owned_connector(
                        database,
                        connection_id=connection_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        require_active=True,
                        lock_authority=True,
                    )
                except ConnectorAccessDenied:
                    await database.rollback()
                    return result_count
                from navox.intelligence.attention import evaluate_workspace_attention

                await evaluate_workspace_attention(
                    database,
                    user_id=user.id,
                    workspace_id=workspace_id,
                )
                await database.commit()
            return result_count
        except ConnectorRuntimeError as error:
            raise _failure(error.code, retry_after_seconds=error.retry_after_seconds) from None
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
        try:
            connection = await owned_connector(
                database,
                connection_id=connection_id,
                user_id=user_id,
                workspace_id=workspace_id,
                require_active=True,
            )
        except ConnectorAccessDenied:
            raise _failure("PERMISSION_DENIED") from None
        if connection is None:
            return "DISCONNECTED"
        if connection.status == "DISCONNECTED":
            return "DISCONNECTED"
        if connection.status == "PAUSED":
            connection.health_state = "PAUSED"
            await ensure_provenance_connection(database, connection)
            await database.commit()
            return "PAUSED"

        secret_lease: ScopedSecretLease | None = None
        try:
            registry = build_connector_registry(settings)
            definition = await database.get(
                ConnectorDefinition,
                connection.connector_definition_id,
            )
            if definition is None:
                raise ConnectorRuntimeError("PERMANENT_FAILURE")
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
            registered = registry.get(definition.connector_key)
            preview = registered.factory(context.config, None)
            manifest = preview.get_manifest()
            allowed = CapabilityGateway().evaluate(
                manifest=manifest,
                provider_capabilities=set(connection.provider_capabilities),
                user_authorized=set(connection.authorized_capabilities),
                policy_allowed=set(connection.authorized_capabilities),
                health_state="CONNECTED",
            )
            if not allowed.read:
                raise ConnectorRuntimeError("PERMISSION_DENIED")
            if manifest.required_secrets:
                try:
                    secret_broker = SecretBroker(settings)
                    secret_lease = await secret_broker.lease(
                        database,
                        connection_id=connection.id,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        purpose="health.read",
                        names=frozenset(manifest.required_secrets),
                    )
                except SecretBrokerError:
                    connection.health_state = "AUTH_EXPIRED"
                    connection.last_error_code = "AUTH_EXPIRED"
                    await database.commit()
                    return "AUTH_EXPIRED"
            connector = registry.build(
                definition.connector_key,
                context.config,
                secret_lease,
            )
            try:
                result = await connector.health(context)
            finally:
                if secret_lease is not None:
                    secret_lease.close()
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
        finally:
            if secret_lease is not None:
                secret_lease.close()


@activity.defn
async def connector_reconciliation_activity() -> list[ConnectorSyncWork]:
    settings = get_settings()
    registered_keys = [manifest.id for manifest in build_connector_registry(settings).manifests()]
    async with get_session_factory()() as database:
        now = await database_now(database)
        rows = list(
            await database.scalars(
                select(ConnectorConnection)
                .join(User, User.id == ConnectorConnection.user_id)
                .join(
                    WorkspaceMembership,
                    (
                        (WorkspaceMembership.user_id == ConnectorConnection.user_id)
                        & (WorkspaceMembership.workspace_id == ConnectorConnection.workspace_id)
                    ),
                )
                .join(
                    ConnectorDefinition,
                    ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
                )
                .where(
                    ConnectorConnection.status.in_(["CONNECTED", "DEGRADED"]),
                    User.agent_paused.is_(False),
                    ConnectorDefinition.active.is_(True),
                    ConnectorDefinition.connector_key.in_(registered_keys),
                    or_(
                        ConnectorConnection.retry_not_before.is_(None),
                        ConnectorConnection.retry_not_before <= now,
                    ),
                    or_(
                        ConnectorConnection.sync_lease_token.is_(None),
                        ConnectorConnection.sync_lease_expires_at <= now,
                    ),
                    or_(
                        ConnectorConnection.last_error_code.is_(None),
                        ConnectorConnection.last_error_code.in_(RETRYABLE_CODES),
                    ),
                )
                .order_by(ConnectorConnection.last_synced_at.asc().nullsfirst())
                .limit(100)
            )
        )
        result: list[ConnectorSyncWork] = []
        for connection in rows:
            # Google is still served by its original hardened workflows. A
            # compatibility mirror is not a functioning universal sync adapter.
            if (
                connection.config.get("legacy_bridge")
                or connection.config.get("managed_by_source_workflow")
                or not connection.authorized_capabilities
            ):
                continue
            run = (
                await database.get(ConnectorSyncRun, connection.sync_run_id)
                if connection.sync_run_id
                else None
            )
            pending = run is not None and run.status in {"running", "failed", "interrupted"}
            # Immutable snapshots have no upstream changes. Reconcile only unfinished work.
            if (
                connection.provider == "import"
                and connection.last_synced_at is not None
                and not pending
            ):
                continue
            if (
                not pending
                and connection.last_synced_at is not None
                and utc(connection.last_synced_at) > now - timedelta(minutes=30)
            ):
                continue
            request_id = (
                run.request_id
                if pending and run is not None
                else uuid5(
                    NAMESPACE_URL, f"navox-connector:{connection.id}:{int(now.timestamp()) // 1800}"
                )
            )
            result.append(
                ConnectorSyncWork(
                    connection_id=str(connection.id),
                    user_id=str(connection.user_id),
                    workspace_id=str(connection.workspace_id),
                    request_id=str(request_id),
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
