"""Ordinary typed chat retains only disclosed text and fences forgotten context."""

import json

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select
from test_ai_conversation import install_conversation_runtime, new_conversation
from test_ai_intent_plan import intent_env as shared_intent_fixture

from navox.core.settings import Settings, get_settings
from navox.db.communications import AssistantTurn
from navox.db.conversation_memory import MemoryConversation
from navox.memory.history import bump_and_invalidate
from navox.memory.service import lock_owner

intent_env = shared_intent_fixture


async def prepare(env):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    )
    env.app.dependency_overrides[get_settings] = lambda: settings
    env.client.headers.update(
        {
            "Origin": settings.web_origin,
            "X-NavoX-Account": f"{env.workspace_id}:{env.user_id}",
        }
    )
    response = await env.client.put(
        "/api/v1/memory/privacy",
        json={
            "history_enabled": True,
            "retention_days": 30,
            "off_the_record": False,
            "generation": 0,
            "disclosure_accepted": True,
        },
    )
    assert response.status_code == 200, response.text
    response = await env.client.post(
        "/api/v1/memory/facts",
        json={
            "label": "Review",
            "content": "The synthetic project review is Thursday.",
        },
    )
    assert response.status_code == 201
    return settings


@pytest.mark.asyncio
async def test_typed_chat_reads_permitted_facts_and_archives_complete_text(intent_env):
    env = intent_env
    await prepare(env)
    adapter = await install_conversation_runtime(env, public=True)
    session = await new_conversation(env)
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={
            "session_id": session,
            "utterance": "When is my project review?",
        },
    )
    assert response.status_code == 200, response.text
    supplied = json.loads(adapter.calls[0].context.text)["saved_memory"]
    assert "Thursday" in supplied["facts"][0]["content"]
    rows = (await env.client.get("/api/v1/memory/history")).json()
    assert len(rows) == 1 and rows[0]["source_kind"] == "chat"
    detail = (await env.client.get(f"/api/v1/memory/history/{rows[0]['id']}")).json()
    assert detail["turns"][0]["content"] == "When is my project review?"
    assert detail["turns"][1]["content"] == response.json()["answer"]


@pytest.mark.asyncio
async def test_forget_during_typed_provider_work_rejects_stale_answer_and_archive(intent_env):
    env = intent_env
    await prepare(env)
    adapter = await install_conversation_runtime(env, public=True)
    session = await new_conversation(env)
    original = adapter.execute

    async def erase_during_provider(request):
        result = await original(request)
        async with env.factory() as database:
            await lock_owner(database, env.user_id)
            await bump_and_invalidate(database, env.user_id, env.workspace_id)
            await database.commit()
        return result

    adapter.execute = erase_during_provider
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={
            "session_id": session,
            "utterance": "When is my project review?",
        },
    )
    assert response.status_code != 200
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AssistantTurn)) == 0
        assert await database.scalar(select(func.count()).select_from(MemoryConversation)) == 0
