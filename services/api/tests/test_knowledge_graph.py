"""Structural graph authority and invalidation regressions with authored data."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select

from navox.db.knowledge import (
    KnowledgeEntity,
    KnowledgeExclusion,
    KnowledgeResource,
    KnowledgeResourcePermission,
)
from navox.db.models import ConnectorConnection, ConnectorResource
from navox.knowledge import graph
from navox.knowledge.indexing import backfill_workspace
from navox.knowledge.service import KnowledgeUnavailable
from tests.knowledge_search_support import NOW
from tests.test_knowledge_api import knowledge_api as knowledge_api
from tests.test_knowledge_api import seed_connected_resource


async def seed_graph(fixture):
    factory = fixture["factory"]
    workspace_id, user_id = fixture["workspace_id"], fixture["user_id"]
    connection_id, child_id = await seed_connected_resource(
        factory, workspace_id=workspace_id, user_id=user_id
    )
    async with factory() as database:
        child = await database.get(ConnectorResource, child_id)
        child.external_parent_id = "parent-exact-id"
        parent = ConnectorResource(
            id=uuid4(),
            workspace_id=workspace_id,
            connector_connection_id=connection_id,
            provider="fixture",
            resource_type="communication.message",
            external_id="parent-exact-id",
            canonical={
                "subject": "Same title",
                "content": "Authored parent body.",
                "occurred_at": NOW.isoformat(),
                "source_type": "communication.message",
            },
            provider_metadata={},
            source_url="https://mail.example.com/parent",
            source_created_at=NOW,
            source_updated_at=NOW,
            retrieved_at=NOW,
            content_hash="b" * 64,
        )
        database.add(parent)
        await database.flush()
        await backfill_workspace(database, workspace_id=workspace_id, user_id=user_id, now=NOW)
        child_resource = await database.scalar(
            select(KnowledgeResource.id).where(KnowledgeResource.source_resource_id == child_id)
        )
        parent_resource = await database.scalar(
            select(KnowledgeResource.id).where(KnowledgeResource.source_resource_id == parent.id)
        )
        entity_id = await graph.sync_resource_graph(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_id=child_resource,
            now=NOW,
        )
        await database.commit()
        return child_resource, parent_resource, entity_id, child_id, parent.id, connection_id


@pytest.mark.asyncio
async def test_exact_identity_graph_and_read_only_api(knowledge_api):
    child, parent, entity, *_ = await seed_graph(knowledge_api)
    client = knowledge_api["client"]
    for path in (f"resources/{child}/related", f"entities/{entity}/related"):
        response = await client.get(f"/api/v1/knowledge/{path}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["origin"]["resource_id"] == str(child)
        assert [node["resource_id"] for node in body["entities"]] == [str(parent)]
        assert body["relationships"][0]["evidence_resource_id"] == str(child)
        assert body["coverage"] == "BOUNDED_SOURCE_STRUCTURE"
    assert (await client.get(f"/api/v1/knowledge/entities/{entity}")).status_code == 200
    async with knowledge_api["factory"]() as database:
        rows = list(await database.scalars(select(KnowledgeEntity)))
        assert len(rows) == 2
        assert all(row.display_name == "" for row in rows)
    knowledge_api["app"].state.knowledge_settings = knowledge_api["disabled"]
    assert (await client.get(f"/api/v1/knowledge/entities/{entity}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "parent_deleted",
        "parent_hash",
        "parent_permission",
        "parent_excluded",
        "moved",
        "revoked",
        "child_hash",
    ],
)
async def test_graph_invalidates_current_authority(knowledge_api, change):
    child, parent, entity, child_source, parent_source, connection = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        if change == "parent_deleted":
            (await database.get(ConnectorResource, parent_source)).deleted = True
        elif change == "parent_hash":
            (await database.get(ConnectorResource, parent_source)).content_hash = "c" * 64
        elif change == "parent_permission":
            await database.execute(
                delete(KnowledgeResourcePermission).where(
                    KnowledgeResourcePermission.resource_id == parent
                )
            )
        elif change == "parent_excluded":
            database.add(
                KnowledgeExclusion(
                    workspace_id=knowledge_api["workspace_id"],
                    user_id=knowledge_api["user_id"],
                    scope="RESOURCE",
                    resource_id=parent,
                )
            )
        elif change == "moved":
            (
                await database.get(ConnectorResource, child_source)
            ).external_parent_id = "another-parent"
        elif change == "revoked":
            (await database.get(ConnectorConnection, connection)).authorized_capabilities = []
        else:
            (await database.get(ConnectorResource, child_source)).content_hash = "d" * 64
        await database.commit()
    response = await knowledge_api["client"].get(f"/api/v1/knowledge/entities/{entity}/related")
    if change in {"moved", "revoked", "child_hash"}:
        assert response.status_code == 404
    else:
        assert response.status_code == 200
        assert response.json()["entities"] == []
        assert response.json()["relationships"] == []
    assert "Authored parent body" not in response.text


@pytest.mark.asyncio
async def test_graph_denied_parent_never_selects_title_or_body(knowledge_api):
    child, parent, entity, *_ = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        await database.execute(
            delete(KnowledgeResourcePermission).where(
                KnowledgeResourcePermission.resource_id == parent
            )
        )
        await database.commit()
    statements = []
    engine = knowledge_api["factory"].kw["bind"]

    def capture(_conn, _cursor, statement, parameters, _context, _many):
        statements.append((statement, str(parameters)))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await knowledge_api["client"].get(f"/api/v1/knowledge/entities/{entity}/related")
        assert response.status_code == 200
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    private = [
        (sql, args)
        for sql, args in statements
        if "knowledge_resources.title" in sql and parent.hex in args.replace("-", "")
    ]
    assert private == []
    assert not any("knowledge_chunks.text_content" in sql for sql, _ in statements)


@pytest.mark.asyncio
async def test_graph_cross_workspace_and_rebuild(knowledge_api):
    child, parent, entity, *_ = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        # An existing member cannot use an unrelated workspace as a graph scope.
        with pytest.raises(KnowledgeUnavailable):
            await graph.get_entity(
                database,
                workspace_id=uuid4(),
                user_id=knowledge_api["user_id"],
                entity_id=entity,
                now=NOW,
            )
        again = await graph.sync_resource_graph(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            resource_id=child,
            now=NOW,
        )
        assert again == entity
        await database.commit()
    response = await knowledge_api["client"].get(f"/api/v1/knowledge/entities/{entity}/related")
    assert len(response.json()["relationships"]) == 1


@pytest.mark.asyncio
async def test_graph_final_fence_drops_mid_read_change(knowledge_api, monkeypatch):
    child, parent, entity, _, parent_source, _ = await seed_graph(knowledge_api)
    original = graph._view

    async def changing(database, workspace_id, user_id, node, now):
        result = await original(database, workspace_id, user_id, node, now)
        if node.source_resource_id == parent:
            (await database.get(ConnectorResource, parent_source)).content_hash = "e" * 64
            await database.flush()
        return result

    monkeypatch.setattr(graph, "_view", changing)
    response = await knowledge_api["client"].get(f"/api/v1/knowledge/entities/{entity}/related")
    assert response.status_code == 200
    assert response.json()["entities"] == []
    assert response.json()["relationships"] == []


@pytest.mark.asyncio
async def test_matching_names_do_not_create_edges(knowledge_api):
    child, parent, entity, child_source, parent_source, _ = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        source = await database.get(ConnectorResource, child_source)
        source.external_parent_id = "missing-exact-parent"
        source.canonical = dict(source.canonical, subject="Same title")
        source.content_hash = "f" * 64
        await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await graph.sync_resource_graph(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            resource_id=child,
            now=NOW,
        )
        await database.commit()
    response = await knowledge_api["client"].get(f"/api/v1/knowledge/entities/{entity}/related")
    assert response.status_code == 200
    assert response.json()["origin"]["title"] == "Same title"
    assert response.json()["entities"] == []


@pytest.mark.asyncio
async def test_expired_edges_and_anonymous_reads(knowledge_api):
    from datetime import timedelta

    from httpx import ASGITransport, AsyncClient

    from navox.db.knowledge import KnowledgeRelationship

    child, parent, entity, *_ = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        edge = await database.scalar(select(KnowledgeRelationship))
        edge.valid_until = NOW - timedelta(seconds=1)
        await database.commit()
    response = await knowledge_api["client"].get(f"/api/v1/knowledge/entities/{entity}/related")
    assert response.status_code == 200
    assert response.json()["relationships"] == []
    async with AsyncClient(
        transport=ASGITransport(app=knowledge_api["app"]), base_url="http://testserver"
    ) as client:
        assert (await client.get(f"/api/v1/knowledge/entities/{entity}")).status_code == 401


@pytest.mark.asyncio
async def test_search_expands_exact_parent_and_bundle_keeps_cited_link(knowledge_api):
    from navox.knowledge.search_contracts import SearchRequest
    from navox.knowledge.service import evidence_bundle, search_knowledge

    child, parent, entity, *_ = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        response = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="related quarterly"),
            now=NOW,
        )
        assert {result.resource_id for result in response.results} == {child, parent}
        assert len(response.relationships) == 1
        assert response.relationships[0].evidence_keys == (f"EMAIL:{child}",)
        bundle = evidence_bundle(
            response=response,
            query="related quarterly",
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
        )
        assert bundle.relationships == response.relationships


@pytest.mark.asyncio
async def test_graph_expansion_rechecks_edge_when_parent_moves_mid_search(
    knowledge_api, monkeypatch
):
    from navox.knowledge import graph_retrieval
    from navox.knowledge.search_contracts import SearchRequest
    from navox.knowledge.service import search_knowledge

    child, parent, entity, source, *_ = await seed_graph(knowledge_api)
    original = graph_retrieval.publish_graph

    async def moved(database, **kwargs):
        (await database.get(ConnectorResource, source)).external_parent_id = "changed-parent"
        await database.flush()
        return await original(database, **kwargs)

    monkeypatch.setattr(graph_retrieval, "publish_graph", moved)
    async with knowledge_api["factory"]() as database:
        response = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(query="related quarterly"),
            now=NOW,
        )
        assert all(result.resource_id != parent for result in response.results)
        assert response.relationships == ()


@pytest.mark.asyncio
async def test_graph_expansion_respects_global_type_and_date_filters(knowledge_api):
    from datetime import timedelta

    from navox.knowledge.search_contracts import DateRange, SearchRequest
    from navox.knowledge.service import search_knowledge

    child, parent, *_ = await seed_graph(knowledge_api)
    async with knowledge_api["factory"]() as database:
        response = await search_knowledge(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            request=SearchRequest(
                query="related quarterly",
                date_range=DateRange(start=NOW - timedelta(days=3), end=NOW - timedelta(hours=1)),
            ),
            now=NOW,
        )
        assert {result.resource_id for result in response.results} == {child}
        assert response.relationships == ()
