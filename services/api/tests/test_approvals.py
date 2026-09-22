import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.actions import get_approval_dispatcher
from navox.api.main import app
from navox.approvals import execution
from navox.approvals.dispatcher import ApprovalDispatcher
from navox.approvals.execution import execute_approved_gmail_send
from navox.core.credential_vault import CredentialVault
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    Action,
    Approval,
    Connection,
    ConnectionCredential,
    Plan,
    PlanStep,
    WorkflowRef,
)
from navox.db.session import get_database_session
from navox.providers import google_gmail
from navox.providers.google_gmail import (
    GmailProviderError,
    GmailSendPayload,
    GmailSendReceipt,
    GoogleGmailGateway,
)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class FakeApprovalDispatcher(ApprovalDispatcher):
    async def ensure_started(
        self,
        database: AsyncSession,
        *,
        action: Action,
        settings: Settings,
    ) -> WorkflowRef:
        del settings
        existing = await database.scalar(
            select(WorkflowRef).where(
                WorkflowRef.entity_type == "action",
                WorkflowRef.entity_id == action.id,
                WorkflowRef.workflow_type == "approved_action",
            )
        )
        if existing is None:
            existing = WorkflowRef(
                user_id=action.user_id,
                workspace_id=action.workspace_id,
                entity_type="action",
                entity_id=action.id,
                workflow_type="approved_action",
                temporal_workflow_id=f"test-approved-{action.id}",
                status="running",
            )
            database.add(existing)
            await database.commit()
        return existing

    async def signal(
        self,
        *,
        action_id: UUID,
        decision: str,
        settings: Settings,
    ) -> None:
        del action_id, decision, settings


class FakeGmailGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[GmailSendPayload] = []

    async def send(
        self,
        *,
        access_token: str,
        payload: GmailSendPayload,
        idempotency_key: str,
    ) -> GmailSendReceipt:
        assert access_token == "test-access-token"
        assert idempotency_key.startswith("gmail-send:")
        self.calls.append(payload)
        if self.fail:
            raise GmailProviderError("ambiguous")
        return GmailSendReceipt(message_id="gmail-message-123", thread_id="thread-456")


@pytest_asyncio.fixture
async def approval_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        app_environment="test",
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-client-secret",
        google_token_encryption_key=Fernet.generate_key().decode(),
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_approval_dispatcher] = lambda: FakeApprovalDispatcher()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, session_factory, settings

    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_approval_dispatcher, None)
    await engine.dispose()


async def register(client: AsyncClient, email: str = "owner@example.com") -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "twelve-character-password",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201
    return response.json()


async def add_google_connection(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    account: dict[str, object],
    *,
    include_send_scope: bool = True,
) -> UUID:
    workspace = account["workspace"]
    assert isinstance(workspace, dict)
    async with session_factory() as session:
        credential = ConnectionCredential(
            encrypted_refresh_token=CredentialVault(settings).seal_refresh_token("refresh-token")
        )
        session.add(credential)
        await session.flush()
        connection = Connection(
            user_id=UUID(str(account["id"])),
            workspace_id=UUID(str(workspace["id"])),
            provider="google",
            external_account_id=f"google-{account['id']}",
            external_email="owner@example.com",
            status="active",
            granted_scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                *([GMAIL_SEND_SCOPE] if include_send_scope else []),
            ],
            credential_reference=credential.id,
        )
        session.add(connection)
        await session.commit()
        return connection.id


async def create_commitment(client: AsyncClient) -> dict[str, object]:
    response = await client.post(
        "/api/v1/commitments",
        json={
            "request_id": str(uuid4()),
            "type": "follow_up",
            "title": "Follow up with advisor",
            "priority": 4,
            "due_at": None,
        },
    )
    assert response.status_code == 201
    return response.json()


async def prepare_action(
    client: AsyncClient,
    *,
    commitment_id: str,
    connection_id: UUID,
    request_id: UUID | None = None,
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/commitments/{commitment_id}/actions/gmail-send/prepare",
        json={
            "request_id": str(request_id or uuid4()),
            "connection_id": str(connection_id),
            "to": "advisor@example.edu",
            "subject": "Following up",
            "body_text": "Hello, I wanted to follow up on our conversation.",
            "post_send_state": "waiting",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def approve_action(client: AsyncClient, action_id: str, request_id: UUID | None = None):
    return await client.post(
        f"/api/v1/actions/{action_id}/approve",
        json={"request_id": str(request_id or uuid4())},
    )


@pytest.mark.asyncio
async def test_prepare_requires_exact_gmail_scope_and_never_sends(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(
        session_factory,
        settings,
        account,
        include_send_scope=False,
    )

    blocked = await client.post(
        f"/api/v1/commitments/{commitment['id']}/actions/gmail-send/prepare",
        json={
            "request_id": str(uuid4()),
            "connection_id": str(connection_id),
            "to": "advisor@example.edu",
            "subject": "Following up",
            "body_text": "Hello.",
            "post_send_state": "waiting",
        },
    )
    assert blocked.status_code == 403
    assert "permission" in blocked.json()["detail"].lower()


@pytest.mark.asyncio
async def test_exact_approval_executes_once_verifies_and_updates_commitment(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )

    assert prepared["risk_level"] == "R3"
    assert prepared["requires_approval"] is True
    assert prepared["status"] == "awaiting_approval"
    assert prepared["approval"]["status"] == "pending"
    assert prepared["payload"]["sender"] == "owner@example.com"

    approved = await approve_action(client, prepared["id"])
    assert approved.status_code == 200
    assert approved.json()["approval"]["status"] == "approved"

    async def token(*args: object, **kwargs: object) -> str:
        return "test-access-token"

    monkeypatch.setattr(execution, "access_token_for_connection", token)
    gateway = FakeGmailGateway()

    async with session_factory() as session:
        result = await execute_approved_gmail_send(
            session,
            action_id=UUID(prepared["id"]),
            settings=settings,
            gateway=gateway,
        )
        repeated = await execute_approved_gmail_send(
            session,
            action_id=UUID(prepared["id"]),
            settings=settings,
            gateway=gateway,
        )
        action = await session.get(Action, UUID(prepared["id"]))
        assert action is not None
        plan_step = await session.get(PlanStep, action.plan_step_id)
        assert plan_step is not None
        plan = await session.get(Plan, plan_step.plan_id)

    assert result == "completed"
    assert repeated == "completed"
    assert len(gateway.calls) == 1
    assert action.status == "completed"
    assert action.verified_at is not None
    assert action.result["message_id"] == "gmail-message-123"
    assert plan is not None
    assert plan.status == "completed"

    today = await client.get("/api/v1/today", params={"timezone": "UTC"})
    assert today.status_code == 200
    assert today.json()["waiting_on"][0]["id"] == commitment["id"]


@pytest.mark.asyncio
async def test_payload_tamper_after_approval_is_blocked_before_provider(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )
    assert (await approve_action(client, prepared["id"])).status_code == 200

    async with session_factory() as session:
        action = await session.get(Action, UUID(prepared["id"]))
        assert action is not None
        action.payload = {**action.payload, "to": "attacker@example.com"}
        await session.commit()

    async def token(*args: object, **kwargs: object) -> str:
        return "test-access-token"

    monkeypatch.setattr(execution, "access_token_for_connection", token)
    gateway = FakeGmailGateway()
    async with session_factory() as session:
        result = await execute_approved_gmail_send(
            session,
            action_id=UUID(prepared["id"]),
            settings=settings,
            gateway=gateway,
        )
    assert result == "blocked"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_ambiguous_provider_failure_never_auto_retries(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )
    assert (await approve_action(client, prepared["id"])).status_code == 200

    async def token(*args: object, **kwargs: object) -> str:
        return "test-access-token"

    monkeypatch.setattr(execution, "access_token_for_connection", token)
    gateway = FakeGmailGateway(fail=True)
    async with session_factory() as session:
        first = await execute_approved_gmail_send(
            session,
            action_id=UUID(prepared["id"]),
            settings=settings,
            gateway=gateway,
        )
        second = await execute_approved_gmail_send(
            session,
            action_id=UUID(prepared["id"]),
            settings=settings,
            gateway=gateway,
        )
    assert first == "uncertain"
    assert second == "uncertain"
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_pause_invalidates_approved_action_and_requires_fresh_approval(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )
    assert (await approve_action(client, prepared["id"])).status_code == 200
    assert (await client.post("/api/v1/agent/pause")).status_code == 200

    async def token(*args: object, **kwargs: object) -> str:
        return "test-access-token"

    monkeypatch.setattr(execution, "access_token_for_connection", token)
    gateway = FakeGmailGateway()
    async with session_factory() as session:
        result = await execute_approved_gmail_send(
            session,
            action_id=UUID(prepared["id"]),
            settings=settings,
            gateway=gateway,
        )
        approvals = list(
            await session.scalars(
                select(Approval)
                .where(Approval.action_id == UUID(prepared["id"]))
                .order_by(Approval.version)
            )
        )

    assert result == "awaiting_approval"
    assert gateway.calls == []
    assert [approval.status for approval in approvals] == ["superseded", "pending"]


@pytest.mark.asyncio
async def test_edit_supersedes_old_approval_and_changes_exact_hash(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )
    old_hash = prepared["payload_hash"]

    edited = await client.post(
        f"/api/v1/actions/{prepared['id']}/edit",
        json={
            "to": "new-recipient@example.edu",
            "subject": "Updated subject",
            "body_text": "Updated exact body.",
            "post_send_state": "unchanged",
        },
    )
    assert edited.status_code == 200
    body = edited.json()
    assert body["payload_hash"] != old_hash
    assert body["approval"]["version"] == 2
    assert body["approval"]["status"] == "pending"

    async with session_factory() as session:
        approvals = list(
            await session.scalars(
                select(Approval)
                .where(Approval.action_id == UUID(prepared["id"]))
                .order_by(Approval.version)
            )
        )
    assert [approval.status for approval in approvals] == ["superseded", "pending"]


@pytest.mark.asyncio
async def test_action_routes_are_tenant_scoped(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = approval_environment
    first = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, first)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )

    await register(client, "other@example.com")
    assert (await client.get(f"/api/v1/actions/{prepared['id']}")).status_code == 404
    assert (
        await client.post(
            f"/api/v1/actions/{prepared['id']}/approve",
            json={"request_id": str(uuid4())},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_expired_approval_cannot_be_used(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )

    async with session_factory() as session:
        approval = await session.scalar(
            select(Approval).where(Approval.action_id == UUID(prepared["id"]))
        )
        assert approval is not None
        approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    response = await approve_action(client, prepared["id"])
    assert response.status_code == 409
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_opposite_decision_cannot_reuse_decision_request_id(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)
    prepared = await prepare_action(
        client,
        commitment_id=commitment["id"],
        connection_id=connection_id,
    )
    decision_id = uuid4()
    rejected = await client.post(
        f"/api/v1/actions/{prepared['id']}/reject",
        json={"request_id": str(decision_id)},
    )
    assert rejected.status_code == 200

    approve = await client.post(
        f"/api/v1/actions/{prepared['id']}/approve",
        json={"request_id": str(decision_id)},
    )
    assert approve.status_code == 409


@pytest.mark.asyncio
async def test_google_gateway_builds_base64url_mime_and_requires_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "message-1", "threadId": "thread-1"}

    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def post(self, url: str, **kwargs: object) -> Response:
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(google_gmail.httpx, "AsyncClient", Client)
    receipt = await GoogleGmailGateway().send(
        access_token="token",
        payload=GmailSendPayload(
            sender="owner@example.com",
            to="person@example.com",
            subject="Subject",
            body_text="Body",
        ),
        idempotency_key="action-1",
    )
    assert receipt.message_id == "message-1"
    body = captured["json"]
    assert isinstance(body, dict)
    raw = body["raw"]
    assert isinstance(raw, str)
    parsed = message_from_bytes(base64.urlsafe_b64decode(raw.encode()))
    assert parsed["From"] == "owner@example.com"
    assert parsed["To"] == "person@example.com"
    assert parsed["Subject"] == "Subject"
    assert "Body" in parsed.get_payload()



@pytest.mark.asyncio
async def test_subject_header_injection_is_rejected(
    approval_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession], Settings],
) -> None:
    client, session_factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(session_factory, settings, account)

    response = await client.post(
        f"/api/v1/commitments/{commitment['id']}/actions/gmail-send/prepare",
        json={
            "request_id": str(uuid4()),
            "connection_id": str(connection_id),
            "to": "advisor@example.edu",
            "subject": "Hello\nBcc: attacker@example.com",
            "body_text": "Safe body.",
            "post_send_state": "waiting",
        },
    )
    assert response.status_code == 422
