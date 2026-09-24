"""Provider operations, not downstream intelligence, receive live secret handles."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from navox.connectors import activities
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    SyncPage,
    stable_resource_id,
)
from navox.connectors.jobs import ConnectorHealthWork
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    Workspace,
    WorkspaceMembership,
)


@pytest_asyncio.fixture
async def fixture(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        _env_file=None, connector_secret_encryption_key=Fernet.generate_key().decode()
    )
    broker = SecretBroker(settings)
    manifest = ConnectorManifest.model_validate(
        {
            "id": "lifecycle-test",
            "version": "1.0.0",
            "displayName": "Lifecycle",
            "category": "test",
            "connectorClass": "TOKEN_API",
            "auth": [{"kind": "api_token", "label": "Token", "scopes": []}],
            "resourceTypes": ["test.item"],
            "capabilities": {
                "read": [{"name": "test.items.read", "description": "Read items"}],
                "write": [],
                "events": [],
                "incrementalSync": True,
            },
            "requiredSecrets": ["API_TOKEN"],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )
    observed = {"leases": [], "calls": [], "mode": "ok", "consumer": []}

    class Adapter:
        def __init__(self, config, secrets):
            self.secrets = secrets
            if secrets is not None:
                observed["leases"].append(secrets)
                if observed["mode"] == "factory_error":
                    raise RuntimeError("Synthetic factory error")

        def get_manifest(self):
            return manifest

        async def authorize(self, context):
            raise NotImplementedError

        def check(self, operation):
            assert self.secrets.get("API_TOKEN") == "synthetic-token"
            assert self.secrets.purpose == operation
            observed["calls"].append(operation)
            if observed["mode"] == f"{operation}_error":
                raise RuntimeError("Synthetic provider error")
            if observed["mode"] == f"{operation}_cancel":
                raise asyncio.CancelledError()

        async def health(self, context):
            self.check("health.read")
            return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

        async def sync(self, request):
            self.check("sync.read")
            return SyncPage(
                resources=[
                    CanonicalResource(
                        resource_id=stable_resource_id(request.connection_id, "test.item", "one"),
                        workspace_id=request.workspace_id,
                        connector_connection_id=request.connection_id,
                        provider="test",
                        resource_type="test.item",
                        external_id="one",
                        canonical={"subject": "Fixture item"},
                        retrieved_at=datetime.now(UTC),
                    )
                ],
                next_cursor="complete",
            )

        async def fetch_resource(self, request):
            raise NotImplementedError

        async def execute(self, request):
            raise NotImplementedError

    registry = ConnectorRegistry()
    registry.register(manifest, Adapter)
    async with factory() as database:
        user, workspace = User(email="owner@example.com"), Workspace(name="Personal")
        database.add_all([user, workspace])
        await database.flush()
        database.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id))
        definition = ConnectorDefinition(
            connector_key=manifest.id,
            version=manifest.version,
            display_name=manifest.display_name,
            connector_class=manifest.connector_class,
            trust_level="USER_PRIVATE",
            manifest=manifest.model_dump(mode="json", by_alias=True),
        )
        database.add(definition)
        await database.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="test",
            external_account_id="fixture",
            authorized_capabilities=["test.items.read"],
            provider_capabilities=["test.items.read"],
            config={},
        )
        database.add(connection)
        await database.flush()
        ids = {"connection_id": connection.id, "workspace_id": workspace.id, "user_id": user.id}
        await broker.store(database, {"API_TOKEN": "synthetic-token"}, **ids)
        await database.commit()

        async def consume(resource):
            for handle in observed["leases"]:
                with pytest.raises(SecretBrokerError, match="closed"):
                    handle.get("API_TOKEN")
            observed["consumer"].append(resource.resource_id)

        yield {
            "database": database,
            "factory": factory,
            "settings": settings,
            "registry": registry,
            "observed": observed,
            "ids": ids,
            "runtime": ConnectorRuntime(registry, secret_broker=broker),
            "consume": consume,
        }
    await engine.dispose()


async def run(fixture, **overrides):
    return await fixture["runtime"].sync(
        fixture["database"],
        **{**fixture["ids"], **overrides},
        request_id=uuid4(),
        policy_allowed={"test.items.read"},
        consume=fixture["consume"],
    )


@pytest.mark.asyncio
async def test_runtime_closes_health_and_sync_leases_before_intelligence(fixture):
    result = await run(fixture)
    assert result.status == "completed"
    observed = fixture["observed"]
    assert observed["calls"] == ["health.read", "sync.read"]
    assert len(observed["leases"]) == 2
    assert len(observed["consumer"]) == 1
    for handle in observed["leases"]:
        with pytest.raises(SecretBrokerError, match="closed"):
            handle.get("API_TOKEN")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "health.read_error",
        "sync.read_error",
        "factory_error",
        "health.read_cancel",
        "sync.read_cancel",
    ],
)
async def test_runtime_closes_every_lease_on_failure_and_cancellation(fixture, mode):
    fixture["observed"]["mode"] = mode
    expected = asyncio.CancelledError if mode.endswith("cancel") else RuntimeError
    with pytest.raises(expected):
        await run(fixture)
    assert fixture["observed"]["leases"]
    for handle in fixture["observed"]["leases"]:
        with pytest.raises(SecretBrokerError, match="closed"):
            handle.get("API_TOKEN")
    assert fixture["observed"]["consumer"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["wrong_user", "paused", "no_capabilities"])
async def test_runtime_checks_authority_before_any_credentialed_health_probe(fixture, denial):
    database = fixture["database"]
    connection = await database.get(ConnectorConnection, fixture["ids"]["connection_id"])
    kwargs = {}
    if denial == "wrong_user":
        kwargs["user_id"] = uuid4()
    elif denial == "paused":
        connection.status = "PAUSED"
    else:
        connection.authorized_capabilities = []
    await database.commit()
    with pytest.raises(ConnectorRuntimeError):
        await run(fixture, **kwargs)
    assert fixture["observed"]["calls"] == []
    assert fixture["observed"]["leases"] == []
    assert not list(
        await database.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "connector.credentials.leased")
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "health.read_error", "health.read_cancel", "factory_error"])
async def test_health_activity_closes_lease_on_every_exit(fixture, monkeypatch, mode):
    monkeypatch.setattr(activities, "get_settings", lambda: fixture["settings"])
    monkeypatch.setattr(activities, "get_session_factory", lambda: fixture["factory"])
    monkeypatch.setattr(activities, "build_connector_registry", lambda _: fixture["registry"])
    fixture["observed"]["mode"] = mode
    payload = ConnectorHealthWork(**{k: str(v) for k, v in fixture["ids"].items()})
    if mode == "ok":
        assert await activities.connector_health_activity(payload) == "CONNECTED"
    else:
        expected = asyncio.CancelledError if mode.endswith("cancel") else ApplicationError
        with pytest.raises(expected):
            await activities.connector_health_activity(payload)
    assert fixture["observed"]["leases"]
    for handle in fixture["observed"]["leases"]:
        with pytest.raises(SecretBrokerError, match="closed"):
            handle.get("API_TOKEN")
