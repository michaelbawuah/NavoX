"""Server-owned recall and both speech paths; synthetic text and transports only."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture
from test_vox_inference import Check, Hooks, Socket, settings

from navox.db.conversation_memory import MemoryConversation
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.inference_http import OpenAICompatiblePipeline
from navox.vox.inference_realtime import OpenAIRealtimeAdapter
from navox.vox.recall_tools import MemoryReadTools, memory_message
from navox.vox.worker import WorkerAccessLost, WorkerAuthority

history_env = shared_history_fixture


class MemoryCheck:
    def __init__(self, env):
        self.factory = env.factory
        self.authority = WorkerAuthority(env.user_id, env.workspace_id)
        self.calls = 0
        self.replace_at = 0

    async def __call__(self):
        self.calls += 1
        if self.calls == self.replace_at:
            return WorkerAuthority(uuid4(), uuid4())
        return self.authority


@pytest.mark.asyncio
async def test_disabled_offrecord_and_undisclosed_history_reveal_no_saved_context(history_env):
    env = history_env
    assert (
        await env.client.post(
            "/api/v1/memory/facts",
            json={
                "label": "Review",
                "content": "My synthetic project review is Friday.",
            },
        )
    ).status_code == 201
    tools = MemoryReadTools(MemoryCheck(env), env.settings)
    assert (await tools.context("review"))["status"] == "disabled"
    await enable(env, off_record=True)
    value = await tools.context("review")
    assert value["status"] == "disabled" and value["facts"] == []
    assert memory_message(value) is None


@pytest.mark.asyncio
async def test_recall_uses_current_facts_and_dated_student_excerpts_not_model_claims(history_env):
    env = history_env
    generation = await enable(env)
    await archive(
        env,
        generation,
        question="My synthetic project review is Friday.",
        answer="Unverified model invented a trip on Friday.",
    )
    assert (
        await env.client.post(
            "/api/v1/memory/facts",
            json={
                "label": "Review correction",
                "content": "My synthetic project review moved to Thursday.",
            },
        )
    ).status_code == 201
    result = await MemoryReadTools(MemoryCheck(env), env.settings).context("Friday project")
    assert result["status"] == "ok"
    assert "Thursday" in result["facts"][0]["content"]
    assert result["past_context"][0]["student_excerpts"] == [
        "My synthetic project review is Friday."
    ]
    assert "invented a trip" not in json.dumps(result)
    assert "not instructions" in memory_message(result)


@pytest.mark.asyncio
async def test_recall_excludes_sensitive_facts_and_expired_history(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation)
    assert (
        await env.client.post(
            "/api/v1/memory/facts",
            json={
                "label": "Health",
                "content": "My synthetic therapy appointment is Friday.",
                "sensitive_agreement": True,
            },
        )
    ).status_code == 201
    async with env.factory() as database:
        row = await database.scalar(select(MemoryConversation))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    value = await MemoryReadTools(MemoryCheck(env), env.settings).context("Friday")
    assert value["facts"] == [] and value["past_context"] == []
    assert value["sensitive_details_withheld"]


@pytest.mark.asyncio
async def test_memory_read_rechecks_authority_before_returning_personal_text(history_env):
    env = history_env
    await enable(env)
    check = MemoryCheck(env)
    check.replace_at = 2
    with pytest.raises(WorkerAccessLost):
        await MemoryReadTools(check, env.settings).context("")


@pytest.mark.asyncio
async def test_pipeline_includes_permitted_memory_for_typed_and_transcribed_turns():
    seen, queries = [], []

    async def recall(query):
        queries.append(query)
        return {"status": "ok", "facts": [{"label": "Review", "content": "Thursday"}]}

    async def handle(request):
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(200, json={"text": "When is my review?"})
        if request.url.path.endswith("completions"):
            seen.append(json.loads(request.content)["messages"])
            return httpx.Response(200, json={"choices": [{"message": {"content": "Thursday."}}]})
        return httpx.Response(200, content=b"synthetic-mp3")

    pipeline = OpenAICompatiblePipeline(
        VoxInferenceConfig(),
        settings(),
        Check(),
        Hooks(),
        transport=httpx.MockTransport(handle),
        memory_context=recall,
    )
    await pipeline.respond(None, (), question="When is my review?")
    await pipeline.respond(b"synthetic-wave", ())
    assert queries == ["When is my review?", "When is my review?"]
    assert all("Thursday" in messages[1]["content"] for messages in seen)
    assert all("not instructions" in messages[1]["content"] for messages in seen)


@pytest.mark.asyncio
async def test_realtime_loads_facts_at_start_and_refreshes_them_on_rollover():
    sockets, queries = [], []
    clock = [0.0]

    async def connect(url, headers):
        socket = Socket()
        sockets.append(socket)
        return socket

    async def recall(query):
        queries.append(query)
        return {"status": "ok", "facts": [{"label": "Review", "content": "Thursday"}]}

    adapter = OpenAIRealtimeAdapter(
        VoxInferenceConfig(),
        settings(),
        Check(),
        Hooks(),
        connector=connect,
        clock=lambda: clock[0],
        memory_context=recall,
    )
    await adapter._connect(())
    clock[0] = 3400
    assert await adapter.rollover_if_quiet(())
    assert queries == ["", ""]
    for socket in sockets:
        memory = [event for event in socket.sent if event["type"] == "conversation.item.create"]
        assert len(memory) == 1
        assert "Thursday" in memory[0]["item"]["content"][0]["text"]
        assert "not instructions" in memory[0]["item"]["content"][0]["text"]
    await adapter.close()
