from datetime import datetime
from uuid import uuid4

import pytest
from test_news_foundation import NOW

from navox.api import news_conversations
from navox.news.jobs import NewsConversationWork


@pytest.mark.asyncio
async def test_owned_conversation_dispatch_has_ids_only_and_idempotent_request(
    subscription_env, monkeypatch
):
    env = subscription_env
    env.settings.news_feed_enabled = env.settings.news_chat_enabled = True
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    async def dispatch(settings, payload):
        calls.append(payload)

    monkeypatch.setattr(news_conversations, "datetime", Clock)
    monkeypatch.setattr(news_conversations, "dispatch_news_conversation", dispatch)
    base = "/api/v1/news/conversations"
    response = await env.client.post(base, json={}, headers=env.headers)
    assert response.status_code == 201
    url = base + "/" + response.json()["id"]
    request = {"request_id": str(uuid4()), "question": "What happened?"}
    first = await env.client.post(url + "/messages", json=request, headers=env.headers)
    second = await env.client.post(url + "/messages", json=request, headers=env.headers)
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    assert len(calls) == 1 and isinstance(calls[0], NewsConversationWork)
    assert vars(calls[0]) == {
        "turn_id": first.json()["id"],
        "workspace_id": str(env.workspace_id),
        "user_id": str(env.user_id),
    }
    response = await env.client.get(url)
    assert response.json()[0]["status"] == "PROCESSING"
    assert response.json()[0]["actions_executed"] is False
    assert (
        await env.client.post(
            url + "/messages", json=request | {"question": "Changed question"}, headers=env.headers
        )
    ).status_code == 409
    assert (
        await env.client.post(
            url + "/messages", json=request | {"request_id": str(uuid4())}, headers=env.headers
        )
    ).status_code == 429
    assert (
        await env.client.post(
            url + "/messages", json=request | {"send_email": True}, headers=env.headers
        )
    ).status_code == 422
    assert (
        await env.client.post(
            url + "/messages", json=request, headers={"Origin": "https://attacker.example"}
        )
    ).status_code == 403
    assert (await env.client.get(base + "/" + str(uuid4()))).status_code == 404
    env.settings.news_chat_enabled = False
    assert (await env.client.get(url)).status_code == 503
    await env.client.post("/api/v1/auth/logout", headers=env.headers)
    assert (await env.client.get(url)).status_code == 401


@pytest.mark.asyncio
async def test_news_workflows_load_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from navox.workflows.news import (
        NewsConversationRefreshWorkflow,
        NewsSourceIngestionWorkflow,
        NewsSourceReconciliationWorkflow,
    )

    runner = SandboxedWorkflowRunner()
    for cls in (
        NewsConversationRefreshWorkflow,
        NewsSourceIngestionWorkflow,
        NewsSourceReconciliationWorkflow,
    ):
        runner.prepare_workflow(workflow._Definition.must_from_class(cls))
