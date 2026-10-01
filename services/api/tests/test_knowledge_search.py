"""End-to-end connected search: scope, authority, freshness, exclusions, bounds."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from navox.core.settings import Settings
from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeExclusion,
    KnowledgeRecentSearch,
    KnowledgeResource,
    KnowledgeResourcePermission,
)
from navox.db.models import (
    ConnectorResource,
    RecurringObligation,
    User,
    WorkspaceMembership,
)
from navox.knowledge.contracts import Permission, PrincipalType
from navox.knowledge.exclusions import (
    ExclusionFilters,
    create_exclusion,
    load_exclusions,
)
from navox.knowledge.indexing import backfill_workspace
from navox.knowledge.planner import interpret
from navox.knowledge.recent import clear_recent_searches, list_recent_searches
from navox.knowledge.retrieval import fulltext_retriever, publication_check
from navox.knowledge.search_contracts import (
    AnswerState,
    DateRange,
    EvidenceResource,
    ExclusionCreate,
    ExclusionScope,
    SearchMode,
    SearchRequest,
    StructuredFact,
)
from navox.knowledge.service import KnowledgeUnavailable, evidence_bundle, search_knowledge
from tests.knowledge_search_support import (
    NOW,
    World,
    add_newer_news_source,
    build_engine,
    factory_for,
    load_world,
    news_settings,
    seed_commitment,
    seed_news_story,
    seed_subscription,
    seed_world,
)


@pytest_asyncio.fixture
async def search_engine(tmp_path: object) -> AsyncEngine:
    database = await build_engine(tmp_path)
    yield database.engine
    await database.dispose()


@pytest_asyncio.fixture
async def search_factory(
    search_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    factory = factory_for(search_engine)
    await seed_world(factory)
    return factory


async def project_message(factory: async_sessionmaker[AsyncSession], world: World) -> None:
    async with factory() as database:
        report = await backfill_workspace(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
        )
        assert report.outcomes
        await database.commit()


async def run_search(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    settings: Settings | None = None,
    **overrides: object,
):
    values: dict[str, object] = {"query": "quarterly budget"}
    values.update(overrides)
    request = SearchRequest(**values)
    async with factory() as database:
        return await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=request,
            now=NOW,
            settings=settings,
            record_recent=False,
        )


@pytest.mark.asyncio
async def test_search_finds_permitted_connected_content(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    response = await run_search(search_factory, world)
    assert response.interpreted_mode is SearchMode.SEARCH
    # Both the message and the calendar event mention the budget; the message
    # matches the shared term in its title, so it ranks first.
    assert {item.source_type for item in response.results} == {"EMAIL", "CALENDAR_EVENT"}
    result = response.results[0]
    assert result.source_type == "EMAIL"
    assert result.origin == "CONNECTED"
    assert result.excerpts
    assert "budget forecast" in result.excerpts[0].text
    assert result.provenance["connection_id"] == str(world.connection_id)
    assert response.coverage.returned == 2
    assert "SEMANTIC_UNAVAILABLE" in response.coverage.partial_reasons
    assert response.coverage.not_searchable == 0
    bundle = evidence_bundle(
        response=response,
        query="quarterly budget",
        workspace_id=world.workspace_id,
        user_id=world.user_id,
    )
    assert bundle.schema_version == "evidence-bundle.v1"
    assert bundle.resources[0].key == result.key


@pytest.mark.asyncio
async def test_private_resource_is_invisible_until_shared(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    async with search_factory() as database:
        member_view = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
            record_recent=False,
        )
        assert member_view.results == ()
        assert member_view.coverage.returned == 0
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        database.add(
            KnowledgeResourcePermission(
                resource_id=knowledge.id,
                workspace_id=world.workspace_id,
                principal_type=PrincipalType.USER.value,
                principal_id=world.member_id,
                permission=Permission.VIEW.value,
                valid_from=NOW - timedelta(hours=1),
            )
        )
        await database.commit()
    async with search_factory() as database:
        shared = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
            record_recent=False,
        )
    assert len(shared.results) == 1
    async with search_factory() as database:
        foreign = await search_knowledge(
            database,
            workspace_id=world.other_workspace_id,
            user_id=world.other_user_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
            record_recent=False,
        )
        assert foreign.results == ()


@pytest.mark.asyncio
async def test_revocation_between_retrieval_and_publication_drops_the_result(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    plan = interpret(SearchRequest(query="quarterly budget"))
    async with search_factory() as database:
        retrieved = await fulltext_retriever(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            plan=plan,
            filters=ExclusionFilters(),
            now=NOW,
        )
        assert retrieved.ranking and retrieved.candidates
        for grant in await database.scalars(select(KnowledgeResourcePermission)):
            grant.revoked_at = NOW - timedelta(minutes=1)
        await database.commit()
        kept, dropped = await publication_check(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
            candidates=retrieved.candidates,
            filters=ExclusionFilters(),
        )
    assert kept == set()
    assert dropped == len(retrieved.candidates)
    response = await run_search(search_factory, world)
    assert response.results == ()


@pytest.mark.asyncio
async def test_hash_fence_runs_before_stored_text_is_read(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A moved source revision is dropped before any derived text is loaded."""
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    async with search_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.content_hash = "q" * 64
        canonical.version = "etag-moved"
        await database.commit()
    plan = interpret(SearchRequest(query="forecast"))
    async with search_factory() as database:
        retrieved = await fulltext_retriever(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            plan=plan,
            filters=ExclusionFilters(),
            now=NOW,
        )
    assert retrieved.ranking == []
    assert retrieved.resources == {}
    assert retrieved.stale_dropped == 1
    assert retrieved.examined == 1


@pytest.mark.asyncio
async def test_source_version_change_invalidates_indexed_results(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    before = await run_search(search_factory, world, query="forecast")
    assert [item.source_type for item in before.results] == ["EMAIL"]
    async with search_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.content_hash = "z" * 64
        canonical.version = "etag-9"
        canonical.canonical = {
            "subject": "Quarterly planning review",
            "content": "The source changed after the index was built.",
            "source_type": "communication.message",
        }
        await database.commit()
    stale = await run_search(search_factory, world, query="forecast")
    assert stale.results == ()
    assert stale.coverage.stale_dropped >= 1
    async with search_factory() as database:
        chunks = list(await database.scalars(select(KnowledgeChunk)))
        assert chunks, "the stale index rows are kept until a reindex runs"


@pytest.mark.asyncio
async def test_metadata_claims_cannot_grant_access(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    async with search_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.canonical = {
            **dict(canonical.canonical),
            "permission": "VIEW",
            "principal_type": "PUBLIC",
            "source_read_capability": "communication.messages.read",
            "sensitivity": "PUBLIC",
        }
        await database.commit()
    await project_message(search_factory, world)
    async with search_factory() as database:
        member_view = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
            record_recent=False,
        )
    assert member_view.results == ()
    async with search_factory() as database:
        knowledge = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == "message-1")
        )
        assert knowledge is not None
        # Stored content can never lower sensitivity below the NavoX default.
        assert knowledge.sensitivity == "PERSONAL"
        grants = list(
            await database.scalars(
                select(KnowledgeResourcePermission).where(
                    KnowledgeResourcePermission.resource_id == knowledge.id
                )
            )
        )
        assert grants and all(
            grant.principal_id == world.user_id and grant.principal_type == PrincipalType.USER.value
            for grant in grants
        )


@pytest.mark.asyncio
async def test_exclusions_are_applied_before_ranking_and_reported(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    baseline = await run_search(search_factory, world, query="forecast")
    assert [item.source_type for item in baseline.results] == ["EMAIL"]
    async with search_factory() as database:
        database.add(
            KnowledgeExclusion(
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                scope=ExclusionScope.SOURCE.value,
                source_connection_id=world.connection_id,
            )
        )
        await database.commit()
    excluded = await run_search(search_factory, world, query="forecast")
    assert excluded.results == ()
    assert excluded.exclusion_count == 1
    assert excluded.coverage.exclusions_applied == 1
    async with search_factory() as database:
        database.add(
            KnowledgeExclusion(
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                scope=ExclusionScope.TYPE.value,
                resource_type="CALENDAR_EVENT",
            )
        )
        await database.commit()
    async with search_factory() as database:
        rows = list(await database.scalars(select(KnowledgeExclusion)))
        assert {row.scope for row in rows} == {"SOURCE", "TYPE"}
    filtered = await run_search(
        search_factory,
        world,
        query="budget",
        date_range=DateRange(start=datetime(2026, 10, 1, tzinfo=UTC)),
    )
    assert all(result.source_type != "CALENDAR_EVENT" for result in filtered.results)


@pytest.mark.asyncio
async def test_structured_date_filter_uses_typed_calendar_fields(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    inside = await run_search(
        search_factory,
        world,
        query="budget review meeting",
        date_range=DateRange(
            start=datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
            end=datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
        ),
    )
    assert "CALENDAR_EVENT" in {result.source_type for result in inside.results}
    outside = await run_search(
        search_factory,
        world,
        query="budget review meeting",
        date_range=DateRange(
            start=datetime(2026, 11, 1, tzinfo=UTC),
            end=datetime(2026, 11, 5, tzinfo=UTC),
        ),
    )
    assert "CALENDAR_EVENT" not in {result.source_type for result in outside.results}


@pytest.mark.asyncio
async def test_native_commitments_appear_as_typed_evidence_and_respect_type_exclusions(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await seed_commitment(search_factory, world)
    response = await run_search(search_factory, world, query="quarterly budget summary")
    types = {result.source_type for result in response.results}
    assert "COMMITMENT" in types
    fact_ids = {fact.fact_id for fact in response.structured_facts}
    assert any(fact_id.startswith("commitment:") for fact_id in fact_ids)
    commitment = next(result for result in response.results if result.source_type == "COMMITMENT")
    assert commitment.origin == "NATIVE"
    assert commitment.provenance["authority"] == "SPEC-002 commitments"
    async with search_factory() as database:
        database.add(
            KnowledgeExclusion(
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                scope=ExclusionScope.TYPE.value,
                resource_type="COMMITMENT",
            )
        )
        await database.commit()
    filtered = await run_search(search_factory, world, query="quarterly budget summary")
    assert all(result.source_type != "COMMITMENT" for result in filtered.results)


@pytest.mark.asyncio
async def test_ask_is_honestly_unavailable_but_still_returns_results(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    response = await run_search(search_factory, world, query="What was the budget forecast?")
    assert response.interpreted_mode is SearchMode.ASK
    assert response.answer is None
    assert response.answer_state is AnswerState.UNAVAILABLE
    assert response.results
    assert {mode.value for mode in response.unavailable_modes} == {"SEMANTIC", "GRAPH"}


@pytest.mark.asyncio
async def test_recent_searches_are_listed_and_cleared_without_touching_the_index(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    async with search_factory() as database:
        await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
        )
    async with search_factory() as database:
        await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
        )
    async with search_factory() as database:
        recent = await list_recent_searches(
            database, workspace_id=world.workspace_id, user_id=world.user_id
        )
        assert [row.query for row in recent] == ["quarterly budget"]
        assert recent[0].result_count == 2
        rows = list(await database.scalars(select(KnowledgeRecentSearch)))
        assert len(rows) == 1
    async with search_factory() as database:
        cleared = await clear_recent_searches(
            database, workspace_id=world.workspace_id, user_id=world.user_id
        )
        await database.commit()
        assert cleared == 1
    async with search_factory() as database:
        assert (
            await list_recent_searches(
                database, workspace_id=world.workspace_id, user_id=world.user_id
            )
            == []
        )
        assert (await database.scalar(select(KnowledgeResource))) is not None
        assert list(await database.scalars(select(KnowledgeChunk)))


@pytest.mark.asyncio
async def test_pagination_is_stable_and_truthful(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    async with search_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        for index, digest in enumerate(("e", "f"), start=2):
            database.add(
                ConnectorResource(
                    id=uuid4(),
                    workspace_id=world.workspace_id,
                    connector_connection_id=world.connection_id,
                    provider="fixture",
                    resource_type="communication.message",
                    external_id=f"message-{index}",
                    canonical={
                        "subject": f"Quarterly budget note {index}",
                        "content": "Quarterly budget detail.",
                        "source_type": "communication.message",
                    },
                    provider_metadata={},
                    source_created_at=NOW,
                    source_updated_at=NOW,
                    retrieved_at=NOW,
                    content_hash=digest * 64,
                )
            )
        await database.commit()
    async with search_factory() as database:
        report = await backfill_workspace(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
        )
        await database.commit()
    assert len(report.outcomes) == 4
    seen: list[str] = []
    for offset, more_pages in ((0, True), (1, True), (2, False)):
        page = await run_search(search_factory, world, query="quarterly", limit=1, offset=offset)
        assert page.coverage.returned == 1
        assert page.coverage.truncated is more_pages
        seen.append(page.results[0].key)
    assert len(set(seen)) == 3
    repeat = await run_search(search_factory, world, query="quarterly", limit=1, offset=1)
    assert repeat.results[0].key == seen[1]


@pytest.mark.asyncio
async def test_search_denies_paused_and_nonmember_and_unknown_scope(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    async with search_factory() as database:
        user = await database.get(User, world.user_id)
        assert user is not None
        user.agent_paused = True
        await database.commit()
    async with search_factory() as database:
        with pytest.raises(KnowledgeUnavailable):
            await search_knowledge(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                request=SearchRequest(query="budget"),
                now=NOW,
            )
    async with search_factory() as database:
        user = await database.get(User, world.user_id)
        assert user is not None
        user.agent_paused = False
        membership = await database.get(
            WorkspaceMembership, (world.other_workspace_id, world.user_id)
        )
        assert membership is None
        with pytest.raises(KnowledgeUnavailable):
            await search_knowledge(
                database,
                workspace_id=world.other_workspace_id,
                user_id=world.user_id,
                request=SearchRequest(query="budget"),
                now=NOW,
            )
        unknown = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=SearchRequest(query="budget", sources=(uuid4(),)),
            now=NOW,
            record_recent=False,
        )
        assert unknown.results == ()


@pytest.mark.asyncio
async def test_exclusion_payload_validation(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    from navox.knowledge.exclusions import create_exclusion

    world = await load_world(search_factory)
    async with search_factory() as database:
        with pytest.raises(ValueError):
            await create_exclusion(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                payload=ExclusionCreate(scope=ExclusionScope.SOURCE),
            )
        await database.rollback()
    async with search_factory() as database:
        row = await create_exclusion(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            payload=ExclusionCreate(
                scope=ExclusionScope.FOLDER,
                source_connection_id=world.connection_id,
                external_id="thread-1",
            ),
        )
        await database.commit()
        assert row.external_id == "thread-1"


@pytest.mark.asyncio
async def test_native_domains_respect_type_and_connected_source_filters(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A calendar-only request never returns subscriptions or commitments."""
    world = await load_world(search_factory)
    await seed_commitment(search_factory, world)
    await seed_subscription(search_factory, world)
    everything = await run_search(search_factory, world, query="streamly budget")
    found = {result.source_type for result in everything.results}
    assert {"COMMITMENT", "SUBSCRIPTION"} <= found
    calendar_only = await run_search(
        search_factory, world, query="streamly budget", types=("CALENDAR_EVENT",)
    )
    assert calendar_only.results == ()
    commitment_only = await run_search(
        search_factory, world, query="streamly budget", types=("COMMITMENT",)
    )
    assert {result.source_type for result in commitment_only.results} == {"COMMITMENT"}
    # A connected-source filter names connection UUIDs; native domains are not
    # connections, so the filter excludes them explicitly rather than silently.
    filtered = await run_search(
        search_factory, world, query="streamly budget", sources=(world.connection_id,)
    )
    assert all(result.origin == "CONNECTED" for result in filtered.results)
    assert "CONNECTED_SOURCE_FILTER_EXCLUDES_NATIVE_DOMAINS" in filtered.coverage.partial_reasons


@pytest.mark.asyncio
async def test_connected_structured_resources_emit_typed_facts(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    inside = await run_search(
        search_factory,
        world,
        query="budget review",
        date_range=DateRange(
            start=datetime(2026, 10, 2, tzinfo=UTC),
            end=datetime(2026, 10, 3, tzinfo=UTC),
        ),
    )
    event = next(result for result in inside.results if result.source_type == "CALENDAR_EVENT")
    facts = [fact for fact in inside.structured_facts if fact.resource_id == event.resource_id]
    assert facts
    assert facts[0].label == "Event start"
    assert facts[0].authority == "Calendar (source system)"
    assert any(fact.label == "Event end" for fact in facts)


@pytest.mark.asyncio
async def test_unrelated_keyword_queries_do_not_return_typed_rows(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A date/state-free query still has to match the stored topic."""
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    unrelated = await run_search(
        search_factory,
        world,
        query="compiler release notes",
        types=("CALENDAR_EVENT",),
    )
    assert unrelated.results == ()
    matching = await run_search(
        search_factory, world, query="budget review meeting", types=("CALENDAR_EVENT",)
    )
    assert {result.source_type for result in matching.results} == {"CALENDAR_EVENT"}


@pytest.mark.asyncio
async def test_news_evidence_uses_the_owned_service_and_current_rights(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    story_id, config = await seed_news_story(
        search_factory,
        world,
        headline="Quarterly budget observation",
        description="The station recorded a quarterly budget shift.",
    )
    enabled = news_settings(config)
    found = await run_search(
        search_factory, world, settings=enabled, query="quarterly budget observation"
    )
    story = next((result for result in found.results if result.source_type == "NEWS_STORY"), None)
    assert story is not None
    assert story.resource_id == story_id
    assert story.canonical_url == "https://news.example.com/report"
    # Verification and lifecycle are distinct News fields.
    assert story.provenance["verification_status"] != story.provenance["lifecycle_status"]
    # Without the trusted catalog the same story is not evidence.
    disabled = await run_search(
        search_factory,
        world,
        settings=Settings(_env_file=None, app_environment="test"),
        query="quarterly budget observation",
    )
    assert all(result.source_type != "NEWS_STORY" for result in disabled.results)
    # The operator's News flag is honoured even with a valid catalog present.
    flag_off = news_settings(config).model_copy(update={"news_feed_enabled": False})
    blocked = await run_search(
        search_factory, world, settings=flag_off, query="quarterly budget observation"
    )
    assert all(result.source_type != "NEWS_STORY" for result in blocked.results)
    # Revoking current rights removes it, with no partial or cached evidence.
    from sqlalchemy import select as sa_select

    from navox.db.news import NewsSource
    from navox.news.registry import revoke_rights

    async with search_factory() as database:
        source = await database.scalar(sa_select(NewsSource))
        assert source is not None
        await revoke_rights(database, source, now=NOW)
        await database.commit()
    revoked = await run_search(
        search_factory, world, settings=enabled, query="quarterly budget observation"
    )
    assert all(result.source_type != "NEWS_STORY" for result in revoked.results)


@pytest.mark.asyncio
async def test_news_evidence_never_exposes_a_restricted_snippet(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A source that never allowed snippet storage contributes no stored text."""
    world = await load_world(search_factory)
    _, config = await seed_news_story(search_factory, world, snippet_storage_allowed=False)
    restricted = await run_search(
        search_factory,
        world,
        settings=news_settings(config),
        query="quarterly budget observation",
    )
    story = next(
        (result for result in restricted.results if result.source_type == "NEWS_STORY"),
        None,
    )
    assert story is not None
    assert story.title == "A quarterly budget observation"
    assert story.excerpts == ()
    assert story.canonical_url == "https://news.example.com/report"


@pytest.mark.asyncio
async def test_evidence_bundle_snapshot_is_unique_and_never_truncates_the_trace(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    response = await run_search(search_factory, world, query="forecast")
    first = evidence_bundle(
        response=response, query="forecast", workspace_id=world.workspace_id, user_id=world.user_id
    )
    second = evidence_bundle(
        response=response, query="forecast", workspace_id=world.workspace_id, user_id=world.user_id
    )
    assert first.permission_snapshot_id != second.permission_snapshot_id
    assert first.permission_snapshot_id.startswith("ps-")
    assert first.retrieval_trace_id == second.retrieval_trace_id == response.trace_id
    assert response.trace_id not in first.permission_snapshot_id
    assert str(world.user_id) not in first.permission_snapshot_id


@pytest.mark.asyncio
async def test_publication_reapplies_exclusions_against_fresh_source_scope(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An exclusion added mid-flight, and a moved parent, both remove results."""
    world = await load_world(search_factory)
    await project_message(search_factory, world)
    plan = interpret(SearchRequest(query="forecast"))
    async with search_factory() as database:
        retrieved = await fulltext_retriever(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            plan=plan,
            filters=ExclusionFilters(),
            now=NOW,
        )
        assert retrieved.candidates
        # The source's parent moves without its body hash changing, so only a
        # fresh read can honour the folder exclusion below.
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.external_parent_id = "folder-9"
        await database.commit()
        await create_exclusion(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            payload=ExclusionCreate(
                scope=ExclusionScope.FOLDER,
                source_connection_id=world.connection_id,
                external_id="folder-9",
            ),
        )
        await database.commit()
        filters = await load_exclusions(
            database, workspace_id=world.workspace_id, user_id=world.user_id
        )
        kept, dropped = await publication_check(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
            candidates=retrieved.candidates,
            filters=filters,
        )
    assert kept == set()
    assert dropped == len(retrieved.candidates)
    async with search_factory() as database:
        await create_exclusion(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            payload=ExclusionCreate(scope=ExclusionScope.TYPE, resource_type="EMAIL"),
        )
        await database.commit()
        filters = await load_exclusions(
            database, workspace_id=world.workspace_id, user_id=world.user_id
        )
    async with search_factory() as database:
        retrieved = await fulltext_retriever(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            plan=plan,
            filters=ExclusionFilters(),
            now=NOW,
        )
        kept, dropped = await publication_check(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
            candidates=retrieved.candidates,
            filters=filters,
        )
    assert kept == set() and dropped >= 1


@pytest.mark.asyncio
async def test_native_recheck_values_replace_stale_selections(
    search_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-key native change must never leak the earlier text or facts."""
    from navox.knowledge import service as knowledge_service
    from navox.knowledge.adapters import NativeEvidence

    world = await load_world(search_factory)
    stale = EvidenceResource(
        source_type="SUBSCRIPTION",
        resource_id=uuid4(),
        title="Old plan name",
        excerpts=(),
        source_version="1",
        provenance={"authority": "SPEC-004 subscriptions"},
        origin="NATIVE",
    )
    fresh = stale.model_copy(update={"title": "New plan name", "source_version": "2"})
    old_fact = StructuredFact(
        fact_id=f"subscription:{stale.resource_id}:next_renewal_at",
        label="Next renewal",
        value="2026-10-01T00:00:00+00:00",
        source_type="SUBSCRIPTION",
        resource_id=stale.resource_id,
        authority="SPEC-004 subscriptions",
    )
    new_fact = old_fact.model_copy(update={"value": "2026-11-01T00:00:00+00:00"})
    calls = {"count": 0}

    async def fake_native_evidence(*_args: object, **_kwargs: object) -> NativeEvidence:
        calls["count"] += 1
        if calls["count"] == 1:
            return NativeEvidence(resources=(stale,), facts=(old_fact,), truncated=False)
        return NativeEvidence(resources=(fresh,), facts=(new_fact,), truncated=False)

    monkeypatch.setattr(knowledge_service, "native_evidence", fake_native_evidence)
    response = await run_search(search_factory, world, query="plan")
    assert calls["count"] == 2
    subscription = next(
        result for result in response.results if result.source_type == "SUBSCRIPTION"
    )
    assert subscription.title == "New plan name"
    assert subscription.source_version == "2"
    assert [fact.value for fact in response.structured_facts] == [new_fact.value]


@pytest.mark.asyncio
async def test_native_evidence_reflects_current_values_across_requests(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    subscription_id = await seed_subscription(
        search_factory, world, name="Streamly", plan_name="Streamly Plus"
    )
    first = await run_search(search_factory, world, query="streamly")
    assert any(result.title == "Streamly Plus" for result in first.results)
    async with search_factory() as database:
        row = await database.get(RecurringObligation, subscription_id)
        assert row is not None
        row.plan_name = "Streamly Max"
        await database.commit()
    second = await run_search(search_factory, world, query="streamly")
    titles = {result.title for result in second.results}
    assert "Streamly Max" in titles
    assert "Streamly Plus" not in titles


@pytest.mark.asyncio
async def test_news_citation_uses_the_story_anchor_source(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A newer source must not supply the anchor headline's quote and URL."""
    world = await load_world(search_factory)
    story_id, config = await seed_news_story(
        search_factory,
        world,
        headline="Anchor headline about the budget",
        description="The anchor source statement.",
    )
    await add_newer_news_source(
        search_factory,
        world,
        config,
        story_id,
        headline="Later headline about the budget",
        canonical_url="https://news.example.com/later",
        published_at=NOW + timedelta(minutes=4),
    )
    response = await run_search(
        search_factory, world, settings=news_settings(config), query="budget"
    )
    story = next(result for result in response.results if result.source_type == "NEWS_STORY")
    assert story.title == "Anchor headline about the budget"
    assert story.canonical_url == "https://news.example.com/report"
    assert story.provenance["source_name"] == "Search news fixture"
    assert story.provenance["content_basis"] in {"stored_snippet", "metadata"}
