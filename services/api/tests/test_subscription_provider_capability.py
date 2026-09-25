"""Disposable in-process provider proves writes, independent verification and deny paths."""

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.generic_registration import approved_generic_connectors
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.secrets import SecretBroker
from navox.connectors.subscription_cancellation import (
    CAPABILITY,
    GRANT_EVENT,
    ApprovedCancellationProfile,
    approved_cancellation_profiles,
    available_cancellation_profiles,
    enable_subscription_cancellation,
    inspect_cancellation_binding,
    resolve_subscription_cancellation,
)
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.subscriptions.cancellation_contracts import CancellationTarget

SECRET = "disposable-provider-token-for-tests"
PROFILE = {
    "id": "demo-cancellation",
    "configuration_id": "demo-service",
    "display_name": "Disposable demo provider",
    "merchant_domain": "demo.example",
    "base_url": "https://demo.example",
    "account_path": "/v1/account",
    "inspect_path": "/v1/subscriptions/{subscription_id}",
    "cancel_path": "/v1/subscriptions/{subscription_id}/cancel",
    "management_path": "/account/subscriptions/{subscription_id}",
    "enabled": True,
    "expected_effect": "Stop renewal after the current paid period.",
    "supports_idempotency": True,
    "supports_revision_precondition": True,
    "cancel_payload": {"at_period_end": True},
}
GENERIC = {
    "id": "demo-service",
    "config": {
        "display_name": "Disposable demo provider",
        "provider": "demo_service",
        "base_url": "https://demo.example",
        "endpoints": [
            {
                "name": "subscriptions",
                "path": "/v1/subscriptions",
                "capability": "subscription.read",
                "resource_type": "recurring_obligation",
                "subject_field": "plan_name",
            }
        ],
    },
}


class DisposableProvider:
    def __init__(self):
        self.state = {
            "id": "subscription-1",
            "account_id": "account-1",
            "revision": "r1",
            "status": "active",
            "cancel_allowed": True,
            "plan_name": "Professional",
            "amount": "12.3400",
            "currency": "USD",
            "interval": "MONTH",
            "interval_count": 1,
            "auto_renew": True,
            "renewal_at": "2026-10-25T10:00:00+00:00",
            "access_ends_at": "2026-10-25T10:00:00+00:00",
            "cancellation_fee": "0",
            "refund": "No refund for the current paid period.",
        }
        self.account = {"account_id": "account-1", "capabilities": [CAPABILITY]}
        self.requests = []
        self.writes = []
        self.keys = set()
        self.post_response = None
        self.inspect_response = None
        self.account_response = None
        self.apply_write = True
        self.timeout_post = False

    def handle(self, request):
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.url.host == "demo.example"
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/v1/account":
            return self.account_response or httpx.Response(200, json=self.account)
        if request.method == "GET":
            return self.inspect_response or httpx.Response(200, json=self.state)
        assert (
            request.method == "POST"
            and request.url.path == "/v1/subscriptions/subscription-1/cancel"
        )
        payload = json.loads(request.content)
        assert payload["subscription_id"] == "subscription-1"
        assert payload["expected_revision"] == "r1"
        assert request.headers["if-match"] == "r1"
        key = request.headers["idempotency-key"]
        if key not in self.keys:
            self.writes.append(key)
            self.keys.add(key)
        if self.apply_write:
            self.state.update(
                status="cancelled", auto_renew=False, cancel_allowed=False, revision="r2"
            )
        if self.timeout_post:
            raise httpx.ReadTimeout("synthetic failure", request=request)
        return self.post_response or httpx.Response(
            202,
            json={
                "id": "subscription-1",
                "account_id": "account-1",
                "request_id": "request-1",
                "status": "submitted",
            },
        )


@pytest_asyncio.fixture
async def context(tmp_path) -> AsyncIterator[tuple]:
    dsn = os.environ.get(
        "NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path / 'provider.db'}"
    )
    schema = f"provider_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        connector_secret_encryption_key=Fernet.generate_key().decode(),
        generic_rest_connectors=[GENERIC],
        subscription_cancellation_profiles=[PROFILE],
    )
    generic = approved_generic_connectors(settings)[0]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as database:
        user = User(email=f"{uuid4()}@example.com")
        workspace = Workspace(name="Disposable")
        database.add_all([user, workspace])
        await database.flush()
        database.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id))
        definition = ConnectorDefinition(
            connector_key=generic.connector_key,
            version=generic.manifest.version,
            display_name=generic.config.display_name,
            connector_class="GENERIC_API",
            trust_level="WORKSPACE_PRIVATE",
            manifest=generic.manifest.model_dump(mode="json", by_alias=True),
        )
        database.add(definition)
        await database.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="demo_service",
            external_account_id=f"configured:{user.id}:{generic.connector_key}",
            authorized_capabilities=["subscription.read"],
            provider_capabilities=["subscription.read"],
            config=generic.config.model_dump(mode="json"),
        )
        database.add(connection)
        await database.flush()
        await SecretBroker(settings).store(
            database,
            {"API_TOKEN": SECRET},
            connection_id=connection.id,
            workspace_id=workspace.id,
            user_id=user.id,
        )
        legacy = await ensure_provenance_connection(database, connection)
        await database.commit()
        target = CancellationTarget(
            obligation_id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            connection_id=legacy.id,
            external_resource_id="subscription-1",
            merchant_id=uuid4(),
            merchant_domain="demo.example",
            name="Demo",
            plan_name="Professional",
            revision="1",
        )
        provider = DisposableProvider()
        transport = httpx.MockTransport(provider.handle)
        yield database, settings, connection, target, provider, transport
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def grant(context, request_id=None):
    database, settings, connection, target, _, transport = context
    return await enable_subscription_cancellation(
        database,
        settings,
        connection_id=connection.id,
        workspace_id=target.workspace_id,
        user_id=target.user_id,
        profile_id="demo-cancellation",
        confirmed=True,
        request_id=request_id or uuid4(),
        transport=transport,
    )


async def capability(context):
    database, settings, _, target, _, transport = context
    result = await resolve_subscription_cancellation(
        database, settings, target, transport=transport
    )
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_provider_supported_cancellation_executes_then_independently_verifies(context):
    database, _, connection, target, provider, _ = context
    assert await resolve_subscription_cancellation(database, context[1], target) is None
    assert provider.requests == []
    granted = await grant(context)
    assert granted["legacy_connection_id"] == str(target.connection_id)
    assert granted["connection_id"] == str(connection.id)
    adapter = await capability(context)
    preview = await adapter.inspect(target)
    assert preview.payload["provider_state"]["amount"] == "12.3400"
    assert preview.management_url == "https://demo.example/account/subscriptions/subscription-1"
    submission = await adapter.execute(preview, "cancel:test-1")
    assert submission.status == "SUBMITTED"
    assert provider.writes == ["cancel:test-1"]
    verified = await adapter.verify(preview, submission)
    assert verified.status == "VERIFIED_CANCELLED"
    assert verified.evidence["state"] == "cancelled" and verified.evidence["auto_renew"] is False
    assert provider.requests[-2:] == [
        ("POST", "/v1/subscriptions/subscription-1/cancel"),
        ("GET", "/v1/subscriptions/subscription-1"),
    ]
    assert (
        SECRET
        not in preview.model_dump_json() + submission.model_dump_json() + verified.model_dump_json()
    )
    audits = list(await database.scalars(select(AuditEvent)))
    assert SECRET not in json.dumps([row.event_metadata for row in audits])
    # A replay cannot produce another logical write after provider state changes.
    with pytest.raises(ConnectorRuntimeError):
        await adapter.execute(preview, "cancel:test-1")
    assert len(provider.writes) == 1


@pytest.mark.asyncio
async def test_submission_is_not_verification_and_timeout_is_uncertain(context):
    await grant(context)
    adapter = await capability(context)
    provider = context[4]
    preview = await adapter.inspect(context[3])
    provider.apply_write = False
    provider.timeout_post = True
    submission = await adapter.execute(preview, "cancel:timeout")
    assert submission.status == "UNCERTAIN"
    assert (await adapter.verify(preview, submission)).status == "VERIFIED_ACTIVE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        {"status": "cancel_pending", "auto_renew": False, "revision": "r2"},
        {"status": "cancelled", "auto_renew": True, "revision": "r2"},
        {"status": "cancelled", "auto_renew": False, "revision": "r1"},
        {"status": "expired", "auto_renew": False, "revision": "r2"},
    ],
)
async def test_unverified_or_stale_provider_state_never_marks_cancelled(context, state):
    await grant(context)
    adapter = await capability(context)
    preview = await adapter.inspect(context[3])
    submission = await adapter.execute(preview, "cancel:verification")
    context[4].state.update(state)
    assert (await adapter.verify(preview, submission)).status == "VERIFICATION_PENDING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "price",
        "target",
        "account",
        "revision",
        "expiry",
        "profile",
        "revoke",
        "pause",
        "provider_grant",
        "credential",
    ],
)
async def test_material_change_or_revocation_prevents_write(context, change):
    await grant(context)
    adapter = await capability(context)
    database, settings, connection, target, provider, _ = context
    preview = await adapter.inspect(target)
    if change == "price":
        provider.state["amount"] = "99"
    elif change == "target":
        provider.state["id"] = "another-subscription"
    elif change == "account":
        provider.state["account_id"] = "another-account"
    elif change == "revision":
        provider.state["revision"] = "r2"
    elif change == "expiry":
        preview = preview.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    elif change == "profile":
        settings.subscription_cancellation_profiles = [
            {**PROFILE, "expected_effect": "Immediately terminate access."}
        ]
    elif change == "revoke":
        connection.authorized_capabilities = ["subscription.read"]
    elif change == "pause":
        connection.health_state = "PAUSED"
    elif change == "provider_grant":
        connection.provider_capabilities = ["subscription.read"]
    elif change == "credential":
        await SecretBroker(settings).store(
            database,
            {"API_TOKEN": SECRET},
            connection_id=connection.id,
            workspace_id=target.workspace_id,
            user_id=target.user_id,
        )
    await database.flush()
    with pytest.raises(ConnectorRuntimeError):
        await adapter.execute(preview, "cancel:denied")
    assert provider.writes == []


@pytest.mark.asyncio
async def test_user_provider_strings_without_profile_grant_cannot_enable_cancel(context):
    database, settings, connection, target, provider, transport = context
    connection.authorized_capabilities = ["subscription.read", CAPABILITY]
    connection.provider_capabilities = ["subscription.read", CAPABILITY]
    await database.flush()
    assert (
        await resolve_subscription_cancellation(database, settings, target, transport=transport)
        is None
    )
    assert provider.requests == []


@pytest.mark.asyncio
async def test_provider_must_explicitly_grant_cancel_to_authenticated_token(context):
    context[4].account["capabilities"] = ["subscription.read"]
    with pytest.raises(ConnectorRuntimeError):
        await grant(context)
    assert CAPABILITY not in context[2].authorized_capabilities
    assert not list(
        await context[0].scalars(select(AuditEvent).where(AuditEvent.event_type == GRANT_EVENT))
    )


@pytest.mark.asyncio
async def test_grant_replay_is_bounded_and_cannot_undo_revocation(context):
    request_id = uuid4()
    await grant(context, request_id)
    assert (await grant(context, request_id))["reused"] is True
    assert len(context[4].requests) == 1
    context[2].authorized_capabilities = ["subscription.read"]
    await context[0].flush()
    with pytest.raises(ConnectorRuntimeError):
        await grant(context, request_id)


@pytest.mark.asyncio
async def test_owner_bound_binding_uses_operator_domain_and_actual_provider_state(context):
    await grant(context)
    database, settings, connection, target, _, transport = context
    unknown = target.model_copy(update={"merchant_domain": None, "connection_id": None})
    preview = await inspect_cancellation_binding(
        database,
        settings,
        target=unknown,
        profile_id="demo-cancellation",
        connection_id=connection.id,
        transport=transport,
    )
    assert preview.target.connection_id == target.connection_id
    assert preview.target.merchant_domain == "demo.example"
    assert preview.payload["provider_state"]["plan_name"] == "Professional"
    other = unknown.model_copy(update={"workspace_id": uuid4()})
    with pytest.raises(ConnectorRuntimeError):
        await inspect_cancellation_binding(
            database,
            settings,
            target=other,
            profile_id="demo-cancellation",
            connection_id=connection.id,
            transport=transport,
        )


@pytest.mark.asyncio
async def test_profiles_inventory_separates_read_only_and_explicit_write_grants(context):
    database, settings, connection, target, provider, _ = context
    rows = await available_cancellation_profiles(
        database, settings, workspace_id=target.workspace_id, user_id=target.user_id
    )
    assert (
        rows[0]["connection_ids"] == [str(connection.id)]
        and rows[0]["granted_connection_ids"] == []
    )
    assert provider.requests == []
    await grant(context)
    rows = await available_cancellation_profiles(
        database, settings, workspace_id=target.workspace_id, user_id=target.user_id
    )
    assert rows[0]["granted_connection_ids"] == [str(connection.id)]
    assert SECRET not in json.dumps(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://evil.example/cancel"}),
        httpx.Response(404, json={"cancelled": True}),
        httpx.Response(
            200, json={"id": "subscription-1", "account_id": "account-1", "revision": SECRET}
        ),
        httpx.Response(
            200,
            json={
                "id": "subscription-1",
                "account_id": "account-1",
                "revision": "".join(f"%{ord(c):02x}" for c in SECRET),
            },
        ),
        httpx.Response(200, content=b"{" + b"x" * 128_000),
    ],
)
async def test_redirect_missing_resource_credentials_and_large_responses_never_verify(
    context, response
):
    await grant(context)
    adapter = await capability(context)
    preview = await adapter.inspect(context[3])
    submission = await adapter.execute(preview, "cancel:hostile")
    context[4].inspect_response = response
    with pytest.raises(ConnectorRuntimeError):
        await adapter.verify(preview, submission)


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "http://demo.example"},
        {"base_url": "https://127.0.0.1"},
        {"inspect_path": "//evil.example/{subscription_id}"},
        {"cancel_path": "/x/{subscription_id}?token=foo"},
        {"cancel_path": "/x/{subscription_id}/../admin"},
        {"cancel_path": "/x/%2e%2e/{subscription_id}"},
        {"cancel_payload": {"subscription_id": "different"}},
        {"supports_idempotency": False},
    ],
)
def test_operator_profiles_reject_unsafe_paths_or_unbounded_target_controls(change):
    with pytest.raises((ValueError, ConnectorRuntimeError)):
        ApprovedCancellationProfile.model_validate({**PROFILE, **change})


def test_profiles_must_match_reviewed_generic_origin():
    settings = Settings(
        _env_file=None,
        generic_rest_connectors=[GENERIC],
        subscription_cancellation_profiles=[{**PROFILE, "base_url": "https://other.example"}],
    )
    with pytest.raises(ValueError):
        approved_cancellation_profiles(settings)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "external_id", ["../another", "abc/other", "abc%2Fother", "abc?query", "a" * 129]
)
async def test_evidence_cannot_inject_a_provider_endpoint_via_resource_id(context, external_id):
    await grant(context)
    target = context[3].model_copy(update={"external_resource_id": external_id})
    assert (
        await resolve_subscription_cancellation(
            context[0], context[1], target, transport=context[5]
        )
        is None
    )
    assert context[4].writes == []
