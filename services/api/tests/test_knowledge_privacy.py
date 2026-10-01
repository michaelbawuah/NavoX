"""Privacy regressions: no content reads or existence oracles before authority."""

from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from navox.db.knowledge import (
    KnowledgeExclusion,
    KnowledgeResource,
    KnowledgeResourceIndex,
    KnowledgeResourcePermission,
)
from navox.db.models import ConnectorResource
from navox.knowledge.contracts import Permission, PrincipalType
from navox.knowledge.exclusions import ExclusionFilters, create_exclusion
from navox.knowledge.indexing import backfill_workspace, index_connector_resource
from navox.knowledge.planner import interpret
from navox.knowledge.retrieval import fulltext_retriever
from navox.knowledge.search_contracts import (
    DateRange,
    ExclusionCreate,
    ExclusionScope,
    SearchRequest,
)
from navox.knowledge.service import load_resource_detail, search_knowledge
from tests.knowledge_search_support import (
    NOW,
    World,
    build_engine,
    calendar_pair,
    factory_for,
    load_world,
    seed_world,
)


@pytest_asyncio.fixture
async def privacy_engine(tmp_path: object) -> AsyncEngine:
    database = await build_engine(tmp_path)
    yield database.engine
    await database.dispose()


@pytest_asyncio.fixture
async def privacy_factory(
    privacy_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    factory = factory_for(privacy_engine)
    await seed_world(factory)
    return factory


def selected_columns(statement: str) -> str:
    """The projection list of one SQL statement, lowercased."""
    lowered = statement.lower()
    start = lowered.find("select")
    if start == -1:
        return ""
    end = lowered.find("\nfrom", start)
    if end == -1:
        end = lowered.find(" from ", start)
    return lowered[start:end] if end != -1 else lowered[start:]


def capture(engine: AsyncEngine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(
        _conn: object,
        _cursor: object,
        statement: str,
        _params: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(statement)

    return statements


async def project(factory: async_sessionmaker[AsyncSession], world: World) -> None:
    async with factory() as database:
        await backfill_workspace(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
        )
        await database.commit()


async def detail(factory: async_sessionmaker[AsyncSession], world: World, resource_id: object):
    async with factory() as database:
        return await load_resource_detail(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_id=resource_id,  # type: ignore[arg-type]
            now=NOW,
        )


async def seed_private_no_content(factory: async_sessionmaker[AsyncSession], world: World) -> None:
    """A typed record whose retained content was removed after indexing.

    A date query still matches it structurally, only the owner holds a VIEW
    grant, and the member must never learn that it exists.
    """
    connection, event = await calendar_pair(factory)
    async with factory() as database:
        stored = await database.get(ConnectorResource, event.id)
        assert stored is not None
        await index_connector_resource(database, connection=connection, resource=stored, now=NOW)
        await database.commit()
    async with factory() as database:
        index_row = await database.scalar(
            select(KnowledgeResourceIndex)
            .join(
                KnowledgeResource,
                KnowledgeResource.id == KnowledgeResourceIndex.resource_id,
            )
            .where(KnowledgeResource.external_resource_id == "event-1")
        )
        assert index_row is not None
        index_row.index_state = "NO_CONTENT"
        index_row.chunk_count = 0
        index_row.text_length = 0
        await database.commit()


async def clear_exclusions(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as database:
        for row in await database.scalars(select(KnowledgeExclusion)):
            await database.delete(row)
        await database.commit()


@pytest.mark.asyncio
async def test_denied_retrieval_never_selects_stored_content(
    privacy_engine: AsyncEngine,
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A denied caller's query may reference columns for matching, never load them."""
    world = await load_world(privacy_factory)
    await project(privacy_factory, world)
    plan = interpret(SearchRequest(query="quarterly budget"))
    statements = capture(privacy_engine)
    async with privacy_factory() as database:
        result = await fulltext_retriever(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            plan=plan,
            filters=ExclusionFilters(),
            now=NOW,
        )
    assert result.resources == {}
    assert result.candidates == {}
    assert result.examined == 0
    projections = [selected_columns(statement) for statement in statements]
    assert projections
    assert all("normalized_text" not in projection for projection in projections)
    assert all("metadata" not in projection for projection in projections)
    assert all("canonical" not in projection for projection in projections)


@pytest.mark.asyncio
async def test_denied_search_never_loads_foreign_content(
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(privacy_factory)
    await project(privacy_factory, world)
    statements: list[str] = []
    async with privacy_factory() as database:
        bound = database.bind
        assert isinstance(bound, AsyncEngine)

        @event.listens_for(bound.sync_engine, "before_cursor_execute")
        def _record(
            _conn: object,
            _cursor: object,
            statement: str,
            _params: object,
            _context: object,
            _many: bool,
        ) -> None:
            statements.append(statement)

        await search_knowledge(
            database,
            workspace_id=world.other_workspace_id,
            user_id=world.other_user_id,
            request=SearchRequest(query="quarterly budget"),
            now=NOW,
            record_recent=False,
        )
    assert statements
    assert all("normalized_text" not in selected_columns(row) for row in statements)


@pytest.mark.asyncio
async def test_no_content_counts_are_authorized_only(
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A member's probe must not reveal that a private typed record exists."""
    world = await load_world(privacy_factory)
    await seed_private_no_content(privacy_factory, world)
    window = DateRange(
        start=datetime(2026, 10, 1, tzinfo=NOW.tzinfo),
        end=datetime(2026, 10, 3, tzinfo=NOW.tzinfo),
    )
    async with privacy_factory() as database:
        member_view = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            request=SearchRequest(query="board review", date_range=window),
            now=NOW,
            record_recent=False,
        )
    assert member_view.results == ()
    assert member_view.coverage.not_searchable == 0
    assert "SOURCE_CONTENT_NOT_RETAINED" not in member_view.coverage.partial_reasons
    async with privacy_factory() as database:
        owner_view = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=SearchRequest(query="board review", date_range=window),
            now=NOW,
            record_recent=False,
        )
    assert owner_view.results == ()
    assert owner_view.coverage.not_searchable == 1
    assert "SOURCE_CONTENT_NOT_RETAINED" in owner_view.coverage.partial_reasons


@pytest.mark.asyncio
async def test_resource_detail_honours_exclusions_and_revision_drift(
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(privacy_factory)
    await project(privacy_factory, world)
    async with privacy_factory() as database:
        knowledge = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == "message-1")
        )
        assert knowledge is not None
        resource_id = knowledge.id
    assert (await detail(privacy_factory, world, resource_id)) is not None

    targets = (
        ExclusionCreate(scope=ExclusionScope.TYPE, resource_type="EMAIL"),
        ExclusionCreate(scope=ExclusionScope.SOURCE, source_connection_id=world.connection_id),
        ExclusionCreate(scope=ExclusionScope.RESOURCE, resource_id=resource_id),
    )
    for payload in targets:
        async with privacy_factory() as database:
            await create_exclusion(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                payload=payload,
            )
            await database.commit()
        assert (await detail(privacy_factory, world, resource_id)) is None
        await clear_exclusions(privacy_factory)
    assert (await detail(privacy_factory, world, resource_id)) is not None

    async with privacy_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.content_hash = "r" * 64
        await database.commit()
    assert (await detail(privacy_factory, world, resource_id)) is None
    async with privacy_factory() as database:
        canonical = await database.get(ConnectorResource, world.message_id)
        assert canonical is not None
        canonical.content_hash = "a" * 64
        await database.commit()
    assert (await detail(privacy_factory, world, resource_id)) is not None


@pytest.mark.asyncio
async def test_resource_detail_reports_missing_content_without_a_body(
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(privacy_factory)
    connection, event = await calendar_pair(privacy_factory)
    async with privacy_factory() as database:
        stored = await database.get(ConnectorResource, event.id)
        assert stored is not None
        stored.canonical = {
            "status": "active",
            "content_persisted": False,
            "subject": "Private board review",
        }
        stored.content_hash = "s" * 64
        await database.commit()
    async with privacy_factory() as database:
        stored = await database.get(ConnectorResource, event.id)
        assert stored is not None
        await index_connector_resource(database, connection=connection, resource=stored, now=NOW)
        await database.commit()
    async with privacy_factory() as database:
        knowledge = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == "event-1")
        )
        assert knowledge is not None
        resource_id = knowledge.id
    record = await detail(privacy_factory, world, resource_id)
    assert record is not None
    assert record.index_state == "NO_CONTENT"
    assert record.title is None
    assert record.canonical_url is None
    assert record.chunks == ()


async def seed_private_matching_rows(
    factory: async_sessionmaker[AsyncSession], world: World, *, count: int
) -> None:
    """Bulk private rows whose titles would match a probe query."""
    async with factory() as database:
        for index in range(count):
            resource_id = uuid4()
            await database.execute(
                KnowledgeResource.__table__.insert().values(
                    id=resource_id,
                    workspace_id=world.workspace_id,
                    owner_user_id=world.user_id,
                    source_type="EMAIL",
                    source_connection_id=world.connection_id,
                    external_resource_id=f"private-{index}",
                    source_resource_id=None,
                    source_read_capability="communication.messages.read",
                    title=f"quarterly private note {index}",
                    normalized_text=None,
                    sensitivity="PERSONAL",
                )
            )
            await database.execute(
                KnowledgeResourceIndex.__table__.insert().values(
                    id=uuid4(),
                    workspace_id=world.workspace_id,
                    resource_id=resource_id,
                    source_content_hash="p" * 64,
                    index_state="INDEXED",
                    text_length=0,
                    chunk_count=0,
                )
            )
            await database.execute(
                KnowledgeResourcePermission.__table__.insert().values(
                    id=uuid4(),
                    resource_id=resource_id,
                    workspace_id=world.workspace_id,
                    principal_type=PrincipalType.USER.value,
                    principal_id=world.user_id,
                    permission=Permission.VIEW.value,
                    inherited=False,
                    valid_from=NOW,
                )
            )
        await database.commit()


@pytest.mark.asyncio
async def test_candidate_truncation_is_not_a_private_existence_oracle(
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Private matching rows must not change a member's coverage numbers."""
    world = await load_world(privacy_factory)
    async with privacy_factory() as database:
        baseline = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            request=SearchRequest(query="quarterly"),
            now=NOW,
            record_recent=False,
        )
    assert baseline.results == ()
    assert baseline.coverage.examined == 0
    assert baseline.coverage.truncated is False
    await seed_private_matching_rows(privacy_factory, world, count=210)
    async with privacy_factory() as database:
        probed = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.member_id,
            request=SearchRequest(query="quarterly"),
            now=NOW,
            record_recent=False,
        )
    assert probed.results == ()
    assert probed.coverage.examined == baseline.coverage.examined
    assert probed.coverage.truncated is baseline.coverage.truncated
    assert probed.coverage.returned == baseline.coverage.returned
    # The owner sees the same corpus and gets an honest bounded-scan report.
    async with privacy_factory() as database:
        owner_view = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=SearchRequest(query="quarterly"),
            now=NOW,
            record_recent=False,
        )
    assert owner_view.coverage.truncated is True


@pytest.mark.asyncio
async def test_resource_detail_denies_when_a_no_content_revision_moves(
    privacy_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A record without retained content still requires a current revision."""
    world = await load_world(privacy_factory)
    connection, event = await calendar_pair(privacy_factory)
    async with privacy_factory() as database:
        stored = await database.get(ConnectorResource, event.id)
        assert stored is not None
        stored.canonical = {"status": "active", "content_persisted": False}
        stored.content_hash = "s" * 64
        await database.commit()
    async with privacy_factory() as database:
        stored = await database.get(ConnectorResource, event.id)
        assert stored is not None
        await index_connector_resource(database, connection=connection, resource=stored, now=NOW)
        await database.commit()
    async with privacy_factory() as database:
        knowledge = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == "event-1")
        )
        assert knowledge is not None
        resource_id = knowledge.id
    assert (await detail(privacy_factory, world, resource_id)) is not None
    async with privacy_factory() as database:
        stored = await database.get(ConnectorResource, event.id)
        assert stored is not None
        stored.content_hash = "t" * 64
        await database.commit()
    assert (await detail(privacy_factory, world, resource_id)) is None


@pytest.mark.asyncio
async def test_detail_rechecks_folder_after_source_moves_mid_request(
    privacy_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from navox.knowledge import service

    world = await load_world(privacy_factory)
    await project(privacy_factory, world)
    async with privacy_factory() as database:
        knowledge = await database.scalar(
            select(KnowledgeResource).where(KnowledgeResource.external_resource_id == "message-1")
        )
        assert knowledge is not None
        resource_id = knowledge.id

    original = service.load_exclusions
    reads = 0

    async def move_then_load(database: AsyncSession, *, workspace_id, user_id):
        nonlocal reads
        reads += 1
        if reads == 2:
            canonical = await database.get(ConnectorResource, world.message_id)
            assert canonical is not None
            canonical.external_parent_id = "private-folder"
            database.add(
                KnowledgeExclusion(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    scope="FOLDER",
                    source_connection_id=world.connection_id,
                    external_id="private-folder",
                )
            )
            await database.flush()
        return await original(database, workspace_id=workspace_id, user_id=user_id)

    monkeypatch.setattr(service, "load_exclusions", move_then_load)
    assert await detail(privacy_factory, world, resource_id) is None
    assert reads == 2
