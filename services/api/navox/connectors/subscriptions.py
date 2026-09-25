"""Fenced lifecycle for explicitly supported provider event subscriptions.

Provider calls are made after committing a durable lease. The adapter must
honor the stable idempotency key across retries; database transactions cannot
make a remote subscription call atomic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import (
    ConnectorConnectionContext,
    ConnectorManifest,
    ConnectorRuntimeError,
    EventSubscriptionConnector,
    EventSubscriptionRequest,
    EventSubscriptionResult,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.secrets import ScopedSecretLease, SecretBrokerError
from navox.connectors.sync_state import authorization_hash, database_now, utc
from navox.db.models import ConnectorConnection, ConnectorDefinition, ConnectorSubscription

LEASE_SECONDS = 120
PROVIDER_SECONDS = 60
RENEW_BEFORE = timedelta(days=1)


@dataclass(frozen=True)
class SubscriptionTicket:
    id: UUID
    token: UUID
    generation: int
    fingerprint: str
    request: EventSubscriptionRequest


class EventSecretBroker(Protocol):
    async def lease(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        purpose: str,
        names: set[str] | frozenset[str],
    ) -> ScopedSecretLease: ...


async def _authority(
    database: AsyncSession,
    registry: ConnectorRegistry,
    *,
    connection_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    event: str,
    policy_allowed: frozenset[str],
    active: bool,
) -> tuple[ConnectorConnection, ConnectorDefinition, ConnectorManifest, ConnectorConnectionContext]:
    try:
        connection = await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=active,
            lock_authority=True,
        )
    except ConnectorAccessDenied:
        raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector access denied") from None
    definition = await database.get(
        ConnectorDefinition, connection.connector_definition_id, populate_existing=True
    )
    if definition is None or (active and not definition.active):
        raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector definition unavailable")
    try:
        registered = registry.get(definition.connector_key)
        manifest = registered.manifest
        if definition.version != manifest.version or definition.manifest != manifest.model_dump(
            mode="json", by_alias=True
        ):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector definition changed")
    except KeyError:
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Event adapter unavailable") from None
    context = ConnectorConnectionContext.model_validate(
        {
            "id": connection.id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "connector_id": definition.connector_key,
            "provider": connection.provider,
            "external_account_id": connection.external_account_id,
            "status": connection.health_state,
            "authorized_capabilities": connection.authorized_capabilities,
            "config": connection.config,
        }
    )
    if active:
        effective = CapabilityGateway().evaluate(
            manifest=manifest,
            provider_capabilities=set(connection.provider_capabilities),
            user_authorized=set(connection.authorized_capabilities),
            policy_allowed=policy_allowed,
            health_state=context.status,
        )
        if event not in effective.events:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Event capability not authorized")
    elif event not in manifest.capabilities.events:
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Event is not declared")
    # The protocol is optional; a manifest declaration alone never enables I/O.
    preview = registry.build(definition.connector_key, context.config)
    if not isinstance(preview, EventSubscriptionConnector):
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Event adapter unavailable")
    return connection, definition, manifest, context


async def _adapter(
    database: AsyncSession,
    registry: ConnectorRegistry,
    connection: ConnectorConnection,
    definition: ConnectorDefinition,
    manifest: ConnectorManifest,
    context: ConnectorConnectionContext,
    broker: EventSecretBroker | None,
    *,
    purpose: str,
) -> tuple[EventSubscriptionConnector, ScopedSecretLease | None]:
    lease: ScopedSecretLease | None = None
    if manifest.required_secrets:
        if broker is None:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Connector credential unavailable")
        try:
            lease = await broker.lease(
                database,
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
                purpose=purpose,
                names=frozenset(manifest.required_secrets),
            )
        except SecretBrokerError:
            raise ConnectorRuntimeError(
                "AUTH_EXPIRED", "Connector credential unavailable"
            ) from None
    try:
        adapter = registry.build(definition.connector_key, context.config, lease)
        if not isinstance(adapter, EventSubscriptionConnector):
            raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Event adapter unavailable")
        return adapter, lease
    except BaseException:
        if lease is not None:
            lease.close()
        raise


async def _finish(
    database: AsyncSession,
    ticket: SubscriptionTicket,
    *,
    status: str,
    result: EventSubscriptionResult | None = None,
) -> ConnectorSubscription:
    now = await database_now(database)
    updated = await database.scalar(
        update(ConnectorSubscription)
        .where(
            ConnectorSubscription.id == ticket.id,
            ConnectorSubscription.lease_token == ticket.token,
            ConnectorSubscription.generation == ticket.generation,
            ConnectorSubscription.lease_expires_at > now,
            ConnectorSubscription.status == ("pending" if status == "active" else "cancel_pending"),
        )
        .values(
            status=status,
            external_id=(
                result.external_id
                if result is not None
                else None
                if status == "cancelled"
                else ticket.request.external_id
            ),
            expires_at=result.expires_at if result is not None else None,
            lease_token=None,
            lease_expires_at=None,
        )
        .returning(ConnectorSubscription.id)
        .execution_options(synchronize_session=False)
    )
    if updated is None:
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Subscription lease lost")
    await database.commit()
    row = await database.get(ConnectorSubscription, ticket.id, populate_existing=True)
    assert row is not None
    return row


async def _failed(database: AsyncSession, ticket: SubscriptionTicket) -> None:
    await database.rollback()
    await database.execute(
        update(ConnectorSubscription)
        .where(
            ConnectorSubscription.id == ticket.id,
            ConnectorSubscription.lease_token == ticket.token,
            ConnectorSubscription.generation == ticket.generation,
            ConnectorSubscription.status == "pending",
        )
        .values(status="failed", lease_token=None, lease_expires_at=None)
    )
    await database.execute(
        update(ConnectorSubscription)
        .where(
            ConnectorSubscription.id == ticket.id,
            ConnectorSubscription.lease_token == ticket.token,
            ConnectorSubscription.generation == ticket.generation,
            ConnectorSubscription.status == "cancel_pending",
        )
        .values(lease_token=None, lease_expires_at=None)
    )
    await database.commit()


async def _orphaned(
    database: AsyncSession,
    ticket: SubscriptionTicket,
    result: EventSubscriptionResult,
) -> None:
    """Keep a remote channel discoverable after permission changes during I/O."""
    await database.rollback()
    updated = await database.scalar(
        update(ConnectorSubscription)
        .where(
            ConnectorSubscription.id == ticket.id,
            ConnectorSubscription.lease_token == ticket.token,
            ConnectorSubscription.generation == ticket.generation,
            ConnectorSubscription.status.in_(["pending", "cancel_pending"]),
        )
        .values(
            status="cancel_pending",
            external_id=result.external_id,
            expires_at=result.expires_at,
            lease_token=None,
            lease_expires_at=None,
        )
        .returning(ConnectorSubscription.id)
        .execution_options(synchronize_session=False)
    )
    await database.commit()
    if updated is None:
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Subscription lease lost")


async def register_or_renew(
    database: AsyncSession,
    registry: ConnectorRegistry,
    *,
    connection_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    event: str,
    policy_allowed: set[str] | frozenset[str],
    secret_broker: EventSecretBroker | None = None,
) -> ConnectorSubscription:
    """Create or renew only an explicitly authorized, implemented event channel."""
    policy = frozenset(policy_allowed)
    connection, definition, manifest, context = await _authority(
        database,
        registry,
        connection_id=connection_id,
        workspace_id=workspace_id,
        user_id=user_id,
        event=event,
        policy_allowed=policy,
        active=True,
    )
    now = await database_now(database)
    row = await database.scalar(
        select(ConnectorSubscription)
        .where(
            ConnectorSubscription.connector_connection_id == connection.id,
            ConnectorSubscription.subscription_key == event,
        )
        .with_for_update()
    )
    if row is not None and row.status in {"cancel_pending", "cancelled"}:
        raise ConnectorRuntimeError("PERMISSION_DENIED", "Subscription is being cancelled")
    if (
        row is not None
        and row.status == "active"
        and (row.expires_at is None or utc(row.expires_at) > now + RENEW_BEFORE)
    ):
        return row
    if (
        row is not None
        and row.lease_token is not None
        and (row.lease_expires_at is None or utc(row.lease_expires_at) > now)
    ):
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Subscription already in progress")
    fingerprint = authorization_hash(connection, definition, policy)
    if row is None:
        row = ConnectorSubscription(
            connector_connection_id=connection.id,
            subscription_key=event,
            status="pending",
            generation=0,
        )
        database.add(row)
        await database.flush()
    prior_external_id = row.external_id
    token = uuid4()
    generation = row.generation + 1
    claim = await database.scalar(
        update(ConnectorSubscription)
        .where(
            ConnectorSubscription.id == row.id,
            ConnectorSubscription.generation == row.generation,
        )
        .values(
            generation=generation,
            status="pending",
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            authorization_hash=fingerprint,
        )
        .returning(ConnectorSubscription.id)
        .execution_options(synchronize_session=False)
    )
    if claim is None:
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Subscription already in progress")
    ticket = SubscriptionTicket(
        id=row.id,
        token=token,
        generation=generation,
        fingerprint=fingerprint,
        request=EventSubscriptionRequest(
            connection_id=connection_id,
            workspace_id=workspace_id,
            event=event,
            idempotency_key=row.id,
            external_id=prior_external_id,
        ),
    )
    await database.commit()
    lease: ScopedSecretLease | None = None
    result: EventSubscriptionResult | None = None
    try:
        adapter, lease = await _adapter(
            database,
            registry,
            connection,
            definition,
            manifest,
            context,
            secret_broker,
            purpose="events.manage",
        )
        await database.commit()  # Release the credential/ownership lock before provider I/O.
        async with asyncio.timeout(PROVIDER_SECONDS):
            provider_result = await adapter.subscribe(ticket.request)
        if not isinstance(provider_result, EventSubscriptionResult):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid event subscription")
        result = provider_result
        if result.expires_at is not None and result.expires_at <= await database_now(database):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid event subscription")
        current, current_definition, _, _ = await _authority(
            database,
            registry,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            event=event,
            policy_allowed=policy,
            active=True,
        )
        if authorization_hash(current, current_definition, policy) != ticket.fingerprint:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Subscription authorization changed")
        return await _finish(database, ticket, status="active", result=result)
    except BaseException:
        if result is not None:
            await _orphaned(database, ticket, result)
        else:
            await _failed(database, ticket)
        raise
    finally:
        if lease is not None:
            lease.close()


async def cancel_subscription(
    database: AsyncSession,
    registry: ConnectorRegistry,
    *,
    connection_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    event: str,
    secret_broker: EventSecretBroker | None = None,
) -> ConnectorSubscription | None:
    """Retain remote identity on failure so cleanup can be retried."""
    connection, definition, manifest, context = await _authority(
        database,
        registry,
        connection_id=connection_id,
        workspace_id=workspace_id,
        user_id=user_id,
        event=event,
        policy_allowed=frozenset(),
        active=False,
    )
    row = await database.scalar(
        select(ConnectorSubscription)
        .where(
            ConnectorSubscription.connector_connection_id == connection_id,
            ConnectorSubscription.subscription_key == event,
        )
        .with_for_update()
    )
    if row is None or row.status == "cancelled":
        return row
    now = await database_now(database)
    if row.lease_token is not None and (
        row.lease_expires_at is None or utc(row.lease_expires_at) > now
    ):
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Subscription already in progress")
    token, generation = uuid4(), row.generation + 1
    fingerprint = authorization_hash(connection, definition, frozenset())
    claimed = await database.scalar(
        update(ConnectorSubscription)
        .where(
            ConnectorSubscription.id == row.id,
            ConnectorSubscription.generation == row.generation,
            ConnectorSubscription.status != "cancelled",
        )
        .values(
            status="cancel_pending",
            generation=generation,
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            authorization_hash=fingerprint,
        )
        .returning(ConnectorSubscription.id)
        .execution_options(synchronize_session=False)
    )
    if claimed is None:
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Subscription already in progress")
    ticket = SubscriptionTicket(
        id=row.id,
        token=token,
        generation=generation,
        fingerprint=fingerprint,
        request=EventSubscriptionRequest(
            connection_id=connection_id,
            workspace_id=workspace_id,
            event=event,
            idempotency_key=row.id,
            external_id=row.external_id,
        ),
    )
    await database.commit()
    lease: ScopedSecretLease | None = None
    try:
        if row.external_id is not None:
            adapter, lease = await _adapter(
                database,
                registry,
                connection,
                definition,
                manifest,
                context,
                secret_broker,
                purpose="events.cleanup",
            )
            await database.commit()
            async with asyncio.timeout(PROVIDER_SECONDS):
                await adapter.unsubscribe(ticket.request)
        current, current_definition, _, _ = await _authority(
            database,
            registry,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            event=event,
            policy_allowed=frozenset(),
            active=False,
        )
        if authorization_hash(current, current_definition, frozenset()) != ticket.fingerprint:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Subscription owner changed")
        return await _finish(database, ticket, status="cancelled")
    except BaseException:
        await database.rollback()
        await database.execute(
            update(ConnectorSubscription)
            .where(
                ConnectorSubscription.id == ticket.id,
                ConnectorSubscription.lease_token == ticket.token,
                ConnectorSubscription.generation == ticket.generation,
            )
            .values(status="cancel_pending", lease_token=None, lease_expires_at=None)
        )
        await database.commit()
        raise
    finally:
        if lease is not None:
            lease.close()
