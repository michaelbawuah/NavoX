"""Research uses current attributed evidence, with chronology and publisher breadth."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_news_foundation import NOW, USER, WORKSPACE, seed
from test_news_stories import item, span

from navox.core.settings import Settings
from navox.db.news import NewsConversationTurn
from navox.news.contracts import NewsItemInput
from navox.news.conversations import answer_view, new_conversation
from navox.news.ingestion import store_item
from navox.news.questions import Question
from navox.news.registry import current_rights, revoke_rights
from navox.news.retrieval import RetrievalPlan, select_evidence
from navox.news.stories import index_item


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Show me a timeline of the Orbital mission", "TIMELINE"),
        ("Give me the background on Orbital", "BACKGROUND"),
        ("Catch me up on Orbital", "BACKGROUND"),
        ("Compare reports on Orbital", "COVERAGE_COMPARISON"),
        ("Do deep research on Orbital", "DEEP_RESEARCH"),
        ("What happened to Timeline Corporation?", "CURRENT_NEWS"),
        ("Is Background Research closing?", "CURRENT_NEWS"),
        ("News about Compare Sources Inc", "CURRENT_NEWS"),
    ],
)
def test_narrow_research_requests(question, intent):
    assert Question(request_id=uuid4(), question=question).plan.intent == intent


async def research_sources(factory):
    await seed(factory)
    async with factory() as db:
        first, source, record = await item(db, headline="Orbital mission launches")
        second, _, other = await item(db, "two", headline="Orbital mission returns")
        record.event_started_at = NOW - timedelta(days=2)
        other.event_started_at = NOW - timedelta(days=1)
        definitions = {first.key: first, second.key: second}
        await index_item(db, record, definitions, now=NOW)
        await index_item(db, other, definitions, now=NOW)
        rights = await current_rights(db, source)
        for n in range(5):
            row = await store_item(
                db,
                source,
                rights,
                first,
                NewsItemInput(
                    external_id=f"update-{n}",
                    headline=f"Orbital mission update {n}",
                    canonical_url=f"https://one.example.com/update-{n}",
                    published_at=NOW + timedelta(seconds=n),
                ),
                now=NOW,
            )
            await index_item(db, row, definitions, now=NOW)
        await db.commit()
    return definitions, source, record, other


@pytest.mark.asyncio
async def test_timeline_uses_event_time_before_publication_and_filters_topic(ai_database):
    definitions, _, first, second = await research_sources(ai_database)
    async with ai_database() as db:
        result = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="Show me a timeline of Orbital",
            plan=RetrievalPlan(intent="TIMELINE", depth="QUICK"),
            now=NOW,
        )
        assert [value.id for value in result.items[:2]] == [first.id, second.id]
        assert len(result.items) == 4 and result.limited
        none = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="Show me a timeline of unobtainium",
            plan=RetrievalPlan(intent="TIMELINE"),
            now=NOW,
        )
        assert none.items == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["COVERAGE_COMPARISON", "DEEP_RESEARCH"])
async def test_research_includes_other_source_before_repeat_reports_and_respects_revoke(
    ai_database, intent
):
    definitions, first_source, _, second = await research_sources(ai_database)
    async with ai_database() as db:
        result = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="Orbital",
            plan=RetrievalPlan(intent=intent, depth="QUICK"),
            now=NOW,
        )
        assert len({value.source_id for value in result.items[:2]}) == 2
        assert second.id in [value.id for value in result.items]
        await revoke_rights(db, first_source, now=NOW)
        result = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="Orbital",
            plan=RetrievalPlan(intent=intent),
            now=NOW,
        )
        assert [value.id for value in result.items] == [second.id]


@pytest.mark.asyncio
async def test_timeline_answer_reorders_provider_selections_and_retains_source_times(ai_database):
    definitions, _, first, second = await research_sources(ai_database)
    settings = Settings(
        _env_file=None,
        news_feed_enabled=True,
        news_chat_enabled=True,
        news_source_catalog=[value.model_dump(mode="json") for value in definitions.values()],
    )
    async with ai_database() as db:
        conversation = await new_conversation(
            db, workspace_id=WORKSPACE, user_id=USER, story_id=None, now=NOW
        )
        turn = NewsConversationTurn(
            conversation_id=conversation.id,
            request_id=uuid4(),
            sequence=1,
            question="Show me a timeline of Orbital",
            intent="TIMELINE",
            status="READY",
            source_snapshot={str(first.id): first.revision, str(second.id): second.revision},
            retrieval_plan=RetrievalPlan(intent="TIMELINE").model_dump(mode="json"),
            retrieval_metadata={"limited": True},
            selection={
                "claim_ids": [],
                "excerpts": [
                    span(second).model_dump(mode="json"),
                    span(first).model_dump(mode="json"),
                ],
                "insufficient_context": False,
            },
            created_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )
        db.add(turn)
        await db.flush()
        answer = await answer_view(db, conversation, turn, settings, now=NOW)
        assert answer.status == "READY"
        assert [fact.item_id for fact in answer.facts] == [first.id, second.id]
        assert answer.facts[0].event_started_at == NOW - timedelta(days=2)
        assert all(fact.status == "ATTRIBUTED" for fact in answer.facts)
        assert "publication time" in answer.message
