import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.approvals.service import ApprovalConflictError, ApprovalService
from navox.db.base import Base
from navox.db.models import (
    Action,
    Approval,
    CancellationEvidence,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    Merchant,
    PlanSource,
    RecurringObligation,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.subscriptions.cancellation import (
    abort_cancellation,
    confirm_cancellation,
    execute_cancellation,
    prepare_cancellation,
    verify_cancellation,
)
from navox.subscriptions.cancellation_contracts import (
    CancellationPreview,
    CancellationSubmission,
    CancellationVerification,
)


class Provider:
    def __init__(self):
        self.writes = 0
        self.revision = "revision-1"
        self.submission_status = "SUBMITTED"
        self.verification_status = "VERIFIED_CANCELLED"
        self.verification_source = "provider_state"
        self.verification_target = "subscription-1"
        self.verification_age = timedelta(0)
        self.timeout = False
        self.wrong_preview_target = False

    async def inspect(self, target):
        now = datetime.now(UTC)
        if self.wrong_preview_target:
            target = target.model_copy(update={"obligation_id": uuid4()})
        return CancellationPreview(
            target=target,
            method="CONNECTED_PROVIDER_ACTION",
            provider_revision=self.revision,
            provider_state="active",
            payload={"state": "active"},
            expected_effect="Disable renewal; retain current paid access.",
            inspected_at=now,
            expires_at=now + timedelta(minutes=10),
        )

    async def execute(self, preview, idempotency_key):
        self.writes += 1
        if self.timeout:
            raise TimeoutError("sensitive provider error must not persist")
        return CancellationSubmission(status=self.submission_status)

    async def verify(self, preview, submission):
        return CancellationVerification(
            status=self.verification_status,
            source_type=self.verification_source,
            external_resource_id=self.verification_target,
            provider_revision="revision-2",
            evidence={"state": "cancelled", "auto_renew": False},
            observed_at=datetime.now(UTC) - self.verification_age,
        )


@pytest_asyncio.fixture
async def env() -> AsyncIterator[SimpleNamespace]:
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"cancellation_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(id=uuid4(), email=f"{uuid4()}@example.com")
        workspace = Workspace(id=uuid4(), name="Private")
        db.add_all([user, workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id))
        merchant = Merchant(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            canonical_name="Example",
            normalized_name="example",
            website_domain="example.com",
        )
        connection = Connection(
            id=uuid4(),
            user_id=user.id,
            workspace_id=workspace.id,
            provider="example",
            external_account_id="owner",
            status="active",
        )
        definition = ConnectorDefinition(
            id=uuid4(),
            connector_key="example",
            version="1.0.0",
            display_name="Example",
            connector_class="generic_rest_api",
            trust_level="operator_reviewed",
            manifest={},
        )
        db.add_all([merchant, connection, definition])
        await db.flush()
        native = ConnectorConnection(
            id=uuid4(),
            connector_definition_id=definition.id,
            legacy_connection_id=connection.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="example",
            external_account_id="owner",
            status="CONNECTED",
            health_state="CONNECTED",
            authorized_capabilities=["subscription.cancel"],
            provider_capabilities=["subscription.cancel"],
        )
        obligation = RecurringObligation(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            merchant_id=merchant.id,
            name="Example Pro",
            plan_name="Pro",
            status="ACTIVE",
            billing_amount=Decimal("12.0000"),
            billing_currency="USD",
            billing_interval="MONTH",
            auto_renew=True,
            cancellation_connection_id=connection.id,
            cancellation_external_resource_id="subscription-1",
        )
        db.add_all([native, obligation])
        await db.commit()
        yield SimpleNamespace(
            db=db,
            user=user,
            workspace=workspace,
            native=native,
            obligation=obligation,
            connection=connection,
            provider=Provider(),
            factory=factory,
        )
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def prepare(env):
    return await prepare_cancellation(
        env.db,
        workspace_id=env.workspace.id,
        user_id=env.user.id,
        obligation_id=env.obligation.id,
        request_id=uuid4(),
        capability=env.provider,
    )


async def confirm(env, attempt):
    return await confirm_cancellation(
        env.db,
        workspace_id=env.workspace.id,
        user_id=env.user.id,
        attempt_id=attempt.id,
        request_id=uuid4(),
        preview_hash=attempt.payload_hash,
    )


@pytest.mark.asyncio
async def test_complete_r4_cancellation_has_independent_evidence(env):
    attempt = await prepare(env)
    assert attempt.status == "AWAITING_CONFIRMATION"
    assert env.provider.writes == 0
    assert env.obligation.status == "ACTIVE"
    action = await env.db.get(Action, attempt.action_id)
    assert action.risk_level == "R4"
    assert await env.db.scalar(select(PlanSource.connection_id)) == env.connection.id
    await confirm(env, attempt)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert env.provider.writes == 1
    assert env.obligation.status == "CANCEL_PENDING"
    await verify_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert attempt.status == "VERIFIED_CANCELLED"
    assert env.obligation.status == "CANCELLED"
    assert not env.obligation.auto_renew
    evidence = await env.db.scalar(select(CancellationEvidence))
    assert evidence.verification_status == "VERIFIED_CANCELLED"
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert env.provider.writes == 1


@pytest.mark.asyncio
async def test_prevented_renewals_requires_confirmed_independently_verified_future_cycle(env):
    from navox.subscriptions.metrics import prevented_renewals

    env.obligation.next_renewal_at = datetime.now(UTC) + timedelta(days=5)
    env.obligation.billing_amount = Decimal("119.99")
    await env.db.commit()
    attempt = await prepare(env)
    metric = await prevented_renewals(env.db, workspace_id=env.workspace.id, user_id=env.user.id)
    assert metric.count == 0
    await confirm(env, attempt)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert (
        await prevented_renewals(env.db, workspace_id=env.workspace.id, user_id=env.user.id)
    ).count == 0
    await verify_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    metric = await prevented_renewals(env.db, workspace_id=env.workspace.id, user_id=env.user.id)
    assert metric.count == 1
    assert metric.currency_totals[0].renewal_amount == Decimal("119.99")
    assert (await prevented_renewals(env.db, workspace_id=uuid4(), user_id=env.user.id)).count == 0
    attempt.verification_status = "CONTRADICTED"
    await env.db.flush()
    assert (
        await prevented_renewals(env.db, workspace_id=env.workspace.id, user_id=env.user.id)
    ).count == 0


@pytest.mark.asyncio
async def test_prepare_alone_and_generic_approval_cannot_cancel(env):
    attempt = await prepare(env)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert env.provider.writes == 0
    with pytest.raises(ApprovalConflictError, match="exact R4"):
        await ApprovalService().approve(
            env.db,
            action_id=attempt.action_id,
            user_id=env.user.id,
            workspace_id=env.workspace.id,
            request_id=uuid4(),
        )
    assert env.provider.writes == 0


@pytest.mark.asyncio
async def test_already_disabled_renewal_does_not_inflate_prevention_metric(env):
    from navox.subscriptions.metrics import prevented_renewals

    env.obligation.auto_renew = False
    env.obligation.next_renewal_at = datetime.now(UTC) + timedelta(days=5)
    await env.db.commit()
    attempt = await prepare(env)
    await confirm(env, attempt)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    await verify_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    metric = await prevented_renewals(env.db, workspace_id=env.workspace.id, user_id=env.user.id)
    assert metric.count == 0


@pytest.mark.asyncio
async def test_replayed_confirmation_is_rejected(env):
    attempt = await prepare(env)
    await confirm(env, attempt)
    with pytest.raises(HTTPException):
        await confirm(env, attempt)
    await env.db.rollback()
    assert env.provider.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["payload", "revision", "provider_revision", "grant", "paused", "expired"]
)
async def test_dispatch_rechecks_exact_target_and_authority(env, mutation):
    attempt = await prepare(env)
    await confirm(env, attempt)
    attempt_id = attempt.id
    if mutation == "payload":
        action = await env.db.get(Action, attempt.action_id)
        action.payload = {**action.payload, "expected_effect": "different plan"}
    elif mutation == "revision":
        env.obligation.revision += 1
    elif mutation == "provider_revision":
        env.provider.revision = "changed"
    elif mutation == "grant":
        env.native.authorized_capabilities = []
    elif mutation == "paused":
        env.user.agent_paused = True
    elif mutation == "expired":
        approval = await env.db.get(Approval, attempt.approval_id)
        approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await env.db.commit()
    try:
        result = await execute_cancellation(env.db, attempt_id=attempt_id, capability=env.provider)
        assert result.status == "FAILED"
    except HTTPException as error:
        assert error.status_code in {403, 409}
        await env.db.rollback()
    assert env.provider.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["wrong_target", "stale", "user_confirmation", "email"])
async def test_untrusted_or_stale_verification_never_marks_cancelled(env, mutation):
    attempt = await prepare(env)
    await confirm(env, attempt)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    if mutation == "wrong_target":
        env.provider.verification_target = "other-subscription"
    elif mutation == "stale":
        env.provider.verification_age = timedelta(hours=1)
    elif mutation == "user_confirmation":
        env.provider.verification_source = "user_confirmation"
    else:
        env.provider.verification_source = "confirmation_email"
    await verify_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert attempt.status == "VERIFICATION_PENDING"
    assert env.obligation.status != "CANCELLED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "challenge"])
async def test_uncertain_and_security_challenge_never_retry_write(env, mode):
    attempt = await prepare(env)
    await confirm(env, attempt)
    if mode == "timeout":
        env.provider.timeout = True
    else:
        env.provider.submission_status = "AWAITING_USER"
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert env.provider.writes == 1
    assert attempt.status == ("AWAITING_USER" if mode == "challenge" else "VERIFICATION_PENDING")
    assert "sensitive" not in str(attempt.attempt_metadata)
    assert env.obligation.status == "CANCEL_PENDING"


@pytest.mark.asyncio
async def test_wrong_preview_target_and_cross_workspace_confirmation(env):
    env.provider.wrong_preview_target = True
    with pytest.raises(HTTPException):
        await prepare(env)
    env.provider.wrong_preview_target = False
    attempt = await prepare(env)
    with pytest.raises(HTTPException) as error:
        await confirm_cancellation(
            env.db,
            workspace_id=uuid4(),
            user_id=env.user.id,
            attempt_id=attempt.id,
            request_id=uuid4(),
            preview_hash=attempt.payload_hash,
        )
    assert error.value.status_code == 404
    assert env.provider.writes == 0


@pytest.mark.asyncio
async def test_abort_invalidates_pending_exact_approval(env):
    attempt = await prepare(env)
    await abort_cancellation(
        env.db, workspace_id=env.workspace.id, user_id=env.user.id, attempt_id=attempt.id
    )
    assert attempt.status == "ABORTED"
    with pytest.raises(HTTPException):
        await confirm(env, attempt)
    assert env.provider.writes == 0


@pytest.mark.asyncio
async def test_provider_contradiction_reopens_review(env):
    attempt = await prepare(env)
    await confirm(env, attempt)
    await execute_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    await verify_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    env.provider.verification_status = "VERIFIED_ACTIVE"
    await verify_cancellation(env.db, attempt_id=attempt.id, capability=env.provider)
    assert attempt.verification_status == "CONTRADICTED"
    assert env.obligation.status == "UNKNOWN"
