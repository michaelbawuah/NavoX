"""Synthetic server accounting; no microphone, provider, or invoice qualification."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from test_vox_calls import start
from test_vox_calls import vox_env as _shared_vox_env

from navox.db.models import User
from navox.db.vox import VoxCall
from navox.db.vox_usage import VoxUsageDelta, VoxUsageEvent, VoxUsageStream
from navox.vox import usage
from navox.vox.usage_schemas import ModelPricing, UsageCounters, UsagePolicy, UsageUpdate

vox_env = _shared_vox_env


@pytest.mark.parametrize("field", ["speaking_ms", "idle_ms", "input_text", "cached_audio"])
def test_negative_or_browser_shaped_usage_is_rejected(field):
    with pytest.raises(ValidationError):
        UsageCounters.model_validate({field: -1})
    with pytest.raises(ValidationError):
        UsageCounters.model_validate({"transcript": "private body"})


def test_cached_tokens_are_subsets_and_idle_is_separate_from_speaking():
    with pytest.raises(ValidationError):
        UsageCounters(input_text=3, cached_text=4)
    with pytest.raises(ValidationError):
        UsageCounters(speaking_ms=3, idle_ms=4, connected_ms=3)
    assert UsageCounters(speaking_ms=3, idle_ms=4, connected_ms=7).total_tokens == 0


def test_local_day_month_and_dst_are_calendar_boundaries():
    day_start, day_end, month_start, month_end = usage.local_bounds(
        datetime(2026, 3, 8, 16, tzinfo=UTC), "America/New_York"
    )
    assert (day_end - day_start).total_seconds() == 23 * 3600
    assert day_start == datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert month_start == datetime(2026, 3, 1, 5, tzinfo=UTC)
    assert month_end == datetime(2026, 4, 1, 4, tzinfo=UTC)
    a, b, _, _ = usage.local_bounds(datetime(2026, 11, 1, 17, tzinfo=UTC), "America/New_York")
    assert (b - a).total_seconds() == 25 * 3600


def test_cost_uses_modality_cached_rates_and_unknown_is_not_zero():
    counters = UsageCounters(input_text=100, cached_text=40, output_audio=10)
    assert usage.estimate(counters, None, "provider") is None
    rates = ModelPricing(
        provider="fixture",
        model="synthetic",
        version="fixture-v1",
        input_text=Decimal(2),
        cached_text=Decimal(1),
        output_audio=Decimal(3),
    )
    assert usage.estimate(counters, rates.model_dump(mode="json"), "provider") == Decimal("0.00019")
    assert (
        usage.estimate(UsageCounters(input_audio=1), rates.model_dump(mode="json"), "provider")
        is None
    )
    connected = UsageCounters(idle_ms=60_000, connected_ms=60_000)
    assert usage.estimate(connected, {"usd_per_minute": "0.02"}, "transport") == Decimal("0.02")


async def provider(env, database, call_id, *, ceiling=100):
    stream = uuid4()
    await usage.admit_provider_stream(
        database,
        env.user_id,
        env.workspace_id,
        call_id,
        stream_id=stream,
        provider="fixture",
        model="synthetic",
        token_ceiling=ceiling,
    )
    await database.commit()
    return stream


@pytest.mark.asyncio
async def test_cumulative_retry_no_double_count_and_changed_stale_reset_rejected(vox_env):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        stream = await provider(env, database, UUID(call["id"]))
        event = UsageUpdate(event_id=uuid4(), sequence=1, cumulative=UsageCounters(input_text=10))
        await usage.apply_usage_update(database, stream, event)
        await database.commit()
        await usage.apply_usage_update(database, stream, event)
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(VoxUsageEvent)) == 1
        assert (
            await usage.budget_status(database, env.user_id, env.workspace_id)
        ).daily.tokens == 10
        for changed in [
            event.model_copy(update={"sequence": 2}),
            UsageUpdate(event_id=uuid4(), sequence=1, cumulative=UsageCounters(input_text=20)),
            UsageUpdate(event_id=uuid4(), sequence=2, cumulative=UsageCounters(input_text=9)),
        ]:
            with pytest.raises(usage.UsageError):
                await usage.apply_usage_update(database, stream, changed)
        await database.rollback()
        await usage.apply_usage_update(
            database,
            stream,
            UsageUpdate(
                event_id=uuid4(), sequence=2, cumulative=UsageCounters(input_text=20, output_text=5)
            ),
        )
        await database.commit()
        totals = await usage.call_totals(database, env.user_id, env.workspace_id, UUID(call["id"]))
        assert totals.tokens == 25 and totals.estimated_cost_usd is None


@pytest.mark.asyncio
async def test_reservations_gate_before_io_unknown_failure_holds_then_final_usage_releases(vox_env):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        await usage.update_policy(
            database, UsagePolicy(daily_tokens=100, monthly_tokens=100), expected_revision=0
        )
        stream = await provider(env, database, UUID(call["id"]), ceiling=80)
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert (
            status.remaining_tokens == 20
            and status.notices
            and status.daily.estimated_cost_usd is None
        )
        with pytest.raises(usage.UsageError) as raised:
            await provider(env, database, UUID(call["id"]), ceiling=21)
        assert raised.value.status_code == 429
        await usage.finish_usage_stream(database, stream, usage_complete=False)
        await database.commit()
        assert (
            await usage.budget_status(database, env.user_id, env.workspace_id)
        ).daily.reserved_tokens == 80
        await usage.apply_usage_update(
            database,
            stream,
            UsageUpdate(
                event_id=uuid4(), sequence=1, cumulative=UsageCounters(input_text=10, output_text=5)
            ),
        )
        await usage.finish_usage_stream(database, stream, usage_complete=True)
        await database.commit()
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert (
            status.daily.tokens == 15
            and status.daily.reserved_tokens == 0
            and status.remaining_tokens == 85
        )


@pytest.mark.asyncio
async def test_settings_revision_and_snapshot_price_do_not_need_restart(vox_env):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        policy = UsagePolicy(
            model_pricing=[
                ModelPricing(
                    provider="fixture",
                    model="synthetic",
                    version="synthetic-v1",
                    input_text=Decimal(2),
                )
            ]
        )
        revision, _ = await usage.update_policy(database, policy, expected_revision=0)
        stream = await provider(env, database, UUID(call["id"]))
        with pytest.raises(usage.UsageError):
            await usage.update_policy(database, policy, expected_revision=0)
        await usage.update_policy(
            database, UsagePolicy(daily_tokens=500), expected_revision=revision
        )
        await usage.apply_usage_update(
            database,
            stream,
            UsageUpdate(event_id=uuid4(), sequence=1, cumulative=UsageCounters(input_text=10)),
        )
        await usage.finish_usage_stream(database, stream, usage_complete=True)
        await database.commit()
        assert (
            await usage.call_totals(database, env.user_id, env.workspace_id, UUID(call["id"]))
        ).estimated_cost_usd == Decimal("0.00002")
        assert (
            await usage.budget_status(database, env.user_id, env.workspace_id)
        ).remaining_tokens == 490


@pytest.mark.asyncio
async def test_transport_server_clock_split_midnight_idle_not_voice_and_provider_separate(
    vox_env, monkeypatch
):
    env = vox_env
    call, _ = await start(env)
    clock = datetime.fromisoformat(call["created_at"].replace("Z", "+00:00"))
    # A synthetic timezone chosen to cross its midnight within the real lease.
    initial = datetime(2026, 3, 8, 4, 59, 30, tzinfo=UTC)

    async def now(_):
        return clock

    monkeypatch.setattr(usage, "database_now", now)
    async with env.factory() as database:
        row = await database.get(User, env.user_id)
        row.timezone = "America/New_York"
        stream = uuid4()
        await usage.start_usage_stream(
            database, env.user_id, env.workspace_id, UUID(call["id"]), stream_id=stream
        )
        record = await database.get(VoxUsageStream, stream)
        record.created_at = record.observed_at = initial
        clock = initial + timedelta(seconds=60)
        await usage.apply_usage_update(
            database,
            stream,
            UsageUpdate(
                event_id=uuid4(),
                sequence=1,
                cumulative=UsageCounters(speaking_ms=20_000, idle_ms=40_000, connected_ms=60_000),
            ),
        )
        await database.commit()
        deltas = list(
            await database.scalars(
                select(VoxUsageDelta)
                .where(VoxUsageDelta.stream_id == stream)
                .order_by(VoxUsageDelta.occurred_at)
            )
        )
        assert len(deltas) == 2
        assert [(d.speaking_ms, d.idle_ms) for d in deltas] == [(10_000, 20_000), (10_000, 20_000)]
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.daily.speaking_ms == 10_000 and status.daily.idle_ms == 20_000
        assert status.daily.connected_ms == 30_000 and status.daily.tokens == 0
        assert (
            status.daily.estimated_cost_usd is None
            and "idle" in status.daily.transport_billing_notice
        )
        with pytest.raises(usage.UsageError):
            await usage.apply_usage_update(
                database,
                stream,
                UsageUpdate(
                    event_id=uuid4(),
                    sequence=2,
                    cumulative=UsageCounters(speaking_ms=100_000, connected_ms=100_000),
                ),
            )


@pytest.mark.asyncio
async def test_expired_call_cannot_open_meter_but_24h_call_cleanup_cannot_reset_quota(vox_env):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        stream = await provider(env, database, UUID(call["id"]))
        await usage.apply_usage_update(
            database,
            stream,
            UsageUpdate(event_id=uuid4(), sequence=1, cumulative=UsageCounters(input_text=10)),
        )
        await usage.finish_usage_stream(database, stream, usage_complete=True)
        await database.execute(delete(VoxCall).where(VoxCall.id == UUID(call["id"])))
        await database.commit()
        assert (
            await usage.budget_status(database, env.user_id, env.workspace_id)
        ).daily.tokens == 10
        with pytest.raises(usage.UsageError):
            await provider(env, database, UUID(call["id"]))


@pytest.mark.asyncio
async def test_reconciliation_is_explicit_idempotent_and_daily_is_never_an_invoice(vox_env):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        stream = await provider(env, database, UUID(call["id"]))
        await usage.apply_usage_update(
            database,
            stream,
            UsageUpdate(event_id=uuid4(), sequence=1, cumulative=UsageCounters(input_audio=10)),
        )
        await usage.finish_usage_stream(database, stream, usage_complete=True)
        receipt = uuid4()
        for _ in range(2):
            await usage.reconcile_usage_cost(
                database, stream, reconciliation_id=receipt, cost_usd=Decimal("0.04")
            )
        await database.commit()
        total = await usage.call_totals(database, env.user_id, env.workspace_id, UUID(call["id"]))
        assert total.reconciled_cost_usd == Decimal("0.04") and total.estimated_cost_usd is None
        assert total.cost_basis == "reconciled"
        daily = (await usage.budget_status(database, env.user_id, env.workspace_id)).daily
        assert daily.reconciled_cost_usd is None and daily.cost_basis == "unknown"
        with pytest.raises(usage.UsageError):
            await usage.reconcile_usage_cost(
                database, stream, reconciliation_id=receipt, cost_usd=Decimal("0.05")
            )
        rows = await usage.admin_daily_totals(database)
        assert rows[0]["user_id"] == str(env.user_id)
        assert "email" not in str(rows) and "transcript" not in str(rows)


@pytest.mark.asyncio
async def test_active_request_carries_month_boundary_but_old_uncertain_resets(vox_env, monkeypatch):
    env = vox_env
    call, _ = await start(env)
    async with env.factory() as database:
        await usage.update_policy(
            database, UsagePolicy(daily_tokens=100, monthly_tokens=100), expected_revision=0
        )
        uncertain = await provider(env, database, UUID(call["id"]), ceiling=40)
        active = await provider(env, database, UUID(call["id"]), ceiling=30)
        await usage.finish_usage_stream(database, uncertain, usage_complete=False)
        user = await database.get(User, env.user_id)
        user.timezone = "America/New_York"
        for stream_id in (uncertain, active):
            row = await database.get(VoxUsageStream, stream_id)
            row.created_at = row.observed_at = datetime(2026, 11, 1, 3, 59, tzinfo=UTC)
        await database.commit()

        async def clock(_):
            return datetime(2026, 11, 1, 4, 1, tzinfo=UTC)

        monkeypatch.setattr(usage, "database_now", clock)
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.daily.reserved_tokens == status.monthly.reserved_tokens == 30
        assert status.remaining_tokens == 70
        await usage.finish_usage_stream(database, active, usage_complete=False)
        await database.commit()
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.daily.reserved_tokens == status.monthly.reserved_tokens == 0
        assert status.remaining_tokens == 100
        total = await usage.call_totals(database, env.user_id, env.workspace_id, UUID(call["id"]))
        assert total.estimated_cost_usd is None


@pytest.mark.asyncio
async def test_provider_admission_replay_cannot_increase_ceiling_or_restart_terminal_attempt(
    vox_env,
):
    env = vox_env
    call, _ = await start(env)
    stream = uuid4()
    async with env.factory() as database:

        async def admit(ceiling):
            return await usage.admit_provider_stream(
                database,
                env.user_id,
                env.workspace_id,
                UUID(call["id"]),
                stream_id=stream,
                provider="synthetic",
                model="fixture",
                token_ceiling=ceiling,
            )

        original = await admit(80)
        assert (await admit(80)).id == original.id
        with pytest.raises(usage.UsageError, match="ceiling changed"):
            await admit(81)
        await usage.finish_usage_stream(database, stream, usage_complete=False)
        with pytest.raises(usage.UsageError, match="already ended"):
            await admit(80)
