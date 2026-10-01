"""Live search reuses owned connector dispatch and never promotes cached text."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from navox.api import knowledge_lifecycle
from navox.db.knowledge import KnowledgeResource, KnowledgeResourcePermission
from navox.db.models import ConnectorConnection, ConnectorResource
from navox.knowledge.indexing import backfill_workspace
from navox.knowledge.planner import interpret
from navox.knowledge.search_contracts import Freshness, SearchRequest
from tests.knowledge_search_support import NOW
from tests.test_knowledge_api import knowledge_api as knowledge_api
from tests.test_knowledge_api import seed_connected_resource


async def seed(fixture, *, sources=1):
    connection_id, source_id = await seed_connected_resource(
        fixture["factory"], workspace_id=fixture["workspace_id"], user_id=fixture["user_id"]
    )
    async with fixture["factory"]() as database:
        source = await database.get(ConnectorResource, source_id)
        source.canonical = dict(source.canonical, subject="Latest planning budget")
        source.content_hash = "b" * 64
        connection = await database.get(ConnectorConnection, connection_id)
        for index in range(1, sources):
            other = ConnectorConnection(
                id=uuid4(),
                connector_definition_id=connection.connector_definition_id,
                workspace_id=fixture["workspace_id"],
                user_id=fixture["user_id"],
                provider="fixture",
                external_account_id=f"live-fixture-{index}",
                status="CONNECTED",
                health_state="CONNECTED",
                authorized_capabilities=connection.authorized_capabilities,
                provider_capabilities=connection.provider_capabilities,
            )
            database.add(other)
            await database.flush()
            database.add(
                ConnectorResource(
                    id=uuid4(),
                    workspace_id=fixture["workspace_id"],
                    connector_connection_id=other.id,
                    provider="fixture",
                    resource_type=source.resource_type,
                    external_id=f"message-{index}",
                    canonical=dict(source.canonical),
                    provider_metadata={},
                    retrieved_at=source.retrieved_at,
                    content_hash="b" * 64,
                    source_created_at=source.source_created_at,
                    source_updated_at=source.source_updated_at,
                )
            )
        await database.flush()
        await backfill_workspace(
            database, workspace_id=fixture["workspace_id"], user_id=fixture["user_id"], now=NOW
        )
        await database.commit()
    return connection_id


async def request(fixture, *, query="latest planning", request_id=None, headers=None):
    return await fixture["client"].post(
        "/api/v1/search/live",
        headers=fixture["origin"] if headers is None else headers,
        json={"request_id": str(request_id or uuid4()), "search": {"query": query}},
    )


@pytest.mark.asyncio
async def test_live_query_queues_owned_source_with_stable_id_and_cached_notice(
    knowledge_api, monkeypatch
):
    connection = await seed(knowledge_api)
    calls = []

    async def queue(target, payload, *args):
        calls.append((target, payload.request_id))
        return {"dispatch_status": "queued"}

    monkeypatch.setattr(knowledge_lifecycle, "sync_connection", queue)
    identifier = uuid4()
    for _ in range(2):
        response = await request(knowledge_api, request_id=identifier)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["refresh"] == {
            "state": "REFRESHING",
            "queued": 1,
            "unavailable": 0,
            "bounded": False,
        }
        assert "SOURCE_STALE" in body["coverage"]["partial_reasons"]
        assert body["results"]
    assert len(calls) == 2 and calls[0] == calls[1] and calls[0][0] == connection


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["cached", "fresh", "revoked", "disabled"])
async def test_live_search_does_not_dispatch_ineligible_or_unneeded_refresh(
    knowledge_api, monkeypatch, condition
):
    await seed(knowledge_api)

    async def no_dispatch(*args):
        pytest.fail("No external dispatch is permitted")

    monkeypatch.setattr(knowledge_lifecycle, "sync_connection", no_dispatch)
    async with knowledge_api["factory"]() as database:
        if condition == "fresh":
            for row in await database.scalars(select(KnowledgeResource)):
                row.fresh_until = datetime.now(UTC) + timedelta(hours=1)
        if condition == "revoked":
            await database.execute(delete(KnowledgeResourcePermission))
        await database.commit()
    if condition == "disabled":
        knowledge_api["app"].state.knowledge_settings = knowledge_api["disabled"]
    response = await request(
        knowledge_api, query="planning" if condition == "cached" else "latest planning"
    )
    assert response.status_code == (404 if condition == "disabled" else 200), response.text
    if response.status_code == 200:
        assert response.json()["refresh"]["state"] == "NOT_NEEDED"


@pytest.mark.asyncio
async def test_live_dispatch_bounds_and_outages_are_honest(knowledge_api, monkeypatch):
    await seed(knowledge_api, sources=4)
    calls = []

    async def queue(target, *args):
        calls.append(target)
        if len(calls) == 2:
            raise HTTPException(429, "Private connector diagnostic must not escape")
        return {"dispatch_status": "queued"}

    monkeypatch.setattr(knowledge_lifecycle, "sync_connection", queue)
    response = await request(knowledge_api)
    assert response.status_code == 200, response.text
    assert len(calls) == 3
    assert response.json()["refresh"] == {
        "state": "PARTIAL",
        "queued": 2,
        "unavailable": 1,
        "bounded": True,
    }
    assert "Private connector" not in response.text


@pytest.mark.asyncio
async def test_live_final_retrieval_drops_revocation_during_dispatch(knowledge_api, monkeypatch):
    await seed(knowledge_api)

    async def queue(target, payload, request, account, database, settings):
        await database.execute(delete(KnowledgeResourcePermission))
        await database.commit()
        return {"dispatch_status": "queued"}

    monkeypatch.setattr(knowledge_lifecycle, "sync_connection", queue)
    response = await request(knowledge_api)
    assert response.status_code == 200, response.text
    assert response.json()["results"] == []


@pytest.mark.asyncio
async def test_live_search_origin_and_request_id_required(knowledge_api):
    response = await request(knowledge_api, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    response = await knowledge_api["client"].post(
        "/api/v1/search/live", headers=knowledge_api["origin"], json={"search": {"query": "latest"}}
    )
    assert response.status_code == 422


def test_freshness_cues_are_words_not_substrings():
    assert (
        interpret(SearchRequest(query="snow known lives currentness")).freshness is Freshness.CACHED
    )
    assert interpret(SearchRequest(query="latest planning")).freshness is Freshness.FRESH


@pytest.mark.asyncio
async def test_progressive_reread_does_not_recreate_cleared_search_history(knowledge_api):
    await seed(knowledge_api)
    result = await knowledge_api["client"].post(
        "/api/v1/search/query?remember=false",
        headers=knowledge_api["origin"],
        json={"query": "planning"},
    )
    assert result.status_code == 200 and result.json()["results"]
    history = await knowledge_api["client"].get("/api/v1/search/recent")
    assert history.status_code == 200 and history.json() == []
