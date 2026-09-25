"""Authenticated provider notifications for explicitly verified universal connectors."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from navox.api.auth import DatabaseSession, SettingsDependency
from navox.api.events import MAX_EVENT_BODY_BYTES
from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.catalog import build_connector_registry
from navox.connectors.contracts import (
    ConnectorConnectionContext,
    EventDeliveryRequest,
    EventDeliveryVerifier,
    VerifiedEventDelivery,
)
from navox.connectors.dispatcher import dispatch_connector_sync
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.secrets import ScopedSecretLease, SecretBroker
from navox.connectors.sync_state import authorization_hash, utc
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorEventReceipt,
    ConnectorSubscription,
)

router = APIRouter(prefix="/connectors/events", tags=["connector events"])
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def rejected() -> HTTPException:
    # No credential, channel ID, tenant identity, or provider body in diagnostics.
    return HTTPException(401, "Unverified connector event")


async def bounded_event_stream(request: Request) -> bytes:
    """Reject oversized chunked deliveries before accumulating their bodies."""
    length = request.headers.get("content-length")
    if length is not None:
        try:
            parsed = int(length)
        except ValueError:
            raise HTTPException(400, "Invalid event content length") from None
        if parsed < 0:
            raise HTTPException(400, "Invalid event content length")
        if parsed > MAX_EVENT_BODY_BYTES:
            raise HTTPException(413, "Event payload is too large")
    accumulated = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_EVENT_BODY_BYTES - len(accumulated):
            raise HTTPException(413, "Event payload is too large")
        accumulated.extend(chunk)
    return bytes(accumulated)


async def authority(
    database: DatabaseSession,
    registry: ConnectorRegistry,
    *,
    subscription_id: UUID,
    provider: str,
) -> tuple[ConnectorConnection, ConnectorDefinition, ConnectorSubscription]:
    subscription = await database.get(
        ConnectorSubscription, subscription_id, populate_existing=True
    )
    if subscription is None or subscription.status != "active" or not subscription.external_id:
        raise rejected()
    if subscription.expires_at is not None and utc(subscription.expires_at) <= datetime.now(UTC):
        raise rejected()
    candidate = await database.get(ConnectorConnection, subscription.connector_connection_id)
    if candidate is None or candidate.provider != provider:
        raise rejected()
    try:
        connection = await owned_connector(
            database,
            connection_id=candidate.id,
            workspace_id=candidate.workspace_id,
            user_id=candidate.user_id,
            require_active=True,
            lock_authority=True,
        )
    except ConnectorAccessDenied:
        raise rejected() from None
    subscription = await database.get(
        ConnectorSubscription,
        subscription_id,
        populate_existing=True,
        with_for_update=True,
    )
    if (
        subscription is None
        or subscription.connector_connection_id != connection.id
        or subscription.status != "active"
        or not subscription.external_id
        or (
            subscription.expires_at is not None
            and utc(subscription.expires_at) <= datetime.now(UTC)
        )
    ):
        raise rejected()
    definition = await database.get(
        ConnectorDefinition, connection.connector_definition_id, populate_existing=True
    )
    if definition is None or not definition.active:
        raise rejected()
    try:
        manifest = registry.get(definition.connector_key).manifest
    except KeyError:
        raise rejected() from None
    if definition.version != manifest.version or definition.manifest != manifest.model_dump(
        mode="json", by_alias=True
    ):
        raise rejected()
    allowed = frozenset(connection.authorized_capabilities)
    effective = CapabilityGateway().evaluate(
        manifest=manifest,
        provider_capabilities=set(connection.provider_capabilities),
        user_authorized=set(allowed),
        policy_allowed=allowed,
        health_state=ConnectorConnectionContext.model_validate(
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
        ).status,
    )
    if subscription.subscription_key not in effective.events or (
        subscription.authorization_hash != authorization_hash(connection, definition, allowed)
    ):
        raise rejected()
    return connection, definition, subscription


@router.post("/{provider}", status_code=202)
async def ingest_connector_event(
    provider: str,
    request: Request,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    body = await bounded_event_stream(request)
    if not PROVIDER_RE.fullmatch(provider):
        raise rejected()
    try:
        subscription_id = UUID(request.headers.get("x-navox-subscription-id", ""))
    except ValueError:
        raise rejected() from None
    try:
        registry = build_connector_registry(settings)
        connection, definition, subscription = await authority(
            database, registry, subscription_id=subscription_id, provider=provider
        )
        manifest = registry.get(definition.connector_key).manifest
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
        lease: ScopedSecretLease | None = None
        try:
            if manifest.required_secrets:
                lease = await SecretBroker(settings).lease(
                    database,
                    connection_id=connection.id,
                    workspace_id=connection.workspace_id,
                    user_id=connection.user_id,
                    purpose="events.verify",
                    names=frozenset(manifest.required_secrets),
                )
            adapter = registry.build(definition.connector_key, context.config, lease)
            if not isinstance(adapter, EventDeliveryVerifier):
                raise rejected()
            header_names = adapter.delivery_headers
            if (
                not isinstance(header_names, frozenset)
                or not 0 < len(header_names) <= 16
                or any(
                    not re.fullmatch(r"[a-z0-9-]{1,80}", name)
                    or name in {"authorization", "cookie", "proxy-authorization", "set-cookie"}
                    or name.startswith(("x-forwarded-", "x-navox-"))
                    for name in header_names
                )
            ):
                raise rejected()
            # Release authorization locks during provider verification; recheck below.
            external_id = subscription.external_id
            assert external_id is not None
            generation = subscription.generation
            await database.commit()
            try:
                verified = await asyncio.wait_for(
                    adapter.verify_event(
                        EventDeliveryRequest(
                            subscription_external_id=external_id,
                            headers={
                                key: request.headers[key]
                                for key in header_names
                                if key in request.headers
                            },
                            body=body,
                        )
                    ),
                    timeout=5,
                )
            except Exception:
                # Even a trusted adapter can throw an HTTPException containing
                # untrusted provider text; no adapter exception crosses ingress.
                raise rejected() from None
            if not isinstance(verified, VerifiedEventDelivery) or (
                verified.subscription_external_id != external_id
            ):
                raise rejected()
        finally:
            if lease is not None:
                lease.close()
        connection, _, subscription = await authority(
            database, registry, subscription_id=subscription_id, provider=provider
        )
        if subscription.external_id != external_id or subscription.generation != generation:
            raise rejected()
        payload_hash = sha256(body).hexdigest()
        existing = await database.scalar(
            select(ConnectorEventReceipt).where(
                ConnectorEventReceipt.subscription_id == subscription.id,
                ConnectorEventReceipt.external_event_id == verified.external_event_id,
            )
        )
        duplicate = existing is not None
        if existing is not None and existing.payload_hash != payload_hash:
            raise rejected()
        if existing is None:
            receipt = ConnectorEventReceipt(
                subscription_id=subscription.id,
                connector_connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
                provider=provider,
                event_type=subscription.subscription_key,
                external_event_id=verified.external_event_id,
                external_resource_id=verified.external_resource_id,
                payload_hash=payload_hash,
                status="pending",
            )
            try:
                async with database.begin_nested():
                    database.add(receipt)
                    await database.flush()
            except IntegrityError:
                duplicate = True
                raced = await database.scalar(
                    select(ConnectorEventReceipt).where(
                        ConnectorEventReceipt.subscription_id == subscription.id,
                        ConnectorEventReceipt.external_event_id == verified.external_event_id,
                    )
                )
                if raced is None or raced.payload_hash != payload_hash:
                    raise rejected() from None
                receipt = raced
        else:
            receipt = existing
        await database.commit()
        if receipt.status == "pending":
            try:
                await dispatch_connector_sync(
                    ConnectorSyncWork(
                        connection_id=str(connection.id),
                        workspace_id=str(connection.workspace_id),
                        user_id=str(connection.user_id),
                        request_id=str(receipt.id),
                        trigger="event",
                    ),
                    settings=settings,
                )
            except Exception:
                # Durable pending receipt can be retried by provider replay.
                raise HTTPException(503, "Connector event dispatch unavailable") from None
            receipt.status = "dispatched"
            receipt.dispatched_at = datetime.now(UTC)
            await database.commit()
        return {"status": "duplicate" if duplicate else "accepted"}
    except HTTPException:
        raise
    except Exception:
        raise rejected() from None
