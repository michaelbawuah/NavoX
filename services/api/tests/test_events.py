import base64
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api import events
from navox.api.main import app
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import Connection, IncomingEvent
from navox.db.session import get_database_session
from navox.events.processor import IncomingEventProcessor


@pytest_asyncio.fixture
async def event_test_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        google_gmail_push_subscription="projects/navox/subscriptions/gmail-events",
        google_pubsub_push_audience="https://navox.example.com/api/v1/events/gmail",
        google_pubsub_push_service_account="navox-pubsub@navox.iam.gserviceaccount.com",
        google_gmail_push_verification_token="gmail-webhook-secret",
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, session_factory
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    await engine.dispose()


async def register(client: AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "twelve-character-password",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201
    return response.json()


async def seed_google_connection(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_email: str = "connected@example.com",
    source: str | None = None,
) -> tuple[Connection, str | None, str | None]:
    account = await register(client)
    workspace = account["workspace"]
    assert isinstance(workspace, dict)
    user_id = UUID(str(account["id"]))
    workspace_id = UUID(str(workspace["id"]))
    async with session_factory() as session:
        connection = Connection(
            user_id=user_id,
            workspace_id=workspace_id,
            provider="google",
            external_account_id="google-account-123",
            external_email=external_email,
            status="active",
            granted_scopes=["openid"],
        )
        session.add(connection)
        await session.flush()
        channel_id: str | None = None
        channel_token: str | None = None
        if source is not None:
            subscription, credentials = events.create_google_channel_subscription(
                connection,
                source,
                resource_id="google-resource-123",
            )
            session.add(subscription)
            channel_id = credentials.channel_id
            channel_token = credentials.channel_token
        await session.commit()
        return connection, channel_id, channel_token


@pytest.mark.asyncio
async def test_calendar_notification_is_authenticated_normalized_and_deduplicated(
    event_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = event_test_environment
    connection, channel_id, channel_token = await seed_google_connection(
        client,
        session_factory,
        source="calendar",
    )
    assert channel_id is not None
    assert channel_token is not None
    headers = {
        "X-Goog-Channel-ID": channel_id,
        "X-Goog-Channel-Token": channel_token,
        "X-Goog-Resource-ID": "google-resource-123",
        "X-Goog-Resource-State": "exists",
        "X-Goog-Message-Number": "42",
    }

    first = await client.post("/api/v1/events/calendar", headers=headers)
    duplicate = await client.post("/api/v1/events/calendar", headers=headers)

    assert first.status_code == 200
    assert first.json() == {"status": "processed"}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    async with session_factory() as session:
        stored_events = list(await session.scalars(select(IncomingEvent)))
    assert len(stored_events) == 1
    event = stored_events[0]
    assert event.connection_id == connection.id
    assert event.workspace_id == connection.workspace_id
    assert event.user_id == connection.user_id
    assert event.source == "calendar"
    assert event.status == "processed"
    assert event.event_metadata == {"resource_state": "exists", "message_number": "42"}
    assert event.processed_at is not None


@pytest.mark.asyncio
async def test_channel_notification_with_an_invalid_token_is_rejected(
    event_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = event_test_environment
    _, channel_id, _ = await seed_google_connection(client, session_factory, source="drive")
    assert channel_id is not None

    response = await client.post(
        "/api/v1/events/drive",
        headers={
            "X-Goog-Channel-ID": channel_id,
            "X-Goog-Channel-Token": "not-the-server-issued-token",
            "X-Goog-Resource-ID": "google-resource-123",
            "X-Goog-Resource-State": "exists",
            "X-Goog-Message-Number": "7",
        },
    )

    assert response.status_code == 401
    async with session_factory() as session:
        assert list(await session.scalars(select(IncomingEvent))) == []


@pytest.mark.asyncio
async def test_gmail_pubsub_notification_requires_verified_sender_and_is_deduplicated(
    event_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory = event_test_environment
    connection, _, _ = await seed_google_connection(client, session_factory)

    async def verified_sender(_: str | None, __: Settings) -> None:
        return None

    monkeypatch.setattr(events, "verify_google_pubsub_push", verified_sender)
    notification_data = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"emailAddress": "connected@example.com", "historyId": "history-99"}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    payload = {
        "subscription": "projects/navox/subscriptions/gmail-events",
        "message": {
            "messageId": "pubsub-message-12",
            "publishTime": "2026-09-22T12:00:00Z",
            "data": notification_data,
        },
    }

    first = await client.post(
        "/api/v1/events/gmail?token=gmail-webhook-secret",
        headers={"Authorization": "Bearer verified-in-test"},
        json=payload,
    )
    duplicate = await client.post(
        "/api/v1/events/gmail?token=gmail-webhook-secret",
        headers={"Authorization": "Bearer verified-in-test"},
        json=payload,
    )

    assert first.status_code == 200
    assert first.json() == {"status": "processed"}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    async with session_factory() as session:
        stored_event = await session.scalar(select(IncomingEvent))
    assert stored_event is not None
    assert stored_event.connection_id == connection.id
    assert stored_event.source == "gmail"
    assert stored_event.external_resource_id == "history-99"
    assert stored_event.occurred_at is not None
    assert stored_event.occurred_at.replace(tzinfo=UTC) == datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    assert stored_event.event_metadata == {"history_id": "history-99"}
    assert "connected@example.com" not in json.dumps(stored_event.event_metadata)


@pytest.mark.asyncio
async def test_gmail_rejects_malformed_provider_payload(
    event_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = event_test_environment

    async def verified_sender(_: str | None, __: Settings) -> None:
        return None

    monkeypatch.setattr(events, "verify_google_pubsub_push", verified_sender)
    response = await client.post(
        "/api/v1/events/gmail?token=gmail-webhook-secret",
        headers={"Authorization": "Bearer verified-in-test"},
        json={"subscription": "projects/navox/subscriptions/gmail-events", "message": {}},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_gmail_rejects_an_unsigned_push_request(
    event_test_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = event_test_environment

    response = await client.post(
        "/api/v1/events/gmail?token=gmail-webhook-secret",
        json={"subscription": "projects/navox/subscriptions/gmail-events", "message": {}},
    )

    assert response.status_code == 401


def test_event_processor_only_advances_received_events() -> None:
    event = IncomingEvent(
        connection_id=UUID("12345678-1234-5678-1234-567812345678"),
        user_id=UUID("12345678-1234-5678-1234-567812345679"),
        workspace_id=UUID("12345678-1234-5678-1234-567812345680"),
        provider="google",
        source="calendar",
        event_type="google.calendar.notification",
        external_event_id="channel:1",
        external_resource_id=None,
        payload_hash="f" * 64,
        event_metadata={},
        status="received",
    )
    IncomingEventProcessor().process(event)
    processed_at = event.processed_at
    IncomingEventProcessor().process(event)

    assert event.status == "processed"
    assert processed_at is not None
    assert event.processed_at == processed_at
