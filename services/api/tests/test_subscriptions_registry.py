"""Registry money, provenance, ownership and mutation contract regressions."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.db.base import Base
from navox.db.models import (
    Connection,
    Merchant,
    RecurringObligation,
    RecurringObligationEvidence,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.subscriptions.billing import cycle_fingerprint, equivalents
from navox.subscriptions.schemas import (
    SubscriptionCreate,
    SubscriptionKeep,
    SubscriptionPatch,
    SubscriptionReview,
)
from navox.subscriptions.service import (
    apply_cancellation_binding,
    create_subscription,
    get_subscription,
    keep_subscription,
    list_evidence,
    list_history,
    list_subscriptions,
    list_trials,
    list_upcoming,
    review_subscription,
    subscription_summary,
    to_subscription_read,
    update_subscription,
    validate_source_connection,
)


@pytest_asyncio.fixture
async def env():
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"registry_{uuid4().hex}"
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
        owner = User(email="owner@example.test")
        other = User(email="other@example.test")
        workspace = Workspace(name="Owner")
        db.add_all([owner, other, workspace])
        await db.flush()
        db.add_all(
            [
                WorkspaceMembership(workspace_id=workspace.id, user_id=owner.id),
                WorkspaceMembership(workspace_id=workspace.id, user_id=other.id),
            ]
        )
        connection = Connection(
            workspace_id=workspace.id,
            user_id=owner.id,
            provider="google",
            external_account_id="owner",
            status="active",
        )
        foreign = Connection(
            workspace_id=workspace.id,
            user_id=other.id,
            provider="google",
            external_account_id="other",
            status="active",
        )
        db.add_all([connection, foreign])
        await db.commit()
        yield SimpleNamespace(
            db=db,
            factory=factory,
            workspace_id=workspace.id,
            user_id=owner.id,
            other_id=other.id,
            connection_id=connection.id,
            foreign_id=foreign.id,
        )
    await engine.dispose()
    if admin is not None:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def scope(env):
    return {"workspace_id": env.workspace_id, "user_id": env.user_id}


async def create(env, **fields):
    return await create_subscription(
        env.db, **scope(env), payload=SubscriptionCreate(name="Example", **fields)
    )


@pytest.mark.parametrize(
    ("amount", "interval", "count", "monthly", "yearly"),
    [
        ("12", "MONTH", 1, "12.00", "144.00"),
        ("120", "YEAR", 1, "10.00", "120.00"),
        ("45", "QUARTER", 1, "15.00", "180.00"),
        ("3", "WEEK", 1, "13.00", "156.00"),
        ("24", "MONTH", 3, "8.00", "96.00"),
        ("1", "YEAR", 1, "0.08", "1.00"),
    ],
)
def test_interval_math_keeps_annual_precision(amount, interval, count, monthly, yearly):
    assert equivalents(Decimal(amount), interval, count) == (Decimal(monthly), Decimal(yearly))


def test_unknown_interval_is_not_zero_cost():
    assert equivalents(Decimal("12"), "UNKNOWN") == (None, None)
    assert equivalents(None, "MONTH") == (None, None)


@pytest.mark.parametrize(
    "fields",
    [
        {"billing_amount": "NaN", "billing_currency": "USD"},
        {"billing_amount": "-1", "billing_currency": "USD"},
        {"billing_amount": "1.00001", "billing_currency": "USD"},
        {"billing_amount": "1"},
        {"interval_count": 0},
        {"next_renewal_at": "2026-10-01T00:00:00"},
        {"status": "CANCELLED"},
        {"cancellation_connection_id": str(uuid4())},
        {"name": "  "},
    ],
)
def test_manual_validation_rejects_incomplete_or_unverified_state(fields):
    with pytest.raises(ValidationError):
        SubscriptionCreate.model_validate({"name": "Example", **fields})


@pytest.mark.asyncio
async def test_create_has_immutable_evidence_and_retry_exactly_once(env):
    request = uuid4()
    row = await create(
        env,
        billing_amount="12.30",
        billing_currency="USD",
        billing_interval="MONTH",
        request_id=request,
    )
    again = await create(
        env,
        billing_amount="12.30",
        billing_currency="USD",
        billing_interval="MONTH",
        request_id=request,
    )
    assert again.id == row.id
    assert len(await list_evidence(env.db, **scope(env), obligation_id=row.id)) == 1
    assert len(await list_history(env.db, **scope(env), obligation_id=row.id)) == 1
    assert to_subscription_read(row).monthly_equivalent == Decimal("12.30")
    with pytest.raises(HTTPException) as conflict:
        await create(
            env,
            billing_amount="99",
            billing_currency="USD",
            billing_interval="MONTH",
            request_id=request,
        )
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_updates_preserve_evidence_and_effective_price_history(env):
    row = await create(env, billing_amount="10", billing_currency="USD", billing_interval="MONTH")
    before = (await list_evidence(env.db, **scope(env), obligation_id=row.id))[0]
    await update_subscription(
        env.db,
        **scope(env),
        obligation_id=row.id,
        payload=SubscriptionPatch(billing_amount=Decimal("12")),
    )
    evidence = await list_evidence(env.db, **scope(env), obligation_id=row.id)
    assert len(evidence) == 2
    assert next(item for item in evidence if item.id == before.id).amount == Decimal("10")
    history = await list_history(env.db, **scope(env), obligation_id=row.id)
    assert len(history) == 2
    assert history[0].effective_until is not None
    assert history[1].amount == Decimal("12")
    assert row.revision == 2
    override = await env.db.scalar(
        select(RecurringObligationEvidence).where(
            RecurringObligationEvidence.evidence_type == "USER_OVERRIDE"
        )
    )
    assert override.evidence_metadata["changed_fields"] == ["billing_amount"]


@pytest.mark.asyncio
async def test_cannot_clear_currency_but_keep_existing_amount(env):
    row = await create(env, billing_amount="10", billing_currency="USD")
    with pytest.raises(HTTPException) as error:
        await update_subscription(
            env.db,
            **scope(env),
            obligation_id=row.id,
            payload=SubscriptionPatch(billing_currency=None),
        )
    assert error.value.status_code == 422
    assert row.billing_currency == "USD"


@pytest.mark.asyncio
async def test_summary_keeps_currencies_and_unknown_coverage_separate(env):
    await create(env, billing_amount="10", billing_currency="USD", billing_interval="MONTH")
    await create(env, billing_amount="120", billing_currency="EUR", billing_interval="YEAR")
    await create(env)
    await create(
        env, status="PAUSED", billing_amount="999", billing_currency="USD", billing_interval="MONTH"
    )
    result = await subscription_summary(env.db, **scope(env))
    assert result.known_count == 3
    assert result.unknown_cost_count == 1
    assert result.coverage == "INCOMPLETE"
    assert {item.currency: item.monthly_equivalent for item in result.currency_totals} == {
        "EUR": Decimal("10"),
        "USD": Decimal("10"),
    }


@pytest.mark.asyncio
async def test_cross_owner_and_workspace_ids_are_hidden(env):
    row = await create(env)
    assert (
        await list_subscriptions(env.db, workspace_id=env.workspace_id, user_id=env.other_id) == []
    )
    for workspace_id, user_id in [(env.workspace_id, env.other_id), (uuid4(), env.user_id)]:
        with pytest.raises(HTTPException) as error:
            await get_subscription(
                env.db, workspace_id=workspace_id, user_id=user_id, obligation_id=row.id
            )
        assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await validate_source_connection(env.db, **scope(env), connection_id=env.foreign_id)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_keep_is_cycle_scoped_and_price_change_invalidates(env):
    now = datetime.now(UTC)
    row = await create(
        env,
        billing_amount="10",
        billing_currency="USD",
        billing_interval="MONTH",
        next_renewal_at=now + timedelta(days=2),
    )
    await keep_subscription(env.db, **scope(env), obligation_id=row.id, payload=SubscriptionKeep())
    assert row.kept_cycle_fingerprint == cycle_fingerprint(row)
    await update_subscription(
        env.db,
        **scope(env),
        obligation_id=row.id,
        payload=SubscriptionPatch(billing_amount=Decimal("11")),
    )
    assert row.kept_cycle_fingerprint is None
    assert row.review_state == "CONFIRMED"


@pytest.mark.asyncio
async def test_trials_upcoming_and_not_mine_review(env):
    now = datetime.now(UTC)
    row = await create(
        env,
        status="TRIAL",
        trial_ends_at=now + timedelta(days=2),
        next_renewal_at=now + timedelta(days=2),
    )
    assert [item.id for item in await list_trials(env.db, **scope(env))] == [row.id]
    assert [item.id for item in await list_upcoming(env.db, **scope(env))] == [row.id]
    await review_subscription(
        env.db, **scope(env), obligation_id=row.id, payload=SubscriptionReview(decision="NOT_MINE")
    )
    assert await list_trials(env.db, **scope(env)) == []
    assert await list_upcoming(env.db, **scope(env)) == []
    assert row.status == "TRIAL"
    assert row.revision == 2


@pytest.mark.asyncio
async def test_snooze_requires_future_date(env):
    row = await create(env)
    with pytest.raises(HTTPException) as error:
        await review_subscription(
            env.db,
            **scope(env),
            obligation_id=row.id,
            payload=SubscriptionReview(decision="SNOOZE"),
        )
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_binding_requires_owned_live_connection_and_exact_merchant(env):
    row = await create(env)
    request = uuid4()
    bound = await apply_cancellation_binding(
        env.db,
        **scope(env),
        obligation_id=row.id,
        connection_id=env.connection_id,
        external_resource_id="sub_123",
        verified_merchant_domain="example.test",
        request_id=request,
    )
    assert bound.revision == 2
    assert bound.cancellation_connection_id == env.connection_id
    again = await apply_cancellation_binding(
        env.db,
        **scope(env),
        obligation_id=row.id,
        connection_id=env.connection_id,
        external_resource_id="sub_123",
        verified_merchant_domain="example.test",
        request_id=request,
    )
    assert again.revision == 2
    with pytest.raises(HTTPException) as error:
        await apply_cancellation_binding(
            env.db,
            **scope(env),
            obligation_id=row.id,
            connection_id=env.connection_id,
            external_resource_id="sub_other",
            verified_merchant_domain="other.test",
            request_id=uuid4(),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_account_deletion_cascades_registry(env):
    await create(env)
    # Explicit FK behavior is also exercised by the PostgreSQL CI variant.
    await env.db.execute(delete(User).where(User.id == env.user_id))
    await env.db.commit()
    if os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"):
        assert await env.db.scalar(select(func.count()).select_from(RecurringObligation)) == 0
        assert await env.db.scalar(select(func.count()).select_from(Merchant)) == 0
