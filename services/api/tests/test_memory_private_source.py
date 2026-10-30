"""Private classification withdraws source recall and already loaded contexts."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_conversation_memory import archive
from test_conversation_memory import history_env as shared_history_fixture
from test_memory_candidates import LABEL, QUOTE, body, count_facts, proposal
from test_memory_candidates import candidate_env as shared_candidate_fixture

from navox.ai.context import ContextDenied
from navox.ai.conversation import ConversationContext, ConversationRequest, conversation_task
from navox.ai.foundation.contracts import ProviderPolicy
from navox.db.communications import AssistantSession
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryPrivacy, MemoryThread
from navox.db.models import User, Workspace
from navox.db.vox import VoxCall, VoxState
from navox.memory import history
from navox.memory.service import lock_owner
from navox.vox.recall_delivery import RecallDeliveryCoordinator
from navox.vox.recall_tools import MemoryReadTools, read_owner_memory
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost

candidate_env = shared_candidate_fixture
history_env = shared_history_fixture
CONFIRM = "/api/v1/memory/candidates/confirm"


async def recall(env, query="robotics"):
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        result = await read_owner_memory(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            query,
            context_key=f"vox:{uuid4()}",
        )
        await database.commit()
        return result


@pytest.mark.asyncio
async def test_private_save_withdraws_fact_excerpt_summary_and_thread_but_keeps_history(
    candidate_env,
):
    env = candidate_env
    # Same source also has a non-sensitive automatic preference and open thread.
    await archive(
        env, env.generation, source_id=env.call_id, sequence=2, question="I prefer brief replies."
    )
    await archive(
        env,
        env.generation,
        source_id=env.call_id,
        sequence=3,
        question="I have an interview today.",
        when=datetime.now(UTC) - timedelta(days=1),
    )
    before = await recall(env)
    assert before["past_context"] and before["facts"] and before["threads"]
    review = (await proposal(env))["review"]
    saved = await env.client.post(CONFIRM, json=body(review))
    assert saved.status_code == 201, saved.text
    after = await recall(env, "robotics brief interview")
    assert after["facts"] == after["past_context"] == after["threads"] == []
    assert QUOTE not in str(after)
    detail = await env.client.get(f"/api/v1/memory/history/{review['source_conversation_id']}")
    assert detail.status_code == 200
    assert detail.json()["turns"][0]["content"] == QUOTE
    assert detail.json()["summary"] is None
    async with env.factory() as database:
        source = await database.get(MemoryConversation, UUID(review["source_conversation_id"]))
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        assert transcript["voice_recall_suppressed"] is True
        assert transcript["automatic_extraction_blocked"] is True


@pytest.mark.asyncio
async def test_private_save_fences_warmed_provider_context_and_is_idempotent(candidate_env):
    env = candidate_env
    delivery = RecallDeliveryCoordinator(
        cast(DatabaseAuthority, env.check),
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        context_key=f"vox:{env.call_id}",
        notice_available=True,
    )
    tools = MemoryReadTools(
        cast(DatabaseAuthority, env.check),
        env.settings,
        context_key=f"vox:{env.call_id}",
        delivery=delivery,
    )
    loaded = await tools.context("robotics")
    assert QUOTE in str(loaded)
    notice = await delivery.before_output()
    assert notice is not None
    await delivery.notice_played(notice)
    review = (await proposal(env))["review"]
    saved = await env.client.post(CONFIRM, json=body(review))
    assert saved.status_code == 201, saved.text
    with pytest.raises(WorkerAccessLost):
        await delivery.output_started()
    async with env.factory() as database:
        privacy = await database.get(MemoryPrivacy, (env.user_id, env.workspace_id))
        assert privacy.generation == env.generation + 1
        assert await database.get(VoxCall, env.call_id) is None
        state = await database.scalar(select(VoxState))
        assert state.generation == 1
    again = await env.client.post(CONFIRM, json=body(review))
    assert again.status_code == 201 and again.json() == saved.json()
    async with env.factory() as database:
        privacy = await database.get(MemoryPrivacy, (env.user_id, env.workspace_id))
        assert privacy.generation == env.generation + 1


@pytest.mark.asyncio
async def test_private_source_suppression_survives_future_append_and_transcript_expiry(
    candidate_env,
):
    env = candidate_env
    await archive(
        env, env.generation, source_id=env.call_id, sequence=2, question="I prefer brief replies."
    )
    review = (await proposal(env))["review"]
    # Exercise the sticky archive policy independently of terminal call revocation.
    async with env.factory() as database:
        await lock_owner(database, env.user_id)
        source = await database.get(MemoryConversation, UUID(review["source_conversation_id"]))
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        history.suppress_source_voice_recall(env.settings, source, transcript)
        await database.commit()
    await archive(
        env, env.generation, source_id=env.call_id, sequence=3, question="I have an exam tomorrow."
    )
    async with env.factory() as database:
        source = await database.get(MemoryConversation, UUID(review["source_conversation_id"]))
        assert source.encrypted_summary is None
        transcript = history.decrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            source.id,
            "transcript",
            source.encrypted_transcript,
        )
        assert transcript["voice_recall_suppressed"] is True
        assert await database.scalar(select(MemoryThread.id)) is None
    # A retained private fact's source policy also excludes neutral sibling facts
    # after the transcript/summary are erased by the retention sweep.
    async with env.factory() as database:
        source = await database.get(MemoryConversation, UUID(review["source_conversation_id"]))
        private_id, sibling_id = uuid4(), uuid4()
        for identifier, sensitive, text in (
            (private_id, True, QUOTE),
            (sibling_id, False, "A neutral robotics sibling."),
        ):
            database.add(
                MemoryFact(
                    id=identifier,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    source_conversation_id=source.id,
                    source_kind="vox",
                    sensitive=sensitive,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                    encrypted_content=history.encrypt(
                        env.settings,
                        env.user_id,
                        env.workspace_id,
                        identifier,
                        "fact",
                        {"label": LABEL, "content": text, "supersedes_source": True},
                    ),
                )
            )
        source.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await history.expire_owner_history(database, env.user_id, env.workspace_id)
        await database.commit()
    assert (await recall(env))["facts"] == []


@pytest.mark.asyncio
async def test_private_save_invalidates_only_owner_active_native_chat_contexts(candidate_env):
    env = candidate_env
    own, closed, other = uuid4(), uuid4(), uuid4()
    other_user, other_workspace = uuid4(), uuid4()
    async with env.factory() as database:
        database.add(User(id=other_user, email="other-private-memory@example.com"))
        database.add(Workspace(id=other_workspace, name="Other", workspace_type="personal"))
        await database.flush()
        database.add_all(
            [
                AssistantSession(id=own, user_id=env.user_id, workspace_id=env.workspace_id),
                AssistantSession(
                    id=closed, user_id=env.user_id, workspace_id=env.workspace_id, status="closed"
                ),
                AssistantSession(id=other, user_id=other_user, workspace_id=other_workspace),
            ]
        )
        await database.commit()
    request = ConversationRequest(session_id=own, utterance="What did I say about robotics?")
    task = conversation_task(
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        policy=ProviderPolicy(
            workspace_id=env.workspace_id, user_id=env.user_id, revision=1, grants=()
        ),
        max_cost=Decimal("0.01"),
    )
    context = ConversationContext(
        env.factory,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        request=request,
    )
    assert QUOTE in (await context.build(task, {})).content.text
    review = (await proposal(env))["review"]
    assert (await env.client.post(CONFIRM, json=body(review))).status_code == 201
    # A fresh request cannot replay the same active chat's audited/cached text.
    fresh = ConversationContext(
        env.factory,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        request=request,
    )
    with pytest.raises(ContextDenied):
        await fresh.build(task, {})
    async with env.factory() as database:
        assert await database.get(AssistantSession, own) is None
        assert await database.get(AssistantSession, closed) is not None
        assert await database.get(AssistantSession, other) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["edit", "forget", "expiry", "reset"])
async def test_private_receipt_cannot_overwrite_edit_or_resurrect_erasure(candidate_env, change):
    env = candidate_env
    review = (await proposal(env))["review"]
    saved = await env.client.post(CONFIRM, json=body(review))
    assert saved.status_code == 201
    if change == "edit":
        changed = await env.client.patch(
            f"/api/v1/memory/facts/{review['id']}",
            json={"label": "Reviewed new label", "sensitive_agreement": True},
        )
        assert changed.status_code == 200
    elif change == "forget":
        assert (await env.client.delete(f"/api/v1/memory/facts/{review['id']}")).status_code == 204
    elif change == "expiry":
        async with env.factory() as database:
            source = await database.get(MemoryConversation, UUID(review["source_conversation_id"]))
            source.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await history.expire_owner_history(database, env.user_id, env.workspace_id)
            await database.commit()
    else:
        generation = (await env.client.get("/api/v1/memory/privacy")).json()["generation"]
        response = await env.client.request(
            "DELETE",
            "/api/v1/memory/all",
            json={"generation": generation, "confirmation": "DELETE EVERYTHING"},
        )
        assert response.status_code == 204
    assert (await env.client.post(CONFIRM, json=body(review))).status_code == 409
    assert await count_facts(env) == (0 if change in {"forget", "reset"} else 1)
