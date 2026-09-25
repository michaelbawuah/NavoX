import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api import connections
from navox.api.auth import CurrentAccount
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    Commitment,
    Connection,
    IntelligenceCursor,
    IntelligenceSourceReceipt,
    ObservationEvidence,
    OperationalObservation,
    ProviderEventSubscription,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence import ingestion
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.intelligence.extraction import ModelExtractionResponse, OperationalExtractor
from navox.providers import google_sources
from navox.providers.google_sources import (
    CALENDAR_READ_SCOPE,
    GMAIL_READ_SCOPE,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
    GoogleSourceGateway,
    SourceBatch,
    calendar_document,
    gmail_document,
)

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


def message(identifier: str = "m1") -> dict[str, Any]:
    return {
        "id": identifier,
        "threadId": "thread1",
        "internalDate": "1790078400000",
        "labelIds": ["SENT"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Owner <owner@example.com>"},
                {"name": "To", "value": "Pat <pat@example.com>"},
                {"name": "Subject", "value": "Budget"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"I will send the budget tomorrow. Private incidental content."
                        ).decode()
                    },
                },
                {
                    "mimeType": "text/plain",
                    "filename": "private.txt",
                    "body": {"data": base64.urlsafe_b64encode(b"ATTACHMENT SECRET").decode()},
                },
            ],
        },
    }


async def fetch(gateway: GoogleSourceGateway, source: str, cursor: str | None) -> SourceBatch:
    return await gateway.fetch(
        source=source,
        access_token="test-token",
        workspace_id=uuid4(),
        connection_id=uuid4(),
        cursor=cursor,
        now=NOW,
    )


@pytest.mark.asyncio
async def test_gmail_history_paginates_deduplicates_and_never_reads_attachments() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path.endswith("/history"):
            assert request.url.params["startHistoryId"] == "90"
            if "pageToken" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "historyId": "100",
                        "nextPageToken": "next",
                        "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "historyId": "101",
                    "history": [
                        {
                            "messagesAdded": [{"message": {"id": "m1"}}],
                            "messagesDeleted": [{"message": {"id": "gone"}}],
                        }
                    ],
                },
            )
        assert request.url.path.endswith("/messages/m1")
        return httpx.Response(200, json=message())

    batch = await fetch(GoogleSourceGateway(transport=httpx.MockTransport(handler)), "gmail", "90")
    assert batch.cursor == "101"
    assert len(requests) == 3
    assert len(batch.documents) == 2
    active = batch.documents[0]
    assert active.external_parent_id == "thread1"
    assert active.author is not None and active.author.identity_value == "owner@example.com"
    assert active.metadata["label_ids"] == ["SENT"]
    assert "ATTACHMENT" not in (active.content or "")
    assert batch.documents[1].metadata["status"] == "deleted"


@pytest.mark.asyncio
async def test_gmail_expired_history_bootstraps_with_pre_scan_anchor() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/history"):
            return httpx.Response(404)
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "200"})
        assert request.url.params["q"] == "newer_than:30d"
        return httpx.Response(200, json={"messages": []})

    batch = await fetch(GoogleSourceGateway(transport=httpx.MockTransport(handler)), "gmail", "old")
    assert batch.reset and batch.cursor == "200"
    assert calls[-2:] == ["/gmail/v1/users/me/profile", "/gmail/v1/users/me/messages"]


@pytest.mark.asyncio
async def test_calendar_delta_preserves_token_across_pages_and_cancellations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["syncToken"] == "old"
        assert "timeMin" not in request.url.params and "timeMax" not in request.url.params
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "nextPageToken": "page2",
                    "items": [
                        {
                            "id": "event1",
                            "summary": "Budget review",
                            "status": "confirmed",
                            "start": {"dateTime": "2026-09-23T09:00:00-04:00"},
                            "end": {"dateTime": "2026-09-23T10:00:00-04:00"},
                            "organizer": {"email": "pat@example.com"},
                            "attendees": [{"email": "owner@example.com"}],
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "nextSyncToken": "new",
                "items": [
                    {"id": "event2", "status": "cancelled", "updated": "2026-09-22T10:00:00Z"}
                ],
            },
        )

    batch = await fetch(
        GoogleSourceGateway(transport=httpx.MockTransport(handler)), "calendar", "old"
    )
    assert batch.cursor == "new" and not batch.reset
    assert batch.documents[0].metadata["start_at"] == "2026-09-23T09:00:00-04:00"
    assert batch.documents[1].metadata["status"] == "cancelled"
    assert batch.documents[1].content is None


@pytest.mark.asyncio
async def test_expired_calendar_token_full_scan_and_missing_final_token_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "syncToken" in request.url.params:
            return httpx.Response(410)
        assert "timeMin" in request.url.params and "timeMax" in request.url.params
        return httpx.Response(200, json={"items": [], "nextSyncToken": "fresh"})

    batch = await fetch(
        GoogleSourceGateway(transport=httpx.MockTransport(handler)), "calendar", "old"
    )
    assert batch.reset and batch.cursor == "fresh"
    bad = GoogleSourceGateway(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    with pytest.raises(GoogleSourceError, match="sync token"):
        await fetch(bad, "calendar", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_provider_permission_rejection_fails_closed(status: int) -> None:
    gateway = GoogleSourceGateway(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"error": "secret provider details"})
        )
    )
    with pytest.raises(GoogleSourceAuthorizationError) as caught:
        await fetch(gateway, "gmail", "100")
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_page_budget_does_not_return_partial_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(google_sources, "MAX_PAGES", 2)
    gateway = GoogleSourceGateway(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"items": [], "nextPageToken": "more"})
        )
    )
    with pytest.raises(GoogleSourceError, match="budget"):
        await fetch(gateway, "calendar", "old")


@pytest_asyncio.fixture
async def database() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def connection_fixture(database: AsyncSession) -> Connection:
    user = User(email="owner@example.com", timezone="America/New_York")
    workspace = Workspace(name="Owner")
    database.add_all([user, workspace])
    await database.flush()
    database.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id))
    connection = Connection(
        user_id=user.id,
        workspace_id=workspace.id,
        provider="google",
        external_account_id="google1",
        external_email=user.email,
        granted_scopes=[GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE],
        status="active",
    )
    database.add(connection)
    await database.commit()
    return connection


class ExtractionGateway:
    async def extract_operational(
        self, document: SourceDocument, *, owner_email: str | None = None
    ) -> ModelExtractionResponse:
        return ModelExtractionResponse(
            provider="test",
            model="fixture",
            output={
                "observations": [
                    {
                        "observation_type": "promise",
                        "action_text": "send",
                        "object_text": "the budget",
                        "confidence": 0.99,
                        "email_relevance": {
                            "intent": "action_required",
                            "basis": "direct_request",
                            "applies_to_user": True,
                            "confidence": 0.99,
                        },
                        "evidence": [
                            {
                                "source": "content",
                                "start_char": 0,
                                "end_char": 32,
                                "text": "I will send the budget tomorrow.",
                            }
                        ],
                    }
                ],
            },
        )


@pytest.mark.asyncio
async def test_ingestion_resolves_once_and_persists_no_raw_content(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    document = gmail_document(
        message(), workspace_id=connection.workspace_id, connection_id=connection.id, now=NOW
    )

    async def token(*_: Any, **__: Any) -> str:
        return "fake-access-token"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        return SourceBatch([document], "101")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    model_calls = 0

    class CountingGateway(ExtractionGateway):
        async def extract_operational(
            self, document: SourceDocument, *, owner_email: str | None = None
        ) -> ModelExtractionResponse:
            nonlocal model_calls
            model_calls += 1
            return await super().extract_operational(document, owner_email=owner_email)

    extractor = OperationalExtractor(CountingGateway())
    first = await ingestion.process_batch_connection(
        database,
        connection_id=connection.id,
        source="gmail",
        settings=Settings(),
        extractor=extractor,
    )
    await database.commit()
    second = await ingestion.process_batch_connection(
        database,
        connection_id=connection.id,
        source="gmail",
        settings=Settings(),
        extractor=extractor,
    )
    await database.commit()
    assert first == second and len(first) == 1
    assert model_calls == 1
    receipt = await database.scalar(select(IntelligenceSourceReceipt))
    assert receipt is not None and receipt.outcome == "processed"
    assert await database.scalar(select(func.count()).select_from(OperationalObservation)) == 1
    evidence = list(await database.scalars(select(ObservationEvidence)))
    assert len(evidence) == 1
    assert "Private incidental content" not in str(evidence[0].evidence_locator)
    audits = list(await database.scalars(select(AuditEvent)))
    assert "fake-access-token" not in str([row.event_metadata for row in audits])
    cursor = await database.scalar(select(IntelligenceCursor))
    assert cursor is not None and cursor.cursor == "101"


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["scope", "paused", "membership", "status"])
async def test_ingestion_checks_authority_before_provider_fetch(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    denial: str,
) -> None:
    connection = await connection_fixture(database)
    if denial == "scope":
        connection.granted_scopes = []
    elif denial == "status":
        connection.status = "disconnected"
    elif denial == "paused":
        user = await database.get(User, connection.user_id)
        assert user is not None
        user.agent_paused = True
    else:
        member = await database.get(
            WorkspaceMembership, (connection.workspace_id, connection.user_id)
        )
        assert member is not None
        await database.delete(member)
    await database.commit()

    async def fail(*_: Any, **__: Any) -> str:
        raise AssertionError("Provider must not be called")

    monkeypatch.setattr(ingestion, "access_token_for_connection", fail)
    with pytest.raises(GoogleSourceAuthorizationError):
        await ingestion.process_batch_connection(
            database,
            connection_id=connection.id,
            source="gmail",
            settings=Settings(),
            extractor=OperationalExtractor(ExtractionGateway()),
        )


@pytest.mark.asyncio
async def test_transient_model_failure_retains_cursor_and_checkpoints_prior_document(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    database.add(IntelligenceCursor(connection_id=connection.id, source="gmail", cursor="old"))
    await database.commit()
    connection_id = connection.id
    documents = [
        gmail_document(
            message(identifier),
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            now=NOW,
        ).model_copy(update={"occurred_at": NOW + timedelta(minutes=index)})
        for index, identifier in enumerate(["m1", "m2"])
    ]

    async def token(*_: Any, **__: Any) -> str:
        return "test"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        return SourceBatch(documents, "new")

    calls: list[str] = []
    fail = True

    class BrokenGateway(ExtractionGateway):
        async def extract_operational(
            self, document: SourceDocument, *, owner_email: str | None = None
        ) -> ModelExtractionResponse:
            calls.append(document.external_id)
            if document.external_id == "m2" and fail:
                raise ValueError("model malformed")
            return await super().extract_operational(document, owner_email=owner_email)

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    with pytest.raises(ValueError, match="malformed"):
        await ingestion.process_batch_connection(
            database,
            connection_id=connection_id,
            source="gmail",
            settings=Settings(),
            extractor=OperationalExtractor(BrokenGateway()),
        )
    await database.rollback()
    cursor = await database.scalar(select(IntelligenceCursor))
    assert cursor is not None and cursor.cursor == "old"
    assert await database.scalar(select(func.count()).select_from(OperationalObservation)) == 1
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 1
    fail = False
    await ingestion.process_batch_connection(
        database,
        connection_id=connection_id,
        source="gmail",
        settings=Settings(),
        extractor=OperationalExtractor(BrokenGateway()),
    )
    await database.commit()
    assert calls == ["m1", "m2", "m2"]
    assert cursor.cursor == "new"
    assert await database.scalar(select(func.count()).select_from(OperationalObservation)) == 2


@pytest.mark.asyncio
async def test_invalid_proposal_is_quarantined_without_blocking_or_leaking_content(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    documents = [
        gmail_document(
            message(identifier),
            workspace_id=connection.workspace_id,
            connection_id=connection.id,
            now=NOW,
        ).model_copy(update={"occurred_at": NOW + timedelta(minutes=index)})
        for index, identifier in enumerate(["invalid", "valid"])
    ]
    calls: list[str] = []

    class InvalidGateway(ExtractionGateway):
        async def extract_operational(
            self, document: SourceDocument, *, owner_email: str | None = None
        ) -> ModelExtractionResponse:
            calls.append(document.external_id)
            if document.external_id == "invalid":
                return ModelExtractionResponse(
                    provider="fixture",
                    model="fixture",
                    output={"execute_action": "PRIVATE MALICIOUS PAYLOAD"},
                )
            return await super().extract_operational(document, owner_email=owner_email)

    async def token(*_: Any, **__: Any) -> str:
        return "private-access-token"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        return SourceBatch(documents, "new")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    for _ in range(2):
        ids = await ingestion.process_batch_connection(
            database,
            connection_id=connection.id,
            source="gmail",
            settings=Settings(),
            extractor=OperationalExtractor(InvalidGateway()),
        )
        await database.commit()
        assert len(ids) == 1
    assert calls == ["invalid", "valid"]
    cursor = await database.scalar(select(IntelligenceCursor))
    assert cursor is not None and cursor.cursor == "new"
    receipts = list(await database.scalars(select(IntelligenceSourceReceipt)))
    assert sorted(receipt.outcome for receipt in receipts) == ["processed", "rejected"]
    assert await database.scalar(select(func.count()).select_from(OperationalObservation)) == 1
    audits = list(await database.scalars(select(AuditEvent)))
    rejected = [row for row in audits if row.event_type == "intelligence.extraction.rejected"]
    assert len(rejected) == 1
    assert rejected[0].event_metadata["validation_error"] == {"code": "schema_invalid"}
    metadata = str([row.event_metadata for row in audits])
    assert "PRIVATE" not in metadata and "private-access-token" not in metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_outcome", ["processed", "rejected"])
async def test_empty_or_rejected_newer_revision_prevents_stale_fact_resurrection(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    newer_outcome: str,
) -> None:
    connection = await connection_fixture(database)
    document = gmail_document(
        message(), workspace_id=connection.workspace_id, connection_id=connection.id, now=NOW
    )
    database.add(
        IntelligenceSourceReceipt(
            connection_id=connection.id,
            source="gmail",
            external_id=document.external_id,
            source_hash="a" * 64,
            extractor_version="operational-extraction.v1",
            outcome=newer_outcome,
            commitment_ids=[],
            source_occurred_at=document.occurred_at + timedelta(hours=2),
        )
    )
    await database.commit()

    async def token(*_: Any, **__: Any) -> str:
        return "test"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        return SourceBatch([document], "new")

    class NoModel(ExtractionGateway):
        async def extract_operational(
            self, document: SourceDocument, *, owner_email: str | None = None
        ) -> ModelExtractionResponse:
            raise AssertionError("Stale source must be skipped before the model")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    assert (
        await ingestion.process_batch_connection(
            database,
            connection_id=connection.id,
            source="gmail",
            settings=Settings(),
            extractor=OperationalExtractor(NoModel()),
        )
        == []
    )
    await database.commit()
    assert await database.scalar(select(func.count()).select_from(Commitment)) == 0


@pytest.mark.asyncio
async def test_older_batch_does_not_overwrite_a_concurrently_advanced_cursor(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    cursor = IntelligenceCursor(connection_id=connection.id, source="gmail", cursor="old")
    database.add(cursor)
    await database.commit()

    async def token(*_: Any, **__: Any) -> str:
        return "test"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        # Simulate a newer completed batch while this older snapshot is being read.
        cursor.cursor = "newer"
        await database.commit()
        return SourceBatch([], "older-snapshot")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    await ingestion.process_batch_connection(
        database,
        connection_id=connection.id,
        source="gmail",
        settings=Settings(),
        extractor=OperationalExtractor(ExtractionGateway()),
    )
    await database.commit()
    assert cursor.cursor == "newer"


@pytest.mark.asyncio
async def test_cancelled_event_bypasses_model(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    document = calendar_document(
        {"id": "gone", "status": "cancelled"},
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        now=NOW,
    )

    async def token(*_: Any, **__: Any) -> str:
        return "test"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        return SourceBatch([document], "new")

    class NoModel(ExtractionGateway):
        async def extract_operational(
            self, document: SourceDocument, *, owner_email: str | None = None
        ) -> ModelExtractionResponse:
            raise AssertionError("Provider tombstones must not call model")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    assert (
        await ingestion.process_batch_connection(
            database,
            connection_id=connection.id,
            source="calendar",
            settings=Settings(),
            extractor=OperationalExtractor(NoModel()),
        )
        == []
    )


@pytest.mark.asyncio
async def test_refresh_scope_reduction_stops_before_source_fetch(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)

    async def narrowed_token(*_: Any, **kwargs: Any) -> str:
        kwargs["connection"].granted_scopes = ["openid"]
        return "test"

    async def forbidden(*_: Any, **__: Any) -> SourceBatch:
        raise AssertionError("Revoked read permission must stop source fetch")

    monkeypatch.setattr(ingestion, "access_token_for_connection", narrowed_token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", forbidden)
    with pytest.raises(GoogleSourceAuthorizationError) as error:
        await ingestion.process_batch_connection(
            database,
            connection_id=connection.id,
            source="gmail",
            settings=Settings(),
            extractor=OperationalExtractor(ExtractionGateway()),
        )
    assert error.value.diagnostic() == {"code": "google_scope_missing"}
    assert await database.scalar(select(func.count()).select_from(IntelligenceCursor)) == 0


@pytest.mark.asyncio
async def test_expired_cursor_revalidates_known_resource_and_recognizes_calendar_410() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path.endswith("/old-event")
        return httpx.Response(410)

    gateway = GoogleSourceGateway(transport=httpx.MockTransport(handler))
    documents = await gateway.reconcile_existing(
        source="calendar",
        access_token="test",
        workspace_id=uuid4(),
        connection_id=uuid4(),
        external_ids=["old-event"],
    )
    assert len(calls) == 1
    assert documents[0].metadata["status"] == "cancelled"


@pytest.mark.asyncio
async def test_watch_renewal_stores_lease_and_does_not_repeat_unexpired_watch(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    calls: list[httpx.Request] = []

    async def token(*_: Any, **__: Any) -> str:
        return "watch-token"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path.endswith("/watch")
        assert b"projects/test/topics/navox" in request.content
        return httpx.Response(
            200,
            json={
                "historyId": "999",
                "expiration": str(int(datetime.now(UTC).timestamp() * 1000) + 6 * 86400 * 1000),
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(
        ingestion.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    settings = Settings(google_gmail_watch_topic="projects/test/topics/navox")
    assert await ingestion.renew_source_watch(
        database, connection_id=connection.id, source="gmail", settings=settings
    )
    assert await ingestion.renew_source_watch(
        database, connection_id=connection.id, source="gmail", settings=settings
    )
    assert len(calls) == 1
    subscription = await database.scalar(select(ProviderEventSubscription))
    assert subscription is not None and subscription.status == "active"
    assert subscription.channel_token_hash != "watch-token"
    assert subscription.expiration_confirmed_at is not None
    assert await database.scalar(select(func.count()).select_from(IntelligenceCursor)) == 0


@pytest.mark.asyncio
async def test_failed_watch_registration_does_not_create_valid_lease(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)

    async def token(*_: Any, **__: Any) -> str:
        return "watch-token"

    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(
        ingestion.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(403))
        ),
    )
    with pytest.raises(GoogleSourceError, match="registration failed"):
        await ingestion.renew_source_watch(
            database,
            connection_id=connection.id,
            source="calendar",
            settings=Settings(google_calendar_push_url="https://navox.test/events"),
        )
    subscription = await database.scalar(select(ProviderEventSubscription))
    assert subscription is not None and subscription.status == "failed"
    assert subscription.expiration_confirmed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("expiration", [None, True, 1.25, "-1", "9" * 30, "1", "NaN"])
async def test_watch_never_confirms_a_malformed_or_expired_provider_lease(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch, expiration: Any
) -> None:
    connection = await connection_fixture(database)

    async def token(*_: Any, **__: Any) -> str:
        return "watch-token"

    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(
        ingestion.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"expiration": expiration})
            ),
        ),
    )
    with pytest.raises(GoogleSourceError, match="registration failed"):
        await ingestion.renew_source_watch(
            database,
            connection_id=connection.id,
            source="gmail",
            settings=Settings(google_gmail_watch_topic="projects/test/topics/navox"),
        )
    subscription = await database.scalar(select(ProviderEventSubscription))
    assert subscription is not None and subscription.status == "failed"
    assert subscription.expiration_confirmed_at is None


@pytest.mark.asyncio
async def test_watch_disconnect_during_registration_preserves_confirmed_expiry(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = await connection_fixture(database)

    async def token(*_: Any, **__: Any) -> str:
        return "watch-token"

    async def handler(request: httpx.Request) -> httpx.Response:
        subscription = await database.scalar(select(ProviderEventSubscription))
        assert subscription is not None and subscription.expiration_confirmed_at is None
        connection.status = "disconnected"
        subscription.status = "cancel_pending"
        await database.commit()
        return httpx.Response(
            200,
            json={
                "expiration": str(int((datetime.now(UTC) + timedelta(days=6)).timestamp() * 1000))
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(
        ingestion.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    assert not await ingestion.renew_source_watch(
        database,
        connection_id=connection.id,
        source="gmail",
        settings=Settings(google_gmail_watch_topic="projects/test/topics/navox"),
    )
    subscription = await database.scalar(select(ProviderEventSubscription))
    assert subscription is not None and subscription.status == "cancel_pending"
    assert subscription.expiration_confirmed_at is not None


@pytest.mark.asyncio
async def test_watch_registration_failure_preserves_disconnect_cleanup_state(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = await connection_fixture(database)

    async def token(*_: Any, **__: Any) -> str:
        return "watch-token"

    original_authorize = ingestion.authorized_connection
    calls = 0

    async def disconnect_before_registration(session: AsyncSession, connection_id, source):
        nonlocal calls
        calls += 1
        if calls == 3:
            channel = await session.scalar(select(ProviderEventSubscription))
            assert channel is not None
            channel.status = "cancel_pending"
            await session.commit()
            raise GoogleSourceAuthorizationError("Disconnected during watch registration")
        return await original_authorize(session, connection_id, source)

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(ingestion, "authorized_connection", disconnect_before_registration)
    with pytest.raises(GoogleSourceError, match="registration failed"):
        await ingestion.renew_source_watch(
            database,
            connection_id=connection.id,
            source="calendar",
            settings=Settings(google_calendar_push_url="https://navox.test/events"),
        )
    subscription = await database.scalar(select(ProviderEventSubscription))
    assert subscription is not None and subscription.status == "cancel_pending"


@pytest.mark.asyncio
async def test_watch_renewal_checks_read_permission_before_token_refresh(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    connection.granted_scopes = [GMAIL_READ_SCOPE]
    await database.commit()

    async def token(*_: Any, **__: Any) -> str:
        raise AssertionError("Unauthorized watch must not refresh credentials")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    with pytest.raises(GoogleSourceAuthorizationError):
        await ingestion.renew_source_watch(
            database,
            connection_id=connection.id,
            source="calendar",
            settings=Settings(google_calendar_push_url="https://navox.test/events"),
        )
    assert await database.scalar(select(func.count()).select_from(ProviderEventSubscription)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["valid", "wrong_account", "missing_scope", "extra_scope"])
async def test_intelligence_consent_binds_account_and_exact_read_grants(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    grant: str,
) -> None:
    connection = await connection_fixture(database)
    connection.granted_scopes = list(connections.GOOGLE_IDENTITY_SCOPES)
    await database.commit()
    user = await database.get(User, connection.user_id)
    workspace = await database.get(Workspace, connection.workspace_id)
    assert user is not None and workspace is not None
    account = CurrentAccount(user, workspace)
    settings = Settings(
        google_oauth_client_id="client",
        google_oauth_client_secret="secret",
        google_token_encryption_key=Fernet.generate_key().decode(),
    )
    start = await connections.start_google_intelligence_authorization(
        connection.id,
        account,
        database,
        settings,
    )
    query = parse_qs(urlparse(start.authorization_url).query)
    assert query["include_granted_scopes"] == ["true"]
    assert query["code_challenge_method"] == ["S256"]
    assert set(start.requested_scopes) == {
        *connections.GOOGLE_ACCOUNT_BINDING_SCOPES,
        GMAIL_READ_SCOPE,
        CALENDAR_READ_SCOPE,
    }
    assert connections.GOOGLE_GMAIL_SEND_SCOPE not in start.requested_scopes

    async def exchange(*_: Any) -> dict[str, Any]:
        scopes = list(start.requested_scopes)
        if grant == "missing_scope":
            scopes.remove(CALENDAR_READ_SCOPE)
        if grant == "extra_scope":
            scopes.append("https://www.googleapis.com/auth/drive")
        return {"access_token": "access", "refresh_token": "refresh", "scope": " ".join(scopes)}

    async def profile(*_: Any) -> dict[str, Any]:
        return {
            "sub": "other" if grant == "wrong_account" else "google1",
            "email": "owner@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(connections, "exchange_authorization_code", exchange)
    monkeypatch.setattr(connections, "fetch_google_profile", profile)
    callback = await connections.complete_google_authorization(
        account,
        database,
        settings,
        code="code",
        state_value=query["state"][0],
        error=None,
    )
    if grant == "valid":
        assert callback.headers["location"].endswith("intelligence_enabled")
        assert {GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE}.issubset(connection.granted_scopes)
        assert connection.credential_reference is not None
    else:
        expected = "account_mismatch" if grant == "wrong_account" else "scope_mismatch"
        assert callback.headers["location"].endswith(expected)
        assert GMAIL_READ_SCOPE not in connection.granted_scopes


def test_normalized_plain_text_keeps_evidence_offsets_stable() -> None:
    data = message()
    data["payload"]["parts"][0]["body"]["data"] = base64.urlsafe_b64encode(
        b"  I will send the budget tomorrow.\n\n"
    ).decode()
    document = gmail_document(data, workspace_id=uuid4(), connection_id=uuid4(), now=NOW)
    assert document.content is not None and document.content.startswith("  I will")
    assert document.content[2:34] == "I will send the budget tomorrow."


@pytest.mark.asyncio
async def test_newest_first_batch_resolves_request_before_sent_completion(
    database: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = await connection_fixture(database)
    request = SourceDocument(
        id=uuid4(),
        workspace_id=connection.workspace_id,
        provider="google",
        source_type="gmail_message",
        external_id="request",
        external_parent_id="budget-thread",
        author=SourceIdentity(identity_type="email", identity_value="manager@example.com"),
        recipients=[SourceIdentity(identity_type="email", identity_value="owner@example.com")],
        subject="Budget",
        content="Please send budget.",
        occurred_at=NOW,
        retrieved_at=NOW,
        metadata={"label_ids": ["INBOX"]},
    )
    sent = SourceDocument(
        id=uuid4(),
        workspace_id=connection.workspace_id,
        provider="google",
        source_type="gmail_message",
        external_id="sent",
        external_parent_id="budget-thread",
        author=SourceIdentity(identity_type="email", identity_value="owner@example.com"),
        recipients=[SourceIdentity(identity_type="email", identity_value="manager@example.com")],
        subject="Budget",
        content="Attached budget.",
        occurred_at=NOW + timedelta(hours=1),
        retrieved_at=NOW + timedelta(hours=1),
        metadata={"label_ids": ["SENT"]},
    )
    processed: list[str] = []

    class ChronologicalGateway:
        async def extract_operational(
            self, document: SourceDocument, *, owner_email: str | None = None
        ) -> ModelExtractionResponse:
            processed.append(document.external_id)
            assert document.content is not None
            return ModelExtractionResponse(
                provider="test",
                model="fixture",
                output={
                    "observations": [
                        {
                            "observation_type": "request"
                            if document.external_id == "request"
                            else "completion",
                            "action_text": "send",
                            "object_text": "budget",
                            "confidence": 0.99,
                            "email_relevance": {
                                "intent": "action_required"
                                if document.external_id == "request"
                                else "commitment_update",
                                "basis": "direct_request"
                                if document.external_id == "request"
                                else "commitment_progress",
                                "applies_to_user": True,
                                "confidence": 0.99,
                            },
                            "evidence": [
                                {
                                    "source": "content",
                                    "start_char": 0,
                                    "end_char": len(document.content),
                                    "text": document.content,
                                }
                            ],
                        }
                    ],
                },
            )

    async def token(*_: Any, **__: Any) -> str:
        return "test"

    async def batch(*_: Any, **__: Any) -> SourceBatch:
        return SourceBatch([sent, request], "safely-processed")

    monkeypatch.setattr(ingestion, "access_token_for_connection", token)
    monkeypatch.setattr(GoogleSourceGateway, "fetch", batch)
    ids = await ingestion.process_batch_connection(
        database,
        connection_id=connection.id,
        source="gmail",
        settings=Settings(),
        extractor=OperationalExtractor(ChronologicalGateway()),
    )
    await database.commit()
    assert processed == ["request", "sent"]
    assert len(ids) == 1
    commitment = await database.get(Commitment, ids[0])
    assert commitment is not None and commitment.status == "completed"
    assert await database.scalar(select(func.count()).select_from(Commitment)) == 1
    cursor = await database.scalar(select(IntelligenceCursor))
    assert cursor is not None and cursor.cursor == "safely-processed"
