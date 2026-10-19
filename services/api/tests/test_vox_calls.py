"""Real HTTP authorization and terminal-state tests for metadata-only Vox calls.

No transport, microphone, model, retained conversation, or provider credential is
used. Real row-lock interleavings live separately in test_vox_postgres.py.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from navox.api.auth import hash_session_token
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.models import User, UserSession, WorkspaceMembership
from navox.db.session import get_database_session
from navox.db.vox import VoxCall
from navox.vox import service

ROOT = "/api/v1/vox/calls"


@pytest_asyncio.fixture
async def vox_env(ai_database):
    settings = Settings(_env_file=None, app_environment="test", ai_provider="disabled")
    settings = settings.model_copy(update={"student_vox_enabled": True})

    async def database():
        async with ai_database() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    credentials = {
        "email": "vox-owner@example.com",
        "password": "twelve-character-password",
        "display_name": "Synthetic Vox owner",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        registered = await client.post("/api/v1/auth/register", json=credentials)
        assert registered.status_code == 201, registered.text
        account = registered.json()
        client.headers["X-NavoX-Account"] = f"{account['workspace']['id']}:{account['id']}"
        token = client.cookies.get(settings.session_cookie_name)
        async with ai_database() as database:
            session_id = await database.scalar(
                select(UserSession.id).where(UserSession.token_hash == hash_session_token(token))
            )
        yield SimpleNamespace(
            app=app,
            client=client,
            factory=ai_database,
            settings=settings,
            credentials=credentials,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
            session_id=session_id,
            token=token,
            token_hash=hash_session_token(token),
        )


async def context(env):
    response = await env.client.get(f"{ROOT}/context")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


async def start(env, *, generation=None, client_call_id=None):
    if generation is None:
        generation = (await context(env))["generation"]
    payload = {
        "client_call_id": str(client_call_id or uuid4()),
        "generation": generation,
        "mode": "off-record",
    }
    response = await env.client.post(ROOT, json=payload)
    assert response.status_code == 201, response.text
    return response.json(), payload


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_reserved_metadata_is_bounded_private_and_exact_create_retries_do_not_extend_it(
    vox_env,
):
    env = vox_env
    initial = await context(env)
    assert initial == {"generation": 0, "call": None, "media_enabled": False}
    row, payload = await start(env)
    assert set(row) == {
        "id",
        "client_call_id",
        "generation",
        "status",
        "mode",
        "created_at",
        "expires_at",
        "ended_at",
        "end_reason",
        "media_enabled",
    }
    assert row["status"] == "reserved" and row["mode"] == "off-record"
    assert row["media_enabled"] is False and row["ended_at"] is row["end_reason"] is None
    assert 0 < (instant(row["expires_at"]) - instant(row["created_at"])).total_seconds() <= 120
    assert UUID(row["id"]) != UUID(row["client_call_id"])
    replay = await env.client.post(ROOT, json=payload)
    assert replay.status_code == 201 and replay.json() == row
    assert (await context(env))["call"] == row
    assert env.token not in replay.text and env.token_hash not in replay.text
    assert env.credentials["email"] not in replay.text
    second = await env.client.post(ROOT, json={**payload, "client_call_id": str(uuid4())})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_renew_then_terminal_end_is_immutable_and_old_create_cannot_reopen(vox_env):
    env = vox_env
    row, payload = await start(env)
    path = f"{ROOT}/{row['client_call_id']}"
    renewed = await env.client.post(f"{path}/renew", json={"generation": row["generation"]})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["id"] == row["id"] and renewed.json()["media_enabled"] is False
    ended = await env.client.post(f"{path}/end", json={"generation": row["generation"]})
    assert ended.status_code == 204
    after = await context(env)
    assert after["generation"] > row["generation"] and after["call"] is None
    # Hang-up remains idempotent even when its generation predates termination.
    assert (await env.client.post(f"{path}/end", json={"generation": 0})).status_code == 204
    assert (await env.client.post(ROOT, json=payload)).status_code == 410
    assert (await env.client.post(f"{path}/renew", json={"generation": 0})).status_code == 410
    async with env.factory() as database:
        saved = await database.get(VoxCall, UUID(row["id"]))
        ended_at, reason = saved.ended_at, saved.end_reason
        assert saved.status == "ended" and reason == "hang-up" and ended_at is not None
    assert (await env.client.post(f"{path}/end", json={"generation": 0})).status_code == 204
    async with env.factory() as database:
        saved = await database.get(VoxCall, UUID(row["id"]))
        assert (saved.ended_at, saved.end_reason) == (ended_at, reason)


@pytest.mark.asyncio
async def test_end_before_create_fences_delayed_work_and_fresh_generation_can_start(vox_env):
    env = vox_env
    client_id = uuid4()
    old_generation = (await context(env))["generation"]
    response = await env.client.post(f"{ROOT}/{client_id}/end", json={"generation": old_generation})
    assert response.status_code == 204, response.text
    replay = await env.client.post(
        ROOT,
        json={"client_call_id": str(client_id), "generation": old_generation, "mode": "off-record"},
    )
    assert replay.status_code in {409, 410}
    fresh = await context(env)
    assert fresh["generation"] > old_generation and fresh["call"] is None
    row, _ = await start(env, generation=fresh["generation"])
    assert row["status"] == "reserved"


@pytest.mark.asyncio
async def test_independent_login_session_cannot_adopt_renew_or_end_an_existing_call(vox_env):
    env = vox_env
    row, payload = await start(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers=dict(env.client.headers),
    ) as second:
        response = await second.post("/api/v1/auth/login", json=env.credentials)
        assert response.status_code == 200
        own = await second.get(f"{ROOT}/context")
        assert own.status_code == 200 and own.json()["call"] is None
        assert (await second.post(ROOT, json=payload)).status_code == 404
        assert (
            await second.post(ROOT, json={**payload, "client_call_id": str(uuid4())})
        ).status_code == 409
        for operation in ("renew", "end"):
            denied = await second.post(
                f"{ROOT}/{row['client_call_id']}/{operation}",
                json={"generation": row["generation"]},
            )
            assert denied.status_code == 404, denied.text
        # Cancelling a rejected alternate call cannot invalidate the live session's lease.
        cancelled = await second.post(f"{ROOT}/{uuid4()}/end", json={"generation": 0})
        assert cancelled.status_code == 204
    assert (await context(env))["call"]["id"] == row["id"]
    assert (
        await env.client.post(f"{ROOT}/{row['client_call_id']}/renew", json={"generation": 0})
    ).status_code == 200


@pytest.mark.asyncio
async def test_logout_and_replaced_cookie_login_close_only_the_replaced_session_calls(vox_env):
    env = vox_env
    row, _ = await start(env)
    response = await env.client.post("/api/v1/auth/login", json=env.credentials)
    assert response.status_code == 200
    async with env.factory() as database:
        old_session = await database.get(UserSession, env.session_id)
        old_call = await database.get(VoxCall, UUID(row["id"]))
        assert old_session.revoked_at is not None
        assert old_call.status == "ended" and old_call.end_reason == "sign-out"
    row, _ = await start(env)
    assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    assert (await env.client.get(f"{ROOT}/context")).status_code == 401
    async with env.factory() as database:
        old_call = await database.get(VoxCall, UUID(row["id"]))
        assert old_call.status == "ended" and old_call.end_reason == "sign-out"


@pytest.mark.asyncio
async def test_reset_deletes_call_rows_but_retains_generation_against_old_unsent_starts(vox_env):
    env = vox_env
    row, payload = await start(env)
    response = await env.client.post(
        "/api/v1/student/data/reset",
        json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    fresh = await context(env)
    assert fresh["call"] is None and fresh["generation"] > row["generation"]
    assert (await env.client.post(ROOT, json=payload)).status_code == 409
    assert (
        await env.client.post(ROOT, json={**payload, "client_call_id": str(uuid4())})
    ).status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxCall)) == 0


@pytest.mark.asyncio
async def test_lease_is_capped_by_auth_expiry_and_expired_calls_stay_terminal(vox_env):
    env = vox_env
    deadline = datetime.now(UTC) + timedelta(seconds=45)
    async with env.factory() as database:
        session = await database.get(UserSession, env.session_id)
        session.expires_at = deadline
        await database.commit()
    row, payload = await start(env)
    assert instant(row["expires_at"]) <= deadline
    shortened = datetime.now(UTC) + timedelta(seconds=5)
    async with env.factory() as database:
        (await database.get(VoxCall, UUID(row["id"]))).expires_at = shortened
        await database.commit()
    renewed = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/renew", json={"generation": row["generation"]}
    )
    assert renewed.status_code == 200
    assert shortened < instant(renewed.json()["expires_at"]) <= deadline
    async with env.factory() as database:
        call = await database.get(VoxCall, UUID(row["id"]))
        call.created_at = datetime.now(UTC) - timedelta(seconds=121)
        call.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    assert (
        await env.client.post(
            f"{ROOT}/{row['client_call_id']}/renew", json={"generation": row["generation"]}
        )
    ).status_code == 410
    assert (await env.client.post(ROOT, json=payload)).status_code == 410
    assert (await context(env))["call"] is None


@pytest.mark.asyncio
async def test_feature_off_denies_allocation_but_keeps_terminal_cleanup_available(vox_env):
    env = vox_env
    row, payload = await start(env)
    env.settings.student_vox_enabled = False
    assert (await env.client.get(f"{ROOT}/context")).status_code == 404
    assert (await env.client.post(ROOT, json=payload)).status_code == 404
    assert (
        await env.client.post(f"{ROOT}/{row['client_call_id']}/renew", json={"generation": 0})
    ).status_code == 404
    assert (
        await env.client.post(f"{ROOT}/{row['client_call_id']}/end", json={"generation": 0})
    ).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["Bearer synthetic-token", "Basic malformed", ""])
async def test_any_authorization_header_is_rejected_even_with_a_valid_web_cookie(vox_env, header):
    response = await vox_env.client.get(f"{ROOT}/context", headers={"Authorization": header})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_cookie_authority_header_and_origin_are_independent_required_guards(vox_env):
    env = vox_env
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as anonymous:
        assert (await anonymous.get(f"{ROOT}/context")).status_code == 401
    body = {"client_call_id": str(uuid4()), "generation": 0, "mode": "off-record"}
    response = await env.client.post(
        ROOT, json=body, headers={"X-NavoX-Account": f"{uuid4()}:{env.user_id}"}
    )
    assert response.status_code == 409
    original = env.client.headers.pop("X-NavoX-Account")
    try:
        assert (await env.client.get(f"{ROOT}/context")).status_code == 409
        assert (await env.client.post(ROOT, json=body)).status_code == 409
    finally:
        env.client.headers["X-NavoX-Account"] = original
    origin = env.client.headers.pop("Origin")
    try:
        assert (await env.client.post(ROOT, json=body)).status_code == 403
        assert (
            await env.client.post(ROOT, json=body, headers={"Origin": "https://foreign.example"})
        ).status_code == 403
    finally:
        env.client.headers["Origin"] = origin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"generation": -1},
        {"generation": 2**63},
        {"mode": "record"},
        {"transcript": "synthetic private text"},
        {"room_token": "not-a-real-token"},
    ],
)
async def test_unbounded_or_media_bearing_contracts_are_sanitized_and_not_saved(vox_env, changes):
    body = {"client_call_id": str(uuid4()), "generation": 0, "mode": "off-record", **changes}
    response = await vox_env.client.post(ROOT, json=body)
    assert response.status_code == 422
    assert "synthetic private text" not in response.text and "not-a-real-token" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert (await context(vox_env))["call"] is None


@pytest.mark.asyncio
async def test_duplicate_keys_and_real_stream_size_are_rejected_without_echoing_input(vox_env):
    env = vox_env
    duplicate = (
        json.dumps({"client_call_id": str(uuid4()), "mode": "off-record"})[:-1]
        + ',"generation":0,"generation":1}'
    )
    response = await env.client.post(
        ROOT, content=duplicate, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422

    async def stream():
        yield b'{"private_synthetic_input":"'
        yield b"x" * 1024

    oversized = await env.client.post(
        ROOT, content=stream(), headers={"Content-Type": "application/json"}
    )
    assert oversized.status_code == 413 and "private_synthetic_input" not in oversized.text
    wrong_type = await env.client.post(ROOT, content=b"{}", headers={"Content-Type": "text/plain"})
    assert wrong_type.status_code == 415


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["membership", "expired", "revoked", "extension"])
async def test_fresh_server_authority_is_rechecked_without_returning_media(vox_env, denial):
    env = vox_env
    async with env.factory() as database:
        if denial == "membership":
            await database.delete(
                await database.get(WorkspaceMembership, (env.workspace_id, env.user_id))
            )
        else:
            session = await database.get(UserSession, env.session_id)
            if denial == "expired":
                session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif denial == "revoked":
                session.revoked_at = datetime.now(UTC)
            else:
                session.client_type = "extension"
        await database.commit()
    response = await env.client.get(f"{ROOT}/context")
    assert response.status_code in {401, 403}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_pause_blocks_reservation_and_renewal_but_preserves_hang_up(vox_env):
    env = vox_env
    row, payload = await start(env)
    async with env.factory() as database:
        (await database.get(User, env.user_id)).agent_paused = True
        await database.commit()
    assert (await env.client.post(ROOT, json=payload)).status_code == 409
    path = f"{ROOT}/{row['client_call_id']}"
    assert (await env.client.post(f"{path}/renew", json={"generation": 0})).status_code == 409
    assert (await env.client.post(f"{path}/end", json={"generation": 0})).status_code == 204


@pytest.mark.asyncio
async def test_quota_fence_survives_retention_pruning_and_reused_uuid_rejects_old_end(
    vox_env, monkeypatch
):
    env = vox_env
    monkeypatch.setattr(service, "MAX_CALLS_PER_OWNER", 2)
    assert (await context(env))["generation"] == 0
    now = datetime.now(UTC)
    async with env.factory() as database:
        database.add_all(
            VoxCall(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                auth_session_id=env.session_id,
                client_call_id=uuid4(),
                generation=0,
                status="ended",
                end_reason="hang-up",
                created_at=now - timedelta(hours=1),
                expires_at=now - timedelta(hours=1),
                ended_at=now - timedelta(hours=1),
            )
            for _ in range(2)
        )
        await database.commit()
    client_id = str(uuid4())
    old = {"client_call_id": client_id, "generation": 0, "mode": "off-record"}
    assert (await env.client.post(ROOT, json=old)).status_code == 429
    assert (
        await env.client.post(f"{ROOT}/{client_id}/end", json={"generation": 0})
    ).status_code == 204
    generation = (await context(env))["generation"]
    assert generation > 0
    async with env.factory() as database:
        calls = list(await database.scalars(select(VoxCall)))
        assert len(calls) == 2
        # Move synthetic terminal metadata beyond the 24-hour pruning boundary.
        for call in calls:
            call.created_at = call.expires_at = call.ended_at = now - timedelta(hours=25)
        await database.commit()
    assert (await context(env))["generation"] == generation
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxCall)) == 0
    assert (await env.client.post(ROOT, json=old)).status_code == 409
    current, _ = await start(env, generation=generation, client_call_id=UUID(client_id))
    assert (
        await env.client.post(f"{ROOT}/{client_id}/end", json={"generation": 0})
    ).status_code == 409
    assert (await context(env))["call"]["id"] == current["id"]
    assert (
        await env.client.post(f"{ROOT}/{client_id}/end", json={"generation": generation})
    ).status_code == 204


@pytest.mark.asyncio
async def test_unknown_cancellation_at_quota_preserves_other_call_and_its_renewal(
    vox_env, monkeypatch
):
    env = vox_env
    monkeypatch.setattr(service, "MAX_CALLS_PER_OWNER", 1)
    row, _ = await start(env)
    unknown_id = uuid4()
    assert (
        await env.client.post(f"{ROOT}/{unknown_id}/end", json={"generation": 0})
    ).status_code == 204
    state = await context(env)
    assert state["generation"] == row["generation"] and state["call"]["id"] == row["id"]
    path = f"{ROOT}/{row['client_call_id']}"
    assert (await env.client.post(f"{path}/renew", json={"generation": 0})).status_code == 200
    assert (await env.client.post(f"{path}/end", json={"generation": 0})).status_code == 204
    assert (
        await env.client.post(
            ROOT, json={"client_call_id": str(unknown_id), "generation": 0, "mode": "off-record"}
        )
    ).status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxCall)) == 1
