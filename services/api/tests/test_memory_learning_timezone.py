"""Semantic dates use immutable source-turn timezones, never the later user setting."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture
from test_memory_learning import apply

from navox.db.conversation_memory import (
    MemoryConversation,
    MemoryFact,
    MemoryLearningJob,
    MemoryThread,
)
from navox.db.models import User
from navox.memory import history
from navox.memory.learning import current_source, source_digest
from navox.memory.learning_contracts import EvidenceQuote, LearningFact, LearningOutput
from navox.memory.service import MemoryError

history_env = shared_history_fixture
WHEN = datetime(2026, 10, 26, 23, 30, tzinfo=UTC)
STATEMENT = "I have an exam tomorrow. I prefer brief replies."


async def setup(env):
    generation = await enable(env)
    env.settings.memory_semantic_learning_enabled = True
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "America/New_York"
        await database.commit()
    await archive(env, generation, question=STATEMENT, when=WHEN)
    async with env.factory() as database:
        job = await database.scalar(select(MemoryLearningJob))
        identifier = job.id
        user = await database.get(User, env.user_id)
        user.timezone = "Asia/Tokyo"
        await database.commit()
    return identifier


@pytest.mark.asyncio
async def test_delayed_semantic_thread_keeps_the_archived_student_timezone(history_env):
    env = history_env
    identifier = await setup(env)
    await apply(
        env,
        identifier,
        LearningOutput(threads=(EvidenceQuote(turn_index=0, quote="I have an exam tomorrow."),)),
    )
    async with env.factory() as database:
        thread = await database.scalar(select(MemoryThread))
        assert thread is not None
        value = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            thread.id,
            "thread",
            thread.encrypted_content,
        )
        assert value["event_date"] == "2026-10-27"
        assert value["timezone"] == "America/New_York"
        source = await database.get(MemoryConversation, thread.source_conversation_id)
        source_id = source.id
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        assert transcript["turn_timezones"] == {"0": "America/New_York"}
    public = await env.client.get(f"/api/v1/memory/history/{source_id}")
    assert public.status_code == 200
    assert set(public.json()["turns"][0]) == {"role", "content", "created_at"}


@pytest.mark.asyncio
async def test_legacy_source_without_recorded_zone_abstains_dates_but_keeps_neutral_facts(
    history_env,
):
    env = history_env
    identifier = await setup(env)
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        source = await database.get(MemoryConversation, job.source_conversation_id)
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        transcript.pop("turn_timezones")
        source.encrypted_transcript = history.encrypt(
            env.settings, env.user_id, env.workspace_id, source.id, "transcript", transcript
        )
        # Genuine legacy jobs use the original prefix proof without timezone metadata.
        job.source_digest = source_digest(env.settings, source, transcript["turns"])
        await database.commit()
    await apply(
        env,
        identifier,
        LearningOutput(
            facts=(
                LearningFact(
                    turn_index=0,
                    quote="I prefer brief replies.",
                    label="Reply length",
                    category="preference",
                ),
            ),
            threads=(EvidenceQuote(turn_index=0, quote="I have an exam tomorrow."),),
        ),
    )
    async with env.factory() as database:
        assert list(await database.scalars(select(MemoryThread))) == []
        fact = await database.scalar(select(MemoryFact))
        value = history.decrypt(
            env.settings, env.user_id, env.workspace_id, fact.id, "fact", fact.encrypted_content
        )
        assert value["content"] == "I prefer brief replies."
        assert value["source_timezone"] is None


@pytest.mark.asyncio
async def test_recorded_timezone_is_part_of_the_source_prefix_proof(history_env):
    env = history_env
    identifier = await setup(env)
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        source = await database.get(MemoryConversation, job.source_conversation_id)
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        transcript["turn_timezones"]["0"] = "Asia/Tokyo"
        source.encrypted_transcript = history.encrypt(
            env.settings, env.user_id, env.workspace_id, source.id, "transcript", transcript
        )
        await database.commit()
    with pytest.raises(MemoryError):
        await apply(
            env,
            identifier,
            LearningOutput(
                threads=(EvidenceQuote(turn_index=0, quote="I have an exam tomorrow."),)
            ),
        )
    async with env.factory() as database:
        assert list(await database.scalars(select(MemoryThread))) == []


@pytest.mark.asyncio
async def test_later_profile_change_and_append_preserve_each_turn_zone_and_old_prefix(history_env):
    env = history_env
    identifier = await setup(env)
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        source = await database.get(MemoryConversation, job.source_conversation_id)
        native_source_id = source.source_id
        job.status, job.claim_id = "processing", uuid4()
        generation = job.generation
        await database.commit()
    await archive(
        env,
        generation,
        source_id=native_source_id,
        sequence=2,
        question="I code in Python.",
        when=WHEN + timedelta(minutes=1),
    )
    async with env.factory() as database:
        old = await database.get(MemoryLearningJob, identifier)
        _, transcript, turns = await current_source(
            database, env.settings, old, now=datetime.now(UTC)
        )
        assert turns[0]["timezone"] == "America/New_York"
        assert transcript["turn_timezones"] == {"0": "America/New_York", "2": "Asia/Tokyo"}
        latest = await database.scalar(
            select(MemoryLearningJob).where(MemoryLearningJob.sequence == 2)
        )
        _, _, turns = await current_source(database, env.settings, latest, now=datetime.now(UTC))
        assert [turn["timezone"] for turn in turns] == ["America/New_York", "Asia/Tokyo"]
