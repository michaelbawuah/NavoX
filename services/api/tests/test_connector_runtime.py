from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.connectors.contracts import (
    AuthorizationRequest,
    AuthorizationResult,
    CanonicalResource,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.db.base import Base
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
    Workspace,
)


class FixtureConnector:
    def __init__(self, config: Mapping[str, JsonValue]) -> None:
        self.config = config

    def get_manifest(self) -> ConnectorManifest:
        return ConnectorManifest.model_validate(
            {
                "id": "fixture-service",
                "version": "1.0.0",
                "displayName": "Fixture",
                "category": "test",
                "connectorClass": "GENERIC_API",
                "auth": [{"kind": "none", "label": "None", "scopes": []}],
                "resourceTypes": ["fixture.item"],
                "capabilities": {
                    "read": [
                        {
                            "name": "fixture.items.read",
                            "description": "Read fixture items",
                            "sensitive": False,
                        }
                    ],
                    "write": [],
                    "events": [],
                    "incrementalSync": True,
                },
                "requiredSecrets": [],
                "rateLimitStrategy": "none",
                "minimumNavoxConnectorApiVersion": "1",
            }
        )

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        return AuthorizationResult(authorized=True)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        return SyncPage(
            resources=[
                CanonicalResource(
                    resource_id=stable_resource_id(
                        request.connection_id,
                        "fixture.item",
                        "item-1",
                    ),
                    workspace_id=request.workspace_id,
                    connector_connection_id=request.connection_id,
                    provider="fixture",
                    resource_type="fixture.item",
                    external_id="item-1",
                    canonical={"title": "Portable resource"},
                    retrieved_at=datetime.now(UTC),
                )
            ],
            next_cursor="cursor-2",
            has_more=False,
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        raise NotImplementedError

    async def execute(self, request):
        raise NotImplementedError


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
async def test_runtime_persists_resources_and_advances_cursor_only_after_consumer_accepts(
    database: AsyncSession,
) -> None:
    manifest = FixtureConnector({}).get_manifest()
    registry = ConnectorRegistry()
    registry.register(manifest, FixtureConnector)
    runtime = ConnectorRuntime(registry)

    user = User(email="owner@example.com")
    workspace = Workspace(name="Personal")
    database.add_all([user, workspace])
    await database.flush()
    definition = ConnectorDefinition(
        connector_key=manifest.id,
        version=manifest.version,
        display_name=manifest.display_name,
        connector_class=manifest.connector_class,
        trust_level="NAVOX_FIRST_PARTY",
        manifest=manifest.model_dump(mode="json", by_alias=True),
    )
    database.add(definition)
    await database.flush()
    connection = ConnectorConnection(
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=workspace.id,
        provider="fixture",
        external_account_id="account-1",
        authorized_capabilities=["fixture.items.read"],
        provider_capabilities=["fixture.items.read"],
        config={},
    )
    database.add(connection)
    await database.commit()

    seen: list[UUID] = []

    async def consume(resource: CanonicalResource) -> None:
        seen.append(resource.resource_id)

    run = await runtime.sync(
        database,
        connection_id=connection.id,
        workspace_id=workspace.id,
        request_id=uuid4(),
        policy_allowed={"fixture.items.read"},
        consume=consume,
    )

    refreshed = await database.get(ConnectorConnection, connection.id)
    resource = await database.get(
        ConnectorResource,
        stable_resource_id(connection.id, "fixture.item", "item-1"),
    )
    assert run.status == "completed"
    assert refreshed is not None and refreshed.sync_cursor == "cursor-2"
    assert resource is not None
    assert resource.canonical["title"] == "Portable resource"
    assert seen == [resource.id]


@pytest.mark.asyncio
async def test_runtime_rejects_cross_workspace_resource_before_cursor_advance(
    database: AsyncSession,
) -> None:
    class BadConnector(FixtureConnector):
        async def sync(self, request: SyncRequest) -> SyncPage:
            return SyncPage(
                resources=[
                    CanonicalResource(
                        resource_id=stable_resource_id(
                            request.connection_id,
                            "fixture.item",
                            "item-1",
                        ),
                        workspace_id=uuid4(),
                        connector_connection_id=request.connection_id,
                        provider="fixture",
                        resource_type="fixture.item",
                        external_id="item-1",
                        canonical={"title": "Wrong workspace"},
                        retrieved_at=datetime.now(UTC),
                    )
                ],
                next_cursor="must-not-commit",
            )

    manifest = BadConnector({}).get_manifest()
    registry = ConnectorRegistry()
    registry.register(manifest, BadConnector)
    runtime = ConnectorRuntime(registry)
    user = User(email="owner2@example.com")
    workspace = Workspace(name="Personal")
    database.add_all([user, workspace])
    await database.flush()
    definition = ConnectorDefinition(
        connector_key=manifest.id,
        version=manifest.version,
        display_name=manifest.display_name,
        connector_class=manifest.connector_class,
        trust_level="NAVOX_FIRST_PARTY",
        manifest=manifest.model_dump(mode="json", by_alias=True),
    )
    database.add(definition)
    await database.flush()
    connection = ConnectorConnection(
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=workspace.id,
        provider="fixture",
        external_account_id="account-2",
        authorized_capabilities=["fixture.items.read"],
        provider_capabilities=["fixture.items.read"],
        config={},
    )
    database.add(connection)
    await database.commit()

    async def consume(_resource: CanonicalResource) -> None:
        return None

    with pytest.raises(ConnectorRuntimeError, match="Cross-workspace"):
        await runtime.sync(
            database,
            connection_id=connection.id,
            workspace_id=workspace.id,
            request_id=uuid4(),
            policy_allowed={"fixture.items.read"},
            consume=consume,
        )

    refreshed = await database.get(ConnectorConnection, connection.id)
    assert refreshed is not None
    assert refreshed.sync_cursor is None
