"""Synthetic local fixtures for connected-search regressions.

Everything here is generated data. No provider, credential, account or network
access is used, and no production configuration is read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from navox.ai.foundation.contracts import Sensitivity
from navox.connectors.contracts import ConnectorManifest
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.knowledge import KnowledgeResource, KnowledgeResourcePermission
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    Merchant,
    RecurringObligation,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.news.contracts import ContentRights, NewsItemInput, SourceDefinition
from navox.news.ingestion import store_item
from navox.news.registry import activate_source, current_rights
from navox.news.stories import index_item

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
READ_CAPABILITY = "communication.messages.read"
WRITE_CAPABILITY = "communication.messages.send"
CALENDAR_CAPABILITY = "calendar.events.read"


def manifest(
    connector_key: str,
    read_capability: str,
    resource_types: list[str],
    *,
    write_capability: str | None = None,
) -> ConnectorManifest:
    write = (
        [
            {
                "name": write_capability,
                "description": "Fixture write capability",
                "sensitive": True,
            }
        ]
        if write_capability
        else []
    )
    return ConnectorManifest.model_validate(
        {
            "id": connector_key,
            "version": "1.0.0",
            "displayName": connector_key,
            "category": "test",
            "connectorClass": "GENERIC_API",
            "auth": [{"kind": "none", "label": "None", "scopes": []}],
            "resourceTypes": resource_types,
            "capabilities": {
                "read": [
                    {
                        "name": read_capability,
                        "description": f"Read {connector_key} resources",
                        "sensitive": False,
                    },
                ],
                "write": write,
                "events": [],
                "incrementalSync": True,
            },
            "requiredSecrets": [],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )


@dataclass(frozen=True)
class World:
    workspace_id: UUID
    other_workspace_id: UUID
    user_id: UUID
    other_user_id: UUID
    member_id: UUID
    definition_id: UUID
    connection_id: UUID
    message_id: UUID
    calendar_definition_id: UUID
    calendar_connection_id: UUID
    event_id: UUID


@dataclass
class TestDatabase:
    """One isolated test database: a SQLite file or a private PostgreSQL schema."""

    engine: AsyncEngine
    schema: str | None = None
    admin: AsyncEngine | None = None

    async def dispose(self) -> None:
        await self.engine.dispose()
        if self.admin is not None and self.schema is not None:
            async with self.admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            await self.admin.dispose()


async def build_engine(tmp_path: object) -> TestDatabase:
    """Bounded SQLite (or disposable PostgreSQL) database with foreign keys on.

    On PostgreSQL every call creates its own uniquely named schema and points the
    engine's ``search_path`` at it, matching the repository's isolated-schema
    fixtures, so repeated seeded fixtures cannot collide in a shared database.
    """
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path}/search.db")
    if dsn.startswith("postgresql"):
        schema = f"knowledge_search_{uuid4().hex}"
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
        database = TestDatabase(engine=engine, schema=schema, admin=admin)
    else:
        engine = create_async_engine(dsn)

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        database = TestDatabase(engine=engine)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return database


def factory_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def seed_world(factory: async_sessionmaker[AsyncSession]) -> World:
    workspace_id, other_workspace_id = uuid4(), uuid4()
    user_id, other_user_id, member_id = uuid4(), uuid4(), uuid4()
    async with factory() as database:
        database.add_all(
            [
                User(id=user_id, email="search-owner@example.com"),
                User(id=other_user_id, email="search-other@example.com"),
                User(id=member_id, email="search-member@example.com"),
                Workspace(id=workspace_id, name="Search"),
                Workspace(id=other_workspace_id, name="Other search"),
            ]
        )
        await database.flush()
        database.add_all(
            [
                WorkspaceMembership(workspace_id=workspace_id, user_id=user_id),
                WorkspaceMembership(workspace_id=workspace_id, user_id=member_id),
                WorkspaceMembership(workspace_id=other_workspace_id, user_id=other_user_id),
            ]
        )
        definition = ConnectorDefinition(
            connector_key="google-gmail",
            version="1.0.0",
            display_name="Fixture mail",
            connector_class="GENERIC_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=manifest(
                "google-gmail",
                READ_CAPABILITY,
                ["communication.message", "EMAIL"],
                write_capability=WRITE_CAPABILITY,
            ).model_dump(mode="json", by_alias=True),
        )
        calendar_definition = ConnectorDefinition(
            connector_key="google-calendar",
            version="1.0.0",
            display_name="Fixture calendar",
            connector_class="GENERIC_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=manifest(
                "google-calendar", CALENDAR_CAPABILITY, ["calendar.event"]
            ).model_dump(mode="json", by_alias=True),
        )
        database.add_all([definition, calendar_definition])
        await database.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user_id,
            workspace_id=workspace_id,
            provider="fixture",
            external_account_id="account-1",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[READ_CAPABILITY, WRITE_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY, WRITE_CAPABILITY],
        )
        calendar_connection = ConnectorConnection(
            connector_definition_id=calendar_definition.id,
            user_id=user_id,
            workspace_id=workspace_id,
            provider="fixture",
            external_account_id="calendar-1",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=[CALENDAR_CAPABILITY],
            provider_capabilities=[CALENDAR_CAPABILITY],
        )
        database.add_all([connection, calendar_connection])
        await database.flush()
        message = ConnectorResource(
            id=uuid4(),
            workspace_id=workspace_id,
            connector_connection_id=connection.id,
            provider="fixture",
            resource_type="communication.message",
            external_id="message-1",
            version="etag-1",
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
        database.add(message)
        event = ConnectorResource(
            id=uuid4(),
            workspace_id=workspace_id,
            connector_connection_id=calendar_connection.id,
            provider="fixture",
            resource_type="calendar.event",
            external_id="event-1",
            version="etag-event-1",
            canonical={
                "subject": "Budget review meeting",
                "content": "Budget review meeting with the finance team.",
                "starts_at": "2026-10-02T15:00:00+00:00",
                "ends_at": "2026-10-02T16:00:00+00:00",
                "source_type": "calendar.event",
            },
            provider_metadata={},
            source_created_at=NOW,
            source_updated_at=NOW,
            retrieved_at=NOW,
            content_hash="b" * 64,
        )
        database.add(event)
        await database.commit()
        return World(
            workspace_id=workspace_id,
            other_workspace_id=other_workspace_id,
            user_id=user_id,
            other_user_id=other_user_id,
            member_id=member_id,
            definition_id=definition.id,
            connection_id=connection.id,
            message_id=message.id,
            calendar_definition_id=calendar_definition.id,
            calendar_connection_id=calendar_connection.id,
            event_id=event.id,
        )


async def seed_commitment(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    *,
    title: str = "File the quarterly budget",
    due_at: datetime | None = None,
) -> UUID:
    from navox.db.models import Commitment

    async with factory() as database:
        row = Commitment(
            user_id=world.user_id,
            workspace_id=world.workspace_id,
            commitment_type="task",
            title=title,
            description="Owner: finance. Draft the quarterly budget summary.",
            status="open",
            due_at=due_at or NOW + timedelta(days=3),
            dedupe_key=f"dedupe-{uuid4().hex}",
        )
        database.add(row)
        await database.commit()
        return row.id


async def seed_subscription(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    *,
    name: str = "Streamly",
    plan_name: str = "Streamly Plus",
    status: str = "ACTIVE",
    next_renewal_at: datetime | None = None,
) -> UUID:
    """One owned subscription row for the native subscription adapter."""
    async with factory() as database:
        merchant = Merchant(
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            canonical_name=name,
            normalized_name=name.casefold(),
        )
        database.add(merchant)
        await database.flush()
        row = RecurringObligation(
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            merchant_id=merchant.id,
            obligation_type="SUBSCRIPTION",
            name=name,
            plan_name=plan_name,
            status=status,
            confidence=Decimal("0.900"),
            billing_amount=Decimal("12.99"),
            billing_currency="USD",
            billing_interval="MONTHLY",
            next_renewal_at=next_renewal_at or NOW + timedelta(days=5),
            review_state="ACTIVE",
        )
        database.add(row)
        await database.commit()
        return row.id


def news_settings(*configs: SourceDefinition) -> Settings:
    """Settings whose trusted catalog contains exactly the supplied sources.

    The News feature flag is explicitly on, because search honours it.
    """
    return Settings(
        _env_file=None,
        app_environment="test",
        news_feed_enabled=True,
        news_source_catalog=[config.model_dump(mode="json", by_alias=True) for config in configs],
    )


def news_definition(**rights: object) -> SourceDefinition:
    """A synthetic News source definition with an explicit rights policy."""
    policy: dict[str, object] = {
        "metadata_storage_allowed": True,
        "snippet_storage_allowed": True,
        "summary_generation_allowed": True,
        "retention_days": 2,
        "permission_reference": "https://news.example.com/terms",
        "reviewed_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=20),
    }
    return SourceDefinition(
        key="search-news",
        name="Search news fixture",
        domain="news.example.com",
        source_type="RESEARCH",
        feed_type="RSS",
        endpoint="https://news.example.com/feed",
        article_domains=("news.example.com",),
        category="science",
        independence_group="search-news",
        identity_verified=True,
        rights=ContentRights(**(policy | rights)),
    )


async def seed_news_story(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    *,
    headline: str = "A quarterly budget observation",
    description: str = "The instrument measured a quarterly budget change.",
    **rights: object,
) -> tuple[UUID, SourceDefinition]:
    """One rights-checked News story in the search world's own workspace."""
    async with factory() as database:
        config = news_definition(**rights)
        source = await activate_source(
            database,
            config,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            now=NOW,
        )
        row = await store_item(
            database,
            source,
            await current_rights(database, source),
            config,
            NewsItemInput(
                external_id="story-1",
                headline=headline,
                description=description,
                canonical_url="https://news.example.com/report",
                published_at=NOW,
                categories=("science",),
            ),
            now=NOW,
        )
        story = await index_item(database, row, {config.key: config}, now=NOW)
        await database.commit()
        return story.id, config


async def add_newer_news_source(
    factory: async_sessionmaker[AsyncSession],
    world: World,
    config: SourceDefinition,
    story_id: UUID,
    *,
    headline: str,
    canonical_url: str,
    published_at: datetime,
    decision: str = "SKIPPED",
) -> UUID:
    """Attach a second, newer source item to an existing story.

    News orders story items newest first, so this exercises citation anchoring:
    the story's own anchor item must stay the cited source.
    """
    from navox.db.news import NewsStoryItem
    from navox.news.clustering import duplicate_keys
    from navox.news.ingestion import item_view
    from navox.news.registry import owned_source

    async with factory() as database:
        source = await owned_source(
            database,
            await _source_id_for(database, config, world),
            workspace_id=world.workspace_id,
            user_id=world.user_id,
        )
        row = await store_item(
            database,
            source,
            await current_rights(database, source),
            config,
            NewsItemInput(
                external_id=f"story-1-{headline[:8]}",
                headline=headline,
                description="A second, newer source statement.",
                canonical_url=canonical_url,
                published_at=published_at,
                categories=("science",),
            ),
            # Ingest at the item's own time so a deliberately newer item is valid.
            now=published_at,
        )
        database.add(
            NewsStoryItem(
                news_item_id=row.id,
                cluster_id=story_id,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                item_revision=row.revision,
                url_digest=duplicate_keys(
                    await item_view(database, row, {config.key: config}, now=NOW)
                )[0],
                copy_digest=None,
                decision=decision,
                joined_at=published_at,
            )
        )
        await database.commit()
        return row.id


async def _source_id_for(database: AsyncSession, config: SourceDefinition, world: World) -> UUID:
    from navox.db.news import NewsSource

    source = await database.scalar(
        select(NewsSource).where(
            NewsSource.workspace_id == world.workspace_id,
            NewsSource.user_id == world.user_id,
            NewsSource.source_key == config.key,
        )
    )
    assert source is not None
    return source.id


async def knowledge_rows(
    factory: async_sessionmaker[AsyncSession],
) -> list[KnowledgeResource]:
    async with factory() as database:
        return list(
            await database.scalars(
                select(KnowledgeResource).order_by(KnowledgeResource.external_resource_id)
            )
        )


async def pair_for(
    factory: async_sessionmaker[AsyncSession], connector_key: str, external_id: str
) -> tuple[ConnectorConnection, ConnectorResource]:
    """The seeded (connection, canonical resource) pair for one connector key."""
    async with factory() as database:
        row = (
            await database.execute(
                select(ConnectorConnection, ConnectorResource)
                .join(
                    ConnectorDefinition,
                    ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
                )
                .join(
                    ConnectorResource,
                    ConnectorResource.connector_connection_id == ConnectorConnection.id,
                )
                .where(
                    ConnectorDefinition.connector_key == connector_key,
                    ConnectorResource.external_id == external_id,
                )
            )
        ).first()
        assert row is not None, f"missing seeded {connector_key}/{external_id}"
        return row[0], row[1]


async def gmail_pair(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[ConnectorConnection, ConnectorResource]:
    return await pair_for(factory, "google-gmail", "message-1")


async def calendar_pair(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[ConnectorConnection, ConnectorResource]:
    return await pair_for(factory, "google-calendar", "event-1")


async def load_world(factory: async_sessionmaker[AsyncSession]) -> World:
    """Re-read the seeded identities without assuming fixture insertion order."""
    gmail_connection, message = await gmail_pair(factory)
    calendar_connection, event = await calendar_pair(factory)
    async with factory() as database:
        workspace = await database.scalar(select(Workspace).where(Workspace.name == "Search"))
        other = await database.scalar(select(Workspace).where(Workspace.name == "Other search"))
        owner = await database.scalar(select(User).where(User.email == "search-owner@example.com"))
        other_user = await database.scalar(
            select(User).where(User.email == "search-other@example.com")
        )
        member = await database.scalar(
            select(User).where(User.email == "search-member@example.com")
        )
        assert workspace is not None and other is not None and owner is not None
        assert other_user is not None and member is not None
        return World(
            workspace_id=workspace.id,
            other_workspace_id=other.id,
            user_id=owner.id,
            other_user_id=other_user.id,
            member_id=member.id,
            definition_id=gmail_connection.connector_definition_id,
            connection_id=gmail_connection.id,
            message_id=message.id,
            calendar_definition_id=calendar_connection.connector_definition_id,
            calendar_connection_id=calendar_connection.id,
            event_id=event.id,
        )


async def grants(
    factory: async_sessionmaker[AsyncSession],
) -> list[KnowledgeResourcePermission]:
    async with factory() as database:
        return list(
            await database.scalars(
                select(KnowledgeResourcePermission).order_by(KnowledgeResourcePermission.created_at)
            )
        )


SUPPORTED_SENSITIVITY = Sensitivity.PERSONAL.value
