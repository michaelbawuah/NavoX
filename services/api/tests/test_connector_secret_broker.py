from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import ConnectorConnection, ConnectorDefinition, User, Workspace


@pytest_asyncio.fixture
async def database() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_secret_broker_scopes_and_redacts_secret_values(database: AsyncSession) -> None:
    settings = Settings(
        connector_secret_encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    broker = SecretBroker(settings)
    user = User(email="owner@example.com")
    workspace = Workspace(name="Personal")
    database.add_all([user, workspace])
    await database.flush()
    definition = ConnectorDefinition(
        connector_key="demo-service",
        version="1.0.0",
        display_name="Demo",
        connector_class="TOKEN_API",
        trust_level="USER_PRIVATE",
        manifest={},
    )
    database.add(definition)
    await database.flush()
    credential_id = await broker.store(
        database,
        {"API_TOKEN": "super-secret-value", "OTHER_SECRET": "other-secret"},
    )
    connection = ConnectorConnection(
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=workspace.id,
        provider="demo",
        external_account_id="account-1",
        authorized_capabilities=[],
        provider_capabilities=[],
        config={},
        credential_reference=credential_id,
    )
    database.add(connection)
    await database.flush()

    lease = await broker.lease(
        database,
        connection_id=connection.id,
        purpose="sync.read",
        names={"API_TOKEN"},
    )

    assert lease.get("API_TOKEN") == "super-secret-value"
    assert "super-secret-value" not in repr(lease)
    assert "OTHER_SECRET" not in lease.names
    with pytest.raises(SecretBrokerError):
        lease.get("OTHER_SECRET")


@pytest.mark.asyncio
async def test_secret_broker_cannot_lease_another_connections_bundle(
    database: AsyncSession,
) -> None:
    settings = Settings(
        connector_secret_encryption_key=Fernet.generate_key().decode("utf-8"),
    )
    broker = SecretBroker(settings)
    with pytest.raises(SecretBrokerError):
        await broker.lease(
            database,
            connection_id=uuid4(),
            purpose="sync.read",
            names={"API_TOKEN"},
        )
