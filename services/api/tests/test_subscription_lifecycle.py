"""Subscription facts use the existing attention engine; fixtures do not prove live cancellation."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.db.base import Base
from navox.db.models import (
    CancellationAttempt,
    Commitment,
    CommitmentSource,
    Connection,
    Merchant,
    RecurringObligation,
    RecurringObligationEvidence,
    SubscriptionEvent,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence import ingestion
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import InvalidOperationalExtraction
from navox.subscriptions import activities
from navox.subscriptions.activities import SubscriptionState, SubscriptionWork
from navox.subscriptions.billing import cycle_fingerprint
from navox.subscriptions.events import project_pending_events, queue_event, reevaluate_obligation
from navox.workflows import subscriptions

NOW = datetime(2026, 9, 25, 16, tzinfo=UTC)


@pytest_asyncio.fixture
async def env():
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"subscription_lifecycle_{uuid4().hex}"
    administrative = None
    if dsn.startswith("postgresql"):
        administrative = create_async_engine(dsn)
        async with administrative.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(
            id=uuid4(), email="subscription@example.com", timezone="UTC", agent_paused=False
        )
        workspace = Workspace(id=uuid4(), name="Private")
        db.add_all([user, workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id, role="owner"))
        merchant = Merchant(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            canonical_name="Example",
            normalized_name="example",
            merchant_metadata={},
        )
        db.add(merchant)
        await db.flush()
        obligation = RecurringObligation(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            merchant_id=merchant.id,
            name="Example plan",
            status="ACTIVE",
            confidence=Decimal("1"),
            billing_amount=Decimal("12.00"),
            billing_currency="USD",
            billing_interval="MONTH",
            interval_count=1,
            next_renewal_at=NOW + timedelta(days=1),
            revision=1,
            obligation_metadata={},
            review_state="CONFIRMED",
        )
        db.add(obligation)
        await db.flush()
        db.add(
            RecurringObligationEvidence(
                id=uuid4(),
                workspace_id=workspace.id,
                user_id=user.id,
                obligation_id=obligation.id,
                evidence_type="MANUAL",
                source_type="manual",
                dedupe_key=uuid4().hex,
                evidence_metadata={},
                observed_at=NOW,
            )
        )
        await db.commit()
        yield SimpleNamespace(
            db=db, factory=factory, user=user, workspace=workspace, obligation=obligation
        )
    await engine.dispose()
    if administrative is not None:
        async with administrative.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await administrative.dispose()


async def drain(env, now=NOW):
    await reevaluate_obligation(env.db, obligation=env.obligation, now=now)
    return await project_pending_events(
        env.db, workspace_id=env.workspace.id, user_id=env.user.id, now=now
    )


@pytest.mark.asyncio
async def test_outbox_replay_creates_one_existing_today_commitment_and_manual_provenance(env):
    assert await drain(env) == 1
    await env.db.commit()
    assert await drain(env) == 0
    assert await env.db.scalar(select(func.count()).select_from(SubscriptionEvent)) == 1
    card = await env.db.scalar(select(Commitment))
    source = await env.db.scalar(select(CommitmentSource))
    assert card.commitment_type == "renewal"
    assert card.attention_score > 0
    assert source.provider == "navox" and source.source_type == "manual"
    assert source.connection_id is None
    assert card.intelligence_metadata["subscription_id"] == str(env.obligation.id)


@pytest.mark.asyncio
async def test_keep_suppresses_same_cycle_but_material_price_change_reopens_attention(env):
    await drain(env)
    first = await env.db.scalar(select(Commitment))
    env.obligation.kept_cycle_fingerprint = cycle_fingerprint(env.obligation)
    env.obligation.review_state = "KEPT"
    await drain(env)
    assert first.attention_score == 0
    assert first.status == "superseded"
    env.obligation.billing_amount = Decimal("15")
    env.obligation.revision += 1
    await queue_event(env.db, env.obligation, "obligation.price_changed", "price-2", {})
    await drain(env)
    cards = list(await env.db.scalars(select(Commitment)))
    active = [card for card in cards if card.attention_score > 0]
    assert any(
        card.intelligence_metadata["subscription_event_type"] == "obligation.price_changed"
        for card in active
    )
    assert first.attention_score == 0


@pytest.mark.asyncio
async def test_review_later_expires_without_new_event_or_separate_reminder(env):
    env.obligation.review_after = NOW + timedelta(hours=2)
    await drain(env)
    card = await env.db.scalar(select(Commitment))
    assert card.attention_score == 0
    await drain(env, NOW + timedelta(hours=3))
    assert card.attention_score > 0
    assert await env.db.scalar(select(func.count()).select_from(SubscriptionEvent)) == 1


@pytest.mark.asyncio
async def test_trial_clock_never_infers_payment_or_conversion_and_supersedes_ending_card(env):
    env.obligation.status = "TRIAL"
    env.obligation.next_renewal_at = None
    env.obligation.trial_ends_at = NOW + timedelta(hours=1)
    await drain(env)
    await drain(env, NOW + timedelta(hours=2))
    assert env.obligation.status == "TRIAL"
    events = list(await env.db.scalars(select(SubscriptionEvent)))
    assert {event.event_type for event in events} == {"trial.ending", "trial.ended"}
    ending = next(event for event in events if event.event_type == "trial.ending")
    assert (await env.db.get(Commitment, ending.commitment_id)).attention_score == 0


@pytest.mark.asyncio
async def test_confirming_uncertain_discovery_retires_review_card(env):
    env.obligation.status = "CANDIDATE"
    await queue_event(env.db, env.obligation, "obligation.discovered", "discovered", {})
    await drain(env)
    event = await env.db.scalar(
        select(SubscriptionEvent).where(SubscriptionEvent.event_type == "obligation.discovered")
    )
    card = await env.db.get(Commitment, event.commitment_id)
    assert card.status == "candidate"
    env.obligation.status = "ACTIVE"
    await drain(env)
    assert card.attention_score == 0


@pytest.mark.asyncio
async def test_foreign_event_obligation_is_rejected_before_projection(env):
    second = Workspace(id=uuid4(), name="Another")
    env.db.add(second)
    await env.db.flush()
    env.db.add(
        SubscriptionEvent(
            id=uuid4(),
            workspace_id=env.workspace.id,
            user_id=env.user.id,
            obligation_id=env.obligation.id,
            event_type="trial.ending",
            dedupe_key=uuid4().hex,
            payload={},
        )
    )
    env.obligation.workspace_id = second.id
    await env.db.flush()
    with pytest.raises(PermissionError, match="foreign obligation"):
        await project_pending_events(env.db, workspace_id=env.workspace.id, user_id=env.user.id)


@pytest.mark.asyncio
async def test_paused_owner_leaves_outbox_pending_for_resume(env):
    env.user.agent_paused = True
    assert await drain(env) == 0
    event = await env.db.scalar(select(SubscriptionEvent))
    assert event.delivery_state == "PENDING"
    env.user.agent_paused = False
    assert await drain(env) == 1


@pytest.mark.asyncio
async def test_rejected_spec002_card_is_not_resurrected_by_keep_projection(env):
    await drain(env)
    card = await env.db.scalar(select(Commitment))
    card.status = "rejected"
    env.obligation.kept_cycle_fingerprint = cycle_fingerprint(env.obligation)
    await drain(env)
    env.obligation.kept_cycle_fingerprint = None
    await drain(env)
    assert card.status == "rejected" and card.attention_score == 0


@pytest.mark.asyncio
async def test_rejected_operational_extraction_still_runs_authorized_subscription_discovery(
    env, monkeypatch
):
    connection = Connection(
        id=uuid4(),
        workspace_id=env.workspace.id,
        user_id=env.user.id,
        provider="google",
        external_account_id="test",
        status="active",
        granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    env.db.add(connection)
    await env.db.commit()
    calls = []

    async def discover(db, **kwargs):
        calls.append(kwargs)

    class RejectingExtractor:
        extractor_version = "fixture-v1"
        receipt_versions = ("fixture-v1",)

        async def extract(self, document, **kwargs):
            raise InvalidOperationalExtraction()

    monkeypatch.setattr(ingestion, "ingest_source_document", discover)
    document = SourceDocument(
        id=uuid4(),
        workspace_id=env.workspace.id,
        provider="google",
        source_type="gmail_message",
        external_id="receipt-1",
        content="Your plan renews at USD12 per month.",
        occurred_at=NOW,
        retrieved_at=NOW,
    )
    _, outcome = await ingestion._process_document(
        env.db,
        connection=connection,
        user=env.user,
        document=document,
        source="gmail",
        extractor=RejectingExtractor(),
        mirror_source=False,
    )
    assert outcome == "rejected"
    assert len(calls) == 1 and calls[0]["connection_id"] == connection.id


@pytest.mark.asyncio
async def test_cancellation_recovery_only_verifies_ambiguous_submission(env, monkeypatch):
    attempt = CancellationAttempt(
        id=uuid4(),
        workspace_id=env.workspace.id,
        user_id=env.user.id,
        obligation_id=env.obligation.id,
        method="PROVIDER_API",
        status="IN_PROGRESS",
        request_id=uuid4(),
        payload_hash="a" * 64,
        preview={},
        confirmed_at=NOW,
        attempt_metadata={"verification_rechecks": 4},
    )
    env.db.add(attempt)
    await env.db.commit()
    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    payload = SubscriptionWork(
        str(env.workspace.id), str(env.user.id), str(env.obligation.id), str(attempt.id)
    )
    assert (await activities.cancellation_state_activity(payload)).status == "review_required"
    assert all(
        item.attempt_id != str(attempt.id)
        for item in await activities.pending_subscriptions_activity()
    )


@pytest.mark.asyncio
async def test_temporal_submission_is_never_automatically_retried(monkeypatch):
    payload = SubscriptionWork(str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4()))
    options = []

    async def execute(fn, received, **kwargs):
        options.append((fn, kwargs))
        if fn is activities.cancellation_state_activity:
            return SubscriptionState("confirmed")
        return SubscriptionState("IN_PROGRESS")

    async def child(fn, received, **kwargs):
        assert fn is subscriptions.VerifyCancellationWorkflow.run
        return "review_required"

    monkeypatch.setattr(subscriptions.workflow, "execute_activity", execute)
    monkeypatch.setattr(subscriptions.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(subscriptions.workflow, "uuid4", uuid4)
    assert await subscriptions.CancelSubscriptionWorkflow().run(payload) == "review_required"
    writes = [option for fn, option in options if fn is activities.execute_cancellation_activity]
    assert len(writes) == 1 and writes[0]["retry_policy"].maximum_attempts == 1


@pytest.mark.asyncio
async def test_old_lifecycle_revision_is_fenced_after_material_change(env, monkeypatch):
    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    payload = SubscriptionWork(
        str(env.workspace.id), str(env.user.id), str(env.obligation.id), revision=0
    )
    assert (await activities.reevaluate_subscription_activity(payload)).status == "superseded"
    assert await env.db.scalar(select(func.count()).select_from(SubscriptionEvent)) == 0
