from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.connectors.builtin.google import (
    GOOGLE_MANIFEST,
    ensure_google_connector_connection,
    google_canonical_resource,
    google_capabilities,
    mirror_google_document,
)
from navox.db.base import Base
from navox.db.models import Connection, ConnectionCredential, User, Workspace
from navox.intelligence.contracts import SourceDocument
from navox.providers.google_sources import CALENDAR_READ_SCOPE, GMAIL_READ_SCOPE


@pytest_asyncio.fixture
async def database() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_google_connection_mirrors_capabilities_without_copying_credentials(
    database: AsyncSession,
) -> None:
    user = User(email="owner@example.com")
    workspace = Workspace(name="Personal")
    credential = ConnectionCredential(encrypted_refresh_token="encrypted-secret")
    database.add_all([user, workspace, credential])
    await database.flush()
    legacy = Connection(
        user_id=user.id,
        workspace_id=workspace.id,
        provider="google",
        external_account_id="google-account",
        external_email="owner@example.com",
        status="active",
        granted_scopes=[GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE],
        credential_reference=credential.id,
    )
    database.add(legacy)
    await database.flush()

    mirror = await ensure_google_connector_connection(database, legacy)

    assert mirror.legacy_connection_id == legacy.id
    assert mirror.credential_reference == credential.id
    assert set(mirror.authorized_capabilities) == {
        "communication.messages.read",
        "communication.messages.changed",
        "calendar.events.read",
        "calendar.events.changed",
    }
    assert mirror.config == {"legacy_bridge": True}
    assert "encrypted-secret" not in str(mirror.config)
    assert GOOGLE_MANIFEST.id == "google-workspace"


def test_google_capabilities_do_not_invent_authority() -> None:
    assert google_capabilities([]) == frozenset()
    assert google_capabilities(["openid"]) == frozenset()


def test_google_canonical_resource_does_not_persist_message_body() -> None:
    connection_id = UUID("11111111-1111-4111-8111-111111111111")
    document = SourceDocument(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        workspace_id=UUID("33333333-3333-4333-8333-333333333333"),
        provider="google",
        source_type="gmail_message",
        external_id="message-123",
        subject="Sensitive subject",
        content="Sensitive body that is transient.",
        occurred_at=datetime(2026, 9, 24, 9, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 9, 24, 9, 1, tzinfo=UTC),
    )

    resource = google_canonical_resource(document, connector_connection_id=connection_id)

    serialized = resource.model_dump_json()
    assert resource.resource_type == "communication.message"
    assert resource.canonical["has_content"] is True
    assert "Sensitive body" not in serialized
    assert "Sensitive subject" not in serialized
    assert resource.provider_metadata["content_persisted"] is False


@pytest.mark.asyncio
async def test_mirror_google_document_is_workspace_scoped_and_idempotent(
    database: AsyncSession,
) -> None:
    user = User(email="owner2@example.com")
    workspace = Workspace(name="Personal")
    database.add_all([user, workspace])
    await database.flush()
    legacy = Connection(
        user_id=user.id,
        workspace_id=workspace.id,
        provider="google",
        external_account_id="google-account-2",
        external_email="owner2@example.com",
        status="active",
        granted_scopes=[GMAIL_READ_SCOPE],
    )
    database.add(legacy)
    await database.flush()
    document = SourceDocument(
        id=UUID("44444444-4444-4444-8444-444444444444"),
        workspace_id=workspace.id,
        provider="google",
        source_type="gmail_message",
        external_id="message-456",
        subject="Budget",
        content="Please send the budget.",
        occurred_at=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        retrieved_at=datetime(2026, 9, 24, 10, 1, tzinfo=UTC),
    )

    first = await mirror_google_document(
        database,
        legacy_connection=legacy,
        document=document,
    )
    second = await mirror_google_document(
        database,
        legacy_connection=legacy,
        document=document,
    )

    assert first.id == second.id
    assert first.workspace_id == workspace.id
    assert first.content_hash == second.content_hash
