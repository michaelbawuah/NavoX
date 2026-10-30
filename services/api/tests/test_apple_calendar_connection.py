"""Actual native authorization transactions with a synthetic Apple transport."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from navox.api import apple_calendars as api
from navox.api.auth import hash_session_token
from navox.api.main import create_app
from navox.core.credential_vault import CredentialVault
from navox.core.settings import Settings, get_settings
from navox.db.apple_calendars import AppleCalendarGrant
from navox.db.models import Connection, ConnectionCredential, OAuthAuthorizationAttempt, UserSession
from navox.db.session import get_database_session
from navox.db.student_calendars import StudentCalendarState
from navox.providers.apple_availability import AppleAvailabilitySnapshot
from navox.student import service as schedule
from navox.student_calendars import apple
from navox.student_calendars import service as calendars

BASE = "/api/v1/student/calendars/apple"
ACCOUNT = "synthetic-apple@example.test"
PASSWORD = "aaaa-bbbb-cccc-dddd"


@pytest_asyncio.fixture
async def apple_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        student_calendar_availability_enabled=True,
        google_token_encryption_key=Fernet.generate_key().decode(),
    )

    async def database():
        async with ai_database() as session:
            yield session

    control = SimpleNamespace(state="complete", hook=None, reads=0)

    class Provider:
        async def collect(self, *, apple_id, app_password, start_at, end_at, check):
            assert apple_id == ACCOUNT and app_password == PASSWORD
            await check()
            control.reads += 1
            if control.hook:
                await control.hook()
            await check()
            return AppleAvailabilitySnapshot(control.state, 2, (), start_at)

    monkeypatch.setattr(apple, "AppleAvailabilityProvider", Provider)
    app = create_app()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "synthetic-owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Calendar student",
            },
        )
        assert response.status_code == 201
        account = response.json()
        user_id, workspace_id = UUID(account["id"]), UUID(account["workspace"]["id"])
        yield SimpleNamespace(
            app=app,
            client=client,
            settings=settings,
            factory=ai_database,
            control=control,
            user_id=user_id,
            workspace_id=workspace_id,
            token_hash=hash_session_token(client.cookies.get(settings.session_cookie_name)),
            headers={"Origin": settings.web_origin, "X-NavoX-Account": f"{workspace_id}:{user_id}"},
        )


async def status(env):
    response = await env.client.get(BASE, headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def payload(env, **changes):
    current = await status(env)
    return {
        "generation": current["generation"],
        "revision": current["revision"],
        "request_id": str(uuid4()),
        "consent_version": apple.CONSENT_VERSION,
        "confirmed": True,
        "apple_id": ACCOUNT,
        "app_password": PASSWORD,
        **changes,
    }


async def connect(env, **changes):
    body = await payload(env, **changes)
    response = await env.client.post(BASE + "/connect", json=body, headers=env.headers)
    return response, body


@pytest.mark.asyncio
async def test_real_grant_and_encrypted_owner_bound_credential_without_fake_oauth(apple_env):
    env = apple_env
    response, body = await connect(env)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["revision"] == body["revision"] + 1
    assert current["grants"][0]["provider"] == "icloud"
    assert current["grants"][0]["status"] == "connected"
    assert response.headers["cache-control"] == "private, no-store"
    assert PASSWORD not in response.text and "encrypted_refresh_token" not in response.text
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AppleCalendarGrant)) == 1
        assert await db.scalar(select(func.count()).select_from(OAuthAuthorizationAttempt)) == 0
        credential = await db.scalar(select(ConnectionCredential))
        assert PASSWORD not in credential.encrypted_refresh_token
        value = CredentialVault(env.settings).reveal_refresh_token(
            credential.encrypted_refresh_token
        )
        assert PASSWORD in value and str(env.user_id) in value and str(env.workspace_id) in value


@pytest.mark.asyncio
async def test_exact_connect_retry_does_not_read_or_duplicate_credentials(apple_env):
    env = apple_env
    first, body = await connect(env)
    second = await env.client.post(BASE + "/connect", json=body, headers=env.headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() and env.control.reads == 1
    changed = await env.client.post(
        BASE + "/connect", json={**body, "app_password": "eeee-ffff-gggg-hhhh"}, headers=env.headers
    )
    assert changed.status_code == 409 and env.control.reads == 1
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 1


@pytest.mark.asyncio
async def test_reconnect_rotates_and_erases_the_previous_credential(apple_env):
    env = apple_env
    first, _ = await connect(env)
    async with env.factory() as db:
        old_id = await db.scalar(select(ConnectionCredential.id))
    second, _ = await connect(env)
    assert first.status_code == second.status_code == 200
    assert first.json()["grants"][0]["id"] == second.json()["grants"][0]["id"]
    assert second.json()["grants"][0]["revision"] == 2
    async with env.factory() as db:
        assert await db.get(ConnectionCredential, old_id) is None
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["partial", "unavailable"])
async def test_unverified_provider_access_does_not_save_grants_or_credentials(apple_env, state):
    env = apple_env
    env.control.state = state
    response, _ = await connect(env)
    assert response.status_code == 503
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AppleCalendarGrant)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0
        assert (
            await db.scalar(
                select(func.count()).select_from(Connection).where(Connection.provider == "icloud")
            )
            == 0
        )


@pytest.mark.asyncio
async def test_state_change_during_provider_io_fences_connection_publication(apple_env):
    env = apple_env

    async def change():
        async with env.factory() as db:
            row = await db.scalar(select(StudentCalendarState))
            row.revision += 1
            await db.commit()

    env.control.hook = change
    response, _ = await connect(env)
    assert response.status_code == 409
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AppleCalendarGrant)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0


@pytest.mark.asyncio
async def test_logout_during_provider_io_cannot_create_a_late_connection(apple_env):
    env = apple_env

    async def change():
        async with env.factory() as db:
            row = await db.scalar(
                select(UserSession).where(UserSession.token_hash == env.token_hash)
            )
            from navox.connectors.sync_state import database_now

            row.revoked_at = await database_now(db)
            await db.commit()

    env.control.hook = change
    response, _ = await connect(env)
    assert response.status_code == 401
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0


@pytest.mark.asyncio
async def test_disconnect_erases_grant_and_credential_even_while_admission_disabled(apple_env):
    env = apple_env
    response, _ = await connect(env)
    current = response.json()
    env.settings.student_calendar_availability_enabled = False
    response = await env.client.post(
        BASE + "/" + current["grants"][0]["id"] + "/disconnect",
        json={
            "generation": current["generation"],
            "revision": current["revision"],
            "request_id": str(uuid4()),
        },
        headers=env.headers,
    )
    assert response.status_code == 200 and response.json()["grants"] == []
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AppleCalendarGrant)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0
        connection = await db.scalar(select(Connection).where(Connection.provider == "icloud"))
        assert connection.credential_reference is None and connection.status == "disconnected"


@pytest.mark.asyncio
async def test_reset_cleanup_erases_credentials_and_old_commands_cannot_reconnect(apple_env):
    env = apple_env
    response, old = await connect(env)
    assert response.status_code == 200
    async with env.factory() as db:
        await calendars.boundary(db, env.user_id, env.workspace_id, env.token_hash)
        await apple.forget_calendar_data(db, env.user_id, env.workspace_id)
        await calendars.forget_calendar_data(db, env.user_id, env.workspace_id)
        await db.commit()
    assert (await status(env))["grants"] == []
    replay = await env.client.post(BASE + "/connect", json=old, headers=env.headers)
    assert replay.status_code == 409
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0


@pytest.mark.asyncio
async def test_snapshot_credential_envelope_rejects_other_owner_and_connection(apple_env):
    env = apple_env
    response, _ = await connect(env)
    assert response.status_code == 200
    async with env.factory() as db:
        current = await calendars.boundary(db, env.user_id, env.workspace_id, env.token_hash)
        grants = await apple.snapshots(db, env.user_id, env.workspace_id, current)
        scope = calendars.CalendarScope(
            env.user_id, env.workspace_id, current.generation, current.revision, grants
        )
        account, password = await apple.credentials(db, scope, grants[0], env.settings)
        assert (account, password) == (ACCOUNT, PASSWORD)
        with pytest.raises(schedule.StudentError):
            await apple.credentials(db, replace(scope, user_id=uuid4()), grants[0], env.settings)
        changed_grant = replace(grants[0], connection_id=uuid4())
        with pytest.raises(schedule.StudentError):
            await apple.credentials(
                db, replace(scope, grants=(changed_grant,)), changed_grant, env.settings
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, 1, "true"])
async def test_confirmation_and_secret_validation_never_echo_private_input(apple_env, confirmed):
    env = apple_env
    body = await payload(env, confirmed=confirmed)
    response = await env.client.post(BASE + "/connect", json=body, headers=env.headers)
    assert (
        response.status_code == 422
        and PASSWORD not in response.text
        and ACCOUNT not in response.text
    )
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_missing_cookie_account_origin_and_bearer_are_rejected(apple_env):
    env = apple_env
    body = await payload(env)
    assert (await env.client.get(BASE)).status_code == 409
    assert (
        await env.client.post(
            BASE + "/connect",
            json=body,
            headers={"X-NavoX-Account": env.headers["X-NavoX-Account"]},
        )
    ).status_code == 403
    assert (
        await env.client.get(BASE, headers={**env.headers, "Authorization": "Bearer private-token"})
    ).status_code == 403
    env.settings.student_calendar_availability_enabled = False
    assert (
        await env.client.post(BASE + "/connect", json=body, headers=env.headers)
    ).status_code == 404
    env.client.cookies.clear()
    assert (await env.client.get(BASE, headers=env.headers)).status_code == 401


@pytest.mark.asyncio
async def test_invalid_body_and_missing_encryption_fail_before_provider(apple_env):
    env = apple_env
    body = await payload(env, app_password="primary-password-private")
    response = await env.client.post(BASE + "/connect", json=body, headers=env.headers)
    assert response.status_code == 422 and "primary-password-private" not in response.text
    env.settings.google_token_encryption_key = None
    response, _ = await connect(env)
    assert response.status_code == 503 and env.control.reads == 0


@pytest.mark.asyncio
async def test_provider_cancellation_persists_no_credential_or_grant(apple_env):
    env = apple_env
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Event().wait()

    env.control.hook = wait
    body = apple.AppleCalendarConnect.model_validate(await payload(env))
    async with env.factory() as db:
        task = asyncio.create_task(
            apple.connect(db, env.user_id, env.workspace_id, env.token_hash, body, env.settings)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 0


@pytest.mark.asyncio
async def test_other_account_cannot_read_or_disconnect_the_apple_connection(apple_env):
    env = apple_env
    response, _ = await connect(env)
    grant_id = response.json()["grants"][0]["id"]
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as other:
        registered = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "other-owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Other student",
            },
        )
        assert registered.status_code == 201
        account = registered.json()
        headers = {
            "Origin": env.settings.web_origin,
            "X-NavoX-Account": f"{account['workspace']['id']}:{account['id']}",
        }
        current = await other.get(BASE, headers=headers)
        assert current.status_code == 200 and current.json()["grants"] == []
        response = await other.post(
            BASE + "/" + grant_id + "/disconnect",
            json={"generation": 0, "revision": 0, "request_id": str(uuid4())},
            headers=headers,
        )
        assert response.status_code == 404 and ACCOUNT not in response.text
    assert len((await status(env))["grants"]) == 1


@pytest.mark.asyncio
async def test_read_requires_fresh_combined_scope_and_is_fenced_by_disconnect(
    apple_env, monkeypatch
):
    env = apple_env
    response, _ = await connect(env)
    assert response.status_code == 200
    original = calendars.scope_snapshot

    async def combined(database, user_id, workspace_id, session_hash, *, lock=True):
        native = await original(database, user_id, workspace_id, session_hash, lock=lock)
        current = await calendars.boundary(database, user_id, workspace_id, session_hash, lock=lock)
        existing = tuple(
            item for item in native.grants if getattr(item, "provider", "google") != "icloud"
        )
        return replace(
            native,
            grants=existing + await apple.snapshots(database, user_id, workspace_id, current),
        )

    # This is the explicitly required common-service wiring. The test exercises
    # real native boundaries and records its synthetic Apple provider separately.
    monkeypatch.setattr(calendars, "scope_snapshot", combined)
    async with env.factory() as db:
        captured = await combined(db, env.user_id, env.workspace_id, env.token_hash)
        grant = captured.grants[0]
        current = await calendars.boundary(db, env.user_id, env.workspace_id, env.token_hash)
        first = current.now.replace(microsecond=0)
        snapshot = await apple.read(
            db,
            captured,
            grant,
            env.settings,
            start_at=first,
            end_at=first + timedelta(hours=1),
            session_hash=env.token_hash,
        )
        assert snapshot.state == "complete"
        await db.rollback()

        async def disconnect_during_read():
            async with env.factory() as fresh:
                await apple.disconnect(
                    fresh,
                    env.user_id,
                    env.workspace_id,
                    env.token_hash,
                    grant.id,
                    apple.CalendarCommand(
                        generation=captured.generation,
                        revision=captured.revision,
                        request_id=uuid4(),
                    ),
                    env.settings,
                )

        env.control.hook = disconnect_during_read
        with pytest.raises(schedule.StudentError) as raised:
            await apple.read(
                db,
                captured,
                grant,
                env.settings,
                start_at=first,
                end_at=first + timedelta(hours=1),
                session_hash=env.token_hash,
            )
        assert raised.value.status_code == 409
