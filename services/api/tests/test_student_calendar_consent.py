"""Synthetic OAuth and real native transactions; no live provider qualification."""

import asyncio
import os
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from navox.api.auth import hash_session_token
from navox.api.main import create_app
from navox.connectors.builtin.google import ensure_google_connector_connection
from navox.connectors.lifecycle import disconnect as disconnect_google
from navox.core.credential_vault import CredentialVault
from navox.core.settings import Settings, get_settings
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt, User
from navox.db.session import get_database_session
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarState
from navox.student import service as schedule
from navox.student_calendars import oauth, service
from navox.student_calendars.schemas import AVAILABILITY_SCOPES, CONSENT_VERSION

BASE = "/api/v1/student/calendars"
IDENTITY = ["openid", "https://www.googleapis.com/auth/userinfo.email"]
GMAIL = "https://www.googleapis.com/auth/gmail.send"


@pytest_asyncio.fixture
async def calendar_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        student_calendar_availability_enabled=True,
        google_oauth_client_id="synthetic-calendar-client",
        google_oauth_client_secret="synthetic-calendar-secret",
        google_token_encryption_key=Fernet.generate_key().decode(),
    )

    async def database():
        async with ai_database() as session:
            yield session

    async def exchange(*_):
        return {
            "access_token": "synthetic-access-never-persist",
            "refresh_token": "synthetic-refresh-private",
            "scope": " ".join([*IDENTITY, *AVAILABILITY_SCOPES]),
            "expires_in": 3600,
        }

    async def profile(*_):
        return {
            "sub": "synthetic-google-calendar",
            "email": "calendar@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(oauth, "exchange_authorization_code", exchange)
    monkeypatch.setattr(oauth, "fetch_profile", profile)
    app = create_app()
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        registered = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "calendar-native@example.com",
                "password": "twelve-character-password",
                "display_name": "Calendar student",
            },
        )
        assert registered.status_code == 201
        account = registered.json()
        user_id, workspace_id = UUID(account["id"]), UUID(account["workspace"]["id"])
        headers = {"Origin": settings.web_origin, "X-NavoX-Account": f"{workspace_id}:{user_id}"}
        yield SimpleNamespace(
            settings=settings,
            client=client,
            app=app,
            factory=ai_database,
            user_id=user_id,
            workspace_id=workspace_id,
            headers=headers,
            token_hash=hash_session_token(client.cookies.get(settings.session_cookie_name)),
        )


async def status(env):
    response = await env.client.get(BASE, headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def start(env, **overrides):
    view = await status(env)
    body = {
        "generation": view["generation"],
        "revision": view["revision"],
        "request_id": str(uuid4()),
        "consent_version": CONSENT_VERSION,
        "confirmed": True,
        **overrides,
    }
    response = await env.client.post(BASE + "/google/start", json=body, headers=env.headers)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return response.json(), query, body


async def callback(env, query):
    return await env.client.get(
        "/api/v1/connections/google/callback",
        params={"code": "synthetic-code", "state": query["state"][0]},
    )


async def disconnect(env, grant_id):
    view = await status(env)
    return await env.client.post(
        BASE + "/" + grant_id + "/disconnect",
        json={
            "generation": view["generation"],
            "revision": view["revision"],
            "request_id": str(uuid4()),
        },
        headers=env.headers,
    )


@pytest.mark.asyncio
async def test_cookie_account_origin_flag_and_private_guards(calendar_env):
    env = calendar_env
    good = await env.client.get(BASE, headers=env.headers)
    assert good.headers["cache-control"] == "private, no-store"
    assert (await env.client.get(BASE)).status_code == 409
    assert (
        await env.client.get(BASE, headers={**env.headers, "Authorization": "Bearer anything"})
    ).status_code == 403
    assert (
        await env.client.post(
            BASE + "/google/start",
            json={},
            headers={"X-NavoX-Account": env.headers["X-NavoX-Account"]},
        )
    ).status_code == 403
    env.settings.student_calendar_availability_enabled = False
    assert (await env.client.get(BASE, headers=env.headers)).status_code == 404
    env.client.cookies.clear()
    assert (await env.client.get(BASE, headers=env.headers)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, 1, "true"])
async def test_confirmation_is_explicit_and_validation_is_sanitized(calendar_env, confirmed):
    response = await calendar_env.client.post(
        BASE + "/google/start",
        json={
            "generation": 0,
            "revision": 0,
            "request_id": str(uuid4()),
            "consent_version": CONSENT_VERSION,
            "confirmed": confirmed,
            "private_input": "must-not-be-echoed",
        },
        headers=calendar_env.headers,
    )
    assert response.status_code == 422
    assert "must-not-be-echoed" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_database_failure_cannot_log_or_echo_private_bound_values(
    calendar_env, monkeypatch, caplog
):
    async def failed_start(*_):
        raise SQLAlchemyError("synthetic-private-oauth-value")

    monkeypatch.setattr(oauth, "start", failed_start)
    response = await calendar_env.client.post(
        BASE + "/google/start",
        json={
            "generation": 0,
            "revision": 0,
            "request_id": str(uuid4()),
            "consent_version": CONSENT_VERSION,
            "confirmed": True,
        },
        headers=calendar_env.headers,
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert "synthetic-private-oauth-value" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_start_requests_only_calendar_list_busy_and_bound_identity(calendar_env):
    env = calendar_env
    response, query, body = await start(env)
    assert response["requested_scopes"] == [*IDENTITY, *AVAILABILITY_SCOPES]
    assert query["scope"] == [" ".join(response["requested_scopes"])]
    assert query["code_challenge_method"] == ["S256"]
    assert query["include_granted_scopes"] == ["true"]
    assert not any(
        "gmail" in scope or scope.endswith("/calendar") for scope in response["requested_scopes"]
    )
    async with env.factory() as database:
        snapshot = await service.scope_snapshot(
            database, env.user_id, env.workspace_id, env.token_hash
        )
        assert len(snapshot.grants) == 1 and snapshot.grants[0].status == "pending"
        assert snapshot.grants[0].connection_id is None
    replay = await env.client.post(BASE + "/google/start", json=body, headers=env.headers)
    assert replay.status_code == 409


@pytest.mark.asyncio
async def test_callback_creates_encrypted_purpose_connection_without_event_or_gmail_grants(
    calendar_env,
):
    env = calendar_env
    started, query, _ = await start(env)
    response = await callback(env, query)
    assert response.status_code == 303 and response.headers["location"].endswith(
        "calendar_availability_enabled"
    )
    assert response.headers["cache-control"] == "private, no-store"
    view = await status(env)
    assert view["grants"][0]["status"] == "connected"
    async with env.factory() as database:
        grant = await database.get(StudentCalendarGrant, UUID(started["grant_id"]))
        connection = await database.get(Connection, grant.connection_id)
        credential = await database.get(ConnectionCredential, connection.credential_reference)
        assert connection.status == "active"
        assert set(connection.granted_scopes) == set([*IDENTITY, *AVAILABILITY_SCOPES])
        assert "synthetic-refresh-private" not in credential.encrypted_refresh_token
        assert (
            CredentialVault(env.settings).reveal_refresh_token(credential.encrypted_refresh_token)
            == "synthetic-refresh-private"
        )
        assert (
            await service.scope_snapshot(database, env.user_id, env.workspace_id, env.token_hash)
        ).grants[0].status == "active"
    assert (await callback(env, query)).status_code == 400


@pytest.mark.asyncio
async def test_withdrawal_while_disabled_preserves_existing_google_gmail_connection(
    calendar_env, monkeypatch
):
    env = calendar_env
    async with env.factory() as database:
        credential = ConnectionCredential(
            encrypted_refresh_token=CredentialVault(env.settings).seal_refresh_token(
                "old-gmail-refresh"
            )
        )
        database.add(credential)
        await database.flush()
        connection = Connection(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="synthetic-google-calendar",
            external_email="calendar@example.com",
            status="active",
            granted_scopes=[*IDENTITY, GMAIL],
            credential_reference=credential.id,
        )
        database.add(connection)
        await database.commit()
        connection_id = connection.id

    async def exchange(*_):
        return {
            "access_token": "synthetic-access",
            "refresh_token": "new-refresh",
            "scope": " ".join([*IDENTITY, GMAIL, *AVAILABILITY_SCOPES]),
        }

    monkeypatch.setattr(oauth, "exchange_authorization_code", exchange)
    started, query, _ = await start(env, connection_id=str(connection_id))
    assert (
        (await callback(env, query)).headers["location"].endswith("calendar_availability_enabled")
    )
    env.settings.student_calendar_availability_enabled = False
    response = await env.client.post(
        BASE + "/" + started["grant_id"] + "/disconnect",
        json={"generation": 0, "revision": 2, "request_id": str(uuid4())},
        headers=env.headers,
    )
    assert response.status_code == 200 and response.json()["grants"][0]["status"] == "revoked"
    async with env.factory() as database:
        connection = await database.get(Connection, connection_id)
        assert connection.status == "active" and GMAIL in connection.granted_scopes
        assert connection.credential_reference is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disconnect", "reset", "logout"])
async def test_late_profile_cannot_restore_permission_or_store_credentials(
    calendar_env, monkeypatch, change
):
    env = calendar_env
    started, query, body = await start(env)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_profile(*_):
        entered.set()
        await release.wait()
        return {"sub": "late-google", "email": "late@example.com", "email_verified": True}

    monkeypatch.setattr(oauth, "fetch_profile", slow_profile)
    task = asyncio.create_task(callback(env, query))
    await asyncio.wait_for(entered.wait(), 5)
    if change == "disconnect":
        assert (await disconnect(env, started["grant_id"])).status_code == 200
    elif change == "reset":
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
            headers=env.headers,
        )
        assert response.status_code == 200, response.text
    else:
        assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    release.set()
    response = await asyncio.wait_for(task, 5)
    assert response.headers["location"].endswith("failed")
    async with env.factory() as database:
        assert list(await database.scalars(select(ConnectionCredential))) == []
        assert list(await database.scalars(select(Connection))) == []
        grant = await database.get(StudentCalendarGrant, UUID(started["grant_id"]))
        assert grant is None or grant.status != "active"
        if change == "reset":
            state = await service.state(database, env.user_id, env.workspace_id)
            assert state.generation == 1
    if change != "logout":
        replay = await env.client.post(BASE + "/google/start", json=body, headers=env.headers)
        assert replay.status_code == 409


@pytest.mark.asyncio
async def test_scope_proof_rejects_new_inventory_and_pause(calendar_env):
    env = calendar_env
    async with env.factory() as database:
        snapshot = await service.scope_snapshot(
            database, env.user_id, env.workspace_id, env.token_hash
        )
        await database.commit()
    await start(env)
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError, match="permissions changed"):
            await service.check_scope(database, snapshot, env.token_hash)
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.agent_paused = True
        await database.commit()
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError, match="Resume NavoX"):
            await service.scope_snapshot(database, env.user_id, env.workspace_id, env.token_hash)


@pytest.mark.asyncio
async def test_same_owner_new_login_cannot_complete_old_cookie_authorization(
    calendar_env, monkeypatch
):
    env = calendar_env
    _, query, _ = await start(env)
    response = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "calendar-native@example.com", "password": "twelve-character-password"},
    )
    assert response.status_code == 200

    async def forbidden_exchange(*_):
        raise AssertionError("The old cookie's callback must fail before provider I/O")

    monkeypatch.setattr(oauth, "exchange_authorization_code", forbidden_exchange)
    assert (await callback(env, query)).headers["location"].endswith("failed")
    async with env.factory() as database:
        assert list(await database.scalars(select(ConnectionCredential))) == []
        assert list(await database.scalars(select(Connection))) == []


@pytest.mark.asyncio
async def test_wrong_google_account_and_missing_scopes_never_activate(calendar_env, monkeypatch):
    env = calendar_env
    async with env.factory() as database:
        credential = ConnectionCredential(encrypted_refresh_token="unchanged-encrypted")
        database.add(credential)
        await database.flush()
        connection = Connection(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="expected-account",
            external_email="expected@example.com",
            status="active",
            granted_scopes=[*IDENTITY, GMAIL],
            credential_reference=credential.id,
        )
        database.add(connection)
        await database.commit()
    _, query, _ = await start(env, connection_id=str(connection.id))
    assert (await callback(env, query)).headers["location"].endswith("account_mismatch")

    async def narrowed(*_):
        return {"access_token": "narrowed", "scope": " ".join(IDENTITY)}

    monkeypatch.setattr(oauth, "exchange_authorization_code", narrowed)
    _, query, _ = await start(env, connection_id=str(connection.id))
    assert (await callback(env, query)).headers["location"].endswith("scope_mismatch")
    async with env.factory() as database:
        row = await database.get(Connection, connection.id)
        assert row.granted_scopes == [*IDENTITY, GMAIL]
        assert row.credential_reference == credential.id


@pytest.mark.asyncio
async def test_reset_without_prior_calendar_state_fences_late_first_start(calendar_env):
    env = calendar_env
    before = await status(env)
    assert before["generation"] == 0
    response = await env.client.post(
        "/api/v1/student/data/reset",
        json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
        headers=env.headers,
    )
    assert response.status_code == 200
    assert (await status(env))["generation"] == 1
    response = await env.client.post(
        BASE + "/google/start",
        json={
            "generation": 0,
            "revision": 0,
            "request_id": str(uuid4()),
            "consent_version": CONSENT_VERSION,
            "confirmed": True,
        },
        headers=env.headers,
    )
    assert response.status_code == 409
    async with env.factory() as database:
        assert list(await database.scalars(select(OAuthAuthorizationAttempt))) == []
        assert len(list(await database.scalars(select(StudentCalendarState)))) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Observed PostgreSQL row-lock ordering requires the isolated test database",
)
async def test_postgres_generic_disconnect_wins_before_callback_native_lock(
    calendar_env, monkeypatch
):
    """Observe the callback waiting on the connector, never taking User first."""
    env = calendar_env
    async with env.factory() as database:
        credential = ConnectionCredential(
            encrypted_refresh_token=CredentialVault(env.settings).seal_refresh_token("old-refresh")
        )
        database.add(credential)
        await database.flush()
        connection = Connection(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="synthetic-google-calendar",
            external_email="calendar@example.com",
            status="active",
            granted_scopes=[*IDENTITY, GMAIL],
            credential_reference=credential.id,
        )
        database.add(connection)
        await database.flush()
        await ensure_google_connector_connection(database, connection)
        await database.commit()
    _, query, _ = await start(env, connection_id=str(connection.id))
    pids = {}
    entered_disconnect, entered_callback = asyncio.Event(), asyncio.Event()
    original_pending = oauth.pending

    async def observed_pending(database, *args, **kwargs):
        pids["callback"] = await database.scalar(text("SELECT pg_backend_pid()"))
        entered_callback.set()
        return await original_pending(database, *args, **kwargs)

    monkeypatch.setattr(oauth, "pending", observed_pending)

    async def retire_google():
        async with env.factory() as database:
            pids["disconnect"] = await database.scalar(text("SELECT pg_backend_pid()"))
            entered_disconnect.set()
            await disconnect_google(
                database,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=connection.id,
                request_id=uuid4(),
            )

    async def blocked(waiter, blocker):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            async with env.factory() as observer:
                blockers = await observer.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter}
                )
            if blocker in blockers:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("Expected PostgreSQL lock dependency was not observed")

    tasks = []
    try:
        async with env.factory() as guard:
            guard_pid = await guard.scalar(text("SELECT pg_backend_pid()"))
            await guard.scalar(select(User).where(User.id == env.user_id).with_for_update())
            retirement = asyncio.create_task(retire_google())
            tasks.append(retirement)
            await asyncio.wait_for(entered_disconnect.wait(), 5)
            await blocked(pids["disconnect"], guard_pid)
            completion = asyncio.create_task(callback(env, query))
            tasks.append(completion)
            await asyncio.wait_for(entered_callback.wait(), 5)
            await blocked(pids["callback"], pids["disconnect"])
            await guard.commit()
        await asyncio.wait_for(retirement, 8)
        response = await asyncio.wait_for(completion, 8)
        assert response.headers["location"].endswith("failed")
        async with env.factory() as database:
            current = await database.get(Connection, connection.id)
            grant = await database.scalar(select(StudentCalendarGrant))
            assert current.status == "disconnected"
            assert grant.status != "active"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
