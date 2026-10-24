"""Synthetic database/API checks only; no wake assets, vendor keys or provider calls."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from test_vox_calls import ROOT
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import begin, confirm

from navox.connectors.sync_state import utc
from navox.core.settings import Settings
from navox.db.models import User, UserSession, WorkspaceMembership
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.vox import media
from navox.vox.provider import LiveKitCloudProvider
from navox.vox.wake_config import WakeConfig

vox_env = shared_vox_fixture
KEY = "synthetic-porcupine-browser-key"


@pytest_asyncio.fixture
async def wake_env(vox_env):
    env = vox_env
    env.settings.student_vox_media_enabled = True
    env.settings.student_vox_local_wake_enabled = True
    env.settings.student_vox_porcupine_browser_credential_approved = True
    env.settings.student_vox_porcupine_access_key = SecretStr(KEY)
    env.settings.student_vox_porcupine_keyword_public_path = "/audio/wake/hey-navox-v1.ppn"
    env.settings.student_vox_porcupine_model_public_path = "/audio/wake/porcupine-v1.pv"
    row, intent = await begin(env)
    await confirm(env, intent)
    return env, row, intent


def endpoint(row, intent):
    return f"{ROOT}/{row['client_call_id']}/media/{intent.connection_id}/wake-config"


async def request(wake_env, **kwargs):
    env, row, intent = wake_env
    return await env.client.post(
        endpoint(row, intent), json={"generation": row["generation"]}, **kwargs
    )


async def deadlines(env, row, intent):
    async with env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        call = await database.get(VoxCall, UUID(row["id"]))
        session = await database.get(UserSession, env.session_id)
        state = await database.get(VoxState, intent.owner_scope_id)
        cleanup = await database.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == intent.connection_id)
        )
        return (
            utc(connection.expires_at),
            utc(call.expires_at),
            utc(session.expires_at),
            utc(cleanup.connection_expires_at),
            state.generation,
            cleanup.status,
            connection.status,
        )


def test_wake_delivery_defaults_are_disabled_and_unconfigured():
    settings = Settings(_env_file=None)
    assert settings.student_vox_local_wake_enabled is False
    assert settings.student_vox_porcupine_browser_credential_approved is False
    assert settings.student_vox_porcupine_access_key is None
    assert settings.student_vox_porcupine_keyword_public_path == ""
    assert settings.student_vox_porcupine_model_public_path == ""


@pytest.mark.asyncio
async def test_config_is_exact_private_repr_safe_and_never_renews_or_calls_provider(
    wake_env,
    monkeypatch,
):
    env, row, intent = wake_env
    renew = AsyncMock(side_effect=AssertionError("Wake config must not renew media"))
    provision = AsyncMock(side_effect=AssertionError("Wake config must not call a provider"))
    monkeypatch.setattr(media, "renew_connection", renew)
    monkeypatch.setattr(LiveKitCloudProvider, "provision", provision)
    before = await deadlines(env, row, intent)
    for _ in range(2):
        response = await request(wake_env)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        body = response.json()
        assert set(body) == {
            "client_call_id",
            "connection_id",
            "generation",
            "expires_at",
            "engine",
            "access_key",
            "keyword_public_path",
            "model_public_path",
            "label",
        }
        assert body["client_call_id"] == row["client_call_id"]
        assert body["connection_id"] == str(intent.connection_id)
        assert body["generation"] == row["generation"]
        assert body["engine"] == "porcupine" and body["label"] == "Hey NavoX"
        assert body["access_key"] == KEY
        assert body["keyword_public_path"] == "/audio/wake/hey-navox-v1.ppn"
        assert body["model_public_path"] == "/audio/wake/porcupine-v1.pv"
        assert utc(WakeConfig.model_validate(body).expires_at) == before[0]
        assert KEY not in repr(WakeConfig.model_validate(body))
    assert KEY not in repr(env.settings)
    assert await deadlines(env, row, intent) == before
    renew.assert_not_called()
    provision.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "status"),
    [
        ("student_vox_enabled", False, 404),
        ("student_vox_media_enabled", False, 404),
        ("student_vox_local_wake_enabled", False, 404),
        ("student_vox_porcupine_browser_credential_approved", False, 403),
        ("student_vox_porcupine_access_key", None, 503),
        ("student_vox_porcupine_access_key", SecretStr("  "), 503),
        ("student_vox_porcupine_access_key", SecretStr("x" * 1025), 503),
        ("student_vox_porcupine_access_key", SecretStr("synthetic\nkey"), 503),
        ("student_vox_porcupine_access_key", SecretStr("synthetic\rkey"), 503),
        ("student_vox_porcupine_keyword_public_path", "/" + "a" * 513 + "-v1.ppn", 503),
        ("student_vox_porcupine_model_public_path", "/" + "a" * 513 + "-v1.pv", 503),
        ("student_vox_porcupine_keyword_public_path", "", 503),
        ("student_vox_porcupine_model_public_path", "", 503),
        ("student_vox_porcupine_keyword_public_path", "https://external.invalid/wake-v1.ppn", 503),
        ("student_vox_porcupine_keyword_public_path", "//external.invalid/wake-v1.ppn", 503),
        ("student_vox_porcupine_keyword_public_path", "/wake/../wake-v1.ppn", 503),
        ("student_vox_porcupine_keyword_public_path", "/wake/%2e%2e/wake-v1.ppn", 503),
        ("student_vox_porcupine_keyword_public_path", "/wake/hey-navox.ppn", 503),
        ("student_vox_porcupine_model_public_path", "/wake/model-v1.pv?token=fixture", 503),
        ("student_vox_porcupine_model_public_path", "/wake/model-v1.ppn", 503),
    ],
)
async def test_delivery_requires_both_approvals_and_complete_versioned_same_origin_assets(
    wake_env,
    field,
    value,
    status,
):
    env, row, intent = wake_env
    setattr(env.settings, field, value)
    before = await deadlines(env, row, intent)
    response = await request(wake_env)
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {"detail": "Local wake listening is not available"}
    assert KEY not in response.text and "access_key" not in response.text
    assert await deadlines(env, row, intent) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "preparing",
        "revoked",
        "expired_media",
        "expired_call",
        "expired_session",
        "revoked_session",
        "generation_changed",
        "paused",
        "membership_removed",
        "cleanup_missing",
        "cleanup_pending",
        "cleanup_mismatch",
        "cleanup_identity",
        "call_ended",
    ],
)
async def test_fresh_authority_and_confirmed_armed_connection_are_required(wake_env, change):
    env, row, intent = wake_env
    now = datetime.now(UTC)
    async with env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        call = await database.get(VoxCall, UUID(row["id"]))
        session = await database.get(UserSession, env.session_id)
        cleanup = await database.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == intent.connection_id)
        )
        if change == "preparing":
            connection.status = "preparing"
        elif change == "revoked":
            connection.status = "revoked"
            connection.revoked_at, connection.reason = now, "authority-lost"
        elif change == "expired_media":
            connection.created_at, connection.expires_at = (
                now - timedelta(seconds=30),
                now - timedelta(seconds=1),
            )
            cleanup.connection_expires_at = connection.expires_at
        elif change == "expired_call":
            call.created_at, call.expires_at = (
                now - timedelta(seconds=30),
                now - timedelta(seconds=1),
            )
        elif change == "expired_session":
            session.expires_at = now - timedelta(seconds=1)
        elif change == "revoked_session":
            session.revoked_at = now
        elif change == "generation_changed":
            state = await database.get(VoxState, intent.owner_scope_id)
            state.generation += 1
        elif change == "paused":
            user = await database.get(User, env.user_id)
            user.agent_paused = True
        elif change == "membership_removed":
            await database.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.user_id == env.user_id)
            )
        elif change == "cleanup_missing":
            await database.delete(cleanup)
        elif change == "cleanup_pending":
            cleanup.status, cleanup.requested_at, cleanup.next_attempt_at = "pending", now, now
        elif change == "cleanup_mismatch":
            cleanup.connection_expires_at += timedelta(seconds=1)
        elif change == "cleanup_identity":
            cleanup.agent_identity = f"other-{uuid4().hex}"
        elif change == "call_ended":
            call.status, call.ended_at, call.end_reason = "ended", now, "hang-up"
        await database.commit()
    response = await request(wake_env)
    assert response.status_code in {401, 403, 404, 409, 410}, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert KEY not in response.text and "access_key" not in response.text


@pytest.mark.asyncio
async def test_swapped_call_connection_generation_and_validation_never_disclose_config(wake_env):
    env, row, intent = wake_env
    for url, payload in [
        (
            endpoint({**row, "client_call_id": str(uuid4())}, intent),
            {"generation": row["generation"]},
        ),
        (
            f"{ROOT}/{row['client_call_id']}/media/{uuid4()}/wake-config",
            {"generation": row["generation"]},
        ),
        (endpoint(row, intent), {"generation": row["generation"] + 1}),
        (endpoint(row, intent), {"generation": KEY}),
        (endpoint(row, intent), {"generation": row["generation"], "access_key": KEY}),
    ]:
        response = await env.client.post(url, json=payload)
        assert response.status_code in {404, 409, 422}
        assert response.headers["cache-control"] == "private, no-store"
        assert KEY not in response.text and "access_key" not in response.text


@pytest.mark.asyncio
async def test_independent_web_session_cannot_read_the_original_connection(wake_env):
    env, row, intent = wake_env
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={
            "Origin": env.settings.web_origin,
            "X-NavoX-Account": f"{env.workspace_id}:{env.user_id}",
        },
    ) as other:
        assert (await other.post("/api/v1/auth/login", json=env.credentials)).status_code == 200
        response = await other.post(endpoint(row, intent), json={"generation": row["generation"]})
    assert response.status_code == 404
    assert response.headers["cache-control"] == "private, no-store"
    assert KEY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["cookie", "origin", "account", "duplicate-account", "bearer", "other-owner"]
)
async def test_cookie_origin_account_and_owner_boundaries_fail_private(wake_env, boundary):
    env, row, intent = wake_env
    headers = {
        "Origin": env.settings.web_origin,
        "X-NavoX-Account": f"{env.workspace_id}:{env.user_id}",
    }
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers=headers,
    ) as client:
        if boundary != "cookie":
            client.cookies.set(env.settings.session_cookie_name, env.token)
        if boundary == "origin":
            client.headers["Origin"] = "https://other.invalid"
        elif boundary == "account":
            client.headers["X-NavoX-Account"] = f"{uuid4()}:{env.user_id}"
        elif boundary == "duplicate-account":
            client.headers = list(client.headers.multi_items()) + [
                ("X-NavoX-Account", headers["X-NavoX-Account"])
            ]
        elif boundary == "bearer":
            client.headers["Authorization"] = "Bearer synthetic-other-session"
        elif boundary == "other-owner":
            client.cookies.clear()
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "different-owner@example.com",
                    "password": "twelve-character-password",
                    "display_name": "Other",
                },
            )
            assert registration.status_code == 201
            account = registration.json()
            client.headers["X-NavoX-Account"] = f"{account['workspace']['id']}:{account['id']}"
        response = await client.post(endpoint(row, intent), json={"generation": row["generation"]})
    assert response.status_code in {401, 403, 404, 409}
    assert response.headers["cache-control"] == "private, no-store"
    assert KEY not in response.text and "access_key" not in response.text


@pytest.mark.asyncio
async def test_extension_token_in_browser_cookie_cannot_read_wake_config(wake_env):
    env, row, intent = wake_env
    before = await deadlines(env, row, intent)
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={
            "Origin": env.settings.web_origin,
            "X-NavoX-Account": f"{env.workspace_id}:{env.user_id}",
        },
    ) as extension:
        login = await extension.post(
            "/api/v1/auth/extension/login",
            json={
                "email": env.credentials["email"],
                "password": env.credentials["password"],
            },
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        # Prove this is a live extension credential, not an arbitrary bad cookie.
        identity = await extension.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert identity.status_code == 200
        assert identity.json()["id"] == str(env.user_id)
        extension.cookies.set(env.settings.session_cookie_name, token)
        response = await extension.post(
            endpoint(row, intent), json={"generation": row["generation"]}
        )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    assert KEY not in response.text and "access_key" not in response.text
    assert await deadlines(env, row, intent) == before
