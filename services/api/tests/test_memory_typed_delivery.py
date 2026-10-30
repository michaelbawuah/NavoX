"""An offered check-in is retryable until an actual typed answer can be returned."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_ai_conversation import install_conversation_runtime, new_conversation
from test_ai_intent_plan import intent_env as shared_intent_fixture
from test_memory_conversation_integration import prepare

from navox.ai.context import ContextDenied
from navox.db.conversation_memory import MemoryThread
from navox.memory import history
from navox.memory.service import lock_owner
from navox.vox.recall_tools import read_owner_memory

intent_env = shared_intent_fixture
PATH = "/api/v1/ai/assistant/conversation"
PROMPT = "How did your interview go?"


async def event_thread(env, settings):
    when = datetime.now(UTC) - timedelta(days=2)
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        privacy = await history.privacy_row(database, env.user_id, env.workspace_id)
        await history.append_completed_turn(
            database,
            settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            source_id=uuid4(),
            source_kind="vox",
            expected_generation=privacy.generation,
            sequence=1,
            question="I have an interview today.",
            answer="Good luck with the interview.",
            now=when,
        )
        await database.commit()


async def saved_thread(env, settings):
    async with env.factory() as database:
        thread = await database.scalar(select(MemoryThread))
        value = history.decrypt(
            settings,
            env.user_id,
            env.workspace_id,
            thread.id,
            "thread",
            thread.encrypted_content,
        )
        return thread, value


@pytest.mark.asyncio
async def test_typed_answer_marks_exposure_before_return_and_hides_internal_claim_ids(intent_env):
    env = intent_env
    settings = await prepare(env)
    await event_thread(env, settings)
    identifier = (await saved_thread(env, settings))[0].id
    adapter = await install_conversation_runtime(
        env, public=True, output=json.dumps({"answer": PROMPT})
    )
    session = await new_conversation(env)
    response = await env.client.post(PATH, json={"session_id": session, "utterance": "Hello."})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == PROMPT
    supplied = json.loads(adapter.calls[0].context.text)["saved_memory"]
    assert supplied["threads"][0]["content"]["prompt"] == PROMPT
    assert "_followup_claim_ids" not in supplied
    assert str(identifier) not in adapter.calls[0].context.text
    thread, value = await saved_thread(env, settings)
    assert thread.status == "responded"
    assert value["delivery_version"] == 1
    assert value["offered_context"] == f"chat:{session}"
    assert "exposure_started_at" in value and "responded_at" in value


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["qualification", "invalid_output"])
async def test_no_answer_releases_claim_for_another_context(intent_env, failure):
    env = intent_env
    settings = await prepare(env)
    await event_thread(env, settings)
    await install_conversation_runtime(
        env,
        public=True,
        failure="qualification" if failure == "qualification" else None,
        output="not-json" if failure == "invalid_output" else None,
    )
    session = await new_conversation(env)
    response = await env.client.post(PATH, json={"session_id": session, "utterance": "Hello."})
    assert response.status_code == 503, response.text
    thread, value = await saved_thread(env, settings)
    assert thread.status == "open"
    assert "offered_context" not in value and "exposure_started_at" not in value
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        retry = await read_owner_memory(
            database,
            settings,
            env.user_id,
            env.workspace_id,
            "",
            context_key=f"chat:{uuid4()}",
            claim_receipts=True,
        )
        assert retry["threads"][0]["content"]["prompt"] == PROMPT
        await database.commit()


@pytest.mark.asyncio
async def test_archive_failure_rolls_back_exposure_then_releases_unreturned_answer(
    intent_env, monkeypatch
):
    env = intent_env
    settings = await prepare(env)
    await event_thread(env, settings)
    await install_conversation_runtime(env, public=True, output=json.dumps({"answer": PROMPT}))
    session = await new_conversation(env)

    async def fail_archive(*args, **kwargs):
        raise ContextDenied("Synthetic archive refusal before returning an answer")

    monkeypatch.setattr(history, "append_completed_turn", fail_archive)
    response = await env.client.post(PATH, json={"session_id": session, "utterance": "Hello."})
    assert response.status_code == 403, response.text
    thread, value = await saved_thread(env, settings)
    assert thread.status == "open"
    assert "offered_context" not in value and "exposure_started_at" not in value


@pytest.mark.asyncio
async def test_returned_answer_without_exact_followup_keeps_conservative_exposure(intent_env):
    env = intent_env
    settings = await prepare(env)
    await event_thread(env, settings)
    await install_conversation_runtime(env, public=True, output='{"answer":"Hello there."}')
    session = await new_conversation(env)
    response = await env.client.post(PATH, json={"session_id": session, "utterance": "Hello."})
    assert response.status_code == 200
    thread, value = await saved_thread(env, settings)
    assert thread.status == "offered" and "exposure_started_at" in value
    assert "responded_at" not in value  # The host never pretends the check-in was asked.
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        other = await read_owner_memory(
            database,
            settings,
            env.user_id,
            env.workspace_id,
            "",
            context_key=f"chat:{uuid4()}",
            claim_receipts=True,
        )
        assert other["threads"] == []
