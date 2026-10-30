"""A quoted memory proposal is not a saved fact or model-granted consent."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_conversation_memory import archive, enable
from test_conversation_memory import history_env as shared_history_fixture

from navox.api.auth import hash_session_token
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryPrivacy
from navox.db.models import UserSession
from navox.db.vox import VoxCall, VoxState
from navox.memory import candidates
from navox.memory.candidates import CandidateArguments, prepare_candidate
from navox.memory.service import MemoryError
from navox.vox import service as vox_service
from navox.vox.memory_save_tools import SAVE_MEMORY_TOOL, MemorySaveTools
from navox.vox.recall_tools import read_owner_memory
from navox.vox.schemas import VoxLease
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost, WorkerAuthority

history_env = shared_history_fixture
QUOTE = "Our synthetic robotics project uses a yellow prototype enclosure."
LABEL = "Robotics project"
CONFIRM = "/api/v1/memory/candidates/confirm"


class Check:
    def __init__(self, env):
        self.factory = env.factory
        self.authority = WorkerAuthority(env.user_id, env.workspace_id)
        self.changed = False
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.changed and self.calls > 1:
            return WorkerAuthority(uuid4(), uuid4())
        return self.authority


@pytest_asyncio.fixture
async def candidate_env(history_env):
    env = history_env
    env.generation = await enable(env)
    env.call_id = uuid4()
    now = datetime.now(UTC)
    token = env.client.cookies.get(env.settings.session_cookie_name)
    async with env.factory() as database:
        session = await database.scalar(
            select(UserSession).where(UserSession.token_hash == hash_session_token(token))
        )
        env.auth_session_id = session.id
        database.add(VoxState(user_id=env.user_id, workspace_id=env.workspace_id, generation=0))
        database.add(
            VoxCall(
                id=env.call_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                auth_session_id=session.id,
                client_call_id=uuid4(),
                generation=0,
                status="reserved",
                created_at=now,
                expires_at=now + timedelta(minutes=20),
            )
        )
        await database.commit()
    await archive(env, env.generation, source_id=env.call_id, question=QUOTE)
    env.published = []
    env.check = Check(env)

    async def publish(value):
        env.published.append(value)

    env.tools = MemorySaveTools(
        cast(DatabaseAuthority, env.check), env.settings, call_id=env.call_id, publish=publish
    )
    return env


async def proposal(env, *, quote=QUOTE, label=LABEL):
    async with env.factory() as database:
        return await prepare_candidate(
            database,
            env.settings,
            env.user_id,
            env.workspace_id,
            env.call_id,
            CandidateArguments(quote=quote, label=label),
        )


def body(review, *, agreement=True):
    return {
        "review_id": review["id"],
        "proposal": review["proposal"],
        "confirmation": "SAVE MEMORY",
        "sensitive_agreement": agreement,
    }


async def count_facts(env):
    async with env.factory() as database:
        return await database.scalar(select(func.count()).select_from(MemoryFact))


@pytest.mark.asyncio
async def test_model_prepares_private_review_without_persisting_or_receiving_proof(candidate_env):
    env = candidate_env
    result = await env.tools.execute(SAVE_MEMORY_TOOL, {"quote": QUOTE, "label": LABEL})
    assert result["status"] == "review_required" and result["applied"] is False
    assert "review" not in result and QUOTE not in str(result)
    assert len(env.published) == 1 and await count_facts(env) == 0
    private = env.published[0]
    review = private["review"]
    assert review["content"] == QUOTE and review["sensitive"] is True
    assert review["source_kind"] == "vox" and review["source_turn"] == 1
    assert QUOTE not in review["proposal"] and len(review["proposal"]) <= 8192
    assert private["destination"] == "/settings#memory-privacy"
    assert "?" not in private["destination"]


@pytest.mark.asyncio
async def test_browser_confirm_requires_separate_agreement_and_retries_are_idempotent(
    candidate_env,
):
    env = candidate_env
    review = (await proposal(env))["review"]
    denied = await env.client.post(CONFIRM, json=body(review, agreement=False))
    assert denied.status_code == 422 and await count_facts(env) == 0
    assert denied.headers["cache-control"] == "private, no-store"
    saved = await env.client.post(CONFIRM, json=body(review))
    assert saved.status_code == 201, saved.text
    value = saved.json()
    assert value["id"] == review["id"] and value["content"] == QUOTE
    assert value["sensitive"] is True
    assert value["source_conversation_id"] == review["source_conversation_id"]
    again = await env.client.post(CONFIRM, json=body(review))
    assert again.status_code == 201 and again.json() == value
    assert await count_facts(env) == 1
    async with env.factory() as database:
        row = await database.get(MemoryFact, UUID(value["id"]))
        assert QUOTE not in row.encrypted_content
        context = await read_owner_memory(
            database, env.settings, env.user_id, env.workspace_id, "robotics"
        )
        assert context["facts"] == [] and context["sensitive_details_withheld"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quote", ["yellow prototype enclosure", "We can plan around Friday.", "Unsaid claim."]
)
async def test_only_whole_archived_student_messages_match(candidate_env, quote):
    env = candidate_env
    with pytest.raises(MemoryError):
        await proposal(env, quote=quote)
    assert await count_facts(env) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "erased", "suppressed", "foreign", "generation"])
async def test_invalid_or_obsolete_sources_never_create_reviews(candidate_env, change):
    env = candidate_env
    async with env.factory() as database:
        source = await database.scalar(select(MemoryConversation))
        if change == "expired":
            source.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "erased":
            source.erased_at = datetime.now(UTC)
            source.encrypted_transcript = source.encrypted_summary = None
        elif change == "suppressed":
            source.encrypted_summary = None
        elif change == "foreign":
            source.source_id = uuid4()
        else:
            source.generation += 1
        await database.commit()
    with pytest.raises(MemoryError):
        await proposal(env)
    assert await count_facts(env) == 0


@pytest.mark.asyncio
async def test_recent_source_bound_and_no_model_authority_arguments(candidate_env):
    env = candidate_env
    for sequence in range(2, 6):
        await archive(
            env,
            env.generation,
            source_id=env.call_id,
            sequence=sequence,
            question=f"A later synthetic message number {sequence}.",
        )
    with pytest.raises(MemoryError):
        await proposal(env)
    for arguments in (
        {"quote": QUOTE, "label": LABEL, "user_id": str(env.user_id)},
        {"quote": QUOTE, "label": LABEL, "source_conversation_id": str(uuid4())},
        {"quote": QUOTE, "label": LABEL, "approved": True},
        {"quote": QUOTE, "label": LABEL, "sensitive": False},
        {"quote": "My password is a yellow prototype.", "label": LABEL},
        {"quote": "My pass\u200bword is a yellow prototype.", "label": LABEL},
    ):
        result = await env.tools.execute(SAVE_MEMORY_TOOL, arguments)
        assert result["status"] == "invalid_arguments" and result["applied"] is False
    assert env.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["offrecord", "forgotten", "expired", "wrong_id", "tampered"])
async def test_proof_fences_rechecked_at_confirmation(candidate_env, monkeypatch, change):
    env = candidate_env
    review = (await proposal(env))["review"]
    payload = body(review)
    if change == "offrecord":
        await enable(env, off_record=True)
    elif change == "forgotten":
        deleted = await env.client.delete(
            f"/api/v1/memory/history/{review['source_conversation_id']}"
        )
        assert deleted.status_code == 204
    elif change == "expired":

        async def later(database):
            return datetime.now(UTC) + timedelta(minutes=6)

        monkeypatch.setattr(candidates, "database_now", later)
    elif change == "wrong_id":
        payload["review_id"] = str(uuid4())
    else:
        payload["proposal"] = "x" + payload["proposal"][1:]
    rejected = await env.client.post(CONFIRM, json=payload)
    assert rejected.status_code == 409, rejected.text
    assert QUOTE not in rejected.text and await count_facts(env) == 0


@pytest.mark.asyncio
async def test_review_cannot_cross_web_login_even_for_same_owner(candidate_env):
    env = candidate_env
    review = (await proposal(env))["review"]
    login = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "history-owner@example.com", "password": "twelve-character-password"},
    )
    assert login.status_code == 200
    rejected = await env.client.post(CONFIRM, json=body(review))
    assert rejected.status_code == 409 and await count_facts(env) == 0


@pytest.mark.asyncio
async def test_edit_cannot_silently_lower_private_candidate_classification(candidate_env):
    env = candidate_env
    review = (await proposal(env))["review"]
    saved = await env.client.post(CONFIRM, json=body(review))
    assert saved.status_code == 201 and saved.json()["sensitive"] is True
    path = f"/api/v1/memory/facts/{review['id']}"
    refused = await env.client.patch(path, json={"label": "Updated private project"})
    assert refused.status_code == 422
    changed = await env.client.patch(
        path, json={"label": "Updated private project", "sensitive_agreement": True}
    )
    assert changed.status_code == 200 and changed.json()["sensitive"] is True
    changed_again = await env.client.patch(
        path, json={"label": "Another project label", "sensitive_agreement": True}
    )
    assert changed_again.status_code == 200 and changed_again.json()["sensitive"] is True


@pytest.mark.asyncio
async def test_changed_authority_prevents_private_review_publication(candidate_env):
    env = candidate_env
    env.check.changed = True
    with pytest.raises(WorkerAccessLost):
        await env.tools.execute(SAVE_MEMORY_TOOL, {"quote": QUOTE, "label": LABEL})
    assert env.published == [] and await count_facts(env) == 0


@pytest.mark.asyncio
async def test_manual_review_can_finish_after_hangup_but_not_after_forget(candidate_env):
    env = candidate_env
    review = (await proposal(env))["review"]
    async with env.factory() as database:
        call = await database.get(VoxCall, env.call_id)
        await vox_service.end(
            database,
            env.user_id,
            env.workspace_id,
            hash_session_token(env.client.cookies.get(env.settings.session_cookie_name)),
            call.client_call_id,
            VoxLease(generation=call.generation),
        )
    async with env.factory() as database:
        state = await database.scalar(select(VoxState))
        assert state.generation == 1  # Exercise the real native end generation advance.
    saved = await env.client.post(CONFIRM, json=body(review))
    assert saved.status_code == 201
    deleted = await env.client.delete(f"/api/v1/memory/facts/{review['id']}")
    assert deleted.status_code == 204
    assert (await env.client.post(CONFIRM, json=body(review))).status_code == 409
    assert await count_facts(env) == 0


@pytest.mark.asyncio
async def test_review_confirmation_requires_exact_browser_authority_and_no_input_echo(
    candidate_env,
):
    env = candidate_env
    review = (await proposal(env))["review"]
    rejected = await env.client.post(
        CONFIRM, json=body(review), headers={"Origin": "https://wrong.example"}
    )
    assert rejected.status_code == 403
    rejected = await env.client.post(CONFIRM, json={**body(review), "content": QUOTE})
    assert rejected.status_code == 422 and QUOTE not in rejected.text
    assert rejected.headers["cache-control"] == "private, no-store"
    assert await count_facts(env) == 0


@pytest.mark.asyncio
async def test_source_classification_is_server_owned_and_conservative(candidate_env):
    env = candidate_env
    now = datetime.now(UTC)
    assert candidates.source_sensitivity(QUOTE, LABEL, {}, now=now, timezone="UTC")
    assert not candidates.source_sensitivity(
        "I study best in the morning.", "Study time", {}, now=now, timezone="UTC"
    )
    assert candidates.source_sensitivity(
        "I study best in the morning.",
        "Study time",
        {"automatic_extraction_blocked": True},
        now=now,
        timezone="UTC",
    )
    review = (await proposal(env))["review"]
    async with env.factory() as database:
        privacy = await database.get(MemoryPrivacy, (env.user_id, env.workspace_id))
        privacy.generation += 1
        await database.commit()
    assert (await env.client.post(CONFIRM, json=body(review))).status_code == 409


@pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Concurrent review confirmations require PostgreSQL owner locks",
)
@pytest.mark.asyncio
async def test_concurrent_confirmation_creates_one_fact(candidate_env):
    env = candidate_env
    review = (await proposal(env))["review"]
    responses = await asyncio.gather(
        env.client.post(CONFIRM, json=body(review)), env.client.post(CONFIRM, json=body(review))
    )
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    assert await count_facts(env) == 1
