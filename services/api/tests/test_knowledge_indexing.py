"""Projection of retained canonical content into the connected knowledge index."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from navox.db.knowledge import KnowledgeChunk, KnowledgeResource, KnowledgeResourceIndex
from navox.db.models import (
    ConnectorConnection,
    ConnectorResource,
    User,
    WorkspaceMembership,
)
from navox.knowledge.indexing import (
    INDEXED,
    NO_CONTENT,
    backfill_workspace,
    chunk_spans,
    index_connector_resource,
    knowledge_type_for,
    structured_window,
    trusted_read_capability,
)
from tests.knowledge_search_support import (
    NOW,
    build_engine,
    calendar_pair,
    factory_for,
    gmail_pair,
    load_world,
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


message_resource = gmail_pair


def test_provider_resource_type_mapping_is_deterministic() -> None:
    assert knowledge_type_for("communication.message").value == "EMAIL"
    assert knowledge_type_for("communication.thread").value == "EMAIL_THREAD"
    assert knowledge_type_for("calendar.event").value == "CALENDAR_EVENT"
    assert knowledge_type_for("academic.assignment").value == "CANVAS_ASSIGNMENT"
    assert knowledge_type_for("something.unknown").value == "OTHER"


def test_trusted_capability_mapping_denies_unmapped_types() -> None:
    assert trusted_read_capability("fixture-mail", "communication.message") is None
    assert trusted_read_capability("google-gmail", "communication.message") == (
        "communication.messages.read"
    )
    assert trusted_read_capability("google-gmail", "unknown.type") is None


def test_chunk_spans_are_bounded_and_paragraph_aware() -> None:
    spans = chunk_spans("alpha\n\nbeta\n\n" + "x" * 2_500, limit=100)
    assert spans[0] == "alpha\n\nbeta"
    assert all(len(span) <= 100 for span in spans)
    assert len(spans) >= 2


def test_structured_window_only_for_typed_sources() -> None:
    kind, start, end = structured_window(
        knowledge_type_for("calendar.event"),
        {"starts_at": "2026-10-02T15:00:00+00:00", "ends_at": "2026-10-02T16:00:00+00:00"},
    )
    assert (kind, start is not None, end is not None) == ("calendar.event", True, True)
    assert structured_window(knowledge_type_for("communication.message"), {}) == (None, None, None)
    assert structured_window(knowledge_type_for("calendar.event"), {"starts_at": "not-a-time"}) == (
        "calendar.event",
        None,
        None,
    )


@pytest.mark.asyncio
async def test_projecting_retained_content_indexes_text_and_grants_owner_view(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        outcome = await index_connector_resource(
            database, connection=connection, resource=resource, now=NOW
        )
        await database.commit()
    assert outcome.state == INDEXED
    assert outcome.chunks >= 1
    assert outcome.grant_created is True
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        assert knowledge.source_resource_id == resource.id
        assert knowledge.source_read_capability == "communication.messages.read"
        assert knowledge.normalized_text is not None
        assert "budget forecast" in knowledge.normalized_text
        index_row = await database.scalar(select(KnowledgeResourceIndex))
        assert index_row is not None
        assert index_row.index_state == INDEXED
        assert index_row.chunk_count == outcome.chunks
        chunks = list(await database.scalars(select(KnowledgeChunk)))
        assert chunks and all(chunk.workspace_id == knowledge.workspace_id for chunk in chunks)


@pytest.mark.asyncio
async def test_unretained_content_is_recorded_without_text(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.canonical = {"status": "active", "content_persisted": False}
        stored.content_hash = "c" * 64
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        outcome = await index_connector_resource(
            database, connection=connection, resource=reloaded, now=NOW
        )
        await database.commit()
    assert outcome.state == NO_CONTENT
    assert outcome.chunks == 0
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None and knowledge.normalized_text is None
        assert list(await database.scalars(select(KnowledgeChunk))) == []


@pytest.mark.asyncio
async def test_projection_is_idempotent_and_refreshes_on_content_change(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        first = await index_connector_resource(
            database, connection=connection, resource=resource, now=NOW
        )
        second = await index_connector_resource(
            database, connection=connection, resource=resource, now=NOW
        )
        await database.commit()
    assert first.grant_created is True
    assert second.grant_created is False
    assert first.chunks == second.chunks
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.canonical = {
            "subject": "Quarterly planning review",
            "content": "Updated quarterly budget numbers changed the forecast.",
            "source_type": "communication.message",
        }
        stored.content_hash = "d" * 64
        stored.version = "etag-2"
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        refreshed = await index_connector_resource(
            database, connection=connection, resource=reloaded, now=NOW
        )
        await database.commit()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        assert knowledge.source_version == "etag-2"
        assert knowledge.normalized_text is not None
        assert "Updated quarterly budget" in knowledge.normalized_text
        index_row = await database.scalar(select(KnowledgeResourceIndex))
        assert index_row is not None and index_row.source_content_hash == "d" * 64
        assert refreshed.chunks == index_row.chunk_count


@pytest.mark.asyncio
async def test_revoked_owner_grant_is_never_resurrected_by_a_rebuild(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    from navox.db.knowledge import KnowledgeResourcePermission

    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        await index_connector_resource(database, connection=connection, resource=resource, now=NOW)
        await database.commit()
    async with search_factory() as database:
        grant = await database.scalar(select(KnowledgeResourcePermission))
        assert grant is not None
        grant.revoked_at = NOW
        await database.commit()
    async with search_factory() as database:
        outcome = await index_connector_resource(
            database, connection=connection, resource=resource, now=NOW, force=True
        )
        await database.commit()
    assert outcome.grant_created is False
    async with search_factory() as database:
        rows = list(await database.scalars(select(KnowledgeResourcePermission)))
        assert len(rows) == 1
        assert rows[0].revoked_at is not None


@pytest.mark.asyncio
async def test_unmapped_resource_type_and_unauthorized_connection_are_denied(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.resource_type = "unknown.thing"
        await database.commit()
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=stored, now=NOW
            )
        await database.rollback()
    async with search_factory() as database:
        reloaded_connection = await database.get(ConnectorConnection, connection.id)
        reloaded_resource = await database.get(ConnectorResource, resource.id)
        assert reloaded_connection is not None and reloaded_resource is not None
        reloaded_connection.status = "DISCONNECTED"
        await database.commit()
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=reloaded_connection, resource=reloaded_resource, now=NOW
            )
        await database.rollback()


@pytest.mark.asyncio
async def test_cross_workspace_projection_is_rejected(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.workspace_id = uuid4()
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=stored, now=NOW
            )
        await database.rollback()


@pytest.mark.asyncio
async def test_structured_calendar_event_records_a_typed_window(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, event = await calendar_pair(search_factory)
    async with search_factory() as database:
        stored_event = await database.get(ConnectorResource, event.id)
        assert stored_event is not None
        await index_connector_resource(
            database, connection=connection, resource=stored_event, now=NOW
        )
        await database.commit()
    async with search_factory() as database:
        index_row = await database.scalar(
            select(KnowledgeResourceIndex).where(KnowledgeResourceIndex.structured_at.is_not(None))
        )
        assert index_row is not None
        assert index_row.structured_kind == "calendar.event"
        assert index_row.structured_at is not None
        assert index_row.structured_at.day == 2


@pytest.mark.asyncio
async def test_backfill_is_bounded_reported_and_skips_unauthorized_rows(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = await load_world(search_factory)
    async with search_factory() as database:
        report = await backfill_workspace(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
            limit=10,
        )
        await database.commit()
    assert report.bound == 10
    assert report.truncated is False
    assert len(report.outcomes) == 2
    assert {outcome.source_type.value for outcome in report.outcomes} == {
        "EMAIL",
        "CALENDAR_EVENT",
    }
    async with search_factory() as database:
        report = await backfill_workspace(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
            limit=1,
        )
        assert report.truncated is True
        with pytest.raises(ValueError):
            await backfill_workspace(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                now=NOW,
                limit=10_000,
            )
        await database.rollback()


@pytest.mark.asyncio
async def test_source_content_cannot_lower_sensitivity_below_the_default(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.canonical = {**dict(stored.canonical), "sensitivity": "PUBLIC"}
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        await index_connector_resource(database, connection=connection, resource=reloaded, now=NOW)
        await database.commit()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        assert knowledge.sensitivity == "PERSONAL"


@pytest.mark.asyncio
async def test_removed_source_fields_are_cleared_not_carried_over(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        await index_connector_resource(database, connection=connection, resource=resource, now=NOW)
        await database.commit()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None and knowledge.canonical_url is not None
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.source_url = None
        stored.canonical = {
            "subject": "Quarterly planning review",
            "content": "Revised quarterly budget content.",
            "source_type": "communication.message",
        }
        stored.content_hash = "g" * 64
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        await index_connector_resource(database, connection=connection, resource=reloaded, now=NOW)
        await database.commit()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        assert knowledge.canonical_url is None
        assert knowledge.title == "Quarterly planning review"


@pytest.mark.asyncio
async def test_deleted_source_retires_the_projection_permanently(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        await index_connector_resource(database, connection=connection, resource=resource, now=NOW)
        await database.commit()
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.deleted = True
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=reloaded, now=NOW
            )
        await database.commit()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        assert knowledge.deleted_at is not None
        assert knowledge.normalized_text is None
        assert knowledge.title is None
        assert knowledge.canonical_url is None
        assert list(await database.scalars(select(KnowledgeChunk))) == []
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.deleted = False
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=reloaded, now=NOW
            )
        await database.rollback()


@pytest.mark.asyncio
async def test_force_never_rebuilds_a_retired_projection(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        await index_connector_resource(database, connection=connection, resource=resource, now=NOW)
        await database.commit()
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.deleted = True
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=reloaded, now=NOW
            )
        await database.commit()
    async with search_factory() as database:
        stored = await database.get(ConnectorResource, resource.id)
        assert stored is not None
        stored.deleted = False
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        with pytest.raises(ValueError):
            await index_connector_resource(
                database,
                connection=connection,
                resource=reloaded,
                now=NOW,
                force=True,
            )
        await database.rollback()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None and knowledge.deleted_at is not None
        assert list(await database.scalars(select(KnowledgeChunk))) == []


@pytest.mark.asyncio
async def test_stale_caller_connection_and_paused_owner_cannot_index(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    async with search_factory() as database:
        stored = await database.get(ConnectorConnection, connection.id)
        assert stored is not None
        stored.authorized_capabilities = []
        stored.provider_capabilities = []
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        # The caller's copy still claims the capability; the stored row no longer does.
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=reloaded, now=NOW
            )
        await database.rollback()
    async with search_factory() as database:
        stored = await database.get(ConnectorConnection, connection.id)
        assert stored is not None
        stored.authorized_capabilities = ["communication.messages.read"]
        stored.provider_capabilities = ["communication.messages.read"]
        await database.commit()
    async with search_factory() as database:
        owner = await database.get(User, connection.user_id)
        assert owner is not None
        owner.agent_paused = True
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=reloaded, now=NOW
            )
        await database.rollback()
    async with search_factory() as database:
        assert list(await database.scalars(select(KnowledgeResource))) == []


@pytest.mark.asyncio
async def test_indexing_uses_the_stored_row_and_requires_current_owner_membership(
    search_factory: async_sessionmaker[AsyncSession],
) -> None:
    connection, resource = await message_resource(search_factory)
    stale = ConnectorResource(
        id=resource.id,
        workspace_id=resource.workspace_id,
        connector_connection_id=resource.connector_connection_id,
        provider=resource.provider,
        resource_type=resource.resource_type,
        external_id=resource.external_id,
        canonical={"subject": "Caller supplied", "content": "Caller supplied"},
        provider_metadata={},
        retrieved_at=resource.retrieved_at,
        content_hash="z" * 64,
        version="caller-supplied",
    )
    async with search_factory() as database:
        await index_connector_resource(database, connection=connection, resource=stale, now=NOW)
        await database.commit()
    async with search_factory() as database:
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        assert knowledge.source_version != "caller-supplied"
        assert knowledge.source_version == resource.version
    async with search_factory() as database:
        membership = await database.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == connection.workspace_id,
                WorkspaceMembership.user_id == connection.user_id,
            )
        )
        assert membership is not None
        await database.delete(membership)
        await database.commit()
    async with search_factory() as database:
        reloaded = await database.get(ConnectorResource, resource.id)
        assert reloaded is not None
        with pytest.raises(ValueError):
            await index_connector_resource(
                database, connection=connection, resource=reloaded, now=NOW
            )
        await database.rollback()
