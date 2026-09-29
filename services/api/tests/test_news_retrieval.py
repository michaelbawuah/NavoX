"""Authored retrieval regressions, not a measured live-news quality corpus."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, seed
from test_news_stories import item

from navox.core.settings import Settings
from navox.db.models import User
from navox.db.news import NewsConversationTurn, NewsStory
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
    trending_topic_terms,
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


@pytest.mark.asyncio
async def test_trending_intent_uses_observed_activity_not_literal_query_terms(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long shared report describing the same measured event "
            "with enough context to establish an exact-copy story grouping."
        )
        hot_a, _, first = await item(
            db, "hot-a", headline="Measured event update", description=copied
        )
        hot_b, _, second = await item(
            db, "hot-b", headline="Measured event update", description=copied
        )
        quiet_config, _, quiet = await item(db, "quiet", headline="Routine separate report")
        definitions = {
            hot_a.key: hot_a,
            hot_b.key: hot_b,
            quiet_config.key: quiet_config,
        }
        hot_story = await index_item(db, first, definitions, now=NOW)
        assert (await index_item(db, second, definitions, now=NOW)).id == hot_story.id
        await index_item(db, quiet, definitions, now=NOW)
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            now=NOW,
        )
        assert {row.id for row in selected.items[:2]} == {first.id, second.id}
        assert quiet.id in {row.id for row in selected.items}


@pytest.mark.parametrize(
    "question,expected",
    [
        # Framing alone never becomes a lexical requirement.
        ("What's trending?", ()),
        ("What is hot right now?", ()),
        ("What are people talking about?", ()),
        ("What's trending right now?", ()),
        ("What's buzzing today?", ()),
        # Topical words survive even when they are common trend framing words.
        ("What's trending in New York?", ("new", "york")),
        ("What's trending about the political right?", ("political", "right")),
        ("What's trending about viral infections?", ("infections", "viral")),
        ("What's trending in hot springs?", ("hot", "springs")),
        ("What's trending about Buzz Aldrin?", ("aldrin", "buzz")),
        ("Is People magazine trending?", ("magazine", "people")),
        ("What's trending about the Orbital Project?", ("orbital", "project")),
        ("Trending in the telescope programme?", ("programme", "telescope")),
        ("Any buzz about the telescope programme?", ("programme", "telescope")),
    ],
)
def test_trend_questions_strip_framing_but_keep_topics(question, expected):
    assert trending_topic_terms(question) == expected


def test_trend_framing_is_removed_before_the_topic_cap():
    """Framing words must not consume the ten-topic bound and hide a real topic."""
    question = (
        "What is hot in cobalt, copper, gold, helium, indium, lithium,"
        " nickel, platinum, silver, tin, titanium or zinc?"
    )
    terms = trending_topic_terms(question)
    assert len(terms) == 10
    assert "tin" in terms
    assert terms == (
        "cobalt",
        "copper",
        "gold",
        "helium",
        "indium",
        "lithium",
        "nickel",
        "platinum",
        "silver",
        "tin",
    )


@pytest.mark.asyncio
async def test_trending_topics_that_look_like_framing_still_select_evidence(ai_database):
    """Dropping topic words such as "new", "right" or "viral" would miss relevant evidence."""
    await seed(ai_database)
    async with ai_database() as db:
        york_config, _, york = await item(
            db, "york", headline="New transit plan approved for the harbour city"
        )
        right_config, _, right = await item(
            db, "right", headline="The right consolidates regional support"
        )
        viral_config, _, viral = await item(
            db, "viral", headline="Viral outbreak spreads in coastal districts"
        )
        noise_config, _, noise = await item(db, "noise", headline="Unrelated routine report")
        definitions = {
            york_config.key: york_config,
            right_config.key: right_config,
            viral_config.key: viral_config,
            noise_config.key: noise_config,
        }
        for row in (york, right, viral, noise):
            await index_item(db, row, definitions, now=NOW)
        for question, expected in (
            ("What's trending in New York?", york.id),
            ("What's trending about the political right?", right.id),
            ("What's trending about viral infections?", viral.id),
        ):
            selected = await select_evidence(
                db,
                definitions,
                workspace_id=WORKSPACE,
                user_id=USER,
                question=question,
                plan=RetrievalPlan(intent="TRENDING"),
                now=NOW,
            )
            assert [row.id for row in selected.items] == [expected], question


@pytest.mark.asyncio
async def test_trending_topic_terms_still_scope_retrieval(ai_database):
    """A trend ask with a topic never needs the word "trending" in the evidence."""
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long shared report describing the same measured event "
            "with enough context to establish an exact-copy story grouping."
        )
        quiet_config, _, quiet = await item(
            db, "quiet", headline="Orbital Project reaches a commissioning milestone"
        )
        hot_a, _, first = await item(
            db, "hot-a", headline="Trending event update", description=copied
        )
        hot_b, _, second = await item(
            db, "hot-b", headline="Trending event update", description=copied
        )
        definitions = {
            quiet_config.key: quiet_config,
            hot_a.key: hot_a,
            hot_b.key: hot_b,
        }
        await index_item(db, quiet, definitions, now=NOW)
        hot_story = await index_item(db, first, definitions, now=NOW)
        assert (await index_item(db, second, definitions, now=NOW)).id == hot_story.id
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending about the Orbital Project?",
            plan=RetrievalPlan(intent="TRENDING"),
            now=NOW,
        )
        assert [row.id for row in selected.items] == [quiet.id]


@pytest.mark.asyncio
async def test_non_trending_intents_keep_the_lexical_requirement(ai_database):
    """Only TRENDING drops control words; every other intent still searches them."""
    await seed(ai_database)
    async with ai_database() as db:
        config, _, row = await item(db, headline="Orbital Project commissioning milestone")
        definitions = {config.key: config}
        await index_item(db, row, definitions, now=NOW)
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(),
            now=NOW,
        )
        assert selected.items == () and selected.examined == 0


@pytest.mark.asyncio
async def test_trending_orders_by_observed_activity_with_deterministic_ties(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long shared report describing the same measured event "
            "with enough context to establish an exact-copy story grouping."
        )
        a_config, _, first = await item(db, "a", description=copied)
        copy_config, _, second = await item(db, "copy", description=copied)
        b_config, _, third = await item(db, "b", headline="Single report b")
        c_config, _, fourth = await item(db, "c", headline="Single report c")
        definitions = {
            a_config.key: a_config,
            copy_config.key: copy_config,
            b_config.key: b_config,
            c_config.key: c_config,
        }
        active = await index_item(db, first, definitions, now=NOW)
        assert (await index_item(db, second, definitions, now=NOW)).id == active.id
        quiet_b = await index_item(db, third, definitions, now=NOW)
        quiet_c = await index_item(db, fourth, definitions, now=NOW)
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            now=NOW,
        )
        identifiers = [row.id for row in selected.items]
        assert set(identifiers) == {first.id, second.id, third.id, fourth.id}
        assert identifiers[:2] == sorted((first.id, second.id), key=lambda value: value.hex)
        singles = {quiet_b.id: third.id, quiet_c.id: fourth.id}
        assert identifiers[2:] == [
            singles[story_id] for story_id in sorted(singles, key=lambda value: value.hex)
        ]


@pytest.mark.asyncio
async def test_trending_keeps_explicit_reference_and_story_scope(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long shared report describing the same measured event "
            "with enough context to establish an exact-copy story grouping."
        )
        a_config, _, first = await item(db, "a", description=copied)
        copy_config, _, second = await item(db, "copy", description=copied)
        quiet_config, _, quiet = await item(db, "quiet", headline="Separate routine report")
        definitions = {
            a_config.key: a_config,
            copy_config.key: copy_config,
            quiet_config.key: quiet_config,
        }
        active = await index_item(db, first, definitions, now=NOW)
        assert (await index_item(db, second, definitions, now=NOW)).id == active.id
        quiet_story = await index_item(db, quiet, definitions, now=NOW)
        referenced = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            reference_item_id=second.id,
            now=NOW,
        )
        assert referenced.resolved_reference
        assert {row.id for row in referenced.items} == {first.id, second.id}
        scoped = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            story_id=quiet_story.id,
            now=NOW,
        )
        assert [row.id for row in scoped.items] == [quiet.id]


@pytest.mark.asyncio
async def test_trending_followup_keeps_previously_displayed_items_first(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long shared report describing the same measured event "
            "with enough context to establish an exact-copy story grouping."
        )
        a_config, _, first = await item(db, "a", description=copied)
        copy_config, _, second = await item(db, "copy", description=copied)
        quiet_config, _, quiet = await item(db, "quiet", headline="Separate routine report")
        definitions = {
            a_config.key: a_config,
            copy_config.key: copy_config,
            quiet_config.key: quiet_config,
        }
        active = await index_item(db, first, definitions, now=NOW)
        assert (await index_item(db, second, definitions, now=NOW)).id == active.id
        await index_item(db, quiet, definitions, now=NOW)
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            previous_item_ids=(quiet.id,),
            now=NOW,
        )
        assert [row.id for row in selected.items][0] == quiet.id
        assert {row.id for row in selected.items} == {first.id, second.id, quiet.id}


@pytest.mark.asyncio
async def test_trending_preserves_permission_freshness_and_caps(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        a_config, first_source, fresh = await item(db, "a", headline="Fresh report one")
        b_config, _, stale = await item(db, "b", headline="Stale report two")
        c_config, third_source, revoked = await item(db, "c", headline="Revoked report three")
        stale.last_observed_at = NOW - timedelta(hours=2)
        definitions = {a_config.key: a_config, b_config.key: b_config, c_config.key: c_config}
        for row in (fresh, stale, revoked):
            await index_item(db, row, definitions, now=NOW)
        await revoke_rights(db, third_source, now=NOW)
        result = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            now=NOW,
        )
        assert [row.id for row in result.items] == [fresh.id]
        assert result.refresh_source_ids == (stale.source_id,)
        assert revoked.id not in {row.id for row in result.items}
        rights = await current_rights(db, first_source)
        for n in range(6):
            row = await store_item(
                db,
                first_source,
                rights,
                a_config,
                NewsItemInput(
                    external_id=f"bulk-{n}",
                    headline=f"Bulk report {n}",
                    canonical_url=f"https://a.example.com/bulk-{n}",
                    published_at=NOW,
                ),
                now=NOW,
            )
            await index_item(db, row, definitions, now=NOW)
        capped = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING", depth="QUICK"),
            now=NOW,
        )
        assert len(capped.items) == 4 and capped.limited and capped.examined == 8


@pytest.mark.asyncio
async def test_trending_excludes_suppressed_stories(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, row = await item(db, headline="Suppressed report")
        quiet_config, _, quiet = await item(db, "quiet", headline="Visible report")
        definitions = {config.key: config, quiet_config.key: quiet_config}
        story = await index_item(db, row, definitions, now=NOW)
        await index_item(db, quiet, definitions, now=NOW)
        story.suppressed = True
        await db.flush()
        selected = await select_evidence(
            db,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            question="What's trending?",
            plan=RetrievalPlan(intent="TRENDING"),
            now=NOW,
        )
        assert [row.id for row in selected.items] == [quiet.id]
