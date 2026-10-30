"""Forget review searches real encrypted owner rows without applying any change."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import event

from navox.core.settings import Settings
from navox.db.conversation_memory import MemoryConversation, MemoryFact, MemoryPrivacy
from navox.db.memory import OwnerMemory
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.memory.history import encrypt
from navox.vox.memory_tools import FORGET_TOOL, MemoryTools
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost, WorkerAuthority


class Check:
    def __init__(self, factory, authority):
        self.factory, self.authority = factory, authority
        self.calls = 0
        self.changed = False

    async def __call__(self):
        self.calls += 1
        if self.changed and self.calls > 1:
            return WorkerAuthority(uuid4(), uuid4())
        return self.authority


@pytest_asyncio.fixture
async def memory_tool_env(ai_database):
    user_id, workspace_id = uuid4(), uuid4()
    async with ai_database() as database:
        database.add(User(id=user_id, email="forget-review@example.com"))
        database.add(Workspace(id=workspace_id, name="Personal", workspace_type="personal"))
        await database.flush()
        database.add(WorkspaceMembership(user_id=user_id, workspace_id=workspace_id, role="owner"))
        await database.commit()
    check = Check(ai_database, WorkerAuthority(user_id, workspace_id))
    settings = Settings(
        _env_file=None, connector_secret_encryption_key=Fernet.generate_key().decode()
    )
    published = []

    async def publish(value):
        published.append(value)

    return SimpleNamespace(
        user_id=user_id,
        workspace_id=workspace_id,
        factory=ai_database,
        check=check,
        settings=settings,
        tools=MemoryTools(cast(DatabaseAuthority, check), settings, publish),
        published=published,
    )


def conversation(env, *, title="Saved discussion", text="Private job details", **changes):
    identifier, now = uuid4(), datetime.now(UTC)
    return MemoryConversation(
        id=identifier,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        source_kind="vox",
        source_id=uuid4(),
        generation=0,
        last_sequence=1,
        encrypted_transcript=encrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            identifier,
            "transcript",
            {
                "title": title,
                "turns": [{"role": "user", "content": text, "created_at": now.isoformat()}],
            },
        ),
        encrypted_summary=encrypt(
            env.settings, env.user_id, env.workspace_id, identifier, "summary", text
        ),
        message_count=1,
        created_at=now,
        updated_at=now,
        **changes,
    )


def fact(env, *, label="Saved preference", text="Private job details"):
    identifier, now = uuid4(), datetime.now(UTC)
    return MemoryFact(
        id=identifier,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        source_kind="user_saved",
        encrypted_content=encrypt(
            env.settings,
            env.user_id,
            env.workspace_id,
            identifier,
            "fact",
            {"label": label, "content": text},
        ),
        sensitive=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_forget_returns_only_bounded_review_metadata_and_does_not_write(memory_tool_env):
    env = memory_tool_env
    saved_fact = fact(env, text="Private job details with a unique secret phrase")
    saved_conversation = conversation(env, text="Private job details with a unique secret phrase")
    legacy = OwnerMemory(
        id=uuid4(),
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        kind="project_fact",
        preference_key=None,
        label="Legacy preference",
        content="Private job details with a unique secret phrase",
        enabled=False,
    )
    async with env.factory() as database:
        database.add_all([saved_fact, saved_conversation, legacy])
        await database.commit()
        engine = database.get_bind()
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = await env.tools.execute(FORGET_TOOL, {"query": "  ＪＯＢ   DETAILS "})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result["status"] == "review_required" and result["applied"] is False
    assert len(env.published) == 1
    browser_review = env.published[0]
    assert {match["id"] for match in browser_review["matches"]} == {
        str(saved_fact.id),
        str(saved_conversation.id),
        str(legacy.id),
    }
    assert {match["kind"] for match in browser_review["matches"]} == {
        "fact",
        "conversation",
        "legacy_fact",
    }
    assert result["match_count"] == 3
    assert result["ambiguous"] is result["search_complete"] is True
    assert result["has_more_matches"] is False and result["searched_items"] == 3
    assert browser_review["destination"].startswith("/settings?section=memory&forget=")
    assert "unique secret phrase" not in json.dumps(result)
    assert all(
        "content" not in match and "turns" not in match for match in browser_review["matches"]
    )
    assert set(statements) == {"SELECT"}
    assert env.check.calls == 3
    async with env.factory() as database:
        assert (await database.get(MemoryConversation, saved_conversation.id)).erased_at is None
        assert await database.get(MemoryFact, saved_fact.id) is not None
        assert await database.get(OwnerMemory, legacy.id) is not None
        assert await database.get(MemoryPrivacy, (env.user_id, env.workspace_id)) is None


@pytest.mark.asyncio
async def test_forget_private_matches_reach_only_verified_browser_not_model(memory_tool_env):
    env = memory_tool_env
    saved_fact = fact(env, label="Family medical treatment", text="A private topic to forget")
    saved_fact.sensitive = True
    saved_conversation = conversation(
        env, title="Private diagnosis discussion", text="A private topic to forget"
    )
    published = []

    async def publish(value):
        published.append(value)

    env.tools.publish = publish
    async with env.factory() as database:
        database.add_all([saved_fact, saved_conversation])
        await database.commit()
    result = await env.tools.execute(FORGET_TOOL, {"query": "private topic"})
    assert len(published) == 1
    assert {match["id"] for match in published[0]["matches"]} == {
        str(saved_fact.id),
        str(saved_conversation.id),
    }
    assert {match["label"] for match in published[0]["matches"]} == {
        "Family medical treatment",
        "Private diagnosis discussion",
    }
    # execute's return is serialized directly into the provider's tool output.
    # A model instruction not to repeat labels cannot enforce private recall.
    assert set(result) == {
        "status",
        "applied",
        "match_count",
        "ambiguous",
        "has_more_matches",
        "search_complete",
        "searched_items",
        "message",
    }
    assert result["match_count"] == 2 and result["ambiguous"] is True
    serialized = json.dumps(result)
    for private in (
        "Family medical treatment",
        "Private diagnosis discussion",
        "private topic",
        str(saved_fact.id),
        str(saved_conversation.id),
    ):
        assert private not in serialized


@pytest.mark.asyncio
async def test_forget_never_matches_other_owner_or_other_workspace(memory_tool_env):
    env = memory_tool_env
    user_id, workspace_id = uuid4(), uuid4()
    async with env.factory() as database:
        database.add(User(id=user_id, email="other-forget-review@example.com"))
        database.add(Workspace(id=workspace_id, name="Other", workspace_type="personal"))
        await database.commit()
        for owner, workspace in ((user_id, env.workspace_id), (env.user_id, workspace_id)):
            database.add(
                OwnerMemory(
                    id=uuid4(),
                    user_id=owner,
                    workspace_id=workspace,
                    kind="project_fact",
                    preference_key=None,
                    label="Other private topic",
                    content="Private job details",
                )
            )
        await database.commit()
    result = await env.tools.execute(FORGET_TOOL, {"query": "job"})
    assert result["match_count"] == 0 and result["searched_items"] == 0
    assert env.published[0]["matches"] == []
    assert result["applied"] is False and "No matching" in result["message"]


@pytest.mark.asyncio
async def test_forget_excludes_expired_and_erased_history_without_erasing_it(memory_tool_env):
    env = memory_tool_env
    expired = conversation(env, expires_at=datetime.now(UTC) - timedelta(days=1))
    erased = conversation(env, erased_at=datetime.now(UTC))
    async with env.factory() as database:
        database.add_all([expired, erased])
        await database.commit()
    result = await env.tools.execute(FORGET_TOOL, {"query": "job"})
    assert result["match_count"] == 0 and result["searched_items"] == 0
    assert env.published[0]["matches"] == []
    async with env.factory() as database:
        row = await database.get(MemoryConversation, expired.id)
        assert row.encrypted_transcript == expired.encrypted_transcript and row.erased_at is None


@pytest.mark.asyncio
async def test_forget_bounds_total_scan_and_discloses_partial_results(memory_tool_env):
    env = memory_tool_env
    async with env.factory() as database:
        database.add_all(
            [conversation(env, title=f"Saved job discussion {index}") for index in range(101)]
        )
        await database.commit()
    result = await env.tools.execute(FORGET_TOOL, {"query": "job"})
    assert result["match_count"] == 4
    assert len(env.published[0]["matches"]) == 4
    assert result["has_more_matches"] is result["ambiguous"] is True
    assert result["searched_items"] == 100 and result["search_complete"] is False
    assert "100 most recently updated" in result["message"]
    assert "Private job details" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": " "},
        {"query": "a" * 201},
        {"query": "job", "approved": True},
        {"query": "job", "user_id": str(uuid4())},
        {"query": 12},
        {"query": "job\x00details"},
    ],
)
async def test_forget_rejects_injected_authority_and_invalid_queries(memory_tool_env, arguments):
    result = await memory_tool_env.tools.execute(FORGET_TOOL, arguments)
    assert result["status"] == "invalid_arguments" and result["applied"] is False
    assert "destination" not in result


@pytest.mark.asyncio
async def test_forget_drops_results_when_fresh_authority_changes(memory_tool_env):
    env = memory_tool_env
    async with env.factory() as database:
        database.add(fact(env))
        await database.commit()
    env.check.changed = True
    with pytest.raises(WorkerAccessLost):
        await env.tools.execute(FORGET_TOOL, {"query": "job"})
    assert env.published == []


@pytest.mark.asyncio
async def test_forget_never_returns_model_result_after_browser_publish_loses_owner(memory_tool_env):
    env = memory_tool_env
    async with env.factory() as database:
        database.add(fact(env))
        await database.commit()

    async def publish(value):
        env.check.changed = True

    env.tools.publish = publish
    with pytest.raises(WorkerAccessLost):
        await env.tools.execute(FORGET_TOOL, {"query": "job"})


@pytest.mark.asyncio
async def test_forget_without_browser_handoff_still_withholds_private_metadata(memory_tool_env):
    env = memory_tool_env
    saved = fact(env, label="Private family topic")
    async with env.factory() as database:
        database.add(saved)
        await database.commit()
    env.tools.publish = None
    result = await env.tools.execute(FORGET_TOOL, {"query": "job"})
    assert result["match_count"] == 1 and result["applied"] is False
    assert result["ambiguous"] is False and result["search_complete"] is True
    assert "Private family topic" not in json.dumps(result)
    assert not {"query", "matches", "destination"} & result.keys()


@pytest.mark.asyncio
async def test_forget_corrupt_ciphertext_returns_no_content(memory_tool_env):
    env = memory_tool_env
    saved = fact(env)
    saved.encrypted_content = "corrupt private text must never escape"
    async with env.factory() as database:
        database.add(saved)
        await database.commit()
    result = await env.tools.execute(FORGET_TOOL, {"query": "job"})
    assert result["status"] == "unavailable" and result["applied"] is False
    assert "corrupt" not in json.dumps(result) and "matches" not in result


def test_forget_definition_never_provides_delete_or_approval_arguments(memory_tool_env):
    definition = memory_tool_env.tools.definitions()[0]
    assert definition["name"] == FORGET_TOOL
    assert definition["parameters"]["required"] == ["query"]
    assert set(definition["parameters"]["properties"]) == {"query"}
    assert definition["parameters"]["additionalProperties"] is False
