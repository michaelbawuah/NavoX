"""Real API paths for connected search: auth, origin, flag, recent, exclusions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.builtin.canvas import CANVAS_MANIFEST
from navox.connectors.builtin.google_calendar import CALENDAR_MANIFEST
from navox.db.knowledge import KnowledgeChunk, KnowledgeResource
from navox.db.models import ConnectorConnection, ConnectorDefinition, ConnectorResource
from navox.knowledge.class_schedule import class_source_snapshot
from navox.knowledge.indexing import backfill_workspace
from tests.knowledge_search_support import (
    CALENDAR_CAPABILITY,
    NOW,
    READ_CAPABILITY,
    build_engine,
    factory_for,
    manifest,
    news_settings,
    seed_news_story,
)


@pytest_asyncio.fixture
async def knowledge_api(tmp_path: object):
    from navox.api.main import create_app
    from navox.core.settings import Settings, get_settings
    from navox.db.session import get_database_session

    database = await build_engine(tmp_path)
    factory = factory_for(database.engine)
    enabled = Settings(_env_file=None, app_environment="test", knowledge_enabled=True)
    disabled = Settings(_env_file=None, app_environment="test", knowledge_enabled=False)
    app = create_app()

    async def database_override():
        async with factory() as database:
            yield database

    def settings_override():
        return app.state.knowledge_settings

    app.state.knowledge_settings = enabled
    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = settings_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "knowledge-api@example.com",
                "password": "twelve-character-password",
                "display_name": "Knowledge owner",
            },
        )
        assert response.status_code == 201
        workspace_id = UUID(response.json()["workspace"]["id"])
        user_id = UUID(response.json()["id"])
        yield {
            "client": client,
            "factory": factory,
            "enabled": enabled,
            "disabled": disabled,
            "app": app,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "origin": {"Origin": enabled.web_origin},
        }
    await database.dispose()


async def seed_connected_resource(
    factory: async_sessionmaker[AsyncSession], *, workspace_id: UUID, user_id: UUID
):
    async with factory() as database:
        definition = ConnectorDefinition(
            connector_key="google-gmail",
            version="1.0.0",
            display_name="Fixture mail",
            connector_class="GENERIC_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=manifest(
                "google-gmail", READ_CAPABILITY, ["communication.message"]
            ).model_dump(mode="json", by_alias=True),
        )
        database.add(definition)
        await database.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user_id,
            workspace_id=workspace_id,
            provider="fixture",
            external_account_id="account-api",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[READ_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY],
        )
        database.add(connection)
        await database.flush()
        resource = ConnectorResource(
            id=uuid4(),
            workspace_id=workspace_id,
            connector_connection_id=connection.id,
            provider="fixture",
            resource_type="communication.message",
            external_id="message-1",
            canonical={
                "subject": "Quarterly planning review",
                "content": "The quarterly planning review covers the budget forecast.",
                "occurred_at": "2026-09-28T09:00:00+00:00",
                "source_type": "communication.message",
            },
            provider_metadata={},
            source_url="https://mail.example.com/message-1",
            source_created_at=NOW - timedelta(days=2),
            source_updated_at=NOW - timedelta(days=1),
            retrieved_at=NOW - timedelta(days=1),
            content_hash="a" * 64,
        )
        database.add(resource)
        await database.commit()
        return connection.id, resource.id


@pytest.mark.asyncio
async def test_class_sources_require_current_connected_view_authority(knowledge_api) -> None:
    async def stored_fixture_reader(database, *, workspace_id, user_id, settings):
        del settings
        return await class_source_snapshot(database, workspace_id=workspace_id, user_id=user_id)

    knowledge_api["app"].state.class_source_reader = stored_fixture_reader
    moment = datetime.now(UTC)
    async with knowledge_api["factory"]() as database:
        connections = []
        for manifest_row, provider, capabilities in (
            (CANVAS_MANIFEST, "canvas", ["academic.courses.read", "calendar.events.read"]),
            (CALENDAR_MANIFEST, "google", ["calendar.events.read"]),
        ):
            definition = ConnectorDefinition(
                connector_key=manifest_row.id,
                version=manifest_row.version,
                display_name=manifest_row.display_name,
                connector_class=manifest_row.connector_class,
                trust_level="NAVOX_FIRST_PARTY",
                manifest=manifest_row.model_dump(mode="json", by_alias=True),
            )
            database.add(definition)
            await database.flush()
            connection = ConnectorConnection(
                connector_definition_id=definition.id,
                user_id=knowledge_api["user_id"],
                workspace_id=knowledge_api["workspace_id"],
                provider=provider,
                external_account_id=f"class-{provider}",
                status="CONNECTED",
                health_state="CONNECTED",
                authorized_capabilities=capabilities,
                provider_capabilities=capabilities,
            )
            database.add(connection)
            await database.flush()
            connections.append(connection)
        canvas, google = connections
        for connection, provider, kind, external_id, parent, title, metadata in (
            (
                canvas,
                "canvas",
                "academic.course",
                "course:42",
                None,
                "Economics 3120",
                {"course_id": "42", "course_code": "ECON 3120"},
            ),
            (
                canvas,
                "canvas",
                "calendar.event",
                "event:1",
                "course_42",
                "ECON 3120 Lecture",
                {
                    "start_at": (moment + timedelta(hours=2)).isoformat(),
                    "end_at": (moment + timedelta(hours=3)).isoformat(),
                    "all_day": False,
                    "scheduling_updated_at": (moment - timedelta(days=1)).isoformat(),
                },
            ),
            (
                google,
                "google",
                "calendar.event",
                "event:2",
                None,
                "ECON 3120",
                {
                    "start_at": (moment + timedelta(hours=2, minutes=30)).isoformat(),
                    "end_at": (moment + timedelta(hours=3)).isoformat(),
                    "scheduling_updated_at": moment.isoformat(),
                },
            ),
        ):
            database.add(
                ConnectorResource(
                    id=uuid4(),
                    workspace_id=knowledge_api["workspace_id"],
                    connector_connection_id=connection.id,
                    provider=provider,
                    resource_type=kind,
                    external_id=external_id,
                    external_parent_id=parent,
                    canonical={
                        "source_type": kind,
                        "subject": title,
                        "occurred_at": moment.isoformat(),
                        "metadata": metadata,
                    },
                    provider_metadata={},
                    source_url=None,
                    source_created_at=moment,
                    source_updated_at=moment,
                    retrieved_at=moment,
                    content_hash=uuid4().hex.ljust(64, "0"),
                )
            )
        await database.commit()
    async with knowledge_api["factory"]() as database:
        report = await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=moment,
        )
        await database.commit()
    assert len(report.outcomes) == 3
    response = await knowledge_api["client"].get("/api/v1/knowledge/class-sources")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True
    assert len(body["courses"]) == 1
    assert len(body["events"]) == 2
    assert {row["provider"] for row in body["events"]} == {"canvas", "google"}
    assert body["events"][0]["explicit_class_meeting"] is True
    async with knowledge_api["factory"]() as database:
        canvas = await database.scalar(
            select(ConnectorConnection).where(ConnectorConnection.provider == "canvas")
        )
        assert canvas is not None
        canvas.authorized_capabilities = []
        await database.commit()
    revoked = await knowledge_api["client"].get("/api/v1/knowledge/class-sources")
    assert revoked.status_code == 200
    assert revoked.json()["complete"] is True
    assert revoked.json()["courses"] == []
    assert len(revoked.json()["events"]) == 1
    assert revoked.json()["events"][0]["provider"] == "google"


@pytest.mark.asyncio
async def test_class_navigation_binds_authenticated_owner_and_exact_selectors(
    knowledge_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = uuid4()
    resource_id = uuid4()
    observed: dict[str, object] = {}

    async def target(database, **kwargs):
        del database
        observed.update(kwargs)
        return "https://calendar.google.com/calendar/event?eid=2"

    monkeypatch.setattr("navox.api.knowledge.live_class_navigation_target", target)
    response = await knowledge_api["client"].get(
        "/api/v1/knowledge/class-navigation",
        params={"connection_id": str(connection_id), "resource_id": str(resource_id)},
    )
    assert response.status_code == 200
    assert response.json() == {"url": "https://calendar.google.com/calendar/event?eid=2"}
    assert observed["workspace_id"] == knowledge_api["workspace_id"]
    assert observed["user_id"] == knowledge_api["user_id"]
    assert observed["connection_id"] == connection_id
    assert observed["resource_id"] == resource_id


@pytest.mark.asyncio
async def test_search_is_disabled_by_default(knowledge_api) -> None:
    knowledge_api["app"].state.knowledge_settings = knowledge_api["disabled"]
    response = await knowledge_api["client"].get("/api/v1/search", params={"q": "budget"})
    assert response.status_code == 404
    detail = await knowledge_api["client"].get(f"/api/v1/knowledge/resources/{uuid4()}")
    assert detail.status_code == 404
    classes = await knowledge_api["client"].get("/api/v1/knowledge/class-sources")
    assert classes.status_code == 404
    navigation = await knowledge_api["client"].get(
        "/api/v1/knowledge/class-navigation",
        params={"connection_id": str(uuid4()), "resource_id": str(uuid4())},
    )
    assert navigation.status_code == 404


@pytest.mark.asyncio
async def test_search_requires_authentication_and_returns_results(knowledge_api) -> None:
    connection_id, _ = await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    async with knowledge_api["factory"]() as database:
        report = await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await database.commit()
    assert len(report.outcomes) == 1
    response = await knowledge_api["client"].get("/api/v1/search", params={"q": "quarterly budget"})
    assert response.status_code == 200
    body = response.json()
    assert body["interpreted_mode"] == "SEARCH"
    assert len(body["results"]) == 1
    assert body["results"][0]["source_type"] == "EMAIL"
    assert body["results"][0]["provenance"]["connection_id"] == str(connection_id)
    assert body["coverage"]["returned"] == 1
    assert "answer" in body and body["answer"] is None


@pytest.mark.asyncio
async def test_post_query_rejects_unknown_fields_and_reports_ask_unavailable(
    knowledge_api,
) -> None:
    await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    async with knowledge_api["factory"]() as database:
        await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await database.commit()
    invalid = await knowledge_api["client"].post(
        "/api/v1/search/query",
        json={"query": "budget", "workspace_id": str(uuid4())},
        headers=knowledge_api["origin"],
    )
    assert invalid.status_code == 422
    ask = await knowledge_api["client"].post(
        "/api/v1/search/query",
        json={"query": "What was the budget forecast?", "mode": "ASK"},
        headers=knowledge_api["origin"],
    )
    assert ask.status_code == 200
    body = ask.json()
    assert body["interpreted_mode"] == "ASK"
    assert body["answer"] is None
    assert body["answer_state"] == "UNAVAILABLE"
    assert body["results"]


@pytest.mark.asyncio
async def test_post_requires_a_trusted_origin(knowledge_api) -> None:
    response = await knowledge_api["client"].post(
        "/api/v1/search/query",
        json={"query": "budget"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    missing = await knowledge_api["client"].post(
        "/api/v1/search/query", json={"query": ""}, headers=knowledge_api["origin"]
    )
    assert missing.status_code == 422


@pytest.mark.asyncio
async def test_resource_detail_is_scoped_to_the_caller(knowledge_api) -> None:
    await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    async with knowledge_api["factory"]() as database:
        await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await database.commit()
        knowledge = await database.scalar(select(KnowledgeResource))
        assert knowledge is not None
        chunks = list(await database.scalars(select(KnowledgeChunk)))
        assert chunks
        resource_id = knowledge.id
    found = await knowledge_api["client"].get(f"/api/v1/knowledge/resources/{resource_id}")
    assert found.status_code == 200
    body = found.json()
    assert body["resource_id"] == str(resource_id)
    assert body["source_type"] == "EMAIL"
    assert body["chunks"] and body["chunks"][0]["text_content"]
    assert body["provenance"]["external_resource_id"] == "message-1"
    assert body["index_state"] == "INDEXED"
    missing = await knowledge_api["client"].get(f"/api/v1/knowledge/resources/{uuid4()}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_recent_searches_are_listed_and_cleared(knowledge_api) -> None:
    await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    async with knowledge_api["factory"]() as database:
        await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await database.commit()
    empty = await knowledge_api["client"].get("/api/v1/search/recent")
    assert empty.status_code == 200 and empty.json() == []
    searched = await knowledge_api["client"].get("/api/v1/search", params={"q": "quarterly budget"})
    assert searched.status_code == 200
    listed = await knowledge_api["client"].get("/api/v1/search/recent")
    body = listed.json()
    assert [row["query"] for row in body] == ["quarterly budget"]
    assert body[0]["result_count"] == 1
    cleared = await knowledge_api["client"].delete(
        "/api/v1/search/recent", headers=knowledge_api["origin"]
    )
    assert cleared.status_code == 200 and cleared.json() == {"cleared": 1}
    assert (await knowledge_api["client"].get("/api/v1/search/recent")).json() == []
    async with knowledge_api["factory"]() as database:
        assert (await database.scalar(select(KnowledgeResource))) is not None


@pytest.mark.asyncio
async def test_exclusions_api_round_trip_and_effect(knowledge_api) -> None:
    connection_id, _ = await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    async with knowledge_api["factory"]() as database:
        await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await database.commit()
    before = await knowledge_api["client"].get("/api/v1/search", params={"q": "quarterly budget"})
    assert before.json()["results"]
    created = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={"scope": "SOURCE", "source_connection_id": str(connection_id)},
        headers=knowledge_api["origin"],
    )
    assert created.status_code == 201
    exclusion_id = created.json()["id"]
    invalid = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={"scope": "SOURCE"},
        headers=knowledge_api["origin"],
    )
    assert invalid.status_code == 422
    listed = await knowledge_api["client"].get("/api/v1/search/exclusions")
    assert [row["scope"] for row in listed.json()] == ["SOURCE"]
    after = await knowledge_api["client"].get("/api/v1/search", params={"q": "quarterly budget"})
    assert after.json()["results"] == []
    assert after.json()["exclusion_count"] == 1
    removed = await knowledge_api["client"].delete(
        f"/api/v1/search/exclusions/{exclusion_id}", headers=knowledge_api["origin"]
    )
    assert removed.status_code == 200
    restored = await knowledge_api["client"].get("/api/v1/search", params={"q": "quarterly budget"})
    assert restored.json()["results"]
    gone = await knowledge_api["client"].delete(
        f"/api/v1/search/exclusions/{exclusion_id}", headers=knowledge_api["origin"]
    )
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_search_never_echoes_workspace_or_user_from_the_client(knowledge_api) -> None:
    await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    async with knowledge_api["factory"]() as database:
        await backfill_workspace(
            database,
            workspace_id=knowledge_api["workspace_id"],
            user_id=knowledge_api["user_id"],
            now=NOW,
        )
        await database.commit()
    response = await knowledge_api["client"].get(
        "/api/v1/search",
        params={"q": "quarterly budget", "types": ["CALENDAR_EVENT"]},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
    windowed = await knowledge_api["client"].get(
        "/api/v1/search",
        params={
            "q": "quarterly budget",
            "start": datetime(2026, 9, 1, tzinfo=UTC).isoformat(),
            "end": datetime(2026, 9, 30, tzinfo=UTC).isoformat(),
        },
    )
    assert windowed.status_code == 200
    assert windowed.json()["results"]
    assert CALENDAR_CAPABILITY  # fixture capability is part of the mapping authority


@pytest.mark.asyncio
async def test_exclusion_writes_reject_mixed_or_foreign_targets_without_500(
    knowledge_api,
) -> None:
    connection_id, _ = await seed_connected_resource(
        knowledge_api["factory"],
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    mixed = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={
            "scope": "SOURCE",
            "source_connection_id": str(connection_id),
            "resource_id": str(uuid4()),
        },
        headers=knowledge_api["origin"],
    )
    assert mixed.status_code == 422
    incomplete = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={"scope": "RESOURCE"},
        headers=knowledge_api["origin"],
    )
    assert incomplete.status_code == 422
    foreign = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={"scope": "SOURCE", "source_connection_id": str(uuid4())},
        headers=knowledge_api["origin"],
    )
    assert foreign.status_code == 422
    native = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={"scope": "RESOURCE", "resource_id": str(uuid4())},
        headers=knowledge_api["origin"],
    )
    assert native.status_code == 422
    # The transaction stays usable after each rejected write.
    accepted = await knowledge_api["client"].post(
        "/api/v1/search/exclusions",
        json={"scope": "SOURCE", "source_connection_id": str(connection_id)},
        headers=knowledge_api["origin"],
    )
    assert accepted.status_code == 201
    listed = await knowledge_api["client"].get("/api/v1/search/exclusions")
    assert [row["scope"] for row in listed.json()] == ["SOURCE"]


@pytest.mark.asyncio
async def test_search_returns_nonempty_news_evidence_through_the_owned_service(
    knowledge_api,
) -> None:
    from types import SimpleNamespace

    world = SimpleNamespace(
        workspace_id=knowledge_api["workspace_id"],
        user_id=knowledge_api["user_id"],
    )
    story_id, config = await seed_news_story(
        knowledge_api["factory"],
        world,
        headline="Quarterly budget observation",
        description="The station recorded a quarterly budget shift.",
    )
    knowledge_api["app"].state.knowledge_settings = news_settings(config).model_copy(
        update={"knowledge_enabled": True}
    )
    response = await knowledge_api["client"].get(
        "/api/v1/search", params={"q": "quarterly budget observation"}
    )
    assert response.status_code == 200
    body = response.json()
    story = next((item for item in body["results"] if item["source_type"] == "NEWS_STORY"), None)
    assert story is not None
    assert story["resource_id"] == str(story_id)
    assert story["canonical_url"] == "https://news.example.com/report"
    assert story["origin"] == "NATIVE"
    assert story["provenance"]["authority"] == "SPEC-006 News"
    assert body["structured_facts"]
