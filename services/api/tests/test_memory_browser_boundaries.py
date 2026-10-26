"""Private memory controls bind the submitted account to a fresh browser session."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_conversation_memory import history_env as history_env

from navox.api import memory_history
from navox.db.conversation_memory import MemoryFact, MemoryPrivacy
from navox.db.models import UserSession


@pytest.mark.asyncio
async def test_wrong_account_header_cannot_mutate_another_signed_in_owner(history_env):
    env = history_env
    response = await env.client.post(
        "/api/v1/memory/facts",
        headers={"X-NavoX-Account": f"{uuid4()}:{uuid4()}"},
        json={"label": "Wrong account", "content": "Must not be saved"},
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "private, no-store"
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(MemoryFact)) == 0
        assert await database.scalar(select(func.count()).select_from(MemoryPrivacy)) == 0


@pytest.mark.asyncio
async def test_memory_controls_require_exact_origin_and_browser_cookie(history_env):
    env = history_env
    response = await env.client.post(
        "/api/v1/memory/facts",
        headers={"Origin": "https://other.example"},
        json={"label": "Origin", "content": "Must not be saved"},
    )
    assert response.status_code == 403
    response = await env.client.get(
        "/api/v1/memory/privacy", headers={"Authorization": "Bearer invalid"}
    )
    assert response.status_code in {401, 403}
    env.client.headers.pop("X-NavoX-Account")
    assert (await env.client.get("/api/v1/memory/privacy")).status_code == 409


@pytest.mark.asyncio
async def test_revocation_after_authentication_is_rechecked_under_owner_lock(
    history_env, monkeypatch
):
    original = memory_history.lock_owner

    async def revoke(database, user_id):
        await original(database, user_id)
        session = await database.scalar(select(UserSession).where(UserSession.user_id == user_id))
        session.revoked_at = datetime.now(UTC)
        await database.flush()

    monkeypatch.setattr(memory_history, "lock_owner", revoke)
    response = await history_env.client.post(
        "/api/v1/memory/facts", json={"label": "Stale", "content": "Never persist"}
    )
    assert response.status_code == 401
    async with history_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(MemoryFact)) == 0


@pytest.mark.asyncio
async def test_validation_never_echoes_private_submitted_text(history_env):
    secret = "private-medical-sentinel " * 50
    response = await history_env.client.post(
        "/api/v1/memory/facts", json={"label": "Invalid", "content": secret}
    )
    assert response.status_code == 422
    assert "private-medical-sentinel" not in response.text
    assert response.json() == {"detail": "Check this memory request"}
