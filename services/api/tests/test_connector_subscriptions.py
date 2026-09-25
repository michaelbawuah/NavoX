"""Event subscriptions must survive retries and permission changes without orphaning IDs."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.connectors.contracts import (
    ConnectorManifest,
    ConnectorRuntimeError,
    EventSubscriptionResult,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.subscriptions import cancel_subscription, register_or_renew
from navox.db.base import Base
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorSubscription,
    User,
    Workspace,
    WorkspaceMembership,
)

EVENT = "fixture.items.changed"


@pytest_asyncio.fixture
async def system(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")
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
            "requiredSecrets": [],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )
    state = SimpleNamespace(
        calls=[], removals=[], on_subscribe=None, fail_cancel=False, bad_response=False
    )

    class Adapter:
        def __init__(self, config, secrets):
            pass

        def get_manifest(self):
            return manifest

        async def subscribe(self, request):
            state.calls.append(request)
            if state.on_subscribe:
                await state.on_subscribe()
            if state.bad_response:
                return {"external_id": "unvalidated-provider-data"}
            return EventSubscriptionResult(
                external_id="remote-channel-1",
                expires_at=datetime.now(UTC) + timedelta(days=5),
            )

        async def unsubscribe(self, request):
            state.removals.append(request)
            if state.fail_cancel:
                raise ConnectionError("Provider unavailable")

    registry = ConnectorRegistry()
    registry.register(manifest, Adapter)
    async with factory() as db:
        user, workspace = User(email="events@example.com"), Workspace(name="Events")
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
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="fixture",
            external_account_id="account",
            authorized_capabilities=[EVENT],
            provider_capabilities=[EVENT],
            config={},
        )
        db.add(connection)
        await db.commit()
        state.connection_id = connection.id
        state.workspace_id = workspace.id
        state.user_id = user.id
    state.registry = registry
    state.factory = factory
    yield state
    await engine.dispose()


def args(system):
    return {
        "connection_id": system.connection_id,
        "workspace_id": system.workspace_id,
        "user_id": system.user_id,
        "event": EVENT,
    }


@pytest.mark.asyncio
async def test_idempotent_registration_and_due_renewal(system):
    async with system.factory() as db:
        first = await register_or_renew(db, system.registry, **args(system), policy_allowed={EVENT})
        assert first.status == "active"
        assert first.external_id == "remote-channel-1"
        assert first.lease_token is None and first.generation == 1
        second = await register_or_renew(
            db, system.registry, **args(system), policy_allowed={EVENT}
        )
        assert second.id == first.id
        assert len(system.calls) == 1
        first.expires_at = datetime.now(UTC) + timedelta(hours=1)
        await db.commit()
        renewed = await register_or_renew(
            db, system.registry, **args(system), policy_allowed={EVENT}
        )
        assert renewed.generation == 2
        assert len(system.calls) == 2
        assert system.calls[0].idempotency_key == system.calls[1].idempotency_key
        assert system.calls[1].external_id == "remote-channel-1"


@pytest.mark.asyncio
async def test_denied_event_and_unknown_adapter_do_not_call_provider(system):
    async with system.factory() as db:
        with pytest.raises(ConnectorRuntimeError) as denied:
            await register_or_renew(db, system.registry, **args(system), policy_allowed=set())
        assert denied.value.code == "PERMISSION_DENIED"
        await db.rollback()
        assert await db.scalar(select(ConnectorSubscription)) is None
    assert system.calls == []


@pytest.mark.asyncio
async def test_invalid_provider_reply_clears_claim_without_accepting_untrusted_id(system):
    system.bad_response = True
    async with system.factory() as db:
        with pytest.raises(ConnectorRuntimeError) as invalid:
            await register_or_renew(db, system.registry, **args(system), policy_allowed={EVENT})
        assert invalid.value.code == "INVALID_PROVIDER_RESPONSE"
        sub = await db.scalar(
            select(ConnectorSubscription).execution_options(populate_existing=True)
        )
        assert sub is not None and sub.status == "failed"
        assert sub.external_id is None and sub.lease_token is None


@pytest.mark.asyncio
async def test_revocation_during_provider_call_preserves_remote_id_for_cleanup(system):
    async def revoke():
        async with system.factory() as db:
            connection = await db.get(ConnectorConnection, system.connection_id)
            assert connection is not None
            connection.authorized_capabilities = []
            await db.commit()

    system.on_subscribe = revoke
    async with system.factory() as db:
        with pytest.raises(ConnectorRuntimeError) as denied:
            await register_or_renew(db, system.registry, **args(system), policy_allowed={EVENT})
        assert denied.value.code == "PERMISSION_DENIED"
        sub = await db.scalar(select(ConnectorSubscription))
        assert sub is not None and sub.status == "cancel_pending"
        assert sub.external_id == "remote-channel-1" and sub.lease_token is None
        cleaned = await cancel_subscription(db, system.registry, **args(system))
        assert cleaned is not None and cleaned.status == "cancelled"
        assert cleaned.external_id is None
    assert system.removals[0].external_id == "remote-channel-1"


@pytest.mark.asyncio
async def test_disconnect_during_provider_call_does_not_reactivate_subscription(system):
    async def disconnect():
        async with system.factory() as db:
            connection = await db.get(ConnectorConnection, system.connection_id)
            assert connection is not None
            connection.status = "DISCONNECTED"
            sub = await db.scalar(select(ConnectorSubscription))
            assert sub is not None
            sub.status = "cancel_pending"
            await db.commit()

    system.on_subscribe = disconnect
    async with system.factory() as db:
        with pytest.raises(ConnectorRuntimeError):
            await register_or_renew(db, system.registry, **args(system), policy_allowed={EVENT})
        sub = await db.scalar(select(ConnectorSubscription))
        assert sub is not None and sub.status == "cancel_pending"
        assert sub.external_id == "remote-channel-1"
        cleaned = await cancel_subscription(db, system.registry, **args(system))
        assert cleaned is not None and cleaned.status == "cancelled"


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_remote_identity_for_retry(system):
    async with system.factory() as db:
        await register_or_renew(db, system.registry, **args(system), policy_allowed={EVENT})
        system.fail_cancel = True
        with pytest.raises(ConnectionError):
            await cancel_subscription(db, system.registry, **args(system))
        sub = await db.scalar(select(ConnectorSubscription))
        assert sub is not None and sub.status == "cancel_pending"
        assert sub.external_id == "remote-channel-1" and sub.lease_token is None
        system.fail_cancel = False
        cleaned = await cancel_subscription(db, system.registry, **args(system))
        assert cleaned is not None and cleaned.status == "cancelled"
    assert len(system.removals) == 2
