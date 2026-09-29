"""Offline news gateway and conversation boundaries; these fixtures do not qualify models."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, seed
from test_news_stories import item

from navox.ai.context import ContextDenied
from navox.ai.foundation.contracts import JSONDocument, ProviderGrant, Sensitivity
from navox.ai.routing import PolicyRules
from navox.ai.validation import OutputRejected
from navox.core.settings import Settings
from navox.db.models import User
from navox.db.news import NewsConversationTurn
from navox.news import ai_context, conversations
from navox.news.ai_context import NewsContext, run_news_task
from navox.news.ai_contracts import CONVERSATION, SYNTHESIS, NewsSelection, validate_news_output
from navox.news.contracts import NewsError
from navox.news.conversations import (
    Question,
    answer_view,
    begin_question,
    finish_question,
    new_conversation,
    owned_conversation,
)
from navox.news.registry import revoke_rights
from navox.news.stories import index_item


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(ai_context, "datetime", Clock)
    monkeypatch.setattr(conversations, "datetime", Clock)


async def fixture(factory):
    await seed(factory)
    async with factory() as db:
        config, source, row = await item(db)
        story = await index_item(db, row, {config.key: config}, now=NOW)
        conversation = await new_conversation(
            db, workspace_id=WORKSPACE, user_id=USER, story_id=story.id, now=NOW
        )
        await db.commit()
    settings = Settings(
        news_feed_enabled=True,
        news_chat_enabled=True,
        news_source_catalog=[config.model_dump(mode="json")],
    )
    return settings, source, row, conversation


class SelectingRuntime:
    """Authored fixture output through the same task/context/semantic boundaries."""

    def __init__(self):
        self.store = SimpleNamespace(
            operator_policy=PolicyRules(
                grants=(
                    ProviderGrant(
                        provider="openai", sensitivities=frozenset({Sensitivity.PERSONAL})
                    ),
                )
            )
        )
        self.calls = []
        self.during = None

    async def execute(self, task, *, context_builder, documents, semantic_validator):
        context = await context_builder.build(task, documents)
        self.calls.append((task, context))
        selected = json.loads(context.content.text)["news_context"]["items"][0]
        output = dict(
            claim_ids=[],
            excerpts=[
                dict(
                    item_id=selected["id"],
                    item_revision=selected["revision"],
                    field="headline",
                    start=0,
                    end=len(selected["headline"]),
                    claim_type="STATEMENT",
                )
            ],
            insufficient_context=False,
        )
        semantic_validator(output)
        if self.during:
            await self.during()
        await context_builder.build(task, documents)
        return SimpleNamespace(output=JSONDocument(text=json.dumps(output)), trace_id=uuid4())


@pytest.mark.asyncio
async def test_news_context_rechecks_rights_after_provider_and_keeps_private_query_personal(
    ai_database,
):
    settings, source, row, _ = await fixture(ai_database)
    context = NewsContext(
        ai_database,
        settings,
        workspace_id=WORKSPACE,
        user_id=USER,
        item_ids=(row.id,),
        question="How does this relate to my work?",
    )
    runtime = SelectingRuntime()
    result, selection, fingerprint = await run_news_task(runtime, context, CONVERSATION)
    assert isinstance(selection, NewsSelection) and len(fingerprint) == 64
    assert runtime.calls[0][0].profile == "NEWS_SYNTHESIS"
    assert runtime.calls[0][0].sensitivity == Sensitivity.PERSONAL
    assert runtime.calls[0][0].max_cost <= 0.05

    async def revoke():
        async with ai_database() as db:
            await revoke_rights(db, source, now=NOW)
            await db.commit()

    runtime.during = revoke
    with pytest.raises(ContextDenied):
        await run_news_task(runtime, context, CONVERSATION)


@pytest.mark.asyncio
async def test_fabricated_citations_status_actions_and_fragmented_claims_are_rejected(ai_database):
    settings, _, row, _ = await fixture(ai_database)
    context = NewsContext(
        ai_database,
        settings,
        workspace_id=WORKSPACE,
        user_id=USER,
        item_ids=(row.id,),
        question="What happened?",
    )
    snapshot = await context.prepare()
    for output in [
        dict(claim_ids=[str(uuid4())], excerpts=[], insufficient_context=False),
        dict(claim_ids=[], excerpts=[], insufficient_context=False),
        dict(claim_ids=[], excerpts=[], insufficient_context=True, execute="gmail.send"),
        dict(
            claim_ids=[],
            excerpts=[],
            insufficient_context=True,
            source_url="https://fabricated.example",
        ),
        dict(
            claim_ids=[],
            excerpts=[
                dict(
                    item_id=str(row.id),
                    item_revision=1,
                    field="headline",
                    start=3,
                    end=len(row.headline),
                )
            ],
            insufficient_context=False,
        ),
    ]:
        with pytest.raises(OutputRejected):
            validate_news_output(CONVERSATION, output, snapshot)
    with pytest.raises(OutputRejected):
        validate_news_output(
            SYNTHESIS,
            {
                "headline_item_id": str(row.id),
                "sections": [{"heading": "what_happened", "claim_ids": [str(uuid4())]}],
            },
            snapshot,
        )


@pytest.mark.asyncio
async def test_conversation_is_idempotent_keeps_followup_and_revalidates_citations(
    ai_database, monkeypatch
):
    settings, source, row, conversation = await fixture(ai_database)
    runtime = SelectingRuntime()

    async def build(*args):
        return runtime

    monkeypatch.setattr(conversations, "build_runtime", build)
    request = Question(request_id=uuid4(), question="What happened?")
    async with ai_database() as db:
        turn, created = await begin_question(
            db, settings, conversation.id, request, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert created
        duplicate, created = await begin_question(
            db, settings, conversation.id, request, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert duplicate.id == turn.id and not created
        await db.commit()
    await finish_question(ai_database, settings, turn.id, workspace_id=WORKSPACE, user_id=USER)
    await finish_question(ai_database, settings, turn.id, workspace_id=WORKSPACE, user_id=USER)
    assert len(runtime.calls) == 1
    async with ai_database() as db:
        saved = await db.get(NewsConversationTurn, turn.id)
        result = await answer_view(db, conversation, saved, settings, now=NOW)
        assert result.status == "READY" and result.facts[0].source_url == row.canonical_url
        assert result.facts[0].status == "ATTRIBUTED" and not result.actions_executed
        assert row.headline not in json.dumps(saved.selection)
        follow, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="Tell me more about that."),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        await db.commit()
    await finish_question(ai_database, settings, follow.id, workspace_id=WORKSPACE, user_id=USER)
    snapshot = json.loads(runtime.calls[-1][1].content.text)["news_context"]
    assert snapshot["previous_questions"] == [request.question]
    assert snapshot["items"][0]["id"] == str(row.id)
    async with ai_database() as db:
        await revoke_rights(db, source, now=NOW)
        assert (
            await answer_view(db, conversation, saved, settings, now=NOW)
        ).status == "SOURCES_CHANGED"


@pytest.mark.asyncio
async def test_conversation_scope_pause_pending_and_expiry(ai_database):
    settings, _, row, conversation = await fixture(ai_database)
    async with ai_database() as db:
        with pytest.raises(NewsError, match="conversation_unavailable"):
            await owned_conversation(
                db, conversation.id, workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER, now=NOW
            )
        request = Question(request_id=uuid4(), question="What happened?")
        await begin_question(
            db, settings, conversation.id, request, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        with pytest.raises(NewsError, match="rate_limited"):
            await begin_question(
                db,
                settings,
                conversation.id,
                request.model_copy(update={"request_id": uuid4()}),
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
            )
        with pytest.raises(NewsError, match="conversation_unavailable"):
            await owned_conversation(
                db,
                conversation.id,
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW + timedelta(days=8),
            )
        user = await db.get(User, USER)
        user.agent_paused = True
        await db.commit()
    context = NewsContext(
        ai_database,
        settings,
        workspace_id=WORKSPACE,
        user_id=USER,
        item_ids=(row.id,),
        question="What happened?",
    )
    with pytest.raises(ContextDenied):
        await context.prepare()
