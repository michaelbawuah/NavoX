import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api import connections, intelligence_sync
from navox.api.main import create_app
from navox.connectors.builtin.google import ensure_google_connector_connection
from navox.connectors.jobs import ConnectorDisconnectWork
from navox.connectors.management import freshness
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorEventReceipt,
    ConnectorImportSnapshot,
    ConnectorResource,
    ConnectorSubscription,
    ConnectorSyncRun,
    OAuthAuthorizationAttempt,
    ObservationEvidence,
    OperationalObservation,
    Person,
    PersonIdentity,
    PersonIdentitySource,
    ProviderEventSubscription,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.session import get_database_session
from navox.intelligence.contracts import SourceIdentity
from navox.intelligence.resolution import resolve_identity
from navox.providers.google_sources import CALENDAR_READ_SCOPE, GMAIL_READ_SCOPE


@pytest_asyncio.fixture
async def env() -> AsyncIterator[SimpleNamespace]:
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"management_{uuid4().hex}"
    administrative = None
    if dsn.startswith("postgresql"):
        administrative = create_async_engine(dsn)
        async with administrative.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    settings = Settings(
        _env_file=None,
        ai_provider="openai",
        openai_api_key="test",
        google_oauth_client_id="client",
        google_oauth_client_secret="secret",
        google_token_encryption_key=Fernet.generate_key().decode(),
    )

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Owner",
            },
        )
        assert response.status_code == 201
        account = response.json()
        from uuid import UUID

        user_id, workspace_id = UUID(account["id"]), UUID(account["workspace"]["id"])
        async with factory() as db:
            credential = ConnectionCredential(encrypted_refresh_token="SECRET-SENTINEL")
            db.add(credential)
            await db.flush()
            row = Connection(
                user_id=user_id,
                workspace_id=workspace_id,
                provider="google",
                external_account_id="account",
                external_email="owner@example.com",
                status="active",
                granted_scopes=[GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE],
                credential_reference=credential.id,
            )
            db.add(row)
            await db.commit()
            connection_id, credential_id = row.id, credential.id
        yield SimpleNamespace(
            client=client,
            factory=factory,
            settings=settings,
            id=connection_id,
            user_id=user_id,
            workspace_id=workspace_id,
            credential_id=credential_id,
        )
    await engine.dispose()
    if administrative is not None:
        async with administrative.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await administrative.dispose()


def command():
    return {"request_id": str(uuid4())}


def reviewed_rest_config(auth="bearer"):
    return {
        "id": "example",
        "config": {
            "display_name": "Reviewed Example",
            "provider": "reviewed_example",
            "base_url": "https://example.net",
            "auth": auth,
            "endpoints": [
                {
                    "name": "obligations",
                    "path": "/v1/obligations",
                    "capability": "external.obligations.read",
                    "resource_type": "external.obligation",
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_reviewed_rest_setup_binds_consent_secret_and_runtime_without_exposing_token(
    env, monkeypatch
):
    from navox.api import generic_rest
    from navox.connectors.catalog import build_connector_registry
    from navox.connectors.generic_registration import approved_generic_connectors
    from navox.connectors.secrets import SecretBroker

    sentinel = "synthetic-bearer-token-do-not-leak"
    env.settings.generic_rest_connectors = [reviewed_rest_config()]
    dispatched = []

    async def dispatch(payload, **kwargs):
        dispatched.append((payload, kwargs))
        return "queued"

    monkeypatch.setattr(generic_rest, "dispatch_connector_sync", dispatch)
    listed = (await env.client.get("/api/v1/connectors")).json()
    assert (
        next(row for row in listed if row["id"] == "generic-rest-api")["availability"]
        == "available"
    )
    choices = await env.client.get("/api/v1/connectors/generic-rest-api/configurations")
    assert choices.json() == [
        {
            "id": "example",
            "name": "Reviewed Example",
            "read_capabilities": ["external.obligations.read"],
            "authentication": "api_token",
        }
    ]
    body = {
        "configuration_id": "example",
        "capabilities": ["external.obligations.read"],
        "token": sentinel,
        "confirmed": True,
        **command(),
    }
    created = await env.client.post("/api/v1/connectors/generic-rest-api/connect", json=body)
    assert created.status_code == 201, created.text
    connection_id = created.json()["connection_id"]
    assert created.json()["dispatch_status"] == "queued"
    assert sentinel not in created.text
    assert len(dispatched) == 1
    assert dispatched[0][0].connection_id == connection_id
    assert sentinel not in repr(dispatched)
    async with env.factory() as database:
        row = await database.get(ConnectorConnection, UUID(connection_id))
        assert row is not None and row.credential_reference
        assert row.config["base_url"] == "https://example.net"
        assert row.config["auth"] == "bearer"
        assert sentinel not in str(row.config)
        definition = await database.get(ConnectorDefinition, row.connector_definition_id)
        assert (
            definition.connector_key == approved_generic_connectors(env.settings)[0].connector_key
        )
        broker = SecretBroker(env.settings)
        with await broker.lease(
            database,
            connection_id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            purpose="sync.read",
            names={"API_TOKEN"},
        ) as lease:
            assert lease.get("API_TOKEN") == sentinel
        registry = build_connector_registry(env.settings)
        assert (
            registry.build(definition.connector_key, row.config).get_manifest().id
            == definition.connector_key
        )
        with pytest.raises(ValueError, match="does not match"):
            registry.build(definition.connector_key, row.config | {"base_url": "https://evil.net"})
    view = await env.client.get(f"/api/v1/connections/{connection_id}")
    assert view.json()["connector_id"] == "generic-rest-api"
    assert view.json()["can_reauthorize"] is True
    assert view.json()["sources"][0]["can_sync"] is True
    assert sentinel not in view.text
    repeated = await env.client.post("/api/v1/connectors/generic-rest-api/connect", json=body)
    assert repeated.status_code == 201 and repeated.json()["reused"] is True
    assert len(dispatched) == 1
    manual = await env.client.post(
        f"/api/v1/connections/{connection_id}/sync", json={**command(), "source": "resources"}
    )
    assert manual.status_code == 202 and manual.json()["dispatch_status"] == "queued"
    assert len(dispatched) == 2


@pytest.mark.asyncio
async def test_rest_setup_rejects_unreviewed_targets_secret_leaks_and_capability_escalation(env):
    env.settings.generic_rest_connectors = [reviewed_rest_config()]
    path = "/api/v1/connectors/generic-rest-api/connect"
    body = {
        "configuration_id": "example",
        "capabilities": ["external.obligations.read"],
        "token": "synthetic-credential-stays-secret",
        "confirmed": True,
        **command(),
    }
    for invalid in (
        {**body, "configuration_id": "other"},
        {**body, "capabilities": ["communication.messages.send"]},
        {**body, "base_url": "https://evil.net"},
        {**body, "confirmed": False},
        {**body, "token": "unsafe\r\nheader"},
        {**body, "token": "synthetic-credential-stays-secret", "capabilities": []},
    ):
        result = await env.client.post(path, json=invalid)
        assert result.status_code in {404, 422} and body["token"] not in result.text
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(ConnectorConnection)) == 0
        assert await database.scalar(select(func.count()).select_from(ConnectionCredential)) == 1
    env.settings.generic_rest_connectors = [
        {
            **reviewed_rest_config(),
            "config": {**reviewed_rest_config()["config"], "base_url": "http://localhost"},
        }
    ]
    assert (
        await env.client.get("/api/v1/connectors/generic-rest-api/configurations")
    ).status_code == 503


@pytest.mark.asyncio
async def test_rest_reauthorization_is_owner_scoped_idempotent_and_preserves_pause(
    env, monkeypatch
):
    from navox.api import generic_rest

    env.settings.generic_rest_connectors = [reviewed_rest_config()]

    async def dispatch(*args, **kwargs):
        return "queued"

    monkeypatch.setattr(generic_rest, "dispatch_connector_sync", dispatch)
    created = await env.client.post(
        "/api/v1/connectors/generic-rest-api/connect",
        json={
            "configuration_id": "example",
            "capabilities": ["external.obligations.read"],
            "token": "first-token",
            "confirmed": True,
            **command(),
        },
    )
    assert created.status_code == 201, created.text
    identifier = UUID(created.json()["connection_id"])
    async with env.factory() as database:
        row = await database.get(ConnectorConnection, identifier)
        row.health_state = "AUTH_EXPIRED"
        await database.commit()
    reauth_path = f"/api/v1/connectors/generic-rest-api/connections/{identifier}/credential"
    replace = {"token": "second-token", "confirmed": True, **command()}
    replaced = await env.client.post(reauth_path, json=replace)
    assert replaced.status_code == 200 and replaced.json()["dispatch_status"] == "queued"
    repeated = await env.client.post(reauth_path, json=replace)
    assert repeated.status_code == 200 and repeated.json()["reused"] is True
    async with env.factory() as database:
        row = await database.get(ConnectorConnection, identifier)
        assert row.health_state == "CONNECTED"
        assert row.authorized_capabilities == ["external.obligations.read"]
        from navox.connectors.secrets import SecretBroker

        with await SecretBroker(env.settings).lease(
            database,
            connection_id=row.id,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            purpose="sync.read",
            names={"API_TOKEN"},
        ) as lease:
            assert lease.get("API_TOKEN") == "second-token"
    paused = await env.client.post(f"/api/v1/connections/{identifier}/pause", json=command())
    assert paused.status_code == 200
    pending = await env.client.post(
        reauth_path, json={"token": "third-token", "confirmed": True, **command()}
    )
    assert pending.status_code == 200 and pending.json()["dispatch_status"] == "pending"
    still_paused = await env.client.get(f"/api/v1/connections/{identifier}")
    assert still_paused.json()["health"] == "PAUSED"
    assert not still_paused.json()["sources"][0]["can_sync"]
    env.settings.generic_rest_connectors = []
    assert (
        await env.client.post(reauth_path, json={"token": "fourth", "confirmed": True, **command()})
    ).status_code == 409
    view = await env.client.get(f"/api/v1/connections/{identifier}")
    assert view.json()["sources"][0]["can_sync"] is False


@pytest.mark.asyncio
async def test_reviewed_public_rest_requires_no_credential_and_cannot_gain_one(env, monkeypatch):
    from navox.api import generic_rest

    env.settings.generic_rest_connectors = [reviewed_rest_config("none")]

    async def dispatch(*args, **kwargs):
        return "queued"

    monkeypatch.setattr(generic_rest, "dispatch_connector_sync", dispatch)
    choice = (await env.client.get("/api/v1/connectors/generic-rest-api/configurations")).json()
    assert choice[0]["authentication"] == "none"
    body = {
        "configuration_id": "example",
        "capabilities": ["external.obligations.read"],
        "confirmed": True,
        **command(),
    }
    assert (
        await env.client.post(
            "/api/v1/connectors/generic-rest-api/connect", json={**body, "token": "unwanted"}
        )
    ).status_code == 422
    created = await env.client.post("/api/v1/connectors/generic-rest-api/connect", json=body)
    assert created.status_code == 201, created.text
    view = await env.client.get(f"/api/v1/connections/{created.json()['connection_id']}")
    assert view.json()["can_reauthorize"] is False
    async with env.factory() as database:
        row = await database.get(ConnectorConnection, UUID(created.json()["connection_id"]))
        assert row.credential_reference is None
    assert (
        await env.client.post(
            f"/api/v1/connectors/generic-rest-api/connections/{created.json()['connection_id']}/credential",
            json={"token": "surprise", "confirmed": True, **command()},
        )
    ).status_code == 409


async def overview(env):
    response = await env.client.get("/api/v1/connections")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_catalog_is_authenticated_and_does_not_claim_unfinished_setup(env):
    entries = (await env.client.get("/api/v1/connectors")).json()
    assert {e["id"] for e in entries} == {
        "google-workspace",
        "canvas-lms",
        "generic-import",
        "generic-rest-api",
        "mcp",
    }
    assert [e["id"] for e in entries if e["availability"] == "available"] == [
        "google-workspace",
        "generic-import",
    ]
    for entry in entries:
        response = await env.client.get(f"/api/v1/connectors/{entry['id']}")
        assert response.json() == entry
        if entry["availability"] != "available":
            response = await env.client.post(
                f"/api/v1/connectors/{entry['id']}/connect", json=command()
            )
            assert response.status_code == 409
    env.client.cookies.clear()
    assert (await env.client.get("/api/v1/connectors")).status_code == 401
    assert (await env.client.get("/api/v1/connections")).status_code == 401


@pytest.mark.asyncio
async def test_discovery_reads_are_private_metadata_only_and_do_not_create_mirrors(
    env, monkeypatch
):
    async def forbidden(*args, **kwargs):
        raise AssertionError("A status read attempted provider access")

    monkeypatch.setattr(connections, "refresh_google_access_token", forbidden)
    rows = await overview(env)
    assert len(rows) == 1 and rows[0]["id"] == str(env.id)
    assert rows[0]["health"] == "CONNECTED"
    assert all(s["freshness"] == "never_synced" for s in rows[0]["sources"])
    response = await env.client.get(f"/api/v1/connections/{env.id}")
    assert response.json() == rows[0]
    assert response.headers["cache-control"] == "no-store"
    assert "SECRET-SENTINEL" not in response.text
    assert "credential_reference" not in response.text and "config" not in response.text
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorConnection)) == 0
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("same_workspace", [False, True])
async def test_other_users_connections_are_unreadable_and_immutable(env, same_workspace):
    async with env.factory() as db:
        user = User(email=f"other-{uuid4()}@example.com")
        workspace = Workspace(name="Other")
        db.add_all([user, workspace])
        await db.flush()
        wid = env.workspace_id if same_workspace else workspace.id
        db.add(WorkspaceMembership(workspace_id=wid, user_id=user.id, role="owner"))
        row = Connection(
            user_id=user.id,
            workspace_id=wid,
            provider="google",
            external_account_id="other",
            external_email="hidden@example.com",
            status="active",
            granted_scopes=[],
        )
        db.add(row)
        await db.commit()
        foreign = row.id
    assert len(await overview(env)) == 1
    assert (await env.client.get(f"/api/v1/connections/{foreign}")).status_code == 404
    for operation in ("pause", "resume", "reauthorize"):
        assert (
            await env.client.post(f"/api/v1/connections/{foreign}/{operation}", json=command())
        ).status_code == 404
    assert (
        await env.client.post(
            f"/api/v1/connections/{foreign}/sync", json={**command(), "source": "gmail"}
        )
    ).status_code == 404
    async with env.factory() as db:
        assert (await db.get(Connection, foreign)).status == "active"
    assert (
        await env.client.request("DELETE", f"/api/v1/connections/{foreign}", json=command())
    ).status_code == 404
    assert (
        await env.client.post(f"/api/v1/connections/{foreign}/delete-data", json=command())
    ).status_code == 404


@pytest.mark.asyncio
async def test_disconnect_fences_workers_and_erases_unshared_credentials(env):
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        mirror = await ensure_google_connector_connection(db, account)
        mirror.sync_generation = 4
        mirror.sync_cursor = "SENSITIVE-CURSOR"
        mirror.sync_lease_token = uuid4()
        mirror.sync_lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        run = ConnectorSyncRun(
            connector_connection_id=mirror.id,
            workspace_id=env.workspace_id,
            request_id=uuid4(),
            status="running",
        )
        db.add(run)
        await db.flush()
        mirror.sync_run_id = run.id
        await db.commit()
        mirror_id, run_id = mirror.id, run.id
    request = command()
    response = await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["health"] == "DISCONNECTED"
    assert response.json()["can_delete_data"] is True
    assert "SENSITIVE-CURSOR" not in response.text
    async with env.factory() as db:
        mirror = await db.get(ConnectorConnection, mirror_id)
        account = await db.get(Connection, env.id)
        assert mirror.sync_generation == 5 and mirror.sync_lease_token is None
        assert mirror.status == "DISCONNECTED" and account.status == "disconnected"
        assert (await db.get(ConnectorSyncRun, run_id)).status == "interrupted"
        assert mirror.sync_cursor == "SENSITIVE-CURSOR"
        assert account.credential_reference is None
        assert await db.get(ConnectionCredential, env.credential_id) is None
    replay = await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=request)
    assert replay.status_code == 200
    async with env.factory() as db:
        assert (await db.get(ConnectorConnection, mirror_id)).sync_generation == 5
    assert (
        await env.client.post(f"/api/v1/connections/{env.id}/resume", json=command())
    ).status_code == 409


@pytest.mark.asyncio
async def test_disconnect_retains_cleanup_credential_and_blocks_deletion_until_channel_retires(env):
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        mirror = await ensure_google_connector_connection(db, account)
        mirror.credential_reference = env.credential_id
        mirror.sync_lease_token = uuid4()
        mirror.sync_lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        subscription = ConnectorSubscription(
            connector_connection_id=mirror.id,
            subscription_key="communication.messages.changed",
            external_id="remote-channel",
            status="active",
        )
        db.add(subscription)
        await db.commit()
        mirror_id, subscription_id = mirror.id, subscription.id
    response = await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    assert response.status_code == 200, response.text
    assert response.json()["can_delete_data"] is False
    async with env.factory() as db:
        mirror = await db.get(ConnectorConnection, mirror_id)
        account = await db.get(Connection, env.id)
        subscription = await db.get(ConnectorSubscription, subscription_id)
        assert mirror is not None and account is not None and subscription is not None
        assert mirror.status == "DISCONNECTED" and mirror.sync_lease_token is None
        assert mirror.credential_reference == account.credential_reference == env.credential_id
        assert (
            subscription.status == "cancel_pending" and subscription.external_id == "remote-channel"
        )
        assert await db.get(ConnectionCredential, env.credential_id) is not None
    blocked = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert blocked.status_code == 409
    async with env.factory() as db:
        subscription = await db.get(ConnectorSubscription, subscription_id)
        assert subscription is not None
        subscription.status = "cancelled"
        subscription.external_id = None
        await db.commit()
    deleted = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert deleted.status_code == 200, deleted.text
    async with env.factory() as db:
        assert await db.get(ConnectorSubscription, subscription_id) is None
        assert await db.get(ConnectionCredential, env.credential_id) is None


@pytest.mark.asyncio
async def test_legacy_provider_channel_blocks_deletion_and_keeps_cleanup_credential(env):
    async with env.factory() as db:
        channel = ProviderEventSubscription(
            connection_id=env.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            source="calendar",
            channel_id=f"channel-{uuid4()}",
            channel_token_hash="f" * 64,
            status="active",
        )
        db.add(channel)
        await db.commit()
        channel_id = channel.id
    result = await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    assert result.status_code == 200 and result.json()["can_delete_data"] is False
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        channel = await db.get(ProviderEventSubscription, channel_id)
        assert account is not None and channel is not None
        assert account.credential_reference == env.credential_id
        assert channel.status == "cancel_pending"
    assert (
        await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    ).status_code == 409


@pytest.mark.asyncio
async def test_already_cancelled_legacy_channel_does_not_reopen_cleanup(env):
    async with env.factory() as db:
        channel = ProviderEventSubscription(
            connection_id=env.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            source="calendar",
            channel_id=f"cancelled-{uuid4()}",
            channel_token_hash="f" * 64,
            status="cancelled",
        )
        db.add(channel)
        await db.commit()
        channel_id = channel.id
    disconnected = await env.client.request(
        "DELETE", f"/api/v1/connections/{env.id}", json=command()
    )
    assert disconnected.status_code == 200 and disconnected.json()["can_delete_data"] is True
    async with env.factory() as db:
        channel = await db.get(ProviderEventSubscription, channel_id)
        account = await db.get(Connection, env.id)
        assert channel is not None and channel.status == "cancelled"
        assert account is not None and account.credential_reference is None
    assert (
        await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, endpoint, scope",
    [
        ("calendar", "GOOGLE_CALENDAR_STOP", CALENDAR_READ_SCOPE),
        ("drive", "GOOGLE_DRIVE_STOP", "https://www.googleapis.com/auth/drive.readonly"),
    ],
)
async def test_legacy_channel_retired_only_after_confirmed_provider_stop(
    env, monkeypatch, source, endpoint, scope
):
    from navox.connectors import google_watch_cleanup as cleanup

    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        account.granted_scopes = list(account.granted_scopes) + [scope]
        row = ProviderEventSubscription(
            connection_id=env.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            source=source,
            channel_id=f"channel-{uuid4()}",
            channel_token_hash="f" * 64,
            resource_id="resource-sentinel",
            status="active",
        )
        db.add(row)
        await db.commit()
        subscription_id = row.id
    disconnected = await env.client.request(
        "DELETE", f"/api/v1/connections/{env.id}", json=command()
    )
    assert disconnected.status_code == 200

    async def token(*args, **kwargs):
        return "ACCESS-TOKEN-SENTINEL"

    monkeypatch.setattr(cleanup, "access_token_for_watch_cleanup", token)
    requests: list[Request] = []
    succeed = False

    def respond(request: Request) -> Response:
        requests.append(request)
        assert request.url == getattr(cleanup, endpoint)
        assert request.headers["Authorization"] == "Bearer ACCESS-TOKEN-SENTINEL"
        assert request.content == (
            b'{"id":"' + channel_id.encode() + b'","resourceId":"resource-sentinel"}'
        )
        return Response(204 if succeed else 503)

    client_type = AsyncClient
    monkeypatch.setattr(
        cleanup.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=MockTransport(respond)),
    )
    async with env.factory() as db:
        channel_id = (await db.get(ProviderEventSubscription, subscription_id)).channel_id
        assert not await cleanup.cancel_google_watch(
            db, subscription_id=subscription_id, settings=env.settings
        )
        assert (await db.get(ProviderEventSubscription, subscription_id)).status == "cancel_pending"
        assert (await db.get(Connection, env.id)).credential_reference == env.credential_id
    succeed = True
    async with env.factory() as db:
        assert await cleanup.cancel_google_watch(
            db, subscription_id=subscription_id, settings=env.settings
        )
        assert (await db.get(ProviderEventSubscription, subscription_id)).status == "cancelled"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_legacy_google_watch_missing_resource_stays_pending_without_token_use(
    env, monkeypatch
):
    from navox.connectors import google_watch_cleanup as cleanup

    async with env.factory() as db:
        row = ProviderEventSubscription(
            connection_id=env.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            source="calendar",
            channel_id=f"channel-{uuid4()}",
            channel_token_hash="f" * 64,
            resource_id=None,
            status="active",
        )
        db.add(row)
        await db.commit()
        subscription_id = row.id
    disconnected = await env.client.request(
        "DELETE", f"/api/v1/connections/{env.id}", json=command()
    )
    assert disconnected.status_code == 200

    async def forbidden(*args, **kwargs):
        raise AssertionError("Missing watch resource must not consume a credential")

    monkeypatch.setattr(cleanup, "access_token_for_watch_cleanup", forbidden)
    async with env.factory() as db:
        assert not await cleanup.cancel_google_watch(
            db, subscription_id=subscription_id, settings=env.settings
        )
        assert (await db.get(ProviderEventSubscription, subscription_id)).status == "cancel_pending"


@pytest.mark.asyncio
async def test_gmail_cleanup_never_uses_account_wide_stop(env, monkeypatch):
    from navox.connectors import google_watch_cleanup as cleanup

    async with env.factory() as db:
        channel = ProviderEventSubscription(
            connection_id=env.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            source="gmail",
            channel_id=f"gmail-{uuid4()}",
            channel_token_hash="f" * 64,
            status="active",
        )
        db.add(channel)
        await db.commit()
        subscription_id = channel.id
    disconnected = await env.client.request(
        "DELETE", f"/api/v1/connections/{env.id}", json=command()
    )
    assert disconnected.status_code == 200

    async def forbidden(*args, **kwargs):
        raise AssertionError("Gmail cleanup must never consume a credential or call stop")

    monkeypatch.setattr(cleanup, "access_token_for_watch_cleanup", forbidden)
    monkeypatch.setattr(cleanup.httpx, "AsyncClient", forbidden)
    async with env.factory() as db:
        assert subscription_id not in await cleanup.due_google_watches(db)
        assert not await cleanup.cancel_google_watch(
            db, subscription_id=subscription_id, settings=env.settings
        )
        assert (await db.get(ProviderEventSubscription, subscription_id)).status == "cancel_pending"


@pytest.mark.asyncio
async def test_internal_disconnect_keeps_cancelled_channels_retired(env, monkeypatch):
    from navox.connectors import activities

    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        mirror = await ensure_google_connector_connection(db, account)
        retired = ConnectorSubscription(
            connector_connection_id=mirror.id,
            subscription_key="communication.messages.changed",
            status="cancelled",
        )
        pending = ConnectorSubscription(
            connector_connection_id=mirror.id,
            subscription_key="calendar.events.changed",
            external_id="remote-calendar-channel",
            status="active",
        )
        db.add_all([retired, pending])
        await db.commit()
        mirror_id, retired_id, pending_id = mirror.id, retired.id, pending.id
    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    status = await activities.connector_disconnect_activity(
        ConnectorDisconnectWork(
            connection_id=str(mirror_id),
            user_id=str(env.user_id),
            workspace_id=str(env.workspace_id),
        )
    )
    assert status == "DISCONNECTED"
    async with env.factory() as db:
        mirror = await db.get(ConnectorConnection, mirror_id)
        assert mirror is not None and mirror.status == "DISCONNECTED"
        assert (await db.get(ConnectorSubscription, retired_id)).status == "cancelled"
        assert (await db.get(ConnectorSubscription, pending_id)).status == "cancel_pending"


@pytest.mark.asyncio
async def test_lifecycle_commands_require_json_and_trusted_origin(env):
    path = f"/api/v1/connections/{env.id}"
    assert (
        await env.client.request(
            "DELETE", path, json=command(), headers={"Origin": "https://attacker.example"}
        )
    ).status_code == 403
    assert (await env.client.request("DELETE", path, data=command())).status_code == 422
    assert (
        await env.client.request("DELETE", path, json={**command(), "workspace_id": str(uuid4())})
    ).status_code == 422
    data_path = f"{path}/delete-data"
    assert (
        await env.client.post(
            data_path, json=command(), headers={"Origin": "https://attacker.example"}
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_delete_data_requires_disconnect_and_removes_unshared_learned_facts(env):
    observation_id, card_id = uuid4(), uuid4()
    async with env.factory() as db:
        observation = OperationalObservation(
            id=observation_id,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            observation_type="request",
            action_text="submit",
            object_text="report",
            confidence=Decimal("0.950"),
            extractor_version="test",
        )
        card = Commitment(
            id=card_id,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            commitment_type="task",
            title="submit report",
            dedupe_key="source-only",
            created_by="ai",
        )
        db.add_all([observation, card])
        await db.flush()
        db.add_all(
            [
                ObservationEvidence(
                    observation_id=observation_id,
                    connection_id=env.id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id="message-1",
                    observed_at=datetime.now(UTC),
                ),
                CommitmentSource(
                    commitment_id=card_id,
                    connection_id=env.id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id="message-1",
                    source_metadata={"observation_id": str(observation_id)},
                ),
            ]
        )
        await db.commit()
    path = f"/api/v1/connections/{env.id}/delete-data"
    assert (await env.client.post(path, json=command())).status_code == 409
    await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    payload = command()
    response = await env.client.post(path, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["health"] == "DISCONNECTED"
    assert (await env.client.post(path, json=payload)).status_code == 200
    async with env.factory() as db:
        assert await db.get(OperationalObservation, observation_id) is None
        assert await db.get(Commitment, card_id) is None
        assert (await db.get(Connection, env.id)).granted_scopes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target_type", ["commitment", "observation"])
@pytest.mark.parametrize("scope", ["workspace", "user"])
async def test_delete_data_rejects_foreign_provenance_target(env, target_type, scope):
    async with env.factory() as db:
        foreign_user = User(email=f"foreign-{uuid4()}@example.com")
        db.add(foreign_user)
        if scope == "workspace":
            foreign_workspace = Workspace(name="Foreign workspace")
            db.add(foreign_workspace)
        await db.flush()
        workspace_id = foreign_workspace.id if scope == "workspace" else env.workspace_id
        if target_type == "commitment":
            target = Commitment(
                workspace_id=workspace_id,
                user_id=foreign_user.id,
                commitment_type="task",
                title="Foreign private card",
                dedupe_key=f"foreign-{uuid4()}",
                created_by="ai",
            )
        else:
            target = OperationalObservation(
                workspace_id=workspace_id,
                user_id=foreign_user.id,
                observation_type="request",
                action_text="Foreign private observation",
                confidence=Decimal("0.950"),
                extractor_version="test",
            )
        db.add(target)
        await db.flush()
        if target_type == "commitment":
            source = CommitmentSource(
                commitment_id=target.id,
                connection_id=env.id,
                provider="google",
                source_type="gmail_message",
                external_resource_id="malformed-cross-scope-edge",
            )
        else:
            source = ObservationEvidence(
                observation_id=target.id,
                connection_id=env.id,
                provider="google",
                source_type="gmail_message",
                external_resource_id="malformed-cross-scope-edge",
                observed_at=datetime.now(UTC),
            )
        db.add(source)
        await db.commit()
        target_id, source_id = target.id, source.id

    disconnected = await env.client.request(
        "DELETE", f"/api/v1/connections/{env.id}", json=command()
    )
    assert disconnected.status_code == 200, disconnected.text
    rejected = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert rejected.status_code == 409, rejected.text
    async with env.factory() as db:
        assert await db.get(type(target), target_id) is not None
        assert await db.get(type(source), source_id) is not None
        assert (await db.get(Connection, env.id)).external_account_id == "account"


@pytest.mark.asyncio
async def test_delete_data_recomputes_shared_commitment_from_surviving_source(env):
    survivor_id, card_id, first_id, second_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with env.factory() as db:
        survivor = Connection(
            id=survivor_id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="survivor",
            status="active",
            granted_scopes=[],
        )
        card = Commitment(
            id=card_id,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            commitment_type="meeting",
            title="removed source text",
            dedupe_key="shared-test",
            created_by="ai",
            intelligence_metadata={"email_relevance": {"removed": "private text"}},
        )
        first = OperationalObservation(
            id=first_id,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            observation_type="meeting",
            action_text="removed",
            object_text="source text",
            confidence=Decimal("0.960"),
            extractor_version="test",
        )
        second = OperationalObservation(
            id=second_id,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            observation_type="request",
            action_text="submit",
            object_text="surviving report",
            confidence=Decimal("0.950"),
            extractor_version="test",
        )
        db.add_all([survivor, card, first, second])
        await db.flush()
        for connection_id, observation_id in ((env.id, first_id), (survivor_id, second_id)):
            db.add_all(
                [
                    ObservationEvidence(
                        observation_id=observation_id,
                        connection_id=connection_id,
                        provider="google",
                        source_type="gmail_message",
                        external_resource_id=str(observation_id),
                        observed_at=datetime.now(UTC),
                    ),
                    CommitmentSource(
                        commitment_id=card_id,
                        connection_id=connection_id,
                        provider="google",
                        source_type="gmail_message",
                        external_resource_id=str(observation_id),
                        source_metadata={"observation_id": str(observation_id)},
                    ),
                ]
            )
        await db.commit()
    await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    response = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert response.status_code == 200, response.text
    async with env.factory() as db:
        card = await db.get(Commitment, card_id)
        assert card.title == "submit surviving report"
        assert card.commitment_type == "task"
        assert card.intelligence_metadata == {"resolution": "SOURCE_RECOMPUTED"}
        assert await db.get(OperationalObservation, first_id) is None
        assert await db.get(OperationalObservation, second_id) is not None
        sources = list(
            await db.scalars(
                select(CommitmentSource).where(CommitmentSource.commitment_id == card_id)
            )
        )
        assert len(sources) == 1 and sources[0].connection_id == survivor_id


@pytest.mark.asyncio
async def test_delete_data_fails_closed_for_unattributed_person_identity(env):
    async with env.factory() as db:
        person = Person(workspace_id=env.workspace_id, canonical_name="Private Person")
        db.add(person)
        await db.flush()
        observation = OperationalObservation(
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            observation_type="request",
            subject_person_id=person.id,
            confidence=Decimal("0.950"),
            extractor_version="test",
        )
        db.add(observation)
        await db.flush()
        db.add_all(
            [
                PersonIdentity(
                    workspace_id=env.workspace_id,
                    person_id=person.id,
                    provider="google",
                    identity_type="email",
                    identity_value="private@example.com",
                ),
                ObservationEvidence(
                    observation_id=observation.id,
                    connection_id=env.id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id="private-message",
                    observed_at=datetime.now(UTC),
                ),
            ]
        )
        await db.commit()
        observation_id = observation.id
    await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    response = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert response.status_code == 409
    async with env.factory() as db:
        assert await db.get(OperationalObservation, observation_id) is not None


@pytest.mark.asyncio
async def test_later_source_does_not_backfill_legacy_identity_provenance(env):
    async with env.factory() as db:
        person = Person(workspace_id=env.workspace_id, canonical_name="Unknown original source")
        db.add(person)
        await db.flush()
        identity = PersonIdentity(
            workspace_id=env.workspace_id,
            person_id=person.id,
            provider="google",
            identity_type="email",
            identity_value="legacy@example.com",
        )
        db.add(identity)
        await db.flush()
        resolved = await resolve_identity(
            db,
            workspace_id=env.workspace_id,
            identity=SourceIdentity(
                identity_type="email", identity_value="legacy@example.com", provider="google"
            ),
            connection_id=env.id,
        )
        assert resolved == person.id
        assert not identity.source_attributed
        assert await db.get(PersonIdentitySource, (identity.id, env.id)) is not None
        await db.commit()

    await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    response = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_delete_data_erases_identity_only_after_last_source_is_removed(env):
    async with env.factory() as db:
        survivor = Connection(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="surviving-identity-source",
            status="active",
            granted_scopes=[],
        )
        db.add(survivor)
        await db.flush()
        survivor_id = survivor.id
        person_id = await resolve_identity(
            db,
            workspace_id=env.workspace_id,
            identity=SourceIdentity(
                identity_type="email",
                identity_value="shared.person@example.com",
                display_name="Name from deleted source",
                provider="google",
            ),
            connection_id=env.id,
        )
        same = await resolve_identity(
            db,
            workspace_id=env.workspace_id,
            identity=SourceIdentity(
                identity_type="email",
                identity_value="SHARED.PERSON@example.com",
                display_name="Name from surviving source",
                provider="google",
            ),
            connection_id=survivor_id,
        )
        assert same == person_id
        identity = await db.scalar(
            select(PersonIdentity).where(PersonIdentity.person_id == person_id)
        )
        assert identity is not None and identity.source_attributed
        identity_id = identity.id
        for connection_id in (env.id, survivor_id):
            observation = OperationalObservation(
                workspace_id=env.workspace_id,
                user_id=env.user_id,
                observation_type="request",
                subject_person_id=person_id,
                confidence=Decimal("0.950"),
                extractor_version="test",
            )
            db.add(observation)
            await db.flush()
            db.add(
                ObservationEvidence(
                    observation_id=observation.id,
                    connection_id=connection_id,
                    provider="google",
                    source_type="gmail_message",
                    external_resource_id=str(observation.id),
                    observed_at=datetime.now(UTC),
                )
            )
        await db.commit()

    await env.client.request("DELETE", f"/api/v1/connections/{env.id}", json=command())
    first = await env.client.post(f"/api/v1/connections/{env.id}/delete-data", json=command())
    assert first.status_code == 200, first.text
    async with env.factory() as db:
        person = await db.get(Person, person_id)
        assert person is not None and person.canonical_name == "shared.person@example.com"
        assert await db.get(PersonIdentity, identity_id) is not None
        support = list(
            await db.scalars(
                select(PersonIdentitySource).where(PersonIdentitySource.identity_id == identity_id)
            )
        )
        assert len(support) == 1 and support[0].connection_id == survivor_id
        assert (
            await db.scalar(
                select(OperationalObservation.id).where(
                    OperationalObservation.subject_person_id == person_id
                )
            )
            is not None
        )

    await env.client.request("DELETE", f"/api/v1/connections/{survivor_id}", json=command())
    last = await env.client.post(f"/api/v1/connections/{survivor_id}/delete-data", json=command())
    assert last.status_code == 200, last.text
    async with env.factory() as db:
        assert await db.get(PersonIdentity, identity_id) is None
        assert await db.get(Person, person_id) is None


@pytest.mark.asyncio
async def test_disconnect_delete_data_erases_import_snapshot_and_source_audit(env):
    async with env.factory() as db:
        definition = ConnectorDefinition(
            connector_key="generic-import",
            version="1.0.0",
            display_name="File imports",
            connector_class="FILE_IMPORT",
            trust_level="NAVOX_FIRST_PARTY",
            manifest={},
        )
        db.add(definition)
        await db.flush()
        row = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="import",
            external_account_id="source-digest",
            display_name="private filename.csv",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=["imports.tabular.read"],
            provider_capabilities=["imports.tabular.read"],
            config={"format": "csv"},
        )
        db.add(row)
        await db.flush()
        retired_subscription = ConnectorSubscription(
            connector_connection_id=row.id,
            subscription_key="imports.changed",
            external_id="retired-channel",
            status="cancelled",
        )
        db.add(retired_subscription)
        await db.flush()
        db.add_all(
            [
                ConnectorEventReceipt(
                    subscription_id=retired_subscription.id,
                    connector_connection_id=row.id,
                    workspace_id=env.workspace_id,
                    user_id=env.user_id,
                    provider="import",
                    event_type="imports.changed",
                    external_event_id="sensitive-event-locator",
                    payload_hash="0" * 64,
                    status="dispatched",
                ),
                ConnectorImportSnapshot(
                    connection_id=row.id,
                    workspace_id=env.workspace_id,
                    user_id=env.user_id,
                    digest="0" * 64,
                    format="csv",
                    record_count=1,
                    encrypted_payload="ENCRYPTED-PRIVATE-SNAPSHOT",
                ),
                AuditEvent(
                    workspace_id=env.workspace_id,
                    user_id=env.user_id,
                    event_type="connector.intelligence.accepted",
                    actor_type="system",
                    entity_type="connector_connection",
                    entity_id=row.id,
                    event_metadata={"source_hash": "source-digest"},
                ),
            ]
        )
        await db.commit()
        identifier = row.id
    first = await env.client.request("DELETE", f"/api/v1/connections/{identifier}", json=command())
    assert first.status_code == 200, first.text
    second = await env.client.post(f"/api/v1/connections/{identifier}/delete-data", json=command())
    assert second.status_code == 200, second.text
    assert "private filename" not in second.text
    async with env.factory() as db:
        assert await db.get(ConnectorImportSnapshot, identifier) is None
        assert (
            await db.scalar(
                select(ConnectorEventReceipt.id).where(
                    ConnectorEventReceipt.connector_connection_id == identifier
                )
            )
            is None
        )
        row = await db.get(ConnectorConnection, identifier)
        assert row.display_name is None and row.config == {}
        assert row.external_account_id == f"deleted:{identifier}"
        prior = await db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.entity_id == identifier,
                AuditEvent.event_type == "connector.intelligence.accepted",
            )
        )
        assert prior is None


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_record", ["run", "resource"])
async def test_delete_data_rejects_partial_ingestion_without_provenance(env, partial_record):
    async with env.factory() as db:
        definition = ConnectorDefinition(
            connector_key="generic-rest-api",
            version="1.0.0",
            display_name="REST",
            connector_class="REST_API",
            trust_level="THIRD_PARTY",
            manifest={},
        )
        db.add(definition)
        await db.flush()
        row = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="rest",
            external_account_id="account",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[],
            provider_capabilities=[],
            config={},
        )
        db.add(row)
        await db.flush()
        if partial_record == "run":
            db.add(
                ConnectorSyncRun(
                    connector_connection_id=row.id,
                    workspace_id=env.workspace_id,
                    request_id=uuid4(),
                    status="running",
                )
            )
        else:
            db.add(
                ConnectorResource(
                    id=uuid4(),
                    workspace_id=env.workspace_id,
                    connector_connection_id=row.id,
                    provider="rest",
                    resource_type="task",
                    external_id="external-resource",
                    canonical={},
                    provider_metadata={},
                    retrieved_at=datetime.now(UTC),
                    content_hash="f" * 64,
                )
            )
        await db.commit()
        identifier = row.id
    assert (
        await env.client.request("DELETE", f"/api/v1/connections/{identifier}", json=command())
    ).status_code == 200
    response = await env.client.post(
        f"/api/v1/connections/{identifier}/delete-data", json=command()
    )
    assert response.status_code == 409
    async with env.factory() as db:
        assert (await db.get(ConnectorConnection, identifier)).external_account_id == "account"


@pytest.mark.asyncio
async def test_pause_resume_replay_preserves_credentials_permissions_cursor_and_global_pause(env):
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        mirror = await ensure_google_connector_connection(db, account)
        mirror.sync_cursor = "OPAQUE-CURSOR"
        mirror.retry_not_before = datetime.now(UTC) + timedelta(minutes=4)
        await db.commit()
        mirror_id, deadline = mirror.id, mirror.retry_not_before
    pause = command()
    response = await env.client.post(f"/api/v1/connections/{env.id}/pause", json=pause)
    assert response.status_code == 200 and response.json()["health"] == "PAUSED"
    assert not any(s["can_sync"] for s in response.json()["sources"])
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, mirror_id)
        generation = row.sync_generation
        assert row.paused_at is not None and row.sync_cursor == "OPAQUE-CURSOR"
        account = await db.get(Connection, env.id)
        assert account.credential_reference == env.credential_id
        assert account.granted_scopes == [GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE]
        user = await db.get(User, env.user_id)
        user.agent_paused = True
        await db.commit()
    response = await env.client.post(f"/api/v1/connections/{env.id}/resume", json=command())
    assert response.status_code == 200 and response.json()["health"] != "PAUSED"
    assert response.json()["agent_paused"] is True
    assert not any(s["can_sync"] for s in response.json()["sources"])
    # Retrying the old pause request cannot undo a later explicit resume.
    replay = await env.client.post(f"/api/v1/connections/{env.id}/pause", json=pause)
    assert replay.json()["health"] != "PAUSED"
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, mirror_id)
        assert row.sync_generation == generation + 1
        assert row.sync_cursor == "OPAQUE-CURSOR"
        assert row.retry_not_before.replace(tzinfo=UTC) == deadline
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 2


@pytest.mark.asyncio
async def test_pause_fences_all_existing_google_sources_without_deleting_checkpoints(env):
    async with env.factory() as db:
        for name in ("gmail", "calendar"):
            definition = ConnectorDefinition(
                connector_key=f"google-{name}",
                version="1.0.0",
                display_name=name,
                connector_class="OAUTH_API",
                trust_level="NAVOX_FIRST_PARTY",
                manifest={},
            )
            db.add(definition)
            await db.flush()
            row = ConnectorConnection(
                id=uuid5(NAMESPACE_URL, f"navox:google-{name}:{env.id}"),
                connector_definition_id=definition.id,
                legacy_connection_id=env.id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                provider="google",
                external_account_id="account",
                status="CONNECTED",
                health_state="CONNECTED",
                authorized_capabilities=[],
                provider_capabilities=[],
                config={},
            )
            db.add(row)
            await db.flush()
            run = ConnectorSyncRun(
                connector_connection_id=row.id,
                workspace_id=env.workspace_id,
                request_id=uuid4(),
                status="running",
                generation=4,
                checkpoint_cursor="PRIVATE-CHECKPOINT",
            )
            db.add(run)
            await db.flush()
            row.sync_run_id, row.sync_generation, row.sync_lease_token = run.id, 4, uuid4()
            row.sync_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        await db.commit()
    assert len(await overview(env)) == 1
    response = await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())
    assert response.status_code == 200
    async with env.factory() as db:
        for run in await db.scalars(select(ConnectorSyncRun)):
            row = await db.get(ConnectorConnection, run.connector_connection_id)
            assert row.sync_generation == 5 and row.sync_lease_token is None
            assert run.status == "interrupted" and run.checkpoint_cursor == "PRIVATE-CHECKPOINT"
    assert "PRIVATE-CHECKPOINT" not in response.text


@pytest.mark.asyncio
async def test_paused_google_health_does_not_make_requests_or_resume_connection(env, monkeypatch):
    await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())

    async def forbidden(*args, **kwargs):
        raise AssertionError("Paused connector contacted Google")

    monkeypatch.setattr(connections, "refresh_google_access_token", forbidden)
    response = await env.client.post(f"/api/v1/connections/google/{env.id}/health")
    assert response.status_code == 200 and response.json()["status"] == "paused"
    assert response.json()["healthy"] is False


@pytest.mark.asyncio
async def test_refresh_mirror_does_not_resume_after_reauthorization(env):
    await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        account.status = "active"  # A successful OAuth result still cannot resume.
        mirror = await ensure_google_connector_connection(db, account)
        assert mirror.status == "PAUSED" and account.status == "paused"
        await db.commit()


@pytest.mark.asyncio
async def test_reauthorize_uses_only_existing_scopes_and_bound_account(env):
    async with env.factory() as db:
        row = await db.get(Connection, env.id)
        row.granted_scopes = [GMAIL_READ_SCOPE]
        await db.commit()
    response = await env.client.post(f"/api/v1/connections/{env.id}/reauthorize", json=command())
    assert response.status_code == 200
    scopes = set(response.json()["requested_scopes"])
    assert GMAIL_READ_SCOPE in scopes and CALENDAR_READ_SCOPE not in scopes
    assert "https://www.googleapis.com/auth/gmail.send" not in scopes
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    async with env.factory() as db:
        attempt = await db.scalar(select(OAuthAuthorizationAttempt))
        assert attempt.connection_id == env.id and attempt.purpose == "reconnect"


@pytest.mark.asyncio
async def test_connect_is_identity_only_and_unavailable_setup_is_fail_closed(env):
    response = await env.client.post("/api/v1/connectors/google-workspace/connect", json=command())
    assert response.status_code == 200
    assert not any("gmail" in s or "calendar" in s for s in response.json()["requested_scopes"])
    assert (await env.client.get("/api/v1/connectors/unknown")).status_code == 404


@pytest.mark.asyncio
async def test_sync_delegates_to_existing_quota_permission_and_dispatch_checks(env, monkeypatch):
    calls = []

    async def dispatch(payload, *, settings, request_id):
        calls.append((payload, request_id))
        return f"intelligence:{payload.connection_id}:{payload.source}:{request_id}"

    monkeypatch.setattr(intelligence_sync, "dispatch_source", dispatch)
    payload = {**command(), "source": "gmail"}
    response = await env.client.post(f"/api/v1/connections/{env.id}/sync", json=payload)
    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert calls[0][0].connection_id == str(env.id)
    assert calls[0][1] == payload["request_id"]
    await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())
    assert (
        await env.client.post(f"/api/v1/connections/{env.id}/sync", json=payload)
    ).status_code == 409
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pause", "resume", "reauthorize", "sync"])
async def test_management_commands_reject_cross_origin_and_unexpected_authority(env, operation):
    payload = command()
    if operation == "sync":
        payload["source"] = "gmail"
    url = f"/api/v1/connections/{env.id}/{operation}"
    assert (
        await env.client.post(url, json=payload, headers={"Origin": "https://attacker.example"})
    ).status_code == 403
    assert (
        await env.client.post(url, json={**payload, "workspace_id": str(uuid4())})
    ).status_code == 422
    assert (await env.client.post(url, data=payload)).status_code == 422


@pytest.mark.parametrize(
    "minutes, expected", [(None, "never_synced"), (10, "fresh"), (120, "stale"), (-10, "unknown")]
)
def test_freshness_is_not_inferred_from_connection_or_oauth_success(minutes, expected):
    now = datetime(2026, 9, 24, tzinfo=UTC)
    assert (
        freshness(now - timedelta(minutes=minutes) if minutes is not None else None, now)
        == expected
    )


@pytest.mark.asyncio
async def test_pause_resume_cannot_restore_failed_authorization(env):
    async with env.factory() as db:
        row = await db.get(Connection, env.id)
        row.status = "needs_reauthorization"
        await db.commit()
    assert (
        await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())
    ).status_code == 200
    assert (
        await env.client.post(f"/api/v1/connections/{env.id}/resume", json=command())
    ).status_code == 200
    async with env.factory() as db:
        row = await db.get(Connection, env.id)
        assert row.status == "needs_reauthorization"
        assert row.credential_reference == env.credential_id
    assert (await overview(env))[0]["health"] == "AUTH_EXPIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_grant", [False, True])
async def test_reconnect_callback_preserves_pause_and_rejects_new_grants(
    env, monkeypatch, extra_grant
):
    # Only previously authorized Gmail and the identity binding scopes are requested.
    scopes = sorted({GMAIL_READ_SCOPE, *connections.GOOGLE_ACCOUNT_BINDING_SCOPES})
    async with env.factory() as db:
        row = await db.get(Connection, env.id)
        row.granted_scopes = scopes
        await db.commit()
    await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())
    response = await env.client.post(f"/api/v1/connections/{env.id}/reauthorize", json=command())
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]

    async def token(*args, **kwargs):
        returned = [*scopes, connections.GOOGLE_GMAIL_SEND_SCOPE] if extra_grant else scopes
        return {
            "access_token": "test-access",
            "refresh_token": "test-refresh",
            "scope": " ".join(returned),
        }

    async def profile(*args, **kwargs):
        return {"sub": "account", "email": "owner@example.com", "email_verified": True}

    monkeypatch.setattr(connections, "exchange_authorization_code", token)
    monkeypatch.setattr(connections, "fetch_google_profile", profile)
    callback = await env.client.get(
        "/api/v1/connections/google/callback", params={"state": state, "code": "synthetic-code"}
    )
    assert callback.status_code == 303
    assert ("scope_mismatch" if extra_grant else "reconnected") in callback.headers["location"]
    async with env.factory() as db:
        row = await db.get(Connection, env.id)
        assert row.status == "paused"
        assert row.granted_scopes == scopes
        assert (row.credential_reference == env.credential_id) is extra_grant
    assert (await overview(env))[0]["health"] == "PAUSED"


@pytest.mark.asyncio
async def test_successful_reconnect_can_resume_despite_stale_source_auth_error(env):
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        account.status = "needs_reauthorization"
        await ensure_google_connector_connection(db, account)
        definition = ConnectorDefinition(
            connector_key="google-gmail",
            version="1.0.0",
            display_name="Gmail",
            connector_class="OAUTH_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest={},
        )
        db.add(definition)
        await db.flush()
        row = ConnectorConnection(
            connector_definition_id=definition.id,
            legacy_connection_id=env.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="account",
            status="CONNECTED",
            health_state="AUTH_EXPIRED",
            last_error_code="AUTH_EXPIRED",
            authorized_capabilities=["communication.messages.read"],
            provider_capabilities=["communication.messages.read"],
            config={},
        )
        db.add(row)
        await db.commit()
    await env.client.post(f"/api/v1/connections/{env.id}/pause", json=command())
    # Reproduce the already verified callback's successful credential authority update.
    async with env.factory() as db:
        account = await db.get(Connection, env.id)
        account.status, account.last_error = "active", None
        await ensure_google_connector_connection(db, account)
        await db.commit()
    response = await env.client.post(f"/api/v1/connections/{env.id}/resume", json=command())
    assert response.status_code == 200
    gmail = next(s for s in response.json()["sources"] if s["id"] == "gmail")
    assert gmail["health"] != "AUTH_EXPIRED" and gmail["can_sync"] is True
    async with env.factory() as db:
        assert (await db.get(Connection, env.id)).status == "active"
