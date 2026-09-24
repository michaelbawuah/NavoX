import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.main import create_app
from navox.connectors.builtin.mcp import MCPServerPolicy
from navox.connectors.catalog import build_connector_registry
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
)
from navox.db.session import get_database_session


def reviewed_policy() -> MCPServerPolicy:
    return MCPServerPolicy(
        server_id="student-tasks",
        provider="student_tasks",
        display_name="Student tasks",
        origin="https://mcp.student.example",
        token_secret_name="MCP_TOKEN",
        resources=[
            {
                "uri_prefix": "tasks://approved/",
                "capability": "tasks.items.read",
                "resource_type": "tasks.item",
            }
        ],
        read_tools=[
            {
                "name": "list_due",
                "capability": "tasks.due.read",
                "resource_type": "tasks.due",
            }
        ],
    )


@pytest_asyncio.fixture
async def env() -> AsyncIterator[SimpleNamespace]:
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"mcp_{uuid4().hex}"
    administrative = None
    if dsn.startswith("postgresql"):
        administrative = create_async_engine(dsn)
        async with administrative.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    policy = reviewed_policy()
    settings = Settings(
        _env_file=None,
        ai_provider="disabled",
        mcp_servers=[policy.model_dump(mode="json")],
        connector_secret_encryption_key=Fernet.generate_key().decode(),
    )

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "mcp-owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Owner",
            },
        )
        assert response.status_code == 201
        yield SimpleNamespace(
            client=client, factory=factory, settings=settings, policy=policy, user=response.json()
        )
    await engine.dispose()
    if administrative is not None:
        async with administrative.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await administrative.dispose()


def command() -> dict[str, object]:
    return {
        "server_id": "student-tasks",
        "capabilities": ["tasks.items.read"],
        "confirmed": True,
        "request_id": str(uuid4()),
        "token": "server-only-secret",
    }


@pytest.mark.asyncio
async def test_mcp_connect_is_owner_consented_encrypted_and_runtime_registered(env) -> None:
    response = await env.client.get("/api/v1/connectors/mcp/servers")
    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "student-tasks",
            "name": "Student tasks",
            "read_capabilities": ["tasks.due.read", "tasks.items.read"],
            "authentication": "api_token",
        }
    ]
    assert "mcp.student.example" not in response.text
    catalog = (await env.client.get("/api/v1/connectors")).json()
    assert next(entry for entry in catalog if entry["id"] == "mcp")["availability"] == "available"
    request = command()
    for denied in (
        {**request, "confirmed": False},
        {**request, "capabilities": ["tasks.items.write"]},
        {**request, "capabilities": ["tasks.items.read", "tasks.items.read"]},
    ):
        rejection = await env.client.post("/api/v1/connectors/mcp/connect", json=denied)
        assert rejection.status_code == 422
        assert "server-only-secret" not in rejection.text
    response = await env.client.post("/api/v1/connectors/mcp/connect", json=request)
    assert response.status_code == 201, response.text
    assert response.json()["dispatch_status"] == "pending"
    connection_id = response.json()["connection_id"]
    assert (await env.client.post("/api/v1/connectors/mcp/connect", json=request)).json() == {
        "connection_id": connection_id,
        "dispatch_status": "existing",
        "reused": True,
    }
    assert (
        await env.client.post(
            "/api/v1/connectors/mcp/connect",
            json={**request, "request_id": str(uuid4()), "capabilities": ["tasks.due.read"]},
        )
    ).status_code == 409
    async with env.factory() as database:
        connection = await database.get(ConnectorConnection, UUID(connection_id))
        assert connection is not None
        assert connection.config == env.policy.connection_config()
        assert connection.authorized_capabilities == ["tasks.items.read"]
        definition = await database.get(ConnectorDefinition, connection.connector_definition_id)
        assert definition is not None
        assert definition.connector_key == env.policy.connector_key
        registry = build_connector_registry(env.settings)
        assert registry.get(definition.connector_key).manifest.capabilities.write == []
        credential = await database.get(ConnectionCredential, connection.credential_reference)
        assert credential is not None
        assert "server-only-secret" not in credential.encrypted_refresh_token
        events = list(
            await database.scalars(
                select(AuditEvent).where(AuditEvent.event_type == "connector.mcp.connected")
            )
        )
        assert len(events) == 1
    detail = await env.client.get(f"/api/v1/connections/{connection_id}")
    assert detail.status_code == 200
    assert detail.json()["connector_id"] == "mcp"
    assert detail.json()["sources"][0]["can_sync"] is True
    assert "server-only-secret" not in detail.text
    sync = await env.client.post(
        f"/api/v1/connections/{connection_id}/sync",
        json={"source": "resources", "request_id": str(uuid4())},
    )
    assert sync.status_code == 202
    assert sync.json() == {"dispatch_status": "pending"}
    # Operator revision revokes the old policy identity without editing the
    # user's stored grants or accidentally activating a new endpoint.
    revised = env.policy.model_copy(update={"origin": "https://new.student.example"})
    env.settings.mcp_servers = [revised.model_dump(mode="json")]
    assert revised.connector_key != env.policy.connector_key
    old = await env.client.get(f"/api/v1/connections/{connection_id}")
    assert old.json()["health"] == "DEGRADED"
    assert old.json()["sources"][0]["can_sync"] is False
    assert (
        await env.client.post(
            f"/api/v1/connections/{connection_id}/sync",
            json={"source": "resources", "request_id": str(uuid4())},
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_mcp_setup_requires_session_and_operator_review(env) -> None:
    env.client.cookies.clear()
    assert (await env.client.get("/api/v1/connectors/mcp/servers")).status_code == 401
    assert (
        await env.client.post("/api/v1/connectors/mcp/connect", json=command())
    ).status_code == 401
    env.settings.mcp_servers = []
    await env.client.post(
        "/api/v1/auth/login",
        json={"email": "mcp-owner@example.com", "password": "twelve-character-password"},
    )
    assert (await env.client.get("/api/v1/connectors/mcp/servers")).json() == []
    assert (
        await env.client.post("/api/v1/connectors/mcp/connect", json=command())
    ).status_code == 409
