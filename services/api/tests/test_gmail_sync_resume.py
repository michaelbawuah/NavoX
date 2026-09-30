"""Real ingestion resumes provider reads without retaining private message bodies."""

import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.ai.errors import AIProviderError
from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    Commitment,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    GmailSyncPlan,
    IntelligenceCursor,
    IntelligenceSourceReceipt,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence import gmail_sync, ingestion
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import ModelExtractionResponse, OperationalExtractor
from navox.providers.google_sources import (
    GMAIL_READ_SCOPE,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
    GoogleSourceGateway,
)

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
PRIVATE_BODY = "Private incidental message content: resume-test-body-secret"
PRIVATE_TOKEN = "resume-test-token-secret"


@pytest_asyncio.fixture
async def database() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def account(database: AsyncSession, *, cursor: str | None = "old") -> UUID:
    user = User(email="owner@example.com", timezone="America/New_York")
    workspace = Workspace(name="Owner")
    database.add_all([user, workspace])
    await database.flush()
    database.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id))
    connection = Connection(
        user_id=user.id,
        workspace_id=workspace.id,
        provider="google",
        external_account_id="google-resume-owner",
        external_email=user.email,
        granted_scopes=[GMAIL_READ_SCOPE],
        status="active",
    )
    database.add(connection)
    await database.flush()
    if cursor is not None:
        database.add(IntelligenceCursor(connection_id=connection.id, source="gmail", cursor=cursor))
    await database.commit()
    return connection.id


def mail(
    identifier: str,
    offset: int,
    *,
    body: str = PRIVATE_BODY,
    sent: bool = False,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "threadId": "budget-thread",
        "internalDate": str(int((NOW + timedelta(minutes=offset)).timestamp() * 1000)),
        "labelIds": ["SENT" if sent else "INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "owner@example.com" if sent else "manager@example.com"},
                {"name": "To", "value": "manager@example.com" if sent else "owner@example.com"},
                {"name": "Subject", "value": "Budget"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


@dataclass
class Mailbox:
    messages: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {"first": mail("first", 0), "second": mail("second", 1)}
    )
    listed: list[str] = field(default_factory=lambda: ["second", "first"])
    fail_at: tuple[str, str] | None = None
    expired: bool = False
    paginated: bool = False
    fail_page: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)
    time: float = 0.0

    async def sleep(self, seconds: float) -> None:
        self.time += seconds

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {PRIVATE_TOKEN}"
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        mode = request.url.params.get("format", request.url.params.get("pageToken", ""))
        if mode == "full" and request.url.params.get("fields") == "id,internalDate":
            mode = "metadata"
        self.calls.append((path, mode))
        if self.fail_at == (path, mode) or (self.fail_page and mode == "page-2"):
            return httpx.Response(
                403,
                headers={"Retry-After": "120"},
                json={
                    "error": {
                        "message": PRIVATE_BODY,
                        "errors": [{"reason": "userRateLimitExceeded"}],
                    }
                },
            )
        if path == "/profile":
            return httpx.Response(200, json={"historyId": "new"})
        if path == "/history":
            if self.expired:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "historyId": "new",
                    "history": [
                        {"messagesAdded": [{"message": {"id": item}} for item in self.listed]}
                    ],
                },
            )
        if path == "/messages":
            identifiers = self.listed
            payload: dict[str, Any] = {}
            if self.paginated:
                identifiers = self.listed[1:] if mode == "page-2" else self.listed[:1]
                if mode != "page-2":
                    payload["nextPageToken"] = "page-2"
            payload["messages"] = [{"id": item} for item in identifiers]
            return httpx.Response(200, json=payload)
        assert path.startswith("/messages/"), str(request.url)
        identifier = path.rsplit("/", 1)[-1]
        if identifier not in self.messages:
            return httpx.Response(404)
        message = self.messages[identifier]
        if mode == "metadata":
            assert "payload" not in request.url.params.get("fields", "")
            return httpx.Response(
                200,
                json={key: value for key, value in message.items() if key != "payload"},
            )
        assert mode == "full"
        return httpx.Response(200, json=message)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Durable pacing has separate concurrency coverage; provider retry sleeps
        # below use virtual time so these progress tests perform no wall-clock waits.
        monkeypatch.setattr(gmail_sync, "READ_SPACING_SECONDS", 0)
        gateway = GoogleSourceGateway(
            transport=httpx.MockTransport(self.handle),
            sleep=self.sleep,
            monotonic=lambda: self.time,
            now=lambda: NOW + timedelta(seconds=self.time),
            jitter=lambda: 0.0,
        )

        async def token(*_: Any, **__: Any) -> str:
            return PRIVATE_TOKEN

        monkeypatch.setattr(ingestion, "GoogleSourceGateway", lambda: gateway)
        monkeypatch.setattr(ingestion, "access_token_for_connection", token)


class Model:
    def __init__(
        self,
        *,
        fail_id: str | None = None,
        observations: bool = False,
        failure: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.fail_id = fail_id
        self.observations = observations
        self.failure = failure

    async def extract_operational(
        self, document: SourceDocument, *, owner_email: str | None = None
    ) -> ModelExtractionResponse:
        self.calls.append(document.external_id)
        if document.external_id == self.fail_id:
            raise self.failure or RuntimeError("temporary model failure")
        observations = []
        if self.observations:
            assert document.content is not None
            observations.append(
                {
                    "observation_type": "completion"
                    if document.external_id == "sent"
                    else "request",
                    "action_text": "send",
                    "object_text": "budget",
                    "confidence": 0.99,
                    "email_relevance": {
                        "intent": "commitment_update"
                        if document.external_id == "sent"
                        else "action_required",
                        "basis": "commitment_progress"
                        if document.external_id == "sent"
                        else "direct_request",
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
            )
        return ModelExtractionResponse(
            provider="fixture", model="resume-test", output={"observations": observations}
        )


async def run(database: AsyncSession, connection_id: UUID, model: Model) -> list[UUID]:
    # These cases exercise *resumption after the retry window*, not scheduling.
    # The shared runtime now persists that deadline; expire it explicitly instead
    # of adding wall-clock sleeps. Dedicated migration tests cover early retries.
    connections = await database.scalars(
        select(ConnectorConnection).where(ConnectorConnection.legacy_connection_id == connection_id)
    )
    # Expire the deadline on the clock the runtime claims against; a host-clock
    # deadline can stay in the future when the database clock lags the test host.
    deadline = await database_now(database) - timedelta(seconds=1)
    for connector in connections:
        if connector.retry_not_before is not None:
            connector.retry_not_before = deadline
    await database.commit()
    result = await ingestion.process_connection(
        database,
        connection_id=connection_id,
        source="gmail",
        settings=Settings(),
        extractor=OperationalExtractor(model),
    )
    await database.commit()
    return result


async def current_cursor(database: AsyncSession) -> str | None:
    return await database.scalar(select(IntelligenceCursor.cursor))


@pytest.mark.asyncio
async def test_expired_page_token_resets_only_incomplete_enumeration(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database, cursor=None)

    class ExpiringPages(Mailbox):
        reject_page = True

        def handle(self, request: httpx.Request) -> httpx.Response:
            if self.reject_page and request.url.params.get("pageToken") == "page-2":
                self.calls.append(("/messages", "page-2"))
                return httpx.Response(400, json={"error": {"message": "Invalid page token"}})
            return super().handle(request)

    mailbox = ExpiringPages(paginated=True)
    mailbox.install(monkeypatch)
    model = Model()
    with pytest.raises(GoogleSourceError) as raised:
        await run(database, connection_id, model)
    assert raised.value.http_status == 400
    await database.rollback()
    plan = await database.scalar(select(GmailSyncPlan))
    assert plan is not None and plan.phase == "list"
    assert plan.entries == [] and plan.pages == 0 and plan.page_token is None
    assert plan.initial_cursor is None and plan.cursor == "new" and not plan.reset
    anchor = plan.created_at
    plan_id = plan.id
    assert await current_cursor(database) is None
    assert model.calls == []
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 0

    mailbox.reject_page = False
    mailbox.calls.clear()
    await run(database, connection_id, model)
    assert mailbox.calls[:2] == [("/messages", ""), ("/messages", "page-2")]
    assert all(path != "/profile" for path, _ in mailbox.calls)
    assert model.calls == ["first", "second"]
    assert await current_cursor(database) == "new"
    assert await database.scalar(select(GmailSyncPlan)) is None
    assert plan.created_at == anchor and plan.id == plan_id


@pytest.mark.asyncio
async def test_late_full_read_failure_resumes_only_pending_message(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database)
    mailbox = Mailbox(fail_at=("/messages/second", "full"))
    mailbox.install(monkeypatch)
    model = Model()
    with pytest.raises(GoogleSourceError):
        await run(database, connection_id, model)
    await database.rollback()
    assert await current_cursor(database) == "old"
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 1
    plan = await database.scalar(select(GmailSyncPlan))
    assert plan is not None and plan.phase == "process" and plan.position == 1
    stored_plan = {
        column.name: getattr(plan, column.name) for column in GmailSyncPlan.__table__.columns
    }
    audits = list(await database.scalars(select(AuditEvent)))
    persisted = json.dumps([stored_plan, [event.event_metadata for event in audits]], default=str)
    assert PRIVATE_BODY not in persisted and PRIVATE_TOKEN not in persisted

    mailbox.fail_at = None
    mailbox.calls.clear()
    await run(database, connection_id, model)
    assert mailbox.calls == [("/messages/second", "full")]
    assert model.calls == ["first", "second"]
    assert await current_cursor(database) == "new"
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 2
    assert await database.scalar(select(GmailSyncPlan)) is None


@pytest.mark.asyncio
async def test_metadata_failure_resumes_without_relisting_or_rereading_completed_metadata(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database)
    mailbox = Mailbox(listed=["first", "second"], fail_at=("/messages/second", "metadata"))
    mailbox.install(monkeypatch)
    model = Model()
    with pytest.raises(GoogleSourceError):
        await run(database, connection_id, model)
    await database.rollback()
    assert await current_cursor(database) == "old"
    assert model.calls == []
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 0
    mailbox.fail_at = None
    mailbox.calls.clear()
    await run(database, connection_id, model)
    assert mailbox.calls == [
        ("/messages/second", "metadata"),
        ("/messages/first", "full"),
        ("/messages/second", "full"),
    ]
    assert model.calls == ["first", "second"]


@pytest.mark.asyncio
async def test_bootstrap_page_failure_preserves_anchor_and_completed_page(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database, cursor=None)
    mailbox = Mailbox(paginated=True, fail_page=True)
    mailbox.install(monkeypatch)
    model = Model()
    with pytest.raises(GoogleSourceError):
        await run(database, connection_id, model)
    await database.rollback()
    assert await current_cursor(database) is None
    mailbox.fail_page = False
    mailbox.calls.clear()
    await run(database, connection_id, model)
    assert mailbox.calls[0] == ("/messages", "page-2")
    assert ("/profile", "") not in mailbox.calls
    assert ("/messages", "") not in mailbox.calls
    assert model.calls == ["first", "second"]
    assert await current_cursor(database) == "new"


@pytest.mark.asyncio
async def test_newest_first_provider_list_still_resolves_request_before_completion(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database, cursor=None)
    mailbox = Mailbox(
        messages={
            "request": mail("request", 0, body="Please send budget."),
            "sent": mail("sent", 60, body="Attached budget.", sent=True),
        },
        listed=["sent", "request"],
    )
    mailbox.install(monkeypatch)
    model = Model(observations=True)
    ids = await run(database, connection_id, model)
    assert model.calls == ["request", "sent"]
    assert len(ids) == 1
    commitment = await database.get(Commitment, ids[0])
    assert commitment is not None and commitment.status == "completed"
    assert await database.scalar(select(func.count()).select_from(Commitment)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["scope", "paused", "membership", "disconnected"])
async def test_saved_progress_never_bypasses_current_authority(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    connection_id = await account(database)
    mailbox = Mailbox(fail_at=("/messages/second", "full"))
    mailbox.install(monkeypatch)
    model = Model()
    with pytest.raises(GoogleSourceError):
        await run(database, connection_id, model)
    await database.rollback()
    connection = await database.get(Connection, connection_id)
    assert connection is not None
    if denial == "scope":
        connection.granted_scopes = []
    elif denial == "disconnected":
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
    mailbox.calls.clear()
    mailbox.fail_at = None
    with pytest.raises(GoogleSourceAuthorizationError):
        await run(database, connection_id, model)
    assert mailbox.calls == []
    assert model.calls == ["first"]
    assert await current_cursor(database) == "old"


@pytest.mark.asyncio
async def test_pause_during_metadata_read_stops_before_next_provider_read(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database)
    mailbox = Mailbox(listed=["first", "second"])
    mailbox.install(monkeypatch)

    async def handle(request: httpx.Request) -> httpx.Response:
        response = mailbox.handle(request)
        if mailbox.calls[-1] == ("/messages/first", "metadata"):
            connection = await database.get(Connection, connection_id)
            assert connection is not None
            user = await database.get(User, connection.user_id)
            assert user is not None
            user.agent_paused = True
            await database.commit()
        return response

    gateway = GoogleSourceGateway(
        transport=httpx.MockTransport(handle),
        sleep=mailbox.sleep,
        monotonic=lambda: mailbox.time,
        now=lambda: NOW + timedelta(seconds=mailbox.time),
        jitter=lambda: 0.0,
    )
    monkeypatch.setattr(ingestion, "GoogleSourceGateway", lambda: gateway)
    model = Model()
    with pytest.raises(GoogleSourceAuthorizationError):
        await run(database, connection_id, model)
    assert mailbox.calls == [("/history", ""), ("/messages/first", "metadata")]
    assert model.calls == []
    assert await current_cursor(database) == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("temporary model failure"),
        AIProviderError("temporary model failure", code="timeout"),
    ],
    ids=["runtime", "ai-timeout"],
)
async def test_model_failure_keeps_receipt_and_retries_only_unprocessed_body(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    connection_id = await account(database)
    mailbox = Mailbox()
    mailbox.install(monkeypatch)
    model = Model(fail_id="second", failure=failure)
    with pytest.raises(RuntimeError, match="temporary model failure"):
        await run(database, connection_id, model)
    await database.rollback()
    assert await current_cursor(database) == "old"
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 1
    plan = await database.scalar(select(GmailSyncPlan))
    assert plan is not None and plan.position == 1 and plan.rejected == 0
    mailbox.calls.clear()
    model.fail_id = None
    await run(database, connection_id, model)
    assert mailbox.calls == [("/messages/second", "full")]
    assert model.calls == ["first", "second", "second"]
    assert await database.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 2


@pytest.mark.asyncio
async def test_expired_history_reconciles_known_ids_outside_bootstrap_and_deletions(
    database: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection_id = await account(database)
    connection = await database.get(Connection, connection_id)
    assert connection is not None
    for identifier in ["known-old", "known-gone"]:
        commitment = Commitment(
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            commitment_type="task",
            title="Known historical source",
            dedupe_key=identifier,
        )
        database.add(commitment)
        await database.flush()
        database.add(
            CommitmentSource(
                commitment_id=commitment.id,
                connection_id=connection_id,
                provider="google",
                source_type="gmail_message",
                external_resource_id=identifier,
            )
        )
    await database.commit()
    mailbox = Mailbox(
        messages={"fresh": mail("fresh", 0), "known-old": mail("known-old", -40 * 24 * 60)},
        listed=["fresh"],
        expired=True,
    )
    mailbox.install(monkeypatch)
    model = Model()
    await run(database, connection_id, model)
    assert model.calls == ["known-old", "fresh"]
    assert ("/messages/known-old", "full") in mailbox.calls
    assert ("/messages/known-gone", "metadata") in mailbox.calls
    assert ("/messages/known-gone", "full") not in mailbox.calls
    receipt = await database.scalar(
        select(IntelligenceSourceReceipt).where(
            IntelligenceSourceReceipt.external_id == "known-gone"
        )
    )
    assert receipt is not None and receipt.extractor_version == "provider-tombstone.v1"
    assert await current_cursor(database) == "new"
