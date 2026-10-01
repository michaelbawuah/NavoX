"""Natural question routing reaches activity retrieval without weakening scope."""

from uuid import uuid4

import pytest
from test_news_foundation import NOW, USER, WORKSPACE, seed

from navox.core.settings import Settings
from navox.news.contracts import NewsError
from navox.news.conversation_retrieval import retrieve_for_turn, turn_plan
from navox.news.conversations import begin_question, new_conversation
from navox.news.questions import Question


@pytest.mark.parametrize(
    "text,intent",
    [
        ("What's trending?", "TRENDING"),
        ("What’s trending in New York?", "TRENDING"),
        ("What is currently trending in science?", "TRENDING"),
        ("Show me trending stories about hot springs", "TRENDING"),
        ("What are people talking about?", "TRENDING"),
        ("What's trending on X?", "X_TRENDS"),
        ("What is trending on Twitter?", "X_TRENDS"),
        ("What happened to Trending Technologies?", "CURRENT_NEWS"),
        ("What happened to X Corp?", "CURRENT_NEWS"),
        ("Tell me more about #2", "CURRENT_NEWS"),
        ("What is happening with viral infections?", "CURRENT_NEWS"),
    ],
)
def test_question_without_technical_options_routes_narrow_request_phrases(text, intent):
    plan = Question(request_id=uuid4(), question=text).plan
    assert plan.intent == intent
    assert plan.freshness == "FRESH" and plan.depth == "STANDARD"


def test_explicit_intent_and_bounds_remain_authoritative():
    question = Question(
        request_id=uuid4(), question="What's trending?", intent="STORY_QUESTION", depth="QUICK"
    )
    assert question.plan.intent == "STORY_QUESTION"
    assert question.plan.item_limit == 4
    with pytest.raises(ValueError):
        Question(request_id=uuid4(), question="What's trending?", freshness="HISTORICAL")


@pytest.mark.asyncio
async def test_inferred_plan_is_persisted_and_idempotent(ai_database):
    await seed(ai_database)
    settings = Settings(_env_file=None, news_feed_enabled=True, news_chat_enabled=True)
    async with ai_database() as db:
        conversation = await new_conversation(
            db, workspace_id=WORKSPACE, user_id=USER, story_id=None, now=NOW
        )
        question = Question(request_id=uuid4(), question="What's trending?")
        turn, created = await begin_question(
            db, settings, conversation.id, question, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert created and turn.intent == "TRENDING"
        assert turn_plan(turn).intent == "TRENDING"
        again, created = await begin_question(
            db, settings, conversation.id, question, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert not created and again.id == turn.id
        with pytest.raises(NewsError, match="source_changed"):
            await begin_question(
                db,
                settings,
                conversation.id,
                question.model_copy(update={"intent": "CURRENT_NEWS"}),
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
            )


@pytest.mark.asyncio
async def test_x_question_cannot_fall_back_to_publisher_trends(ai_database):
    await seed(ai_database)
    settings = Settings(_env_file=None, news_feed_enabled=True, news_chat_enabled=True)
    async with ai_database() as db:
        conversation = await new_conversation(
            db, workspace_id=WORKSPACE, user_id=USER, story_id=None, now=NOW
        )
        turn, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="What's trending on X?"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        with pytest.raises(NewsError, match="news_unavailable"):
            await retrieve_for_turn(db, conversation, turn, settings, now=NOW)
