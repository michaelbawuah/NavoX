"""Synthetic semantic-selection fixtures; no model/provider call qualifies these tests."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture
from test_memory_candidates import candidate_env as shared_candidate_fixture

from navox.ai.foundation.contracts import JSONDocument
from navox.ai.routing import PolicyRules
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryLearningJob
from navox.memory import history, learning_runtime
from navox.memory.history_schemas import FactUpdate
from navox.memory.learning import (
    LearningConfirmation,
    apply_learning,
    confirm_review,
    dismiss_review,
    neutral_fragment,
    sweep_learning_jobs,
)
from navox.memory.learning_contracts import LearningFact, LearningOutput
from navox.memory.learning_runtime import process_one
from navox.memory.service import MemoryError, lock_owner
from navox.vox.memory_learning_tools import LEARN_MEMORY_TOOL, MemoryLearningTools

history_env = shared_history_fixture
candidate_env = shared_candidate_fixture
MULTI = "I prefer brief replies. I code in Python."
PRIVATE = "Our synthetic robotics project uses a yellow prototype enclosure."


def output(*quotes):
    return LearningOutput(
        facts=tuple(
            LearningFact(
                turn_index=None, quote=quote, label=f"Synthetic fact {index}", category="other"
            )
            for index, quote in enumerate(quotes)
        )
    )


async def setup(env, question=MULTI):
    generation = await enable(env)
    env.settings.memory_semantic_learning_enabled = True
    _, source_id = await archive(env, generation, question=question)
    async with env.factory() as database:
        job = await database.scalar(select(MemoryLearningJob))
        assert job is not None
        return generation, source_id, job.id


async def apply(env, identifier, selected):
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        job = await database.get(MemoryLearningJob, identifier)
        claim = job.claim_id or uuid4()
        if job.status == "queued":
            job.status, job.claim_id, job.claimed_at = "processing", claim, datetime.now(UTC)
        result = await apply_learning(
            database, env.settings, job, selected, claim_id=claim, now=datetime.now(UTC)
        )
        await database.commit()
        return result


async def facts(env):
    async with env.factory() as database:
        rows = list(await database.scalars(select(MemoryFact)))
        return [
            (
                row,
                history.decrypt(
                    env.settings,
                    row.user_id,
                    row.workspace_id,
                    row.id,
                    "fact",
                    row.encrypted_content,
                ),
            )
            for row in rows
        ]


@pytest.mark.asyncio
async def test_default_off_has_no_jobs_and_status_read_does_not_opt_in(history_env):
    env = history_env
    await enable(env)
    await archive(env, 1)
    response = await env.client.get("/api/v1/memory/learning")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "configured": False,
        "generation": 1,
        "status_reason": "unconfigured",
        "pending": [],
        "has_more": False,
    }
    assert "no-store" in response.headers["cache-control"]
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(MemoryLearningJob)) == 0


@pytest.mark.asyncio
async def test_semantic_selection_certifies_independent_neutral_sentences_only(history_env):
    env = history_env
    _, _, identifier = await setup(env)
    assert await facts(env) == []  # Whole-turn literal learner cannot parse the compound turn.
    result = await apply(env, identifier, output("I prefer brief replies.", "I code in Python."))
    assert result == {"saved_count": 2, "review_count": 0}
    saved = await facts(env)
    assert {value["key"] for _, value in saved} == {"reply_length", "programming_language"}
    assert all(value["extraction_method"] == "semantic_v1" for _, value in saved)
    assert all(value["source_turn"] == 1 and value["source_turn_index"] == 0 for _, value in saved)
    assert all(not row.sensitive for row, _ in saved)
    assert (
        await apply(env, identifier, output("I prefer brief replies.", "I code in Python."))
        == result
    )
    assert len(await facts(env)) == 2


@pytest.mark.parametrize(
    "text",
    [
        'My friend said "I prefer brief replies."',
        "It is not true that I prefer brief replies.",
        "If I prefer brief replies, choose that option.",
        "Example: I prefer brief replies.",
        "```I prefer brief replies.```",
        "I never said this. I prefer brief replies.",
        "I am discussing my family. I prefer brief replies.",
        "Do I prefer brief replies?",
        "He told me the following. I prefer brief replies.",
        "She wrote these words. I prefer brief replies.",
        "I am reading a script. I prefer brief replies.",
        "A friend is speaking. I prefer brief replies.",
    ],
)
def test_parent_source_qualifiers_cannot_be_erased_by_selected_span(text):
    assert not neutral_fragment(
        {"content": text}, "I prefer brief replies.", timezone="UTC", now=datetime.now(UTC)
    )


@pytest.mark.asyncio
async def test_reported_unknown_parent_is_private_review_not_automatic_fact(history_env):
    env = history_env
    _, _, identifier = await setup(env, "He told me the following. I prefer brief replies.")
    result = await apply(env, identifier, output("I prefer brief replies."))
    assert result == {"saved_count": 0, "review_count": 1} and await facts(env) == []
    state = (await env.client.get("/api/v1/memory/learning")).json()
    assert state["pending"][0]["content"] == "I prefer brief replies."
    assert state["pending"][0]["sensitive"] is True


@pytest.mark.asyncio
async def test_model_unknown_category_never_grants_neutral_storage(history_env):
    env = history_env
    generation, _, identifier = await setup(env, PRIVATE)
    result = await apply(env, identifier, output(PRIVATE))
    assert result == {"saved_count": 0, "review_count": 1} and await facts(env) == []
    response = await env.client.get("/api/v1/memory/learning")
    assert response.status_code == 200
    state = response.json()
    assert state["enabled"] is True and state["configured"] is False
    proposal = state["pending"][0]
    assert proposal["content"] == PRIVATE and proposal["sensitive"] is True
    body = {"generation": generation, "confirmation": "SAVE MEMORY", "sensitive_agreement": False}
    rejected = await env.client.post(f"/api/v1/memory/learning/{proposal['id']}/confirm", json=body)
    assert rejected.status_code == 422 and await facts(env) == []
    body["sensitive_agreement"] = True
    confirmed = await env.client.post(
        f"/api/v1/memory/learning/{proposal['id']}/confirm", json=body
    )
    assert confirmed.status_code == 201
    assert confirmed.json()["id"] == proposal["id"] and confirmed.json()["sensitive"] is True
    retry = await env.client.post(f"/api/v1/memory/learning/{proposal['id']}/confirm", json=body)
    assert retry.status_code == 201 and retry.json() == confirmed.json()
    async with env.factory() as database:
        source = await database.get(MemoryConversation, UUID(proposal["source_conversation_id"]))
        job = await database.get(MemoryLearningJob, identifier)
        assert source.encrypted_transcript and source.encrypted_summary is None
        assert job.encrypted_result is None and job.pending_count == 0
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        assert transcript["voice_recall_suppressed"] is True


@pytest.mark.asyncio
async def test_assistant_or_unarchived_quote_cannot_become_fact(history_env):
    env = history_env
    _, _, identifier = await setup(env)
    with pytest.raises(MemoryError):
        await apply(env, identifier, output("We can plan around Friday."))
    with pytest.raises(MemoryError):
        await apply(env, identifier, output("I prefer detailed replies."))
    assert await facts(env) == []


@pytest.mark.asyncio
async def test_append_keeps_exact_old_review_but_later_sensitive_turn_retracts_learning(
    history_env,
):
    env = history_env
    generation, source_id, identifier = await setup(env)
    await apply(env, identifier, output("I prefer brief replies."))
    assert len(await facts(env)) == 1
    await archive(
        env, generation, source_id=source_id, sequence=2, question="I am discussing my family."
    )
    assert await facts(env) == []
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        assert job.encrypted_result is None and job.status == "superseded"


@pytest.mark.asyncio
async def test_forget_during_provider_work_wins_without_resurrection(history_env):
    env = history_env
    _, _, identifier = await setup(env)
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        source_id = job.source_conversation_id
        job.status, job.claim_id = "processing", uuid4()
        await database.commit()
    async with env.factory() as database:
        await history.forget_conversation(database, env.user_id, env.workspace_id, source_id)
    async with env.factory() as database:
        assert await database.get(MemoryLearningJob, identifier) is None
        assert await database.scalar(select(func.count()).select_from(MemoryFact)) == 0


@pytest.mark.asyncio
async def test_expiry_purges_derived_proposal_ciphertext_with_retained_source_stub(history_env):
    env = history_env
    _, _, identifier = await setup(env, PRIVATE)
    await apply(env, identifier, output(PRIVATE))
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        source_id = job.source_conversation_id
        source = await database.get(MemoryConversation, source_id)
        source.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await history.expire_owner_history(database, env.user_id, env.workspace_id)
        await database.commit()
    async with env.factory() as database:
        assert await database.get(MemoryLearningJob, identifier) is None
        source = await database.get(MemoryConversation, source_id)
        assert source is not None and source.encrypted_transcript is None


@pytest.mark.asyncio
async def test_background_disable_does_not_block_existing_private_review(history_env):
    env = history_env
    generation, _, identifier = await setup(env, PRIVATE)
    await apply(env, identifier, output(PRIVATE))
    state = (await env.client.get("/api/v1/memory/learning")).json()
    suggestion = UUID(state["pending"][0]["id"])
    env.settings.memory_semantic_learning_enabled = False
    async with env.factory() as database:
        saved = await confirm_review(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            suggestion,
            LearningConfirmation(
                generation=generation, confirmation="SAVE MEMORY", sensitive_agreement=True
            ),
        )
        assert saved["id"] == suggestion and saved["sensitive"] is True


@pytest.mark.asyncio
async def test_disabled_foundation_never_routes_background_work_to_native_or_provider(history_env):
    env = history_env
    await setup(env)
    assert env.settings.ai_provider == "disabled"
    assert await process_one(env.factory, env.settings) is False
    async with env.factory() as database:
        job = await database.scalar(select(MemoryLearningJob))
        assert job.status == "queued" and job.claimed_at is None


@pytest.mark.asyncio
async def test_cross_account_and_bad_origin_cannot_access_or_confirm_private_suggestion(
    history_env,
):
    env = history_env
    await setup(env, PRIVATE)
    old = env.client.headers["X-NavoX-Account"]
    env.client.headers["X-NavoX-Account"] = f"{uuid4()}:{uuid4()}"
    assert (await env.client.get("/api/v1/memory/learning")).status_code == 409
    env.client.headers["X-NavoX-Account"] = old
    env.client.headers["Origin"] = "https://untrusted.example"
    assert (
        await env.client.post(
            f"/api/v1/memory/learning/{uuid4()}/confirm",
            json={
                "generation": 1,
                "confirmation": "SAVE MEMORY",
                "sensitive_agreement": True,
            },
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_native_tool_learns_completed_source_with_background_and_foundation_off(
    candidate_env,
):
    env = candidate_env
    assert env.settings.ai_provider == "disabled"
    assert env.settings.memory_semantic_learning_enabled is False
    await archive(env, env.generation, source_id=env.call_id, sequence=2, question=MULTI)
    tool = MemoryLearningTools(env.check, env.settings, call_id=env.call_id)
    assert tool.definitions()[0]["name"] == LEARN_MEMORY_TOOL
    result = await tool.execute(
        LEARN_MEMORY_TOOL,
        output("I prefer brief replies.", "I code in Python.").model_dump(mode="json"),
    )
    assert result["status"] == "processed" and result["saved_count"] == 2
    assert len(await facts(env)) == 2
    unsupported = await tool.execute(
        LEARN_MEMORY_TOOL, output("I prefer detailed replies.").model_dump(mode="json")
    )
    assert unsupported["applied"] is False


@pytest.mark.asyncio
async def test_private_source_confirmation_clears_other_queued_proposals(history_env):
    env = history_env
    generation, _, first = await setup(env, PRIVATE)
    await apply(env, first, output(PRIVATE))
    _, _ = await archive(env, generation, question="My synthetic workshop has a blue prototype.")
    async with env.factory() as database:
        second = await database.scalar(
            select(MemoryLearningJob).where(MemoryLearningJob.id != first)
        )
        second_id = second.id
    await apply(env, second_id, output("My synthetic workshop has a blue prototype."))
    state = (await env.client.get("/api/v1/memory/learning")).json()
    proposal = state["pending"][0]
    response = await env.client.post(
        f"/api/v1/memory/learning/{proposal['id']}/confirm",
        json={
            "generation": generation,
            "confirmation": "SAVE MEMORY",
            "sensitive_agreement": True,
        },
    )
    assert response.status_code == 201
    async with env.factory() as database:
        rows = list(await database.scalars(select(MemoryLearningJob)))
        assert all(row.encrypted_result is None and row.pending_count == 0 for row in rows)


@pytest.mark.asyncio
async def test_dismiss_removes_its_derived_quote_without_saving(history_env):
    env = history_env
    generation, _, identifier = await setup(env, PRIVATE)
    await apply(env, identifier, output(PRIVATE))
    state = (await env.client.get("/api/v1/memory/learning")).json()
    suggestion = UUID(state["pending"][0]["id"])
    async with env.factory() as database:
        await dismiss_review(
            database, env.settings, env.user_id, env.workspace_id, suggestion, generation
        )
        job = await database.get(MemoryLearningJob, identifier)
        value = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            job.id,
            "learning-result",
            job.encrypted_result,
        )
        assert PRIVATE not in str(value) and job.pending_count == 0
    assert await facts(env) == []


@pytest.mark.asyncio
async def test_older_job_cannot_revert_newer_category_or_replace_its_summary(history_env):
    env = history_env
    generation = await enable(env)
    env.settings.memory_semantic_learning_enabled = True
    before = datetime.now(UTC) - timedelta(minutes=2)
    _, source_id = await archive(env, generation, question=MULTI, when=before)
    async with env.factory() as database:
        old_job = await database.scalar(select(MemoryLearningJob))
        identifier = old_job.id
        old_job.status, old_job.claim_id, old_job.claimed_at = "processing", uuid4(), before
        await database.commit()
    await archive(
        env, generation, source_id=source_id, sequence=2, question="I prefer detailed replies."
    )
    selected = LearningOutput(
        facts=output("I prefer brief replies.").facts,
        summary=({"turn_index": 0, "quote": "I prefer brief replies."},),
    )
    # Complete the actually claimed older job after the newer declaration.
    await apply(env, identifier, selected)
    saved = await facts(env)
    assert saved[0][1]["content"] == "I prefer detailed replies."
    async with env.factory() as database:
        source = await database.scalar(select(MemoryConversation))
        summary = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "summary",
            source.encrypted_summary,
        )
        assert "detailed" in summary


@pytest.mark.asyncio
async def test_reviewed_correction_invalidates_pending_jobs_without_overwrite(history_env):
    env = history_env
    _, _, identifier = await setup(env)
    await apply(env, identifier, output("I prefer brief replies."))
    fact_id = (await facts(env))[0][0].id
    async with env.factory() as database:
        await history.edit_fact(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            fact_id,
            FactUpdate(content="I prefer detailed replies."),
        )
    with pytest.raises(MemoryError):
        await apply(env, identifier, output("I prefer brief replies."))
    assert (await facts(env))[0][1]["content"] == "I prefer detailed replies."
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        assert job.encrypted_result is None and job.status == "superseded"


@pytest.mark.asyncio
async def test_private_review_expiry_erases_job_even_when_source_retention_is_never(history_env):
    env = history_env
    generation = await enable(env, days=None)
    env.settings.memory_semantic_learning_enabled = True
    await archive(env, generation, question=PRIVATE)
    async with env.factory() as database:
        job = await database.scalar(select(MemoryLearningJob))
        identifier = job.id
    await apply(env, identifier, output(PRIVATE))
    async with env.factory() as database:
        job = await database.get(MemoryLearningJob, identifier)
        job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    assert await sweep_learning_jobs(env.factory) == 1
    async with env.factory() as database:
        assert await database.get(MemoryLearningJob, identifier) is None
        source = await database.scalar(select(MemoryConversation))
        assert source.expires_at is None and source.encrypted_transcript is not None


@pytest.mark.asyncio
async def test_late_provider_result_after_forget_cannot_recreate_source_or_derivatives(
    history_env, monkeypatch
):
    env = history_env
    _, _, identifier = await setup(env)
    env.settings.ai_provider = "automatic"

    async def ready(*args):
        return True

    class FixtureRuntime:
        store = SimpleNamespace(operator_policy=PolicyRules())

        async def execute(self, task, *, context_builder, **kwargs):
            await context_builder.build(task, {})
            async with env.factory() as database:
                job = await database.get(MemoryLearningJob, identifier)
                await history.forget_conversation(
                    database, env.user_id, env.workspace_id, job.source_conversation_id
                )
            return SimpleNamespace(
                output=JSONDocument(text=output("I prefer brief replies.").model_dump_json())
            )

    monkeypatch.setattr(learning_runtime, "background_configured", ready)
    monkeypatch.setattr(learning_runtime, "primary_only", lambda value: value)
    assert await process_one(env.factory, env.settings, runtime=FixtureRuntime()) is True
    assert await facts(env) == []
    async with env.factory() as database:
        assert await database.get(MemoryLearningJob, identifier) is None
        source = await database.scalar(select(MemoryConversation))
        assert source.encrypted_transcript is None


@pytest.mark.asyncio
async def test_postgres_concurrent_private_confirmation_is_one_fact_and_one_generation_bump(
    history_env,
):
    if not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"):
        pytest.skip("PostgreSQL row locks required")
    env = history_env
    generation, _, identifier = await setup(env, PRIVATE)
    await apply(env, identifier, output(PRIVATE))
    suggestion = UUID((await env.client.get("/api/v1/memory/learning")).json()["pending"][0]["id"])
    payload = LearningConfirmation(
        generation=generation, confirmation="SAVE MEMORY", sensitive_agreement=True
    )

    async def save():
        async with env.factory() as database:
            return await confirm_review(
                database, env.settings, env.user_id, env.workspace_id, suggestion, payload
            )

    first, second = await asyncio.gather(save(), save())
    assert first["id"] == second["id"] == suggestion and len(await facts(env)) == 1
    assert (await env.client.get("/api/v1/memory/privacy")).json()["generation"] == generation + 1


@pytest.mark.asyncio
async def test_same_source_semantic_correction_erases_old_and_unreachable_private_reviews(
    history_env,
):
    env = history_env
    generation = await enable(env)
    env.settings.memory_semantic_learning_enabled = True
    _, source_id = await archive(env, generation, question=MULTI)
    await archive(env, generation, source_id=source_id, sequence=2, question=PRIVATE)
    async with env.factory() as database:
        old = await database.scalar(select(MemoryLearningJob))
        old_id = old.id
    first = await apply(env, old_id, output("I prefer brief replies.", PRIVATE))
    assert first == {"saved_count": 1, "review_count": 1}
    await archive(
        env,
        generation,
        source_id=source_id,
        sequence=3,
        question="I prefer detailed replies. I code in Rust.",
    )
    new_quote = "My synthetic workshop uses a green prototype enclosure."
    await archive(env, generation, source_id=source_id, sequence=4, question=new_quote)
    async with env.factory() as database:
        latest = await database.scalar(
            select(MemoryLearningJob).where(MemoryLearningJob.sequence == 4)
        )
        latest_id = latest.id
    result = await apply(env, latest_id, output("I prefer detailed replies.", new_quote))
    assert result == {"saved_count": 1, "review_count": 0}
    async with env.factory() as database:
        old, latest = (
            await database.get(MemoryLearningJob, old_id),
            await database.get(MemoryLearningJob, latest_id),
        )
        assert old.encrypted_result is None and old.pending_count == 0
        value = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            latest.id,
            "learning-result",
            latest.encrypted_result,
        )
        assert value["proposals"] == [] and new_quote not in str(value)
    assert (await env.client.get("/api/v1/memory/learning")).json()["pending"] == []


@pytest.mark.asyncio
async def test_new_source_correction_erases_old_reviews_but_keeps_new_source_review_reachable(
    history_env,
):
    env = history_env
    generation = await enable(env)
    env.settings.memory_semantic_learning_enabled = True
    _, source_id = await archive(env, generation, question=MULTI)
    await archive(env, generation, source_id=source_id, sequence=2, question=PRIVATE)
    async with env.factory() as database:
        old_id = (await database.scalar(select(MemoryLearningJob))).id
    await apply(env, old_id, output("I prefer brief replies.", PRIVATE))
    _, new_source_id = await archive(
        env, generation, question="I prefer detailed replies. I code in Rust."
    )
    quote = "My synthetic workshop uses a green prototype enclosure."
    await archive(env, generation, source_id=new_source_id, sequence=2, question=quote)
    async with env.factory() as database:
        new_id = (
            await database.scalar(select(MemoryLearningJob).where(MemoryLearningJob.id != old_id))
        ).id
    result = await apply(env, new_id, output("I prefer detailed replies.", quote))
    assert result == {"saved_count": 1, "review_count": 1}
    async with env.factory() as database:
        old = await database.get(MemoryLearningJob, old_id)
        assert old.encrypted_result is None and old.pending_count == 0
    pending = (await env.client.get("/api/v1/memory/learning")).json()["pending"]
    assert len(pending) == 1 and pending[0]["content"] == quote
