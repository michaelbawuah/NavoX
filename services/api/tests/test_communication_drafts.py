import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_approvals import (
    FakeApprovalDispatcher,
    FakeGmailGateway,
    add_google_connection,
    create_commitment,
    register,
)

from navox.ai.foundation.contracts import (
    AIResult,
    FinishReason,
    JSONDocument,
    Provider,
    ProviderGrant,
    Sensitivity,
)
from navox.ai.routing import PolicyRules
from navox.api import communication
from navox.api.actions import get_approval_dispatcher
from navox.api.main import create_app
from navox.approvals import execution
from navox.approvals.execution import execute_approved_gmail_send
from navox.communication import generation
from navox.core.settings import Settings, get_settings
from navox.db.communications import CommunicationDraft, CommunicationDraftVersion
from navox.db.models import Approval, CommitmentSource, Connection
from navox.db.session import get_database_session
from navox.intelligence.contracts import SourceDocument


@pytest_asyncio.fixture
async def approval_environment(ai_database):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        google_oauth_client_id="fixture-client",
        google_oauth_client_secret="fixture-client-secret",
        google_token_encryption_key=Fernet.generate_key().decode(),
    )
    app = create_app()

    async def database():
        async with ai_database() as db:
            yield db

    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_approval_dispatcher] = lambda: FakeApprovalDispatcher()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, ai_database, settings


class DraftRuntime:
    def __init__(self):
        self.store = SimpleNamespace(
            operator_policy=PolicyRules(
                grants=(
                    ProviderGrant(
                        provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PERSONAL})
                    ),
                )
            )
        )
        self.calls = []

    async def execute(self, task, *, context_builder, documents, semantic_validator, user_request):
        from navox.ai.prompts import COMMUNICATION_PROMPT_V2

        assert task.prompt == COMMUNICATION_PROMPT_V2
        assert task.output_schema.name == "communication_draft"
        assert task.output_schema.version == "v1"
        context = await context_builder.build(task, documents, user_request=user_request)
        self.calls.append(context)
        output = {"subject": "Friday meeting", "body": "Does Friday work for you?"}
        semantic_validator(output)
        return AIResult(
            task_id=task.id,
            workspace_id=task.workspace_id,
            user_id=task.user_id,
            trace_id=task.trace_id,
            provider=Provider.OPENAI,
            model="fixture-draft",
            output=JSONDocument(text=json.dumps(output)),
            finish_reason=FinishReason.STOP,
            latency_ms=1,
            prompt=task.prompt,
            output_schema=task.output_schema,
            schema_validated=True,
            semantic_validated=True,
            policy_validated=True,
        )


async def setup(approval_environment, monkeypatch):
    client, factory, settings = approval_environment
    account = await register(client)
    commitment = await create_commitment(client)
    connection_id = await add_google_connection(factory, settings, account)
    async with factory() as db:
        connection = await db.get(Connection, connection_id)
        connection.granted_scopes = [
            *connection.granted_scopes,
            "https://www.googleapis.com/auth/gmail.readonly",
        ]
        source = CommitmentSource(
            commitment_id=UUID(commitment["id"]),
            connection_id=connection_id,
            provider="google",
            source_type="gmail_message",
            external_resource_id="fixture-message",
        )
        db.add(source)
        await db.commit()
        source_id = source.id
    runtime = DraftRuntime()

    async def configured(_):
        return runtime

    async def token(*args, **kwargs):
        return "test-access-token"

    async def message(self, access_token, *, workspace_id, connection_id, external_id, now):
        return SourceDocument(
            id=uuid4(),
            workspace_id=workspace_id,
            provider="google",
            source_type="gmail_message",
            external_id=external_id,
            subject="Meeting",
            content="Can we meet on Friday?",
            occurred_at=now,
            retrieved_at=now,
        )

    monkeypatch.setattr(communication, "build_runtime", configured)
    monkeypatch.setattr(generation, "access_token_for_connection", token)
    monkeypatch.setattr(execution, "access_token_for_connection", token)
    monkeypatch.setattr(generation.GoogleSourceGateway, "gmail_message", message)
    response = await client.post(
        "/api/v1/communication-drafts",
        json={
            "commitment_id": commitment["id"],
            "source_id": str(source_id),
            "recipient": "maya@example.com",
            "instructions": "Ask whether Friday works.",
        },
    )
    assert response.status_code == 201, response.text
    assert len(runtime.calls) == 1
    return client, factory, settings, response.json(), connection_id, runtime


async def prepare(client, draft, connection_id):
    response = await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/prepare",
        json={
            "expected_version": draft["current_version"],
            "connection_id": str(connection_id),
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def approve(client, draft, action, **overrides):
    return await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/approve",
        json={
            "request_id": str(uuid4()),
            "draft_version": draft["current_version"],
            "expected_payload_hash": action["payload_hash"],
            **overrides,
        },
    )


@pytest.mark.asyncio
async def test_generated_edited_draft_sends_exact_final_version_and_verifies(
    approval_environment, monkeypatch
):
    client, factory, settings, draft, connection, runtime = await setup(
        approval_environment, monkeypatch
    )
    edit = await client.patch(
        f"/api/v1/communication-drafts/{draft['id']}",
        json={
            "expected_version": 1,
            "to": ["maya@example.com"],
            "subject": "Friday afternoon",
            "body": "Would Friday afternoon work for you?",
        },
    )
    assert edit.status_code == 200, edit.text
    draft = edit.json()
    assert draft["current_version"] == 2 and len(draft["versions"]) == 2
    assert draft["versions"][1]["created_by"] == "USER"
    action = await prepare(client, draft, connection)
    assert (await approve(client, draft, action)).status_code == 200
    gateway = FakeGmailGateway()
    async with factory() as db:
        assert (
            await execute_approved_gmail_send(
                db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
            )
            == "completed"
        )
        assert (
            await execute_approved_gmail_send(
                db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
            )
            == "completed"
        )
        assert (await db.get(CommunicationDraft, UUID(draft["id"]))).status == "sent"
    assert len(gateway.calls) == 1
    assert gateway.calls[0].subject == "Friday afternoon"
    assert gateway.calls[0].body_text == "Would Friday afternoon work for you?"
    assert gateway.calls[0].to == "maya@example.com"
    assert len(runtime.calls) == 1  # approval/sending never regenerates the draft


@pytest.mark.asyncio
async def test_edit_after_approval_invalidates_and_blocks_old_send(
    approval_environment, monkeypatch
):
    client, factory, settings, draft, connection, _ = await setup(approval_environment, monkeypatch)
    action = await prepare(client, draft, connection)
    assert (await approve(client, draft, action)).status_code == 200
    changed = await client.patch(
        f"/api/v1/communication-drafts/{draft['id']}",
        json={
            "expected_version": 1,
            "to": ["other@example.com"],
            "subject": "Changed",
            "body": "New exact content.",
        },
    )
    assert changed.status_code == 200, changed.text
    gateway = FakeGmailGateway()
    async with factory() as db:
        assert (
            await execute_approved_gmail_send(
                db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
            )
            == "blocked"
        )
        assert (await db.get(CommunicationDraft, UUID(draft["id"]))).approved_version is None
    assert not gateway.calls
    draft = changed.json()
    action2 = await prepare(client, draft, connection)
    assert (
        await approve(client, draft, action2, expected_payload_hash=action["payload_hash"])
    ).status_code == 409
    assert (await approve(client, draft, action2)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["version", "hash", "expired", "revoked", "stored_mutation"])
async def test_stale_or_unavailable_draft_never_sends(approval_environment, monkeypatch, failure):
    client, factory, settings, draft, connection, _ = await setup(approval_environment, monkeypatch)
    action = await prepare(client, draft, connection)
    kwargs = {}
    if failure == "version":
        kwargs["draft_version"] = 2
    if failure == "hash":
        kwargs["expected_payload_hash"] = "0" * 64
    async with factory() as db:
        if failure == "expired":
            row = await db.scalar(select(Approval).where(Approval.action_id == UUID(action["id"])))
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if failure == "revoked":
            row = await db.get(Connection, connection)
            row.status = "disconnected"
        if failure == "stored_mutation":
            row = await db.get(CommunicationDraftVersion, (UUID(draft["id"]), 1))
            row.body = "Mutated without a version"
        await db.commit()
    assert (await approve(client, draft, action, **kwargs)).status_code == 409
    async with factory() as db:
        assert (await db.get(CommunicationDraft, UUID(draft["id"]))).approved_version is None
        gateway = FakeGmailGateway()
        await execute_approved_gmail_send(
            db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
        )
        assert not gateway.calls


@pytest.mark.asyncio
async def test_failed_regeneration_preserves_edits_and_cross_owner_access_denied(
    approval_environment, monkeypatch
):
    client, _, _, draft, _, _ = await setup(approval_environment, monkeypatch)

    async def unavailable(*args, **kwargs):
        from navox.ai.runtime import GatewayUnavailable

        raise GatewayUnavailable()

    monkeypatch.setattr(communication, "generate_content", unavailable)
    response = await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/regenerate",
        json={"expected_version": 1, "instructions": "Try again"},
    )
    assert response.status_code == 503
    assert (await client.get(f"/api/v1/communication-drafts/{draft['id']}")).json()[
        "versions"
    ] == draft["versions"]
    await register(client, email="other@example.com")
    assert (await client.get(f"/api/v1/communication-drafts/{draft['id']}")).status_code == 404
    assert (await client.get("/api/v1/communication-drafts")).json() == []


@pytest.mark.asyncio
async def test_source_deletion_erases_unsent_draft_versions(approval_environment, monkeypatch):
    _, factory, _, draft, connection, _ = await setup(approval_environment, monkeypatch)
    from navox.agent.provenance import erase_source_plans

    async with factory() as db:
        row = await db.get(CommunicationDraft, UUID(draft["id"]))
        await erase_source_plans(
            db, workspace_id=row.workspace_id, user_id=row.user_id, connection_ids={connection}
        )
        await db.commit()
    async with factory() as db:
        assert await db.get(CommunicationDraft, UUID(draft["id"])) is None
        assert not (await db.scalars(select(CommunicationDraftVersion))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "payload", "missing_version"])
async def test_change_during_token_refresh_cannot_send_old_content(
    approval_environment, monkeypatch, change
):
    client, factory, settings, draft, connection, _ = await setup(approval_environment, monkeypatch)
    action = await prepare(client, draft, connection)
    assert (await approve(client, draft, action)).status_code == 200

    async def changed_token(db, *, connection, settings):
        if change == "permission":
            connection.granted_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        elif change == "missing_version":
            await db.delete(await db.get(CommunicationDraftVersion, (UUID(draft["id"]), 1)))
        else:
            from navox.agent.hashing import action_security_hash
            from navox.db.models import Action

            row = await db.get(Action, UUID(action["id"]))
            row.payload = dict(row.payload, body_text="A different approved payload")
            row.payload_hash = action_security_hash(
                provider=row.provider, action_type=row.action_type, payload=row.payload
            )
            approval = await db.scalar(select(Approval).where(Approval.action_id == row.id))
            approval.action_payload_hash = row.payload_hash
        return "test-access-token"

    monkeypatch.setattr(execution, "access_token_for_connection", changed_token)
    gateway = FakeGmailGateway()
    async with factory() as db:
        assert (
            await execute_approved_gmail_send(
                db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
            )
            == "blocked"
        )
        row = await db.scalar(select(Approval).where(Approval.action_id == UUID(action["id"])))
        assert row.consumed_at is None
    assert not gateway.calls


@pytest.mark.asyncio
async def test_approved_send_blocks_source_deletion_and_keeps_draft(
    approval_environment, monkeypatch
):
    from fastapi import HTTPException

    from navox.agent.provenance import erase_source_plans

    client, factory, _, draft, connection, _ = await setup(approval_environment, monkeypatch)
    action = await prepare(client, draft, connection)
    assert (await approve(client, draft, action)).status_code == 200
    async with factory() as db:
        row = await db.get(CommunicationDraft, UUID(draft["id"]))
        with pytest.raises(HTTPException) as error:
            await erase_source_plans(
                db, workspace_id=row.workspace_id, user_id=row.user_id, connection_ids={connection}
            )
        assert error.value.status_code == 409
        await db.rollback()
        assert await db.get(CommunicationDraft, UUID(draft["id"])) is not None
        assert await db.get(CommunicationDraftVersion, (UUID(draft["id"]), 1)) is not None


@pytest.mark.asyncio
async def test_prepare_retry_returns_same_action_and_stale_regeneration_skips_provider(
    approval_environment, monkeypatch
):
    client, _, _, draft, connection, runtime = await setup(approval_environment, monkeypatch)
    payload = {"expected_version": 1, "connection_id": str(connection), "request_id": str(uuid4())}
    one = await client.post(f"/api/v1/communication-drafts/{draft['id']}/prepare", json=payload)
    two = await client.post(f"/api/v1/communication-drafts/{draft['id']}/prepare", json=payload)
    assert one.status_code == two.status_code == 200
    assert one.json()["id"] == two.json()["id"]
    response = await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/regenerate",
        json={"expected_version": 9, "instructions": "Change the draft"},
    )
    assert response.status_code == 409 and len(runtime.calls) == 1


async def configure_reply(monkeypatch):
    from navox.communication import service
    from navox.providers.google_gmail import GmailReplyMetadata

    calls = []

    async def token(*args, **kwargs):
        return "test-access-token"

    async def metadata(self, access_token, *, external_id):
        assert access_token == "test-access-token"
        calls.append(external_id)
        return GmailReplyMetadata(
            source_message_id=external_id,
            thread_id="thread-456",
            source_subject="Friday meeting",
            in_reply_to="<source@example.com>",
            references="<parent@example.com> <source@example.com>",
        )

    monkeypatch.setattr(service, "access_token_for_connection", token)
    monkeypatch.setattr(service.GoogleSourceGateway, "gmail_reply_metadata", metadata)
    return calls


@pytest.mark.asyncio
async def test_reply_correction_versions_unchanged_body_and_hashes_thread(
    approval_environment, monkeypatch
):
    from navox.agent.hashing import action_security_hash
    from navox.db.models import Action

    client, factory, settings, draft, connection, runtime = await setup(
        approval_environment, monkeypatch
    )
    reads = await configure_reply(monkeypatch)
    old = await prepare(client, draft, connection)
    content = draft["versions"][-1]
    edit = await client.patch(
        f"/api/v1/communication-drafts/{draft['id']}",
        json={
            "expected_version": 1,
            "to": content["to"],
            "subject": content["subject"],
            "body": content["body"],
        },
    )
    assert edit.status_code == 200, edit.text
    draft = edit.json()
    request = {
        "expected_version": 2,
        "connection_id": str(connection),
        "request_id": str(uuid4()),
        "reply_to_source": True,
    }
    response = await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/prepare", json=request
    )
    assert response.status_code == 200, response.text
    action = response.json()
    assert action["payload"]["body_text"] == old["payload"]["body_text"]
    assert action["payload"]["draft_version"] == 2
    assert action["payload_hash"] != old["payload_hash"]
    assert action["payload"]["reply"]["source_message_id"] == "fixture-message"
    assert action["payload"]["reply"]["thread_id"] == "thread-456"
    assert action["payload_hash"] == action_security_hash(
        provider="google", action_type="gmail.send", payload=action["payload"]
    )
    altered = {
        **action["payload"],
        "reply": {**action["payload"]["reply"], "thread_id": "other-thread"},
    }
    assert (
        action_security_hash(provider="google", action_type="gmail.send", payload=altered)
        != action["payload_hash"]
    )
    retry = await client.post(f"/api/v1/communication-drafts/{draft['id']}/prepare", json=request)
    assert retry.status_code == 200 and retry.json()["id"] == action["id"]
    request["reply_to_source"] = False
    assert (
        await client.post(f"/api/v1/communication-drafts/{draft['id']}/prepare", json=request)
    ).status_code == 409
    assert reads == ["fixture-message"]
    assert (
        await approve(client, draft, action, expected_payload_hash=old["payload_hash"])
    ).status_code == 409
    async with factory() as db:
        assert (await db.get(Action, UUID(old["id"]))).status == "blocked"
        approval = await db.scalar(select(Approval).where(Approval.action_id == UUID(old["id"])))
        assert approval.status == "superseded" and approval.consumed_at is None
    assert (await approve(client, draft, action)).status_code == 200
    gateway = FakeGmailGateway()
    async with factory() as db:
        assert (
            await execute_approved_gmail_send(
                db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
            )
            == "completed"
        )
    assert len(gateway.calls) == 1 and len(runtime.calls) == 1
    assert gateway.calls[0].reply.model_dump(mode="json") == action["payload"]["reply"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["subject", "connection", "unavailable"])
async def test_reply_prepare_fails_without_creating_action(
    approval_environment, monkeypatch, failure
):
    from navox.communication import service
    from navox.db.models import Action
    from navox.providers.google_sources import GoogleSourceError

    client, factory, _, draft, connection, _ = await setup(approval_environment, monkeypatch)
    reads = await configure_reply(monkeypatch)
    if failure == "subject":
        response = await client.patch(
            f"/api/v1/communication-drafts/{draft['id']}",
            json={
                "expected_version": 1,
                "to": ["maya@example.com"],
                "subject": "Unrelated topic",
                "body": "Same reply.",
            },
        )
        draft = response.json()
    elif failure == "connection":
        connection = uuid4()
    else:

        async def unavailable(*args, **kwargs):
            raise GoogleSourceError("unavailable")

        monkeypatch.setattr(service.GoogleSourceGateway, "gmail_reply_metadata", unavailable)
    response = await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/prepare",
        json={
            "expected_version": draft["current_version"],
            "connection_id": str(connection),
            "request_id": str(uuid4()),
            "reply_to_source": True,
        },
    )
    assert response.status_code == {"subject": 409, "connection": 403, "unavailable": 503}[failure]
    async with factory() as db:
        assert await db.scalar(select(Action)) is None
    if failure == "connection":
        assert not reads


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tampered_thread", "source_rebound", "wrong_receipt"])
async def test_reply_execution_checks_binding_and_receipt(
    approval_environment, monkeypatch, failure
):
    from navox.db.models import Action
    from navox.providers.google_gmail import GmailSendReceipt

    client, factory, settings, draft, connection, _ = await setup(approval_environment, monkeypatch)
    await configure_reply(monkeypatch)
    response = await client.post(
        f"/api/v1/communication-drafts/{draft['id']}/prepare",
        json={
            "expected_version": 1,
            "connection_id": str(connection),
            "request_id": str(uuid4()),
            "reply_to_source": True,
        },
    )
    assert response.status_code == 200, response.text
    action = response.json()
    assert (await approve(client, draft, action)).status_code == 200
    gateway = FakeGmailGateway()
    if failure == "wrong_receipt":
        original = gateway.send

        async def wrong_receipt(**kwargs):
            await original(**kwargs)
            return GmailSendReceipt(message_id="sent-789", thread_id="wrong-thread")

        monkeypatch.setattr(gateway, "send", wrong_receipt)
    async with factory() as db:
        if failure == "tampered_thread":
            row = await db.get(Action, UUID(action["id"]))
            row.payload = {
                **row.payload,
                "reply": {**row.payload["reply"], "thread_id": "other-thread"},
            }
        elif failure == "source_rebound":
            saved = await db.get(CommunicationDraft, UUID(draft["id"]))
            row = await db.get(CommitmentSource, UUID(saved.source_reference))
            row.external_resource_id = "other-source"
        await db.commit()
        result = await execute_approved_gmail_send(
            db, action_id=UUID(action["id"]), settings=settings, gateway=gateway
        )
        assert result == ("uncertain" if failure == "wrong_receipt" else "blocked")
        assert len(gateway.calls) == (1 if failure == "wrong_receipt" else 0)
