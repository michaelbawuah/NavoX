import importlib.util
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.api.middleware import ApplicationCORSMiddleware, RequestHardeningMiddleware
from navox.api.waitlist import create_waitlist_app
from navox.api.waitlist_service import create_app as create_standalone_app
from navox.api.waitlist_service import initialize_database
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.session import get_database_session
from navox.db.waitlist import WaitlistEntry, WaitlistRateLimit


@pytest_asyncio.fixture
async def env(tmp_path):
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path}/waitlist.db")
    schema = f"waitlist_test_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[WaitlistEntry.__table__, WaitlistRateLimit.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def database():
        async with factory() as session:
            yield session

    with patch("navox.api.waitlist.get_settings", return_value=Settings(_env_file=None)):
        public = create_waitlist_app()
    public.dependency_overrides[get_database_session] = database
    app = FastAPI()
    app.add_middleware(ApplicationCORSMiddleware, allow_origins=["http://localhost:3000"])
    app.add_middleware(RequestHardeningMiddleware)
    app.mount("/v1", public)
    yield app, factory, public
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_insert_normalizes_and_persists_without_logging_email(env, caplog):
    app, factory, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with caplog.at_level("INFO"):
            response = await client.post(
                "/v1/waitlist", json={"email": "Student@Example.EDU", "source": "landing"}
            )
        assert response.status_code == 201
        assert response.json() == {"status": "created"}
        duplicate = await client.post("/v1/waitlist", json={"email": "student@example.edu"})
        assert duplicate.status_code == 409
    async with factory() as database:
        entry = await database.get(WaitlistEntry, "student@example.edu")
        assert entry.source == "landing"
        assert entry.school_domain == "example.edu"
        assert entry.created_at is not None
        assert await database.scalar(select(func.count()).select_from(WaitlistEntry)) == 1
    assert "student@example.edu" not in caplog.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"email": "private-invalid-address"},
        {"email": "student@example.edu", "source": "arbitrary"},
        {"email": "student@example.edu", "secret": "extra"},
        {},
    ],
)
async def test_invalid_input_is_redacted(env, payload, caplog):
    app, _, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/waitlist", json=payload)
    assert response.status_code == 422
    assert "student@example.edu" not in response.text + caplog.text
    assert "private-invalid-address" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_rate_limit_survives_clients_and_ignores_spoofed_forwarding(env):
    app, _, _ = env
    with patch("navox.api.waitlist.time", return_value=120):
        for i in range(5):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/v1/waitlist", json={"email": f"s{i}@example.edu"})
                assert response.status_code == 201
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/waitlist",
                json={"email": "six@example.edu"},
                headers={"X-Forwarded-For": "198.51.100.1"},
            )
            assert response.status_code == 429
            assert response.headers["Retry-After"] == "60"
            with patch("navox.api.waitlist.time", return_value=180):
                assert (
                    await client.post("/v1/waitlist", json={"email": "six@example.edu"})
                ).status_code == 201


@pytest.mark.asyncio
async def test_invalid_emails_also_consume_quota(env):
    app, _, _ = env
    with patch("navox.api.waitlist.time", return_value=120):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(5):
                assert (await client.post("/v1/waitlist", json={"email": "bad"})).status_code == 422
            assert (await client.post("/v1/waitlist", json={"email": "bad"})).status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,allowed",
    [
        ("https://navox.net", True),
        ("http://localhost:8080", True),
        ("https://evil.example", False),
        ("http://localhost:3000", False),
    ],
)
async def test_cors_is_scoped_to_landing(env, origin, allowed):
    app, _, _ = env
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/v1/waitlist",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.headers.get("Access-Control-Allow-Origin") == (origin if allowed else None)


@pytest.mark.asyncio
async def test_database_failure_returns_no_email_or_sql(env, caplog):
    app, _, _ = env
    with patch(
        "sqlalchemy.ext.asyncio.AsyncSession.execute", side_effect=SQLAlchemyError("private")
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/waitlist", json={"email": "private@example.edu"})
    assert response.status_code == 503
    assert "private" not in response.text + caplog.text


def test_production_cors_excludes_localhost():
    settings = Settings(
        _env_file=None, app_environment="production", web_origin="https://app.navox.net"
    )
    with patch("navox.api.waitlist.get_settings", return_value=settings):
        app = create_waitlist_app()
    assert app.user_middleware[0].kwargs["allow_origins"] == ["https://navox.net"]


@pytest.mark.asyncio
async def test_standalone_service_exposes_no_student_routes():
    app = create_standalone_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ["/docs", "/openapi.json", "/api/v1/auth/register", "/api/v1/student"]:
            assert (await client.get(path)).status_code == 404
        assert (
            await client.options(
                "/v1/waitlist",
                headers={"Origin": "https://navox.net", "Access-Control-Request-Method": "POST"},
            )
        ).status_code == 200


@pytest.mark.asyncio
async def test_standalone_bootstrap_refuses_the_student_database():
    with patch("navox.api.waitlist_service.get_settings", return_value=Settings(_env_file=None)):
        with pytest.raises(RuntimeError, match="dedicated navox_waitlist"):
            await initialize_database()


def test_migration_upgrade_and_downgrade():
    path = Path(__file__).parents[1] / "migrations/versions/0053_landing_waitlist.py"
    spec = importlib.util.spec_from_file_location("waitlist_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with patch.object(module, "op", Operations(MigrationContext.configure(connection))):
            module.upgrade()
            assert set(inspect(connection).get_table_names()) == {
                "waitlist_entries",
                "waitlist_rate_limits",
            }
            # The migration creates a real unique email index/primary key.
            connection.execute(
                text(
                    "INSERT INTO waitlist_entries (email, source, school_domain) "
                    "VALUES ('test@example.edu', 'landing', 'example.edu')"
                )
            )
            assert connection.scalar(text("SELECT created_at FROM waitlist_entries"))
            assert inspect(connection).get_pk_constraint("waitlist_entries")[
                "constrained_columns"
            ] == ["email"]
            module.downgrade()
            assert inspect(connection).get_table_names() == []
    engine.dispose()
