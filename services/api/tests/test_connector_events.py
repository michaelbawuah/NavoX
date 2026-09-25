"""Signed, owner-bound delivery tests; no provider body is persisted."""

import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.api import connector_events
from navox.api.main import app
from navox.connectors import activities, dispatcher
from navox.connectors.contracts import (
    ConnectorManifest,
    VerifiedEventDelivery,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.secrets import ScopedSecretLease
from navox.connectors.sync_state import authorization_hash
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorEventReceipt,
    ConnectorSubscription,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.session import get_database_session

EVENT = "fixture.items.changed"
SECRET = "shared-verifier-key"
BODY = b'{"id":"item-1","content":"must not persist"}'


@pytest_asyncio.fixture
async def delivery_system(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'deliveries.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    manifest = ConnectorManifest.model_validate(
        {
            "id": "fixture-events",
            "version": "1.0.0",
            "displayName": "Events",
            "category": "test",
            "connectorClass": "WEBHOOK",
            "auth": [],
            "resourceTypes": ["fixture.item"],
            "capabilities": {"events": [EVENT]},
            "requiredSecrets": ["WEBHOOK_SECRET"],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )

    class Adapter:
        delivery_headers = frozenset({"x-provider-signature", "x-provider-delivery-id"})

        def __init__(self, config, secrets):
            self.secrets = secrets

        def get_manifest(self):
            return manifest

        async def verify_event(self, request):
            if request.headers.get("x-provider-delivery-id") == "bad-provider-error":
                raise HTTPException(418, "secret provider diagnostic")
            assert set(request.headers) == {
                "x-provider-signature",
                "x-provider-delivery-id",
            }
            if self.secrets is None:
                raise ValueError("No verifier key")
            signature = hmac.new(
                self.secrets.get("WEBHOOK_SECRET").encode(),
                request.subscription_external_id.encode()
                + b"\x00"
                + request.headers["x-provider-delivery-id"].encode()
                + b"\x00"
                + request.body,
                sha256,
            ).hexdigest()
            if not hmac.compare_digest(signature, request.headers.get("x-provider-signature", "")):
                raise ValueError("Bad signature")
            return VerifiedEventDelivery(
                subscription_external_id=request.subscription_external_id,
                external_event_id=request.headers["x-provider-delivery-id"],
                external_resource_id="item-1",
            )

    registry = ConnectorRegistry()
    registry.register(manifest, Adapter)
    async with factory() as db:
        user, workspace = User(email="event-owner@example.com"), Workspace(name="Events")
        db.add_all([user, workspace])
        await db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id))
        definition = ConnectorDefinition(
            connector_key=manifest.id,
            version=manifest.version,
            display_name=manifest.display_name,
            connector_class=manifest.connector_class,
            trust_level="USER_PRIVATE",
            manifest=manifest.model_dump(mode="json", by_alias=True),
        )
        db.add(definition)
        await db.flush()
        owned = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="fixture",
            external_account_id="account",
            authorized_capabilities=[EVENT],
            provider_capabilities=[EVENT],
            config={},
        )
        db.add(owned)
        await db.flush()
        subscription = ConnectorSubscription(
            connector_connection_id=owned.id,
            subscription_key=EVENT,
            external_id="remote-channel",
            status="active",
            generation=1,
            authorization_hash=authorization_hash(owned, definition, frozenset({EVENT})),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(subscription)
        await db.commit()
        connection_id, subscription_id = owned.id, subscription.id

    class Broker:
        def __init__(self, settings):
            pass

        async def lease(self, db, *, connection_id, purpose, names, **kwargs):
            assert connection_id == owned.id and purpose == "events.verify"
            assert names == frozenset({"WEBHOOK_SECRET"})
            return ScopedSecretLease(connection_id, purpose, {"WEBHOOK_SECRET": SECRET})

    calls = []

    async def dispatch(payload, *, settings):
        calls.append(payload)
        return "queued"

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings()
    monkeypatch.setattr(connector_events, "build_connector_registry", lambda settings: registry)
    monkeypatch.setattr(connector_events, "SecretBroker", Broker)
    monkeypatch.setattr(connector_events, "dispatch_connector_sync", dispatch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, factory, connection_id, subscription_id, calls, registry
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    await engine.dispose()


def signed_headers(subscription_id: UUID, *, body: bytes = BODY, delivery_id: str = "evt-1"):
    signature = hmac.new(
        SECRET.encode(),
        b"remote-channel\x00" + delivery_id.encode() + b"\x00" + body,
        sha256,
    ).hexdigest()
    return {
        "x-navox-subscription-id": str(subscription_id),
        "x-provider-delivery-id": delivery_id,
        "x-provider-signature": signature,
    }


@pytest.mark.asyncio
async def test_verified_event_deduplicates_and_persists_locator_only(delivery_system):
    client, factory, connection_id, sub_id, calls, _ = delivery_system
    headers = signed_headers(sub_id)
    first = await client.post(
        "/api/v1/connectors/events/fixture",
        headers={**headers, "cookie": "session=ambient", "authorization": "Bearer ambient"},
        content=BODY,
    )
    second = await client.post("/api/v1/connectors/events/fixture", headers=headers, content=BODY)
    assert first.status_code == second.status_code == 202
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    assert len(calls) == 1 and calls[0].connection_id == str(connection_id)
    assert calls[0].trigger == "event"
    async with factory() as db:
        rows = list(await db.scalars(select(ConnectorEventReceipt)))
    assert len(rows) == 1 and rows[0].status == "dispatched"
    assert rows[0].external_resource_id == "item-1"
    assert "must not persist" not in str(rows[0].__dict__)


@pytest.mark.asyncio
async def test_conflicting_replay_fails_closed_without_second_dispatch(delivery_system):
    client, factory, _, sub_id, calls, _ = delivery_system
    first = await client.post(
        "/api/v1/connectors/events/fixture", headers=signed_headers(sub_id), content=BODY
    )
    changed = b'{"id":"item-2"}'
    replay = await client.post(
        "/api/v1/connectors/events/fixture",
        headers=signed_headers(sub_id, body=changed),
        content=changed,
    )
    assert first.status_code == 202 and replay.status_code == 401
    assert len(calls) == 1
    forged_id = signed_headers(sub_id)
    forged_id["x-provider-delivery-id"] = "evt-new-unsigned"
    tampered = await client.post(
        "/api/v1/connectors/events/fixture", headers=forged_id, content=BODY
    )
    assert tampered.status_code == 401 and len(calls) == 1
    async with factory() as db:
        assert len(list(await db.scalars(select(ConnectorEventReceipt)))) == 1


@pytest.mark.asyncio
async def test_bad_signature_wrong_provider_and_revocation_fail_closed(delivery_system):
    client, factory, connection_id, sub_id, calls, _ = delivery_system
    headers = signed_headers(sub_id)
    bad = await client.post(
        "/api/v1/connectors/events/fixture",
        headers={**headers, "x-provider-signature": "invalid"},
        content=BODY,
    )
    other = await client.post("/api/v1/connectors/events/another", headers=headers, content=BODY)
    assert bad.status_code == other.status_code == 401
    provider_error = await client.post(
        "/api/v1/connectors/events/fixture",
        headers=signed_headers(sub_id, delivery_id="bad-provider-error"),
        content=BODY,
    )
    assert provider_error.status_code == 401
    assert "secret provider diagnostic" not in provider_error.text
    async with factory() as db:
        connection = await db.get(ConnectorConnection, connection_id)
        connection.authorized_capabilities = []
        await db.commit()
    revoked = await client.post("/api/v1/connectors/events/fixture", headers=headers, content=BODY)
    assert revoked.status_code == 401 and calls == []
    async with factory() as db:
        connection = await db.get(ConnectorConnection, connection_id)
        connection.authorized_capabilities = [EVENT]
        connection.status = "PAUSED"
        await db.commit()
    paused = await client.post("/api/v1/connectors/events/fixture", headers=headers, content=BODY)
    assert paused.status_code == 401 and calls == []
    async with factory() as db:
        assert await db.scalar(select(ConnectorEventReceipt)) is None


@pytest.mark.asyncio
async def test_unsupported_adapter_and_payload_limit_fail_closed(delivery_system):
    client, factory, _, sub_id, calls, _ = delivery_system
    large = await client.post(
        "/api/v1/connectors/events/fixture",
        headers=signed_headers(sub_id, body=b"x" * 65537),
        content=b"x" * 65537,
    )
    assert large.status_code == 413 and calls == []
    async with factory() as db:
        subscription = await db.get(ConnectorSubscription, sub_id)
        subscription.status = "cancel_pending"
        await db.commit()
    cancelled = await client.post(
        "/api/v1/connectors/events/fixture", headers=signed_headers(sub_id), content=BODY
    )
    assert cancelled.status_code == 401 and calls == []


@pytest.mark.asyncio
async def test_chunked_body_without_content_length_is_rejected_while_streaming(delivery_system):
    client, factory, _, sub_id, calls, _ = delivery_system

    async def chunks():
        for _ in range(5):
            yield b"x" * 16_384

    response = await client.post(
        "/api/v1/connectors/events/fixture",
        headers={"x-navox-subscription-id": str(sub_id), "transfer-encoding": "chunked"},
        content=chunks(),
    )
    assert response.status_code == 413 and calls == []
    async with factory() as db:
        assert await db.scalar(select(ConnectorEventReceipt)) is None


@pytest.mark.asyncio
async def test_dispatch_outage_reconciles_pending_receipt_once(delivery_system, monkeypatch):
    client, factory, _, sub_id, calls, registry = delivery_system

    async def unavailable(payload, *, settings):
        raise ConnectionError("queue offline")

    monkeypatch.setattr(connector_events, "dispatch_connector_sync", unavailable)
    response = await client.post(
        "/api/v1/connectors/events/fixture",
        headers=signed_headers(sub_id),
        content=BODY,
    )
    assert response.status_code == 503
    async with factory() as db:
        receipt = await db.scalar(select(ConnectorEventReceipt))
        assert receipt is not None and receipt.status == "pending"

    async def available(payload, *, settings):
        calls.append(payload)
        return "queued"

    monkeypatch.setattr(dispatcher, "dispatch_connector_sync", available)
    monkeypatch.setattr(activities, "build_connector_registry", lambda settings: registry)
    monkeypatch.setattr(activities, "get_settings", Settings)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    assert await activities.connector_event_reconciliation_activity() == 1
    assert await activities.connector_event_reconciliation_activity() == 0
    assert len(calls) == 1 and calls[0].request_id == str(receipt.id)


@pytest.mark.asyncio
async def test_many_revoked_receipts_do_not_starve_new_valid_delivery(delivery_system, monkeypatch):
    _, factory, revoked_id, revoked_sub_id, calls, registry = delivery_system
    async with factory() as db:
        revoked = await db.get(ConnectorConnection, revoked_id)
        assert revoked is not None
        definition = await db.get(ConnectorDefinition, revoked.connector_definition_id)
        assert definition is not None
        revoked.status = "DISCONNECTED"
        for index in range(101):
            db.add(
                ConnectorEventReceipt(
                    subscription_id=revoked_sub_id,
                    connector_connection_id=revoked.id,
                    workspace_id=revoked.workspace_id,
                    user_id=revoked.user_id,
                    provider="fixture",
                    event_type=EVENT,
                    external_event_id=f"revoked-{index}",
                    payload_hash="0" * 64,
                    received_at=datetime.now(UTC) - timedelta(days=1),
                    status="pending",
                )
            )
        valid = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=revoked.user_id,
            workspace_id=revoked.workspace_id,
            provider="fixture",
            external_account_id="another-account",
            authorized_capabilities=[EVENT],
            provider_capabilities=[EVENT],
            config={},
        )
        db.add(valid)
        await db.flush()
        active = ConnectorSubscription(
            connector_connection_id=valid.id,
            subscription_key=EVENT,
            external_id="new-channel",
            status="active",
            generation=1,
            authorization_hash=authorization_hash(valid, definition, frozenset({EVENT})),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(active)
        await db.flush()
        last = ConnectorEventReceipt(
            subscription_id=active.id,
            connector_connection_id=valid.id,
            workspace_id=valid.workspace_id,
            user_id=valid.user_id,
            provider="fixture",
            event_type=EVENT,
            external_event_id="valid-last",
            payload_hash="1" * 64,
            received_at=datetime.now(UTC),
            status="pending",
        )
        db.add(last)
        await db.commit()
        valid_receipt_id = last.id

    async def dispatch(payload, *, settings):
        calls.append(payload)
        return "queued"

    monkeypatch.setattr(dispatcher, "dispatch_connector_sync", dispatch)
    monkeypatch.setattr(activities, "build_connector_registry", lambda settings: registry)
    monkeypatch.setattr(activities, "get_settings", Settings)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)

    assert await activities.connector_event_reconciliation_activity() == 1
    assert [item.request_id for item in calls] == [str(valid_receipt_id)]
    assert await activities.connector_event_reconciliation_activity() == 0
    async with factory() as db:
        rows = list(await db.scalars(select(ConnectorEventReceipt)))
    assert sum(item.status == "invalidated" for item in rows) == 101
    assert sum(item.status == "dispatched" for item in rows) == 1


@pytest.mark.asyncio
async def test_measured_authenticated_event_replay_corpus(delivery_system, measure):
    client, factory, _, sub_id, calls, _ = delivery_system
    duplicate_successes = 0
    # 20 distinct signed event IDs, each replayed 50 times through the HTTP route.
    for index in range(20):
        headers = signed_headers(sub_id, delivery_id=f"measured-{index}")
        first = await client.post(
            "/api/v1/connectors/events/fixture", headers=headers, content=BODY
        )
        assert first.status_code == 202 and first.json() == {"status": "accepted"}
        for _ in range(50):
            response = await client.post(
                "/api/v1/connectors/events/fixture", headers=headers, content=BODY
            )
            duplicate_successes += int(
                response.status_code == 202 and response.json() == {"status": "duplicate"}
            )
    async with factory() as db:
        receipts = list(await db.scalars(select(ConnectorEventReceipt)))
    assert len(receipts) == len(calls) == 20
    measure(
        "event_deduplication",
        duplicate_successes,
        1000,
        "20 authenticated deliveries, each replayed 50 times; exactly 20 receipts and dispatches",
        database="sqlite",
    )
    assert duplicate_successes == 1000
