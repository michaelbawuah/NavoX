"""Authored retrieval regressions, not a measured live-news quality corpus."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, seed
from test_news_stories import item

from navox.core.settings import Settings
from navox.db.models import User
from navox.db.news import NewsConversationTurn
from navox.news import ai_context, conversations
from navox.news.contracts import NewsError, NewsItemInput
from navox.news.conversation_retrieval import refresh_work_for_turn, retrieve_for_turn
from navox.news.conversations import (
    Question,
    begin_question,
    finish_question,
    new_conversation,
)
from navox.news.ingestion import store_item
from navox.news.registry import current_rights, revoke_rights
from navox.news.retrieval import (
    Depth,
    Freshness,
    RetrievalPlan,
    numbered_reference,
    query_terms,
    select_evidence,
)
from navox.news.stories import index_item


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Tell me more about #2", 2),
        ("Explain story 12", 12),
        ("What happened in 2026?", None),
        ("Item 3 please", 3),
    ],
)
def test_explicit_reference_parser(text, expected):
    assert numbered_reference(text) == expected


@pytest.mark.parametrize("text", ["#0", "#1234", "#1 and #2", "number 0000"])
def test_ambiguous_or_invalid_reference_cannot_fall_back(text):
    with pytest.raises(NewsError, match="invalid_reference"):
        numbered_reference(text)


def test_plans_bound_cost_and_freshness_without_labeling_stale_as_current():
    assert RetrievalPlan(depth=Depth.QUICK).item_limit == 4
    assert RetrievalPlan(depth=Depth.DEEP).candidate_limit == 400
    with pytest.raises(ValueError):
        RetrievalPlan(freshness=Freshness.HISTORICAL)
    assert (
        Question(request_id=uuid4(), question="Background?", intent="BACKGROUND").plan.freshness
        == "RECENT"
    )
    assert query_terms("What happened to Orbital Project?") == ("orbital", "project")


@pytest.mark.asyncio
async def test_query_filters_before_recency_limit_and_discloses_bounds(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, source, old = await item(db, headline="Orbital telescope commissioning")
        old.published_at = NOW - timedelta(hours=3)
        definitions = {config.key: config}
        await index_item(db, old, definitions, now=NOW)
        rights = await current_rights(db, source)
        for n in range(205):
            row = await store_item(
                db,
                source,
                rights,
                config,
                NewsItemInput(
                    external_id=f"recent-{n}",
                    headline=f"Routine unrelated update {n}",
                    canonical_url=f"https://one.example.com/recent-{n}",
                    published_at=NOW,
                ),
                now=NOW,
            )
            await index_item(db, row, definitions, now=NOW)
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What happened to the orbital telescope?",
            plan=RetrievalPlan(),
            now=NOW,
        )
        assert [row.id for row in selected.items] == [old.id]
        assert selected.examined == 1 and not selected.limited
        general = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What happened?",
            plan=RetrievalPlan(depth="QUICK"),
            now=NOW,
        )
        assert general.examined == 100 and len(general.items) == 4 and general.limited


@pytest.mark.asyncio
async def test_freshness_rights_owner_and_pause_checked_before_selection(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        a, first_source, fresh = await item(db)
        b, _, stale = await item(db, "two")
        stale.last_observed_at = NOW - timedelta(hours=2)
        definitions = {a.key: a, b.key: b}
        for row in (fresh, stale):
            await index_item(db, row, definitions, now=NOW)
        result = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What happened?",
            plan=RetrievalPlan(),
            now=NOW,
        )
        assert [row.id for row in result.items] == [fresh.id]
        assert result.refresh_source_ids == (stale.source_id,)
        other = await select_evidence(
            db,
            definitions,
            workspace_id=OTHER_WORKSPACE,
            user_id=OTHER_USER,
            question="What happened?",
            plan=RetrievalPlan(),
            now=NOW,
        )
        assert other.items == () and other.refresh_source_ids == ()
        await revoke_rights(db, first_source, now=NOW)
        result = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What happened?",
            plan=RetrievalPlan(),
            now=NOW,
        )
        assert result.items == () and first_source.id not in result.refresh_source_ids
        user = await db.get(User, USER)
        user.agent_paused = True
        await db.flush()
        with pytest.raises(NewsError, match="conversation_unavailable"):
            await select_evidence(
                db,
                definitions,
                workspace_id=WORKSPACE,
                user_id=USER,
                question="What happened?",
                plan=RetrievalPlan(),
                now=NOW,
            )


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(conversations, "datetime", Clock)
    monkeypatch.setattr(ai_context, "datetime", Clock)


@pytest.mark.asyncio
async def test_followup_uses_displayed_order_not_snapshot_dictionary_order(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        a, _, first = await item(db, headline="Orbital telescope commissioning")
        b, _, second = await item(db, "two", headline="Marine instruments installed")
        definitions = {a.key: a, b.key: b}
        for row in (first, second):
            await index_item(db, row, definitions, now=NOW)
        conversation = await new_conversation(
            db, workspace_id=WORKSPACE, user_id=USER, story_id=None, now=NOW
        )
        prior = NewsConversationTurn(
            conversation_id=conversation.id,
            request_id=uuid4(),
            sequence=1,
            question="What happened?",
            intent="CURRENT_NEWS",
            status="READY",
            source_snapshot={str(second.id): second.revision, str(first.id): first.revision},
            selection={
                "claim_ids": [],
                "insufficient_context": False,
                "excerpts": [
                    {
                        "item_id": str(row.id),
                        "item_revision": row.revision,
                        "field": "headline",
                        "start": 0,
                        "end": len(row.headline),
                    }
                    for row in (first, second)
                ],
            },
            created_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
        db.add(prior)
        await db.flush()
        settings = Settings(
            _env_file=None,
            news_feed_enabled=True,
            news_chat_enabled=True,
            news_source_catalog=[a.model_dump(mode="json"), b.model_dump(mode="json")],
        )
        following, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="Tell me more about #2"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        selected, _ = await retrieve_for_turn(db, conversation, following, settings, now=NOW)
        assert selected.resolved_reference and [row.id for row in selected.items] == [second.id]
        following.question = "What happened to the orbital telescope?"
        selected, _ = await retrieve_for_turn(db, conversation, following, settings, now=NOW)
        assert [row.id for row in selected.items] == [first.id]
        following.question = "Tell me more about #2"
        second.revision += 1
        await db.flush()
        with pytest.raises(NewsError, match="invalid_reference"):
            await retrieve_for_turn(db, conversation, following, settings, now=NOW)


@pytest.mark.asyncio
async def test_request_id_cannot_change_depth_or_freshness(ai_database):
    from test_news_conversations import fixture

    settings, _, _, conversation = await fixture(ai_database)
    async with ai_database() as db:
        request = Question(request_id=uuid4(), question="What happened?")
        row, _ = await begin_question(
            db, settings, conversation.id, request, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert row.retrieval_plan["depth"] == "STANDARD"
        for patch in ({"depth": "DEEP"}, {"freshness": "REALTIME"}):
            updated = Question.model_validate(request.model_dump() | patch)
            with pytest.raises(NewsError, match="source_changed"):
                await begin_question(
                    db,
                    settings,
                    conversation.id,
                    updated,
                    workspace_id=WORKSPACE,
                    user_id=USER,
                    now=NOW,
                )


@pytest.mark.asyncio
async def test_refresh_proposals_are_bounded_owned_identifiers_and_replay_stable(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        configs = []
        for n in range(6):
            config, _, row = await item(db, f"s{n}")
            row.last_observed_at = NOW - timedelta(hours=2)
            configs.append(config)
            await index_item(db, row, {config.key: config}, now=NOW)
        settings = Settings(
            _env_file=None,
            news_feed_enabled=True,
            news_chat_enabled=True,
            news_source_catalog=[config.model_dump(mode="json") for config in configs],
        )
        conversation = await new_conversation(
            db, workspace_id=WORKSPACE, user_id=USER, story_id=None, now=NOW
        )
        turn, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="What happened?"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        first = await refresh_work_for_turn(
            db, settings, turn.id, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        second = await refresh_work_for_turn(
            db, settings, turn.id, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert first == second and len(first) == 4
        assert all(row.user_id == str(USER) for row in first)
        settings.news_chat_enabled = False
        with pytest.raises(NewsError, match="news_disabled"):
            await refresh_work_for_turn(
                db, settings, turn.id, workspace_id=WORKSPACE, user_id=USER, now=NOW
            )


@pytest.mark.asyncio
async def test_duplicate_generation_is_reserved_and_invalid_reference_spends_nothing(
    ai_database, monkeypatch, clock
):
    import asyncio

    from test_news_conversations import SelectingRuntime, fixture

    settings, _, _, conversation = await fixture(ai_database)
    runtime = SelectingRuntime()

    async def build(*args):
        return runtime

    monkeypatch.setattr(conversations, "build_runtime", build)
    async with ai_database() as db:
        turn, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="What happened?"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        await db.commit()
    await asyncio.gather(
        *(
            finish_question(ai_database, settings, turn.id, workspace_id=WORKSPACE, user_id=USER)
            for _ in range(2)
        )
    )
    assert len(runtime.calls) == 1
    async with ai_database() as db:
        failed, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="Tell me more about #99"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        await db.commit()
    await finish_question(ai_database, settings, failed.id, workspace_id=WORKSPACE, user_id=USER)
    assert len(runtime.calls) == 1
    async with ai_database() as db:
        result = await db.get(NewsConversationTurn, failed.id)
        assert result.status == "UNAVAILABLE" and result.failure_code == "invalid_reference"
        assert result.trace_id is None and result.source_snapshot == {}


@pytest.mark.asyncio
async def test_source_change_after_generation_blocks_publication(ai_database, monkeypatch, clock):
    from test_news_conversations import SelectingRuntime, fixture

    settings, source, _, conversation = await fixture(ai_database)

    async def build(*args):
        return SelectingRuntime()

    original = conversations.run_news_task

    async def changed_after(*args):
        answer = await original(*args)
        async with ai_database() as db:
            await revoke_rights(db, source, now=NOW)
            await db.commit()
        return answer

    monkeypatch.setattr(conversations, "build_runtime", build)
    monkeypatch.setattr(conversations, "run_news_task", changed_after)
    async with ai_database() as db:
        turn, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="What happened?"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        await db.commit()
    await finish_question(ai_database, settings, turn.id, workspace_id=WORKSPACE, user_id=USER)
    async with ai_database() as db:
        result = await db.get(NewsConversationTurn, turn.id)
        # A story-scoped conversation may be erased with its revoked anchor source.
        assert result is None or (result.status != "READY" and result.selection is None)


@pytest.mark.asyncio
async def test_suppression_invalidates_global_context_and_cached_answers(
    ai_database, monkeypatch, clock
):
    from test_news_conversations import SelectingRuntime, fixture

    from navox.ai.context import ContextDenied
    from navox.db.news import NewsStory
    from navox.news.ai_context import NewsContext
    from navox.news.conversations import answer_view

    settings, _, row, conversation = await fixture(ai_database)

    async def build(*args):
        return SelectingRuntime()

    monkeypatch.setattr(conversations, "build_runtime", build)
    async with ai_database() as db:
        turn, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="What happened?"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        await db.commit()
    await finish_question(ai_database, settings, turn.id, workspace_id=WORKSPACE, user_id=USER)
    async with ai_database() as db:
        story = await db.get(NewsStory, conversation.story_id)
        story.suppressed = True
        await db.commit()
        stored = await db.get(NewsConversationTurn, turn.id)
        assert (
            await answer_view(db, conversation, stored, settings, now=NOW)
        ).status == "SOURCES_CHANGED"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["summary", "snippet", "retention"])
async def test_restricted_content_is_excluded_before_text_candidate_query(ai_database, restriction):
    from navox.news.registry import activate_source

    await seed(ai_database)
    async with ai_database() as db:
        config, _, row = await item(
            db, headline="An ordinary update", description="Sentinelword detail."
        )
        await index_item(db, row, {config.key: config}, now=NOW)
        patch = (
            {"summary_generation_allowed": False}
            if restriction == "summary"
            else (
                {"snippet_storage_allowed": False}
                if restriction == "snippet"
                else {"retention_days": 1}
            )
        )
        if restriction == "retention":
            row.retrieved_at = NOW - timedelta(days=2)
            await db.flush()
        changed = config.model_copy(update={"rights": config.rights.model_copy(update=patch)})
        await activate_source(db, changed, workspace_id=WORKSPACE, user_id=USER, now=NOW)
        result = await select_evidence(
            db,
            {changed.key: changed},
            workspace_id=WORKSPACE,
            user_id=USER,
            question="Sentinelword?",
            plan=RetrievalPlan(),
            now=NOW,
        )
        assert result.items == () and result.examined == 0


@pytest.mark.asyncio
async def test_expired_turn_never_refreshes_or_starts_generation(ai_database, monkeypatch, clock):
    from test_news_conversations import fixture

    settings, _, _, conversation = await fixture(ai_database)

    async def forbidden(*args):
        raise AssertionError("Expired work must not construct a model runtime")

    monkeypatch.setattr(conversations, "build_runtime", forbidden)
    async with ai_database() as db:
        turn, _ = await begin_question(
            db,
            settings,
            conversation.id,
            Question(request_id=uuid4(), question="What happened?"),
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW,
        )
        turn.expires_at = NOW - timedelta(seconds=1)
        await db.commit()
        assert (
            await refresh_work_for_turn(
                db, settings, turn.id, workspace_id=WORKSPACE, user_id=USER, now=NOW
            )
            == []
        )
    await finish_question(ai_database, settings, turn.id, workspace_id=WORKSPACE, user_id=USER)
    async with ai_database() as db:
        saved = await db.get(NewsConversationTurn, turn.id)
        assert saved.processing_started_at is None and saved.trace_id is None
