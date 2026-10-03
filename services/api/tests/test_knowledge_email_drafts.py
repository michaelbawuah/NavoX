"""Focused SPEC-008 M3A coverage for the authorized Gmail reply draft bridge.

Everything here is synthetic: disposable SQLite records, fake Google and AI
gateways. No provider, credential, live mailbox or network access is used.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_approvals import FakeApprovalDispatcher, FakeGmailGateway, register

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
from navox.api.auth import password_hasher
from navox.api.main import create_app
from navox.approvals import execution
from navox.approvals.execution import execute_approved_gmail_send
from navox.communication import generation, service
from navox.core.settings import Settings, get_settings
from navox.db.communications import CommunicationDraft
from navox.db.knowledge import KnowledgeResource, KnowledgeResourcePermission
from navox.db.models import (
    Action,
    Approval,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    Plan,
    PlanStep,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.session import get_database_session
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.providers.google_gmail import GmailReplyMetadata
from navox.providers.google_sources import GMAIL_READ_SCOPE
from tests.knowledge_search_support import READ_CAPABILITY, manifest

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
MAILBOX = "owner@example.com"
SOURCE_AUTHOR = "maya@example.com"
SOURCE_MESSAGE_ID = "fixture-message"
PASSWORD = "twelve-character-password"


@dataclass(frozen=True)
class SeededSource:
    legacy_connection_id: UUID
    connector_connection_id: UUID
    connector_resource_id: UUID
    resource_id: UUID
    external_id: str
    mailbox: str


class DraftRuntime:
    def __init__(self, subject: str = "Friday meeting", body: str = "Does Friday work?") -> None:
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
        self._subject = subject
        self._body = body

    async def execute(self, task, *, context_builder, documents, semantic_validator, user_request):
        context = await context_builder.build(task, documents, user_request=user_request)
        self.calls.append(context)
        output = {"subject": self._subject, "body": self._body}
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


@pytest_asyncio.fixture
async def knowledge_email_env(ai_database):
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
        account = await register(client)
        yield SimpleNamespace(
            client=client,
            app=app,
            factory=ai_database,
            settings=settings,
            account=account,
            workspace_id=UUID(str(account["workspace"]["id"])),
            user_id=UUID(str(account["id"])),
        )


async def seed_source(
    factory,
    *,
    workspace_id: UUID,
    user_id: UUID,
    mailbox: str = MAILBOX,
    external_id: str = SOURCE_MESSAGE_ID,
    resource_type: str = "EMAIL",
    owner_user_id: UUID | None = None,
    view_user_ids: tuple[UUID, ...] = (),
    connector_status: str = "CONNECTED",
    definition_active: bool = True,
    legacy_status: str = "active",
    scopes: list[str] | None = None,
) -> SeededSource:
    # Each seed is an independent fixture mailbox; identity values stay unique so
    # one test database can hold several unrelated sources.
    suffix = uuid4().hex[:12]
    version = f"1.0.0-fixture{suffix}"
    manifest_payload = manifest(
        "google-gmail", READ_CAPABILITY, ["communication.message"]
    ).model_dump(mode="json", by_alias=True)
    manifest_payload["version"] = version
    async with factory() as database:
        legacy = Connection(
            user_id=user_id,
            workspace_id=workspace_id,
            provider="google",
            external_account_id=f"google-{user_id}-{suffix}",
            external_email=mailbox,
            status=legacy_status,
            granted_scopes=scopes
            or [
                "https://www.googleapis.com/auth/userinfo.email",
                GMAIL_READ_SCOPE,
                GMAIL_SEND_SCOPE,
            ],
        )
        database.add(legacy)
        await database.flush()
        definition = ConnectorDefinition(
            connector_key="google-gmail",
            version=version,
            display_name="Fixture Gmail",
            connector_class="OAUTH_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=manifest_payload,
            active=definition_active,
        )
        database.add(definition)
        await database.flush()
        connector = ConnectorConnection(
            connector_definition_id=definition.id,
            legacy_connection_id=legacy.id,
            user_id=user_id,
            workspace_id=workspace_id,
            provider="google",
            external_account_id=legacy.external_account_id,
            display_name=mailbox,
            status=connector_status,
            health_state=connector_status,
            authorized_capabilities=[READ_CAPABILITY],
            provider_capabilities=[READ_CAPABILITY],
        )
        database.add(connector)
        await database.flush()
        stored = ConnectorResource(
            id=uuid4(),
            workspace_id=workspace_id,
            connector_connection_id=connector.id,
            provider="google",
            resource_type="communication.message",
            external_id=external_id,
            canonical={"source_type": "gmail_message", "status": "active"},
            provider_metadata={"content_persisted": False},
            retrieved_at=datetime.now(UTC),
            content_hash="a" * 64,
            deleted=False,
        )
        database.add(stored)
        await database.flush()
        resource = KnowledgeResource(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id or user_id,
            source_type=resource_type,
            source_connection_id=connector.id,
            external_resource_id=external_id,
            source_resource_id=stored.id,
            source_read_capability=READ_CAPABILITY,
            sensitivity="PERSONAL",
        )
        database.add(resource)
        await database.flush()
        for viewer in view_user_ids:
            database.add(
                KnowledgeResourcePermission(
                    resource_id=resource.id,
                    workspace_id=workspace_id,
                    principal_type="USER",
                    principal_id=viewer,
                    permission="VIEW",
                )
            )
        await database.commit()
        return SeededSource(
            legacy_connection_id=legacy.id,
            connector_connection_id=connector.id,
            connector_resource_id=stored.id,
            resource_id=resource.id,
            external_id=external_id,
            mailbox=mailbox,
        )


def install_fakes(
    monkeypatch,
    *,
    author: str | None = SOURCE_AUTHOR,
    status: str = "active",
    external_id: str = SOURCE_MESSAGE_ID,
    source_subject: str = "Friday meeting",
    reply_author: str | None = SOURCE_AUTHOR,
) -> SimpleNamespace:
    runtime = DraftRuntime()
    reads: list[str] = []

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
            author=(
                SourceIdentity(provider="google", identity_type="email", identity_value=author)
                if author is not None
                else None
            ),
            subject="Meeting",
            content="Can we meet on Friday?",
            occurred_at=now,
            retrieved_at=now,
            metadata={"status": status},
        )

    async def reply_metadata(self, access_token, *, external_id):
        reads.append(external_id)
        return GmailReplyMetadata(
            source_message_id=external_id,
            thread_id="thread-456",
            source_subject=source_subject,
            in_reply_to="<source@example.com>",
            references="<source@example.com>",
            source_author=reply_author,
        )

    monkeypatch.setattr(communication, "build_runtime", configured)
    monkeypatch.setattr(generation, "access_token_for_connection", token)
    monkeypatch.setattr(service, "access_token_for_connection", token)
    monkeypatch.setattr(execution, "access_token_for_connection", token)
    monkeypatch.setattr(generation.GoogleSourceGateway, "gmail_message", message)
    monkeypatch.setattr(service.GoogleSourceGateway, "gmail_reply_metadata", reply_metadata)
    return SimpleNamespace(runtime=runtime, reads=reads)


async def create_draft(client, source: SeededSource | SimpleNamespace, instructions: str = "Ask"):
    return await client.post(
        "/api/v1/communication-drafts/from-knowledge-email",
        json={"source_id": str(source.resource_id), "instructions": instructions},
    )


async def prepare(
    client, draft, *, reply_to_source: bool = True, connection_id=None, request_id=None, **extra
):
    payload = {
        "expected_version": draft["current_version"],
        "request_id": str(request_id or uuid4()),
        "reply_to_source": reply_to_source,
        **extra,
    }
    if connection_id is not None:
        payload["connection_id"] = str(connection_id)
    return await client.post(f"/api/v1/communication-drafts/{draft['id']}/prepare", json=payload)


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
async def test_same_owner_draft_approves_and_verifies_without_commitment(
    knowledge_email_env, monkeypatch
):
    env = knowledge_email_env
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id,),
    )
    fakes = install_fakes(monkeypatch)

    generated = await create_draft(env.client, source)
    assert generated.status_code == 201, generated.text
    draft = generated.json()
    assert draft["binding_kind"] == "KNOWLEDGE_EMAIL"
    assert draft["commitment_id"] is None
    assert draft["source_id"] == str(source.resource_id)
    assert draft["source_external_id"] == source.external_id
    assert draft["versions"][0]["to"] == [SOURCE_AUTHOR]

    edited = await env.client.patch(
        f"/api/v1/communication-drafts/{draft['id']}",
        json={
            "expected_version": 1,
            "to": [SOURCE_AUTHOR],
            "subject": "Friday meeting",
            "body": "Would Friday afternoon work for you?",
        },
    )
    assert edited.status_code == 200, edited.text
    draft = edited.json()
    assert draft["current_version"] == 2 and len(draft["versions"]) == 2

    request_id = uuid4()
    prepared = await prepare(env.client, draft, request_id=request_id)
    assert prepared.status_code == 200, prepared.text
    action = prepared.json()
    assert action["commitment_id"] is None
    assert action["payload"]["post_send_state"] == "unchanged"
    assert action["payload"]["to"] == SOURCE_AUTHOR
    assert action["payload"]["reply"]["source_message_id"] == source.external_id
    assert action["payload"]["reply"]["source_author"] == SOURCE_AUTHOR
    assert action["payload"]["draft_version"] == 2

    retry = await prepare(env.client, draft, request_id=request_id)
    assert retry.status_code == 200 and retry.json()["id"] == action["id"]
    assert (await approve(env.client, draft, action)).status_code == 200

    gateway = FakeGmailGateway()
    async with env.factory() as database:
        assert (
            await execute_approved_gmail_send(
                database,
                action_id=UUID(action["id"]),
                settings=env.settings,
                gateway=gateway,
            )
            == "completed"
        )
        stored = await database.get(CommunicationDraft, UUID(draft["id"]))
        assert stored is not None and stored.status == "sent"
        action_row = await database.get(Action, UUID(action["id"]))
        assert action_row is not None and action_row.commitment_id is None
        step = await database.get(PlanStep, action_row.plan_step_id)
        assert step is not None
        plan = await database.get(Plan, step.plan_id)
        assert plan is not None and plan.commitment_id is None
        assert plan.context_snapshot["commitment"] is None
        assert plan.context_snapshot["draft_source"]["source_message_id"] == source.external_id
    assert len(gateway.calls) == 1
    assert gateway.calls[0].to == SOURCE_AUTHOR
    assert gateway.calls[0].body_text == "Would Friday afternoon work for you?"
    assert fakes.reads == [source.external_id]
    assert len(fakes.runtime.calls) == 1  # approval and sending never regenerate


@pytest.mark.asyncio
async def test_shared_view_without_ownership_is_refused(knowledge_email_env, monkeypatch):
    env = knowledge_email_env
    async with env.factory() as database:
        viewer = User(
            email="viewer@example.com",
            display_name="Viewer",
            password_hash=password_hasher.hash(PASSWORD),
        )
        database.add(viewer)
        await database.flush()
        database.add(WorkspaceMembership(workspace_id=env.workspace_id, user_id=viewer.id))
        await database.commit()
        viewer_id = viewer.id
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id, viewer_id),
    )
    fakes = install_fakes(monkeypatch)
    login = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    assert (await create_draft(env.client, source)).status_code == 403
    assert not fakes.reads and not fakes.runtime.calls


@pytest.mark.asyncio
async def test_missing_member_cross_workspace_and_ungranted_sources_are_refused(
    knowledge_email_env, monkeypatch
):
    env = knowledge_email_env
    fakes = install_fakes(monkeypatch)
    assert (await create_draft(env.client, SimpleNamespace(resource_id=uuid4()))).status_code == 404

    async with env.factory() as database:
        stranger = User(
            email="stranger@example.com",
            display_name="Stranger",
            password_hash=password_hasher.hash(PASSWORD),
        )
        database.add(stranger)
        await database.flush()
        foreign_workspace = Workspace(name="Foreign", workspace_type="personal")
        database.add(foreign_workspace)
        await database.flush()
        database.add(
            WorkspaceMembership(
                workspace_id=foreign_workspace.id, user_id=stranger.id, role="owner"
            )
        )
        await database.commit()
        foreign_workspace_id, stranger_id = foreign_workspace.id, stranger.id

    foreign = await seed_source(
        env.factory,
        workspace_id=foreign_workspace_id,
        user_id=stranger_id,
        view_user_ids=(stranger_id,),
    )
    assert (await create_draft(env.client, foreign)).status_code == 404

    ungranted = await seed_source(env.factory, workspace_id=env.workspace_id, user_id=env.user_id)
    assert (await create_draft(env.client, ungranted)).status_code == 403
    assert not fakes.reads and not fakes.runtime.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "resource_deleted",
        "definition_disabled",
        "connector_disconnected",
        "legacy_disconnected",
        "view_revoked",
        "canonical_deleted",
        "canonical_type_changed",
        "read_scope_removed",
    ],
)
async def test_source_changes_block_generation_and_preparation(
    knowledge_email_env, monkeypatch, mutation
):
    env = knowledge_email_env
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id,),
    )
    fakes = install_fakes(monkeypatch)
    generated = await create_draft(env.client, source)
    assert generated.status_code == 201, generated.text
    draft = generated.json()

    async with env.factory() as database:
        if mutation == "resource_deleted":
            row = await database.get(KnowledgeResource, source.resource_id)
            assert row is not None
            row.deleted_at = datetime.now(UTC)
        elif mutation == "definition_disabled":
            connector = await database.get(ConnectorConnection, source.connector_connection_id)
            assert connector is not None
            definition = await database.get(ConnectorDefinition, connector.connector_definition_id)
            assert definition is not None
            definition.active = False
        elif mutation == "connector_disconnected":
            connector = await database.get(ConnectorConnection, source.connector_connection_id)
            assert connector is not None
            connector.status = "DISCONNECTED"
            connector.health_state = "DISCONNECTED"
        elif mutation == "legacy_disconnected":
            legacy = await database.get(Connection, source.legacy_connection_id)
            assert legacy is not None
            legacy.status = "disconnected"
        elif mutation == "view_revoked":
            grant = await database.scalar(
                select(KnowledgeResourcePermission).where(
                    KnowledgeResourcePermission.resource_id == source.resource_id
                )
            )
            assert grant is not None
            grant.revoked_at = datetime.now(UTC)
        elif mutation == "canonical_deleted":
            stored = await database.get(ConnectorResource, source.connector_resource_id)
            assert stored is not None
            stored.deleted = True
        elif mutation == "canonical_type_changed":
            stored = await database.get(ConnectorResource, source.connector_resource_id)
            assert stored is not None
            stored.resource_type = "communication.message.untrusted"
        else:
            legacy = await database.get(Connection, source.legacy_connection_id)
            assert legacy is not None
            legacy.granted_scopes = [GMAIL_SEND_SCOPE]
        await database.commit()

    assert (await create_draft(env.client, source)).status_code in {403, 404}
    assert (await prepare(env.client, draft)).status_code in {403, 404}
    assert not fakes.reads


@pytest.mark.asyncio
async def test_thread_only_and_unsafe_authors_are_refused(knowledge_email_env, monkeypatch):
    env = knowledge_email_env
    thread = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        resource_type="EMAIL_THREAD",
        view_user_ids=(env.user_id,),
    )
    install_fakes(monkeypatch)
    assert (await create_draft(env.client, thread)).status_code == 403

    outgoing = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        external_id="outgoing-1",
        view_user_ids=(env.user_id,),
    )
    install_fakes(monkeypatch, author=MAILBOX, external_id="outgoing-1")
    assert (await create_draft(env.client, outgoing)).status_code == 403

    ambiguous = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        external_id="ambiguous-1",
        view_user_ids=(env.user_id,),
    )
    install_fakes(monkeypatch, author=None, external_id="ambiguous-1")
    assert (await create_draft(env.client, ambiguous)).status_code == 403


@pytest.mark.asyncio
async def test_recipient_edit_wrong_mailbox_and_non_reply_prepare_are_refused(
    knowledge_email_env, monkeypatch
):
    env = knowledge_email_env
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id,),
    )
    fakes = install_fakes(monkeypatch)
    draft = (await create_draft(env.client, source)).json()

    changed_recipient = await env.client.patch(
        f"/api/v1/communication-drafts/{draft['id']}",
        json={
            "expected_version": 1,
            "to": ["attacker@example.com"],
            "subject": "Friday meeting",
            "body": "Redirected",
        },
    )
    assert changed_recipient.status_code == 403
    assert (await env.client.get(f"/api/v1/communication-drafts/{draft['id']}")).json()[
        "current_version"
    ] == 1
    assert (await prepare(env.client, draft, reply_to_source=False)).status_code == 403
    assert (await prepare(env.client, draft, connection_id=uuid4())).status_code == 403
    assert not fakes.reads


@pytest.mark.asyncio
async def test_edit_after_approval_and_pre_execution_revocation_never_send(
    knowledge_email_env, monkeypatch
):
    env = knowledge_email_env
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id,),
    )
    install_fakes(monkeypatch)
    draft = (await create_draft(env.client, source)).json()
    action = (await prepare(env.client, draft)).json()
    assert (await approve(env.client, draft, action)).status_code == 200

    edited = await env.client.patch(
        f"/api/v1/communication-drafts/{draft['id']}",
        json={
            "expected_version": 1,
            "to": [SOURCE_AUTHOR],
            "subject": "Friday meeting",
            "body": "Changed after approval",
        },
    )
    assert edited.status_code == 200, edited.text
    gateway = FakeGmailGateway()
    async with env.factory() as database:
        assert (
            await execute_approved_gmail_send(
                database,
                action_id=UUID(action["id"]),
                settings=env.settings,
                gateway=gateway,
            )
            == "blocked"
        )
        assert (await database.get(CommunicationDraft, UUID(draft["id"]))).approved_version is None
    assert not gateway.calls

    draft = edited.json()
    action2 = (await prepare(env.client, draft)).json()
    assert (await approve(env.client, draft, action2)).status_code == 200
    async with env.factory() as database:
        grant = await database.scalar(
            select(KnowledgeResourcePermission).where(
                KnowledgeResourcePermission.resource_id == source.resource_id
            )
        )
        assert grant is not None
        grant.revoked_at = datetime.now(UTC)
        await database.commit()
    async with env.factory() as database:
        assert (
            await execute_approved_gmail_send(
                database,
                action_id=UUID(action2["id"]),
                settings=env.settings,
                gateway=gateway,
            )
            == "blocked"
        )
        approval = await database.scalar(
            select(Approval).where(Approval.action_id == UUID(action2["id"]))
        )
        assert approval is not None and approval.consumed_at is None
    assert not gateway.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["expired", "wrong_hash", "wrong_version"])
async def test_stale_or_mismatched_approval_never_sends(knowledge_email_env, monkeypatch, failure):
    env = knowledge_email_env
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id,),
    )
    install_fakes(monkeypatch)
    draft = (await create_draft(env.client, source)).json()
    action = (await prepare(env.client, draft)).json()
    kwargs = {}
    if failure == "wrong_hash":
        kwargs["expected_payload_hash"] = "0" * 64
    if failure == "wrong_version":
        kwargs["draft_version"] = 2
    if failure == "expired":
        async with env.factory() as database:
            approval = await database.scalar(
                select(Approval).where(Approval.action_id == UUID(action["id"]))
            )
            assert approval is not None
            approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await database.commit()
    assert (await approve(env.client, draft, action, **kwargs)).status_code == 409
    gateway = FakeGmailGateway()
    async with env.factory() as database:
        await execute_approved_gmail_send(
            database,
            action_id=UUID(action["id"]),
            settings=env.settings,
            gateway=gateway,
        )
    assert not gateway.calls


@pytest.mark.asyncio
async def test_tampered_reply_metadata_blocks_execution(knowledge_email_env, monkeypatch):
    env = knowledge_email_env
    source = await seed_source(
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        view_user_ids=(env.user_id,),
    )
    install_fakes(monkeypatch)
    draft = (await create_draft(env.client, source)).json()
    action = (await prepare(env.client, draft)).json()
    assert (await approve(env.client, draft, action)).status_code == 200
    gateway = FakeGmailGateway()
    async with env.factory() as database:
        row = await database.get(Action, UUID(action["id"]))
        assert row is not None
        row.payload = {**row.payload, "reply": {**row.payload["reply"], "thread_id": "other"}}
        await database.commit()
        assert (
            await execute_approved_gmail_send(
                database,
                action_id=UUID(action["id"]),
                settings=env.settings,
                gateway=gateway,
            )
            == "blocked"
        )
    assert not gateway.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    [
        {"recipient": "attacker@example.com"},
        {"connection_id": str(uuid4())},
        {"workspace_id": str(uuid4())},
        {"commitment_id": str(uuid4())},
        {"source_external_id": "another-message"},
        {"approval_state": "approved"},
    ],
)
async def test_client_cannot_supply_source_authority(knowledge_email_env, authority):
    env = knowledge_email_env
    response = await env.client.post(
        "/api/v1/communication-drafts/from-knowledge-email",
        json={"source_id": str(uuid4()), "instructions": "Draft a reply", **authority},
    )
    assert response.status_code == 422
