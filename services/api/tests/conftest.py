"""Operation counters attached to JUnit evidence, independent of test pass counts."""

import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.evaluation.connector_metrics import PROPERTY, Measurement


@pytest.fixture
def measure(request):
    def record(metric, numerator, denominator, scenario, *, database=False):
        engine = "in_memory"
        if isinstance(database, str):
            engine = database
        elif database:
            engine = (
                "postgresql"
                if os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql")
                else "sqlite"
            )
        sample = Measurement(
            metric=metric,
            numerator=numerator,
            denominator=denominator,
            scenario=scenario,
            environment=engine,
        )
        request.node.user_properties.append((PROPERTY, sample.model_dump_json()))

    return record


@pytest_asyncio.fixture
async def subscription_env(monkeypatch):
    from navox.api import subscriptions
    from navox.api.main import create_app
    from navox.core.settings import Settings, get_settings
    from navox.db.base import Base
    from navox.db.models import Connection
    from navox.db.session import get_database_session

    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"subscription_api_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        if not admin:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, app_environment="test")

    async def database_override():
        async with factory() as database:
            yield database

    async def dispatch(*args, **kwargs):
        return "fixture-dispatch"

    monkeypatch.setattr(subscriptions, "dispatch_cancellation", dispatch)
    monkeypatch.setattr(subscriptions, "dispatch_subscription_reconciliation", dispatch)
    app = create_app()
    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "subscriptions@example.com",
                "password": "twelve-character-password",
                "display_name": "Subscriptions owner",
            },
        )
        assert response.status_code == 201
        user_id = UUID(response.json()["id"])
        workspace_id = UUID(response.json()["workspace"]["id"])
        async with factory() as database:
            connection = Connection(
                user_id=user_id,
                workspace_id=workspace_id,
                provider="google",
                external_account_id="subscription-owner",
                status="active",
            )
            database.add(connection)
            await database.commit()
            yield SimpleNamespace(
                client=client,
                app=app,
                database=database,
                factory=factory,
                settings=settings,
                user_id=user_id,
                workspace_id=workspace_id,
                connection=connection,
                headers={"Origin": settings.web_origin},
            )
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
