"""Corrections supersede stale recall without silently discarding history."""

import pytest
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture
from test_vox_memory_recall import MemoryCheck

from navox.vox.recall_tools import MemoryReadTools

history_env = shared_history_fixture


@pytest.mark.asyncio
async def test_corrected_source_fact_preserves_history_but_suppresses_old_recall(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation, question="The synthetic project review is Friday.")
    source = (await env.client.get("/api/v1/memory/history")).json()[0]
    facts = []
    for label, content in (
        ("Review day", "The synthetic project review is Friday."),
        ("Old review note", "My old review plan refers to Friday."),
    ):
        response = await env.client.post(
            "/api/v1/memory/facts",
            json={
                "label": label,
                "content": content,
                "source_conversation_id": source["id"],
            },
        )
        assert response.status_code == 201
        facts.append(response.json())
    response = await env.client.patch(
        f"/api/v1/memory/facts/{facts[0]['id']}",
        json={
            "content": "The synthetic project review moved to Thursday.",
        },
    )
    assert response.status_code == 200
    visible = (await env.client.get("/api/v1/memory/facts")).json()
    assert len(visible) == 2  # Sibling saved content remains independently visible.
    results = (await env.client.get("/api/v1/memory/history?query=Friday")).json()
    assert results[0]["id"] == source["id"]
    detail = (await env.client.get(f"/api/v1/memory/history/{source['id']}")).json()
    assert "Friday" in detail["turns"][0]["content"]
    assert detail["summary"] is None  # Obsolete derived summary is no longer recalled.
    recalled = await MemoryReadTools(MemoryCheck(env), env.settings).context("Friday review")
    assert len(recalled["facts"]) == 1
    assert "Thursday" in recalled["facts"][0]["content"]
    assert recalled["past_context"] == [] and recalled["threads"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    ["My synthetic therapy is Thursday.", "I take Adderall.", "My sister is in hospital."],
)
async def test_legacy_free_text_cannot_bypass_sensitive_fact_review(history_env, content):
    response = await history_env.client.post(
        "/api/v1/memory",
        json={
            "kind": "project_fact",
            "content": content,
        },
    )
    assert response.status_code == 422
    assert (await history_env.client.get("/api/v1/memory")).json() == []
