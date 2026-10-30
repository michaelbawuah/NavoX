"""First-run account observers use separate SQLite connections, like the browser."""

import asyncio
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import WorkspaceDisplayPreference
from navox.db.session import get_database_session


@asynccontextmanager
async def environment(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    barrier = asyncio.Barrier(2)
    enabled = False

    class ConcurrentSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            value = await super().scalar(statement, *args, **kwargs)
            if (
                enabled
                and isinstance(statement, Select)
                and statement._for_update_arg is not None
                and [str(table) for table in statement.get_final_froms()] == ["users"]
            ):
                # Both handlers have observed the same initial user. SQLite's
                # ignored FOR UPDATE must not be the only creation fence.
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return value

    factory = async_sessionmaker(engine, class_=ConcurrentSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app = create_app()

    async def database():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, app_environment="test"
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            registered = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "preferences-concurrency@example.com",
                    "password": "twelve-character-password",
                    "display_name": "Concurrent owner",
                },
            )
            assert registered.status_code == 201
            enabled = True
            yield client, factory, registered.json()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["UTC", "America/New_York"])
async def test_concurrent_initialization_preserves_exactly_one_device_choice(tmp_path, first):
    async with environment(tmp_path / "init.sqlite3") as (client, factory, account):
        payload = {"user_id": account["id"], "workspace_id": account["workspace"]["id"]}
        responses = await asyncio.gather(
            client.post(
                "/api/v1/workspace/timezone/initialize", json={**payload, "timezone": first}
            ),
            client.post(
                "/api/v1/workspace/timezone/initialize",
                json={**payload, "timezone": "America/Los_Angeles"},
            ),
        )
        assert [response.status_code for response in responses] == [200, 200]
        preferences = (await client.get("/api/v1/workspace/preferences")).json()
        assert preferences["timezone"] in {first, "America/Los_Angeles"}
        assert preferences["timezone_selected"] is True
        assert {response.json()["timezone"] for response in responses} == {preferences["timezone"]}
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(WorkspaceDisplayPreference))
            ) == 1


@pytest.mark.asyncio
async def test_explicit_preferences_win_against_first_device_initialization(tmp_path):
    async with environment(tmp_path / "explicit.sqlite3") as (client, factory, account):
        explicit = {
            "timezone": "Asia/Tokyo",
            "clock_format": "24h",
            "temperature_unit": "fahrenheit",
            "weather_visible": True,
            "weather_city": "Ithaca",
        }
        responses = await asyncio.gather(
            client.post(
                "/api/v1/workspace/timezone/initialize",
                json={
                    "timezone": "America/New_York",
                    "user_id": account["id"],
                    "workspace_id": account["workspace"]["id"],
                },
            ),
            client.post("/api/v1/workspace/preferences", json=explicit),
        )
        assert [response.status_code for response in responses] == [200, 200]
        assert (await client.get("/api/v1/workspace/preferences")).json() == {
            **explicit,
            "timezone_selected": True,
        }
        async with factory() as database:
            row = await database.get(
                WorkspaceDisplayPreference,
                (UUID(account["workspace"]["id"]), UUID(account["id"])),
            )
            assert row is not None and row.clock_format == "24h"


@pytest.mark.asyncio
async def test_concurrent_explicit_first_saves_never_mix_preferences(tmp_path):
    async with environment(tmp_path / "saves.sqlite3") as (client, _, _account):
        choices = [
            {
                "timezone": "America/New_York",
                "clock_format": "24h",
                "temperature_unit": "fahrenheit",
                "weather_visible": True,
                "weather_city": "Ithaca",
            },
            {
                "timezone": "Europe/Paris",
                "clock_format": "12h",
                "temperature_unit": "celsius",
                "weather_visible": False,
                "weather_city": None,
            },
        ]
        responses = await asyncio.gather(
            *(client.post("/api/v1/workspace/preferences", json=choice) for choice in choices)
        )
        assert [response.status_code for response in responses] == [200, 200]
        saved = (await client.get("/api/v1/workspace/preferences")).json()
        assert saved in [{**choice, "timezone_selected": True} for choice in choices]


@pytest.mark.asyncio
async def test_explicit_utc_profile_wins_against_first_device_initialization(tmp_path):
    async with environment(tmp_path / "profile.sqlite3") as (client, _, account):
        responses = await asyncio.gather(
            client.post(
                "/api/v1/workspace/timezone/initialize",
                json={
                    "timezone": "America/New_York",
                    "user_id": account["id"],
                    "workspace_id": account["workspace"]["id"],
                },
            ),
            client.put("/api/v1/student/profile", json={"timezone": "UTC", "revision": 0}),
        )
        assert [response.status_code for response in responses] == [200, 200]
        assert responses[1].json()["timezone"] == "UTC"
        assert (await client.get("/api/v1/student/profile")).json()["timezone"] == "UTC"
        assert (await client.get("/api/v1/workspace/preferences")).json()["timezone"] == "UTC"
