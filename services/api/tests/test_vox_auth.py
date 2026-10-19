"""Real authentication cookies revoke only their own reserved Vox calls."""

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

from navox.api import auth
from navox.api.main import create_app
from navox.connectors.sync_state import utc
from navox.core.settings import Settings, get_settings
from navox.db.models import User, UserSession, WorkspaceMembership
from navox.db.session import get_database_session
from navox.db.vox import VoxCall, VoxState

PASSWORD = "twelve-character-password"


@pytest_asyncio.fixture
async def vox_auth_env(ai_database):
    settings = Settings(_env_file=None, app_environment="test", ai_provider="disabled")

    async def database():
        async with ai_database() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncExitStack() as stack:

        async def client():
            return await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
            )

        yield SimpleNamespace(
            client=await client(),
            new_client=client,
            factory=ai_database,
            settings=settings,
        )


async def register(client, email="vox-auth-a@example.com"):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Synthetic Vox student"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login(client, email):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


def cookie(env, client=None):
    return (client or env.client).cookies.get(env.settings.session_cookie_name)


def cookie_header(env, token):
    return {"Cookie": f"{env.settings.session_cookie_name}={token}"}


async def auth_session(env, token):
    async with env.factory() as database:
        session = await database.scalar(
            select(UserSession).where(UserSession.token_hash == auth.hash_session_token(token))
        )
        assert session is not None
        return session


async def seed_call(env, account, token):
    session = await auth_session(env, token)
    user_id = UUID(account["id"])
    workspace_id = UUID(account["workspace"]["id"])
    now = datetime.now(UTC)
    async with env.factory() as database:
        state = await database.scalar(
            select(VoxState).where(
                VoxState.user_id == user_id, VoxState.workspace_id == workspace_id
            )
        )
        if state is None:
            state = VoxState(id=uuid4(), user_id=user_id, workspace_id=workspace_id, generation=0)
            database.add(state)
        call = VoxCall(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            auth_session_id=session.id,
            client_call_id=uuid4(),
            generation=state.generation,
            status="reserved",
            created_at=now,
            expires_at=now + timedelta(seconds=120),
        )
        database.add(call)
        await database.commit()
        return call.id


async def read_call(env, call_id):
    async with env.factory() as database:
        call = await database.get(VoxCall, call_id)
        assert call is not None
        state = await database.scalar(
            select(VoxState).where(
                VoxState.user_id == call.user_id, VoxState.workspace_id == call.workspace_id
            )
        )
        assert state is not None
        return call, state.generation


@pytest.mark.asyncio
async def test_web_logout_revokes_call_with_feature_off_pause_and_membership_removed(vox_auth_env):
    env = vox_auth_env
    account = await register(env.client)
    token = cookie(env)
    call_id = await seed_call(env, account, token)
    async with env.factory() as database:
        user = await database.get(User, UUID(account["id"]))
        user.agent_paused = True
        await database.execute(
            delete(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        )
        await database.commit()
    response = await env.client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    call, generation = await read_call(env, call_id)
    assert (call.status, call.end_reason, generation) == ("ended", "sign-out", 1)
    assert (await auth_session(env, token)).revoked_at is not None
    for previous in (token, "unknown-synthetic-cookie"):
        response = await env.client.post(
            "/api/v1/auth/logout", headers=cookie_header(env, previous)
        )
        assert response.status_code == 200
    assert (await read_call(env, call_id))[1] == generation


@pytest.mark.asyncio
@pytest.mark.parametrize("same_owner", [True, False])
async def test_successful_login_replaces_only_presented_cookie_and_ends_its_call(
    vox_auth_env, same_owner
):
    env = vox_auth_env
    first = await register(env.client)
    old_token = cookie(env)
    unrelated = await env.new_client()
    if same_owner:
        assert (await login(unrelated, first["email"])).status_code == 200
        target = first
    else:
        target = await register(unrelated, "vox-auth-b@example.com")
    unrelated_token = cookie(env, unrelated)
    call_id = await seed_call(env, first, old_token)
    response = await login(env.client, target["email"])
    assert response.status_code == 200, response.text
    assert response.json()["id"] == target["id"]
    assert cookie(env) != old_token
    assert (await auth_session(env, old_token)).revoked_at is not None
    assert (await auth_session(env, unrelated_token)).revoked_at is None
    assert (await unrelated.get("/api/v1/auth/me")).status_code == 200
    assert (
        await env.client.get("/api/v1/auth/me", headers=cookie_header(env, old_token))
    ).status_code == 401
    call, generation = await read_call(env, call_id)
    assert (call.status, call.end_reason, generation) == ("ended", "sign-out", 1)
    assert old_token not in response.text and unrelated_token not in response.text


@pytest.mark.asyncio
async def test_login_does_not_revoke_an_unrelated_login_or_its_reserved_call(vox_auth_env):
    env = vox_auth_env
    account = await register(env.client)
    presented = cookie(env)
    unrelated = await env.new_client()
    assert (await login(unrelated, account["email"])).status_code == 200
    unrelated_token = cookie(env, unrelated)
    call_id = await seed_call(env, account, unrelated_token)
    assert (await login(env.client, account["email"])).status_code == 200
    assert (await auth_session(env, presented)).revoked_at is not None
    assert (await auth_session(env, unrelated_token)).revoked_at is None
    call, generation = await read_call(env, call_id)
    assert (call.status, generation) == ("reserved", 0)


@pytest.mark.asyncio
async def test_successful_registration_replaces_old_cookie_without_revoking_other_logins(
    vox_auth_env,
):
    env = vox_auth_env
    first = await register(env.client)
    old_token = cookie(env)
    unrelated = await env.new_client()
    assert (await login(unrelated, first["email"])).status_code == 200
    unrelated_token = cookie(env, unrelated)
    call_id = await seed_call(env, first, old_token)
    second = await register(env.client, "vox-auth-new@example.com")
    assert second["id"] != first["id"]
    assert (await auth_session(env, old_token)).revoked_at is not None
    assert (await auth_session(env, unrelated_token)).revoked_at is None
    assert (await env.client.get("/api/v1/auth/me")).json()["id"] == second["id"]
    call, generation = await read_call(env, call_id)
    assert (call.status, generation) == ("ended", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["vox-auth-a@example.com", "nonexistent@example.com"])
async def test_invalid_login_does_not_revoke_existing_cookie_or_call(vox_auth_env, email):
    env = vox_auth_env
    account = await register(env.client)
    token = cookie(env)
    call_id = await seed_call(env, account, token)
    response = await env.client.post(
        "/api/v1/auth/login", json={"email": email, "password": "incorrect-password"}
    )
    assert response.status_code == 401
    assert cookie(env) == token
    assert (await auth_session(env, token)).revoked_at is None
    call, generation = await read_call(env, call_id)
    assert (call.status, generation) == ("reserved", 0)


@pytest.mark.asyncio
async def test_duplicate_registration_leaves_the_presented_session_and_call_unchanged(vox_auth_env):
    env = vox_auth_env
    account = await register(env.client)
    token = cookie(env)
    call_id = await seed_call(env, account, token)
    response = await env.client.post(
        "/api/v1/auth/register",
        json={"email": account["email"], "password": PASSWORD, "display_name": "Duplicate"},
    )
    assert response.status_code == 409
    assert (await auth_session(env, token)).revoked_at is None
    assert (await read_call(env, call_id))[0].status == "reserved"


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_state", ["expired", "revoked"])
async def test_stale_web_cookie_logout_still_cleans_associated_calls(vox_auth_env, previous_state):
    env = vox_auth_env
    account = await register(env.client)
    token = cookie(env)
    call_id = await seed_call(env, account, token)
    session = await auth_session(env, token)
    before = datetime.now(UTC) - timedelta(minutes=5)
    async with env.factory() as database:
        row = await database.get(UserSession, session.id)
        if previous_state == "expired":
            row.expires_at = before
        else:
            row.revoked_at = before
        await database.commit()
    assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    call, generation = await read_call(env, call_id)
    assert (call.status, call.end_reason, generation) == ("ended", "sign-out", 1)
    if previous_state == "revoked":
        assert utc((await auth_session(env, token)).revoked_at) == before


@pytest.mark.asyncio
async def test_extension_logout_revokes_only_its_bearer_session_even_with_a_web_cookie(
    vox_auth_env,
):
    env = vox_auth_env
    account = await register(env.client)
    web_token = cookie(env)
    response = await env.client.post(
        "/api/v1/auth/extension/login", json={"email": account["email"], "password": PASSWORD}
    )
    assert response.status_code == 200
    extension_token = response.json()["access_token"]
    call_id = await seed_call(env, account, extension_token)
    # A bearer credential placed in a cookie cannot select extension logout.
    assert (
        await env.client.post("/api/v1/auth/logout", headers=cookie_header(env, extension_token))
    ).status_code == 200
    assert (await auth_session(env, extension_token)).revoked_at is None
    assert (await read_call(env, call_id))[0].status == "reserved"
    response = await env.client.post(
        "/api/v1/auth/extension/logout", headers={"Authorization": f"Bearer {extension_token}"}
    )
    assert response.status_code == 200
    assert (await auth_session(env, extension_token)).revoked_at is not None
    assert (await auth_session(env, web_token)).revoked_at is None
    assert (await read_call(env, call_id))[0].status == "ended"


@pytest.mark.asyncio
async def test_fresh_session_read_rejects_stale_extension_identity(vox_auth_env):
    env = vox_auth_env
    account = await register(env.client)
    response = await env.client.post(
        "/api/v1/auth/extension/login", json={"email": account["email"], "password": PASSWORD}
    )
    token = response.json()["access_token"]
    session = await auth_session(env, token)
    async with env.factory() as stale:
        remembered = await stale.get(UserSession, session.id)
        assert remembered.revoked_at is None
        async with env.factory() as revoker:
            fresh = await revoker.get(UserSession, session.id)
            fresh.revoked_at = datetime.now(UTC)
            await revoker.commit()
        with pytest.raises(HTTPException) as rejected:
            await auth.revoke_presented_session(
                stale, token, client_type="extension", required=True
            )
        assert rejected.value.status_code == 401
        assert remembered.revoked_at is not None


@pytest.mark.asyncio
async def test_logout_auth_and_call_revocation_roll_back_together_on_helper_failure(
    vox_auth_env, monkeypatch
):
    from navox.vox import service

    env = vox_auth_env
    account = await register(env.client)
    token = cookie(env)
    call_id = await seed_call(env, account, token)
    original = service.revoke_session_calls

    async def interrupted(database, auth_session_id, *, now):
        await original(database, auth_session_id, now=now)
        raise RuntimeError("Synthetic transaction interruption")

    monkeypatch.setattr(service, "revoke_session_calls", interrupted)
    with pytest.raises(RuntimeError, match="Synthetic transaction interruption"):
        await env.client.post("/api/v1/auth/logout")
    assert (await auth_session(env, token)).revoked_at is None
    call, generation = await read_call(env, call_id)
    assert (call.status, generation) == ("reserved", 0)


@pytest.mark.asyncio
async def test_postgres_cross_account_replacements_wait_on_the_same_first_user_lock(vox_auth_env):
    env = vox_auth_env
    if env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL User locks and pg_blocking_pids")
    first = await register(env.client)
    second_client = await env.new_client()
    second = await register(second_client, "vox-auth-b@example.com")
    first_call = await seed_call(env, first, cookie(env))
    second_call = await seed_call(env, second, cookie(env, second_client))
    targets = [(second, cookie(env)), (first, cookie(env, second_client))]
    lowest = min(UUID(first["id"]), UUID(second["id"]))
    async with (
        env.factory() as holder,
        env.factory() as left,
        env.factory() as right,
        env.factory() as observer,
    ):
        await holder.scalar(select(User).where(User.id == lowest).with_for_update())
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        pids = [
            await database.scalar(text("SELECT pg_backend_pid()")) for database in (left, right)
        ]

        async def replace(database, target, token):
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/auth/login",
                    "headers": [
                        (b"cookie", f"{env.settings.session_cookie_name}={token}".encode())
                    ],
                }
            )
            return await auth.login(
                auth.LoginRequest(email=target["email"], password=PASSWORD),
                request,
                Response(),
                database,
                env.settings,
            )

        tasks = [
            asyncio.create_task(replace(database, *target))
            for database, target in zip((left, right), targets, strict=True)
        ]
        try:
            async with asyncio.timeout(10):
                while True:
                    blockers = [
                        await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})
                        for pid in pids
                    ]
                    # PostgreSQL can queue the second row-lock waiter behind
                    # the first waiter rather than directly behind the holder.
                    if all(blockers) and any(holder_pid in values for values in blockers):
                        break
                    await asyncio.sleep(0.01)
            assert not any(task.done() for task in tasks)
            await holder.commit()
            responses = await asyncio.wait_for(asyncio.gather(*tasks), 15)
            assert [str(response.id) for response in responses] == [second["id"], first["id"]]
        finally:
            await holder.rollback()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert (await read_call(env, first_call))[0].status == "ended"
    assert (await read_call(env, second_call))[0].status == "ended"
