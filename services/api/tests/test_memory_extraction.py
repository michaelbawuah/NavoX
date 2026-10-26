"""Literal learning stays source-linked, private and fenced with the archive."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture

from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryThread
from navox.memory import history
from navox.memory.service import lock_owner
from navox.vox.recall_tools import read_owner_memory

history_env = shared_history_fixture


async def facts(env):
    async with env.factory() as database:
        rows = list(
            await database.scalars(
                select(MemoryFact)
                .where(
                    MemoryFact.user_id == env.user_id,
                    MemoryFact.workspace_id == env.workspace_id,
                )
                .order_by(MemoryFact.created_at)
            )
        )
        return [
            (
                row,
                history.decrypt(
                    env.settings,
                    env.user_id,
                    env.workspace_id,
                    row.id,
                    "fact",
                    row.encrypted_content,
                ),
            )
            for row in rows
        ]


async def recall(env, *, context_id, now, query=""):
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        result = await read_owner_memory(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            query,
            context_key=f"vox:{context_id}",
            now=now,
        )
        await database.commit()
        return result


@pytest.mark.asyncio
async def test_literal_preference_has_exact_source_and_no_model_answer_learning(history_env):
    env = history_env
    generation = await enable(env)
    _, source_id = await archive(
        env, generation, question="I prefer brief replies.", answer="I work at Microsoft."
    )
    saved = await facts(env)
    assert len(saved) == 1
    row, value = saved[0]
    assert value["content"] == "I prefer brief replies."
    assert value["extraction_method"] == "literal_v1" and value["source_turn"] == 1
    assert value["key"] == "reply_length" and "Microsoft" not in value["content"]
    async with env.factory() as database:
        source = await database.get(MemoryConversation, row.source_conversation_id)
        assert source.source_id == source_id and source.user_id == env.user_id


@pytest.mark.asyncio
async def test_latest_same_key_correction_keeps_history_and_suppresses_old_source(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation, question="I prefer brief replies.")
    original = (await facts(env))[0][0]
    await archive(env, generation, question="Actually, I prefer detailed replies.")
    saved = await facts(env)
    assert len(saved) == 1 and saved[0][0].id == original.id
    assert saved[0][1]["content"] == "Actually, I prefer detailed replies."
    async with env.factory() as database:
        source = await database.get(MemoryConversation, original.source_conversation_id)
        assert source.encrypted_transcript is not None and source.encrypted_summary is None
    value = await recall(env, context_id=uuid4(), now=datetime.now(UTC), query="brief replies")
    assert all(
        "brief" not in excerpt.casefold()
        for item in value["past_context"]
        for excerpt in item["student_excerpts"]
    )
    assert "detailed" in value["facts"][0]["content"]


@pytest.mark.asyncio
async def test_same_conversation_correction_preserves_only_latest_category_for_recall(history_env):
    env = history_env
    generation = await enable(env)
    _, source_id = await archive(env, generation, question="I prefer brief replies.")
    await archive(
        env,
        generation,
        source_id=source_id,
        sequence=2,
        question="Actually, I prefer detailed replies.",
    )
    value = await recall(env, context_id=uuid4(), now=datetime.now(UTC), query="brief")
    assert value["past_context"] == [] and len(value["facts"]) == 1
    assert "detailed" in value["facts"][0]["content"]


@pytest.mark.asyncio
async def test_reviewed_correction_is_not_automatically_overwritten(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation, question="I prefer brief replies.")
    row = (await facts(env))[0][0]
    response = await env.client.patch(
        f"/api/v1/memory/facts/{row.id}",
        json={
            "content": "My reviewed preference is balanced replies.",
        },
    )
    assert response.status_code == 200
    generation = (await env.client.get("/api/v1/memory/privacy")).json()["generation"]
    await archive(env, generation, question="I prefer detailed replies.")
    saved = await facts(env)
    assert len(saved) == 1 and saved[0][1]["extraction_method"] == "user_corrected"
    assert "balanced" in saved[0][1]["content"]


@pytest.mark.asyncio
async def test_late_crisis_retracts_automatic_derivatives_but_keeps_reviewed_fact(history_env):
    env = history_env
    generation = await enable(env)
    _, source_id = await archive(env, generation, question="I prefer brief replies.")
    source = (await env.client.get("/api/v1/memory/history")).json()[0]
    response = await env.client.post(
        "/api/v1/memory/facts",
        json={
            "label": "Reviewed note",
            "content": "The synthetic review is Friday.",
            "source_conversation_id": source["id"],
        },
    )
    assert response.status_code == 201
    await archive(
        env,
        generation,
        source_id=source_id,
        sequence=2,
        question="I want to end my life.",
        answer="Synthetic supportive response.",
    )
    await archive(
        env, generation, source_id=source_id, sequence=3, question="I prefer a friendly tone."
    )
    saved = await facts(env)
    assert len(saved) == 1 and saved[0][1]["label"] == "Reviewed note"
    async with env.factory() as database:
        row = await database.get(MemoryConversation, UUID(source["id"]))
        assert row.encrypted_transcript is not None  # Ordinary disclosed history still retained.
        assert history.history_view(row, env.settings, include_policy=True)[
            "_automatic_extraction_blocked"
        ]
    value = await recall(
        env, context_id=uuid4(), now=datetime.now(UTC), query="brief friendly life"
    )
    assert value["past_context"] == [] and value["threads"] == []


@pytest.mark.asyncio
async def test_sensitive_source_creates_no_fact_or_thread_and_offrecord_never_learns(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation, question="I have a therapy appointment tomorrow.")
    await enable(env, off_record=True)
    assert not (await archive(env, generation, question="I prefer brief replies."))[0]
    assert await facts(env) == []
    async with env.factory() as database:
        assert list(await database.scalars(select(MemoryThread))) == []


@pytest.mark.asyncio
async def test_named_job_departure_replaces_only_matching_automatic_employer(history_env):
    env = history_env
    generation = await enable(env)
    await archive(env, generation, question="I work at Acme.")
    await archive(env, generation, question="I quit my job at Otherco.")
    assert (await facts(env))[0][1]["content"] == "I work at Acme."
    await archive(env, generation, question="I quit my job at Acme.")
    assert (await facts(env))[0][1]["content"] == "I quit my job at Acme."


@pytest.mark.asyncio
async def test_event_followup_is_later_local_day_and_claimed_once_per_context(history_env):
    env = history_env
    generation = await enable(env)
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    await archive(env, generation, question="I have an interview tomorrow.", when=now)
    context_id, other_id = uuid4(), uuid4()
    before = await recall(env, context_id=context_id, now=now + timedelta(days=1))
    assert before["threads"] == []  # The interview may still be ahead today.
    after = now + timedelta(days=2)
    first = await recall(env, context_id=context_id, now=after)
    assert first["threads"][0]["content"]["prompt"] == "How did your interview go?"
    again = await recall(env, context_id=context_id, now=after)
    assert again["threads"][0]["content"]["offered_context"] == f"vox:{context_id}"
    other = await recall(env, context_id=other_id, now=after)
    assert other["threads"] == []
    await archive(
        env,
        generation,
        source_id=context_id,
        when=after,
        question="Good morning.",
        answer="How did your interview go?",
    )
    assert (await recall(env, context_id=context_id, now=after))["threads"] == []
    async with env.factory() as database:
        thread = await database.scalar(select(MemoryThread))
        assert thread.status == "responded"


@pytest.mark.asyncio
async def test_source_forget_removes_automatic_fact_and_thread_and_fences_replay(history_env):
    env = history_env
    generation = await enable(env)
    _, source_id = await archive(env, generation, question="I prefer brief replies.")
    await archive(
        env, generation, source_id=source_id, sequence=2, question="I have an exam tomorrow."
    )
    source = (await env.client.get("/api/v1/memory/history")).json()[0]
    assert (await env.client.delete(f"/api/v1/memory/history/{source['id']}")).status_code == 204
    assert await facts(env) == []
    async with env.factory() as database:
        assert list(await database.scalars(select(MemoryThread))) == []
    assert not (
        await archive(
            env, generation, source_id=source_id, sequence=3, question="I prefer a friendly tone."
        )
    )[0]


@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Concurrent follow-up claims require isolated PostgreSQL row locks",
)
@pytest.mark.asyncio
async def test_concurrent_contexts_cannot_both_claim_the_same_followup(history_env):
    env = history_env
    generation = await enable(env)
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    await archive(env, generation, question="I have an interview tomorrow.", when=now)
    results = await asyncio.gather(
        recall(env, context_id=uuid4(), now=now + timedelta(days=2)),
        recall(env, context_id=uuid4(), now=now + timedelta(days=2)),
    )
    assert sum(bool(result["threads"]) for result in results) == 1
