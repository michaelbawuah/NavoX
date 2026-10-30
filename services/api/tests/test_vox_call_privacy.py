"""Read-only privacy labels must belong to the exact live browser call."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from test_vox_calls import ROOT, start
from test_vox_calls import vox_env as _shared_vox_env

from navox.db.conversation_memory import MemoryConversation, MemoryPrivacy
from navox.db.models import User, UserSession
from navox.db.vox import VoxCall
from navox.db.vox_context import VoxCallCheckpoint

vox_env = _shared_vox_env


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "off_record", "retention", "mode"),
    [
        (False, False, None, "history-off"),
        (False, True, None, "off-record"),
        (True, True, 30, "off-record"),
        (True, False, 30, "saved-history"),
        (True, False, 90, "saved-history"),
        (True, False, None, "saved-history"),
    ],
)
async def test_call_privacy_uses_disclosed_policy_not_legacy_reservation_mode(
    vox_env, enabled, off_record, retention, mode
):
    env = vox_env
    async with env.factory() as database:
        database.add(
            MemoryPrivacy(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                history_enabled=enabled,
                off_the_record=off_record,
                retention_days=retention,
                disclosure_version=1 if enabled else None,
                generation=7,
                updated_at=datetime.now(UTC),
            )
        )
        await database.commit()
    call, _ = await start(env)
    assert call["mode"] == "off-record"  # This historical field is not memory consent.
    response = await env.client.get(f"{ROOT}/{call['client_call_id']}/privacy")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "user_id": str(env.user_id),
        "workspace_id": str(env.workspace_id),
        "client_call_id": call["client_call_id"],
        "generation": call["generation"],
        "privacy_generation": 7,
        "mode": mode,
        "retention_days": retention if mode == "saved-history" else None,
        "expires_at": call["expires_at"],
    }
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(MemoryConversation)) == 0
        assert await database.scalar(select(func.count()).select_from(VoxCallCheckpoint)) == 0


@pytest.mark.asyncio
async def test_default_privacy_read_does_not_create_consent_or_history(vox_env):
    env = vox_env
    call, _ = await start(env)
    response = await env.client.get(f"{ROOT}/{call['client_call_id']}/privacy")
    assert response.status_code == 200
    assert response.json()["mode"] == "history-off"
    assert response.json()["privacy_generation"] == 0
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(MemoryPrivacy)) == 0


@pytest.mark.asyncio
async def test_privacy_descriptor_cannot_cross_account_or_browser_session(vox_env):
    env = vox_env
    call, _ = await start(env)
    path = f"{ROOT}/{call['client_call_id']}/privacy"
    assert (await env.client.get(f"{ROOT}/{uuid4()}/privacy")).status_code == 404
    assert (
        await env.client.get(path, headers={"X-NavoX-Account": f"{uuid4()}:{env.user_id}"})
    ).status_code == 409
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as other:
        assert (await other.post("/api/v1/auth/login", json=env.credentials)).status_code == 200
        other.headers["X-NavoX-Account"] = f"{env.workspace_id}:{env.user_id}"
        assert (await other.get(path)).status_code == 404
    assert (
        await env.client.get(path, headers={"Authorization": "Bearer unused"})
    ).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "paused", "revoked"])
async def test_privacy_read_rechecks_live_authority(vox_env, change):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        if change == "expired":
            row = await database.get(VoxCall, UUID(call["id"]))
            row.created_at = datetime.now(UTC) - timedelta(minutes=2)
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        elif change == "paused":
            user = await database.get(User, env.user_id)
            user.agent_paused = True
        else:
            session = await database.get(UserSession, env.session_id)
            session.revoked_at = datetime.now(UTC)
        await database.commit()
    response = await env.client.get(f"{ROOT}/{call['client_call_id']}/privacy")
    assert response.status_code == {"expired": 410, "paused": 409, "revoked": 401}[change]
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_privacy_change_ends_old_descriptor_before_next_call_policy(vox_env):
    env = vox_env
    call, _ = await start(env)
    changed = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "generation": 0,
            "history_enabled": False,
            "retention_days": None,
            "off_the_record": True,
            "disclosure_accepted": False,
        },
    )
    assert changed.status_code == 200, changed.text
    assert (await env.client.get(f"{ROOT}/{call['client_call_id']}/privacy")).status_code == 404
    fresh, _ = await start(env)
    response = await env.client.get(f"{ROOT}/{fresh['client_call_id']}/privacy")
    assert response.status_code == 200
    assert response.json()["mode"] == "off-record"
    assert response.json()["privacy_generation"] == changed.json()["generation"]
