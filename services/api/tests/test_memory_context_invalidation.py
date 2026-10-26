"""Privacy/source changes revoke completed implicit native chat context too."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, Text, Uuid, select
from test_ai_conversation import install_conversation_runtime, new_conversation
from test_ai_intent_plan import intent_env as shared_intent_fixture
from test_memory_conversation_integration import prepare

from navox.db.ai_registry import AITaskRun
from navox.db.communications import AssistantSession
from navox.db.models import User, Workspace

intent_env = shared_intent_fixture
PATH = "/api/v1/ai/assistant/conversation"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["forget", "privacy", "correction"])
async def test_completed_chat_cache_cannot_replay_after_privacy_boundary(intent_env, change):
    env = intent_env
    await prepare(env)
    adapter = await install_conversation_runtime(
        env, public=True, output=json.dumps({"answer": "The synthetic review is Thursday."})
    )
    session = await new_conversation(env)
    question = "When is my project review?"
    first = await env.client.post(PATH, json={"session_id": session, "utterance": question})
    assert first.status_code == 200, first.text
    answer = first.json()
    source = next(
        item
        for item in (await env.client.get("/api/v1/memory/history")).json()
        if item["source_kind"] == "chat"
    )
    other_user, other_workspace, other_session = uuid4(), uuid4(), uuid4()
    cache = Table(
        "synthetic_node_cache",
        MetaData(),
        Column("id", Uuid, primary_key=True),
        Column(
            "native_session_id",
            Uuid,
            ForeignKey(AssistantSession.__table__.c.id, ondelete="CASCADE"),
        ),
        Column("body", Text, nullable=False),
    )
    async with env.factory() as database:
        database.add(User(id=other_user, email="unrelated-cache-owner@example.com"))
        database.add(
            Workspace(id=other_workspace, name="Other cache owner", workspace_type="personal")
        )
        await database.flush()
        database.add(
            AssistantSession(id=other_session, user_id=other_user, workspace_id=other_workspace)
        )
        await database.flush()
        connection = await database.connection()
        await connection.run_sync(lambda sync: cache.create(sync))
        await database.execute(
            cache.insert(),
            [
                {
                    "id": uuid4(),
                    "native_session_id": UUID(session),
                    "body": "Synthetic source cache",
                },
                {
                    "id": uuid4(),
                    "native_session_id": other_session,
                    "body": "Unrelated owner cache",
                },
            ],
        )
        await database.commit()
    if change == "forget":
        changed = await env.client.delete(f"/api/v1/memory/history/{source['id']}")
        assert changed.status_code == 204
    elif change == "privacy":
        privacy = (await env.client.get("/api/v1/memory/privacy")).json()
        changed = await env.client.put(
            "/api/v1/memory/privacy",
            json={
                **privacy,
                "off_the_record": True,
                "disclosure_accepted": False,
            },
        )
        assert changed.status_code == 200, changed.text
    else:
        fact = (await env.client.get("/api/v1/memory/facts")).json()[0]
        changed = await env.client.patch(
            f"/api/v1/memory/facts/{fact['id']}",
            json={"content": "The synthetic project review is Friday."},
        )
        assert changed.status_code == 200, changed.text
    replay = await env.client.post(
        PATH,
        json={
            "session_id": session,
            "utterance": "What did you just say?",
            "recent_turns": [
                {"task_id": answer["task_id"], "question": question, "answer": answer["answer"]}
            ],
        },
    )
    assert replay.status_code == 403, replay.text
    assert len(adapter.calls) == 1  # Old cached text never reaches another model call.
    detail = await env.client.get(f"/api/v1/memory/history/{source['id']}")
    assert detail.status_code == (404 if change == "forget" else 200)
    if change != "forget":
        assert detail.json()["turns"][0]["content"] == question
    async with env.factory() as database:
        assert await database.get(AssistantSession, UUID(session)) is None
        assert await database.get(AssistantSession, other_session) is not None
        assert (await database.execute(select(cache.c.body))).scalars().all() == [
            "Unrelated owner cache"
        ]
        receipt = await database.scalar(
            select(AITaskRun).where(AITaskRun.task_id == UUID(answer["task_id"]))
        )
        assert receipt is not None and receipt.status == "COMPLETED"
