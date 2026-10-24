"""Independent browser-session and owner-scope checks for private Vox usage."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from test_vox_calls import start
from test_vox_calls import vox_env as shared_vox_fixture

from navox.db.models import UserSession, WorkspaceMembership

vox_env = shared_vox_fixture
ROOT = "/api/v1/vox/usage"


@pytest.mark.asyncio
async def test_usage_rejects_bearer_even_with_a_valid_browser_cookie(vox_env):
    env = vox_env
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    for suffix in ("status", "policy", "daily", f"calls/{uuid4()}"):
        response = await env.client.get(
            f"{ROOT}/{suffix}", headers={"Authorization": f"Bearer {env.token}"}
        )
        assert response.status_code == 403
        assert response.headers["cache-control"] == "private, no-store"
        assert env.token not in response.text


@pytest.mark.asyncio
async def test_usage_rejects_extension_token_replayed_as_a_browser_cookie(vox_env):
    env = vox_env
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    extension = await env.client.post("/api/v1/auth/extension/login", json=env.credentials)
    assert extension.status_code == 200
    token = extension.json()["access_token"]
    env.client.cookies.clear()
    env.client.cookies.set(env.settings.session_cookie_name, token)
    for suffix in ("status", "policy", "daily", f"calls/{uuid4()}"):
        response = await env.client.get(f"{ROOT}/{suffix}")
        assert response.status_code in {401, 403}, suffix
        assert response.headers["cache-control"] == "private, no-store"
        assert token not in response.text


@pytest.mark.asyncio
async def test_cost_history_is_owner_scoped_and_not_another_users_operator_access(vox_env):
    env = vox_env
    row, _ = await start(env)
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as other:
        registration = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "other-usage-owner@example.com",
                "password": "another-twelve-character-password",
                "display_name": "Other synthetic usage owner",
            },
        )
        assert registration.status_code == 201
        account = registration.json()
        other.headers["X-NavoX-Account"] = f"{account['workspace']['id']}:{account['id']}"
        assert (await other.get(f"{ROOT}/status")).status_code == 200
        assert (await other.get(f"{ROOT}/calls/{row['id']}")).status_code == 404
        assert (await other.get(f"{ROOT}/policy")).status_code == 403
        assert (await other.get(f"{ROOT}/daily")).status_code == 403
    owned = await env.client.get(f"{ROOT}/calls/{row['id']}")
    assert owned.status_code == 200 and owned.json()["call_id"] == row["id"]


@pytest.mark.asyncio
async def test_fresh_browser_session_of_same_owner_can_read_historical_call_totals(vox_env):
    env = vox_env
    row, _ = await start(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={
            "Origin": env.settings.web_origin,
            "X-NavoX-Account": f"{env.workspace_id}:{env.user_id}",
        },
    ) as second:
        assert (await second.post("/api/v1/auth/login", json=env.credentials)).status_code == 200
        response = await second.get(f"{ROOT}/calls/{row['id']}")
        assert response.status_code == 200
        assert response.json()["call_id"] == row["id"]
        assert (await second.get(f"{ROOT}/status")).status_code == 200


@pytest.mark.asyncio
async def test_removed_owner_cannot_read_usage_or_operator_pages(vox_env):
    env = vox_env
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    async with env.factory() as database:
        await database.execute(
            delete(WorkspaceMembership).where(
                WorkspaceMembership.user_id == env.user_id,
                WorkspaceMembership.workspace_id == env.workspace_id,
            )
        )
        await database.commit()
    for suffix in ("status", "policy", "daily", f"calls/{uuid4()}"):
        response = await env.client.get(f"{ROOT}/{suffix}")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["revoked", "expired"])
async def test_usage_rejects_ended_browser_authority(vox_env, state):
    env = vox_env
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    async with env.factory() as database:
        session = await database.get(UserSession, env.session_id)
        if state == "revoked":
            session.revoked_at = datetime.now(UTC)
        else:
            session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    for suffix in ("status", "policy", "daily", f"calls/{uuid4()}"):
        response = await env.client.get(f"{ROOT}/{suffix}")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
