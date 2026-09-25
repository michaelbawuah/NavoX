"""Source erasure includes registry, copied corrections and Today projections."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from navox.connectors.lifecycle import delete_learned_data
from navox.db.models import (
    CancellationAttempt,
    Commitment,
    Merchant,
    RecurringObligation,
    RecurringObligationEvidence,
    SubscriptionEvent,
)
from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.subscriptions.events import project_pending_events, reevaluate_obligation
from navox.subscriptions.resolution import ingest_source_document
from navox.subscriptions.schemas import SubscriptionCreate, SubscriptionPatch
from navox.subscriptions.service import create_subscription, update_subscription


async def discover(env):
    now = datetime.now(UTC)
    result = await ingest_source_document(
        env.database,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        connection_id=env.connection.id,
        document=SourceDocument(
            id=uuid4(),
            workspace_id=env.workspace_id,
            provider="google",
            source_type="email",
            external_id="receipt",
            subject="Account notice",
            author=SourceIdentity(
                identity_type="email",
                identity_value="billing@example.net",
                display_name="Private source name",
            ),
            content=f"Your subscription renews on {(now + timedelta(days=2)).date()}.\n"
            "Amount: USD 10 monthly",
            occurred_at=now,
            retrieved_at=now,
        ),
    )
    row = await env.database.get(RecurringObligation, result.obligation_id)
    await reevaluate_obligation(env.database, obligation=row)
    await project_pending_events(env.database, workspace_id=env.workspace_id, user_id=env.user_id)
    await env.database.commit()
    return row


async def erase(env):
    env.connection.status = "disconnected"
    await env.database.commit()
    await delete_learned_data(
        env.database,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        connection_id=env.connection.id,
        request_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_deleting_only_source_erases_all_derived_registry_and_attention(subscription_env):
    env = subscription_env
    await discover(env)
    assert await env.database.scalar(select(func.count()).select_from(Commitment)) == 1
    await erase(env)
    for model in (
        RecurringObligation,
        RecurringObligationEvidence,
        Merchant,
        Commitment,
        SubscriptionEvent,
    ):
        assert await env.database.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_deletion_preserves_independently_added_manual_subscription(subscription_env):
    env = subscription_env
    await discover(env)
    manual = await create_subscription(
        env.database,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        payload=SubscriptionCreate(
            name="Independent manual",
            billing_amount="15",
            billing_currency="EUR",
            billing_interval="MONTH",
        ),
    )
    identifier = manual.id
    await erase(env)
    assert (await env.database.get(RecurringObligation, identifier)).name == "Independent manual"
    assert await env.database.scalar(select(func.count()).select_from(RecurringObligation)) == 1


@pytest.mark.asyncio
async def test_partial_correction_cannot_preserve_source_snapshot_without_evidence(
    subscription_env,
):
    env = subscription_env
    row = await discover(env)
    identifier = row.id
    await update_subscription(
        env.database,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        obligation_id=identifier,
        payload=SubscriptionPatch(billing_amount="12"),
    )
    with pytest.raises(HTTPException, match="provenance"):
        await erase(env)
    await env.database.rollback()
    assert await env.database.get(RecurringObligation, identifier) is not None
    assert (
        await env.database.scalar(select(func.count()).select_from(RecurringObligationEvidence))
        == 2
    )


@pytest.mark.asyncio
async def test_pending_cancellation_blocks_erasure_without_destroying_execution_fence(
    subscription_env,
):
    env = subscription_env
    row = await discover(env)
    attempt = CancellationAttempt(
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        obligation_id=row.id,
        method="PROVIDER_API",
        status="IN_PROGRESS",
        request_id=uuid4(),
        payload_hash="a" * 64,
        preview={},
    )
    env.database.add(attempt)
    await env.database.commit()
    identifier = attempt.id
    with pytest.raises(HTTPException, match="Resolve or abort"):
        await erase(env)
    await env.database.rollback()
    assert (await env.database.get(CancellationAttempt, identifier)).status == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_foreign_subscription_edge_blocks_source_erasure(subscription_env):
    env = subscription_env
    row = await discover(env)
    source = await env.database.scalar(
        select(RecurringObligationEvidence).where(
            RecurringObligationEvidence.obligation_id == row.id
        )
    )
    foreign_id = uuid4()
    # Foreign-key-valid foreign owner, but an invalid source ownership edge.
    from navox.db.models import User

    env.database.add(User(id=foreign_id, email="foreign@example.com"))
    await env.database.flush()
    source.user_id = foreign_id
    await env.database.commit()
    with pytest.raises(HTTPException, match="provenance"):
        await erase(env)
