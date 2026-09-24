import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api import connections, intelligence_sync
from navox.api.main import create_app
from navox.connectors.builtin.google import ensure_google_connector_connection
from navox.connectors.management import freshness
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorSyncRun,
    OAuthAuthorizationAttempt,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.session import get_database_session
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
    assert [e["id"] for e in entries if e["availability"] == "available"] == ["google-workspace"]
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
