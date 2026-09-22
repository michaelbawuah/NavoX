from collections.abc import AsyncIterator
from dataclasses import asdict
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from navox.api.main import app
from navox.api.workspace import city_weather
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import AuditEvent, Connection, IncomingEvent, User, WorkspaceMembership
from navox.db.session import get_database_session
from navox.intelligence import activities
from navox.intelligence.jobs import SourceWork


@pytest_asyncio.fixture
async def runtime_env() -> AsyncIterator[
    tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = sessions
    app.dependency_overrides[get_settings] = lambda: Settings()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://testserver"
    ) as client:
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Owner",
            },
        )
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_display_preferences_are_validated_persisted_and_private(runtime_env):
    client, _ = runtime_env
    before = (await client.get("/api/v1/workspace/preferences")).json()
    assert before["weather_visible"] is False
    payload = dict(
        before,
        timezone="America/New_York",
        clock_format="24h",
        temperature_unit="fahrenheit",
        weather_visible=True,
        weather_city="Ithaca",
    )
    assert (await client.post("/api/v1/workspace/preferences", json=payload)).json() == payload
    assert (await client.get("/api/v1/workspace/preferences")).json() == payload
    assert (
        await client.post("/api/v1/workspace/preferences", json=dict(payload, timezone="fake/time"))
    ).status_code == 422
    assert (
        await client.post(
            "/api/v1/workspace/preferences", json=dict(payload, grant_permission=True)
        )
    ).status_code == 422
    disabled = (
        await client.post(
            "/api/v1/workspace/preferences", json=dict(payload, weather_visible=False)
        )
    ).json()
    assert disabled["weather_city"] is None
    assert (await client.get("/api/v1/workspace/weather")).json()["status"] == "disabled"
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert (await client.get("/api/v1/workspace/preferences")).json() == before


@pytest.mark.asyncio
async def test_manual_sync_is_tenant_and_scope_gated(runtime_env):
    client, factory = runtime_env
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        membership = await db.scalar(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        )
        connection = Connection(
            user_id=user.id,
            workspace_id=membership.workspace_id,
            provider="google",
            external_account_id="owner-google",
            granted_scopes=["openid"],
        )
        db.add(connection)
        await db.commit()
        connection_id = connection.id
    payload = {"connection_id": str(connection_id), "source": "gmail"}
    assert (await client.post("/api/v1/intelligence/sync", json=payload)).status_code == 403
    assert (
        await client.post(
            "/api/v1/intelligence/sync", json=dict(payload, connection_id=str(uuid4()))
        )
    ).status_code == 404
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert (await client.post("/api/v1/intelligence/sync", json=payload)).status_code == 404


@pytest.mark.asyncio
async def test_city_weather_uses_only_fixed_hosts_and_requested_units():
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200, json={"results": [{"name": "Ithaca", "latitude": 42.44, "longitude": -76.5}]}
            )
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["temperature_unit"] == "fahrenheit"
        return httpx.Response(
            200,
            json={
                "current": {"time": "2026-09-22T12:00", "temperature_2m": 72.4, "weather_code": 2}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await city_weather("Ithaca", "fahrenheit", client)
    assert len(paths) == 2
    assert result.status == "ready"
    assert result.temperature == 72.4
    assert result.observed_at.endswith("Z")


@pytest.mark.asyncio
async def test_processing_failure_keeps_outbox_and_does_not_leak_source(runtime_env, monkeypatch):
    import navox.intelligence.ingestion as ingestion

    client, factory = runtime_env
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        member = await db.scalar(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        )
        connection = Connection(
            user_id=user.id,
            workspace_id=member.workspace_id,
            provider="google",
            external_account_id="g",
            granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        db.add(connection)
        await db.flush()
        event = IncomingEvent(
            connection_id=connection.id,
            user_id=user.id,
            workspace_id=member.workspace_id,
            provider="google",
            source="gmail",
            event_type="change",
            external_event_id="e1",
            payload_hash="a" * 64,
        )
        db.add(event)
        await db.commit()
        payload = SourceWork(
            str(connection.id), str(user.id), str(member.workspace_id), "gmail", str(event.id)
        )

    async def fail(*args, **kwargs):
        raise ValueError("secret source body and credential must not escape")

    monkeypatch.setattr(ingestion, "process_connection", fail)
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        activities,
        "get_settings",
        lambda: Settings(ai_provider="openai", openai_api_key="test-placeholder"),
    )
    with pytest.raises(ApplicationError) as error:
        await activities.process_source_activity(payload)
    assert "secret source" not in str(error.value)
    async with factory() as db:
        event = await db.get(IncomingEvent, UUID(payload.event_id))
        assert event.intelligence_status == "failed"
        audits = list(
            await db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "intelligence.source.failed")
            )
        )
        assert len(audits) == 1
        assert "secret source" not in str(audits[0].event_metadata)
        assert audits[0].event_metadata["error_type"] == "ValueError"
    assert set(asdict(payload)) == {
        "connection_id",
        "user_id",
        "workspace_id",
        "source",
        "event_id",
    }
