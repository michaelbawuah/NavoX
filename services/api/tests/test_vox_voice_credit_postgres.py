"""Actual PostgreSQL voice-credit locks, stale epochs, periods and guarded DDL.

Synthetic counters only; no audio, provider requests or production resources.
"""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, inspect, select
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import begin, confirm

from navox.db.models import User, UserSession
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_usage import VoxUsageDelta, VoxUsageEvent, VoxVoiceCredit
from navox.vox import usage
from navox.vox.usage_schemas import UsageCounters, UsagePolicy, UsageUpdate

vox_env = shared_vox_fixture
pytestmark = pytest.mark.skipif(
    not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"),
    reason="Requires explicitly configured isolated PostgreSQL",
)


async def prepared(env, monkeypatch, *, wall=None, speaking_ms=0):
    _, intent = await begin(env)
    intent = await confirm(env, intent)
    clock = [wall or datetime.now(UTC)]

    async def now(_database):
        return clock[0]

    monkeypatch.setattr(usage, "database_now", now)
    stream_id = uuid4()
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "UTC"
        if wall is not None:
            # Move synthetic authority together; no real lease or provider exists.
            connection = await database.get(VoxMediaConnection, intent.connection_id)
            cleanup = await database.scalar(
                select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == intent.connection_id)
            )
            call = await database.get(VoxCall, intent.reservation_id)
            session = await database.get(UserSession, env.session_id)
            connection.created_at = cleanup.created_at = wall - timedelta(seconds=1)
            connection.expires_at = cleanup.connection_expires_at = wall + timedelta(seconds=120)
            call.expires_at = wall + timedelta(seconds=120)
            session.expires_at = wall + timedelta(hours=1)
            await database.flush()
        await usage.update_policy(
            database,
            UsagePolicy(daily_voice_minutes=1, monthly_voice_minutes=1),
            expected_revision=0,
        )
        await usage.start_usage_stream(
            database,
            env.user_id,
            env.workspace_id,
            intent.reservation_id,
            stream_id=stream_id,
        )
        if speaking_ms:
            database.add(
                VoxUsageDelta(
                    stream_id=stream_id,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    occurred_at=clock[0],
                    speaking_ms=speaking_ms,
                    idle_ms=0,
                    connected_ms=speaking_ms,
                    tokens=0,
                    estimated_cost_usd=None,
                    pricing_version=None,
                )
            )
        await database.commit()
    return SimpleNamespace(
        env=env, intent=intent, clock=clock, stream_id=stream_id, worker_nonce=uuid4()
    )


async def reserve(database, fixture, *, grant_id=None, worker_nonce=None):
    env, intent = fixture.env, fixture.intent
    return await usage.reserve_voice_credit(
        database,
        env.user_id,
        env.workspace_id,
        intent.reservation_id,
        stream_id=fixture.stream_id,
        connection_id=intent.connection_id,
        generation=intent.generation,
        worker_nonce=worker_nonce or fixture.worker_nonce,
        grant_id=grant_id or uuid4(),
    )


@pytest.mark.asyncio
async def test_final_credit_reservations_serialize_without_oversubscribing(vox_env, monkeypatch):
    fixture = await prepared(vox_env, monkeypatch, speaking_ms=59_000)
    locked, release, contender_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def first():
        async with vox_env.factory() as database:
            grant = await reserve(database, fixture)
            assert grant.granted_ms == 1000
            locked.set()
            await release.wait()
            await database.commit()

    async def second():
        await locked.wait()
        async with vox_env.factory() as database:
            contender_started.set()
            with pytest.raises(usage.UsageError) as error:
                await reserve(database, fixture, worker_nonce=uuid4())
            assert error.value.status_code == 429

    a, b = asyncio.create_task(first()), asyncio.create_task(second())
    try:
        await asyncio.wait_for(contender_started.wait(), timeout=10)
        await asyncio.sleep(0.1)
        assert not b.done(), "Second reservation must wait for the owner row lock"
        release.set()
        await asyncio.wait_for(asyncio.gather(a, b), timeout=15)
        async with vox_env.factory() as database:
            status = await usage.budget_status(database, vox_env.user_id, vox_env.workspace_id)
            assert status.daily.speaking_ms == 59_000
            assert status.daily.reserved_voice_ms == 1000 and status.remaining_voice_ms == 0
            assert await database.scalar(select(func.count()).select_from(VoxVoiceCredit)) == 1
    finally:
        release.set()
        await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.asyncio
async def test_operator_reduction_waits_for_issued_credit_and_fences_next_grant(
    vox_env, monkeypatch
):
    fixture = await prepared(vox_env, monkeypatch, speaking_ms=59_000)
    async with vox_env.factory() as database:
        await usage.update_policy(
            database,
            UsagePolicy(daily_voice_minutes=2, monthly_voice_minutes=2),
            expected_revision=1,
        )
        await database.commit()
    locked, release, operator_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def issuer():
        async with vox_env.factory() as database:
            grant = await reserve(database, fixture)
            assert grant.policy_revision == 2
            locked.set()
            await release.wait()
            await database.commit()
            return grant

    async def operator():
        await locked.wait()
        async with vox_env.factory() as database:
            operator_started.set()
            await usage.update_policy(
                database,
                UsagePolicy(daily_voice_minutes=1, monthly_voice_minutes=1),
                expected_revision=2,
            )
            await database.commit()

    a, b = asyncio.create_task(issuer()), asyncio.create_task(operator())
    try:
        await asyncio.wait_for(operator_started.wait(), timeout=10)
        await asyncio.sleep(0.1)
        assert not b.done(), "Policy write must wait for the outstanding shared policy fence"
        release.set()
        grant, _ = await asyncio.wait_for(asyncio.gather(a, b), timeout=15)
        async with vox_env.factory() as database:
            assert await reserve(database, fixture, grant_id=grant.id) == grant
            with pytest.raises(usage.UsageError) as error:
                await reserve(database, fixture)
            assert error.value.status_code == 429
    finally:
        release.set()
        await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.asyncio
async def test_expired_revoked_settlement_is_exact_idempotent_and_never_guessed(
    vox_env, monkeypatch
):
    fixture = await prepared(vox_env, monkeypatch)
    async with vox_env.factory() as database:
        grant = await reserve(database, fixture)
        await database.commit()
    fixture.clock[0] += timedelta(seconds=2)
    async with vox_env.factory() as database:
        connection = await database.get(VoxMediaConnection, fixture.intent.connection_id)
        connection.status = "revoked"
        connection.revoked_at = fixture.clock[0]
        connection.reason = "authority-lost"
        await database.commit()
    async with vox_env.factory() as database:
        before = await usage.budget_status(database, vox_env.user_id, vox_env.workspace_id)
        assert before.daily.speaking_ms == 0
        assert before.daily.reserved_voice_ms == before.daily.uncertain_voice_ms == 1000
        counters = UsageCounters(speaking_ms=400, idle_ms=1600, connected_ms=2000)
        once = await usage.settle_voice_credit(database, grant, cumulative=counters)
        twice = await usage.settle_voice_credit(database, grant, cumulative=counters)
        assert once == twice and once.cumulative.speaking_ms == 400
        await database.commit()
    async with vox_env.factory() as database:
        with pytest.raises(usage.UsageError, match="settlement changed"):
            await usage.settle_voice_credit(
                database,
                grant,
                cumulative=UsageCounters(speaking_ms=401, idle_ms=1599, connected_ms=2000),
            )
        with pytest.raises(usage.UsageError, match="identity changed"):
            await usage.settle_voice_credit(
                database, grant.model_copy(update={"worker_nonce": uuid4()}), cumulative=counters
            )
        status = await usage.budget_status(database, vox_env.user_id, vox_env.workspace_id)
        assert status.daily.speaking_ms == 400
        assert status.daily.reserved_voice_ms == status.daily.uncertain_voice_ms == 0
        assert await database.scalar(select(func.count()).select_from(VoxUsageEvent)) == 1


@pytest.mark.asyncio
async def test_final_own_credit_cannot_be_borrowed_by_another_worker(vox_env, monkeypatch):
    fixture = await prepared(vox_env, monkeypatch, speaking_ms=59_000)
    async with vox_env.factory() as database:
        grant = await reserve(database, fixture)
        assert await reserve(database, fixture, grant_id=grant.id) == grant
        with pytest.raises(usage.UsageError, match="identity changed"):
            await reserve(database, fixture, grant_id=grant.id, worker_nonce=uuid4())
        await database.commit()
    for connection_id, nonce in [
        (None, None),
        (grant.connection_id, uuid4()),
        (uuid4(), grant.worker_nonce),
    ]:
        async with vox_env.factory() as database:
            with pytest.raises(usage.UsageError) as error:
                await usage.admit_provider_stream(
                    database,
                    vox_env.user_id,
                    vox_env.workspace_id,
                    grant.call_id,
                    stream_id=uuid4(),
                    provider="synthetic",
                    model="fixture",
                    token_ceiling=100,
                    voice_credit=grant,
                    voice_connection_id=connection_id,
                    voice_worker_nonce=nonce,
                )
            assert error.value.status_code == 409
    async with vox_env.factory() as database:
        provider = await usage.admit_provider_stream(
            database,
            vox_env.user_id,
            vox_env.workspace_id,
            grant.call_id,
            stream_id=uuid4(),
            provider="synthetic",
            model="fixture",
            token_ceiling=100,
            voice_credit=grant,
            voice_connection_id=grant.connection_id,
            voice_worker_nonce=grant.worker_nonce,
        )
        assert provider.reserved_tokens == 100
        await database.commit()


@pytest.mark.asyncio
async def test_intervening_worker_settlement_keeps_stale_grant_uncertain(vox_env, monkeypatch):
    fixture = await prepared(vox_env, monkeypatch)
    async with vox_env.factory() as database:
        old = await reserve(database, fixture)
        successor = await reserve(database, fixture, worker_nonce=uuid4())
        assert old.base_sequence == successor.base_sequence == 0
        await database.commit()
    fixture.clock[0] += timedelta(seconds=1)
    async with vox_env.factory() as database:
        await usage.settle_voice_credit(
            database, old, cumulative=UsageCounters(speaking_ms=100, connected_ms=100)
        )
        await database.commit()
    async with vox_env.factory() as database:
        with pytest.raises(usage.UsageError, match="checkpoint changed"):
            await usage.settle_voice_credit(
                database, successor, cumulative=UsageCounters(speaking_ms=150, connected_ms=150)
            )
        status = await usage.budget_status(database, vox_env.user_id, vox_env.workspace_id)
        assert status.daily.speaking_ms == 100
        assert status.daily.reserved_voice_ms == status.daily.uncertain_voice_ms == 1000
        row = await database.get(VoxVoiceCredit, successor.id)
        assert row.settled_at is None and row.settled_speaking_ms is None


@pytest.mark.asyncio
async def test_stale_live_credit_cannot_be_replayed_or_reused_for_provider(vox_env, monkeypatch):
    fixture = await prepared(vox_env, monkeypatch)
    async with vox_env.factory() as database:
        grant = await reserve(database, fixture)
        await database.commit()
    fixture.clock[0] += timedelta(milliseconds=100)
    async with vox_env.factory() as database:
        await usage.apply_usage_update(
            database,
            fixture.stream_id,
            UsageUpdate(
                event_id=uuid4(),
                sequence=1,
                cumulative=UsageCounters(speaking_ms=100, connected_ms=100),
            ),
        )
        await database.commit()
    async with vox_env.factory() as database:
        with pytest.raises(usage.UsageError) as error:
            await reserve(database, fixture, grant_id=grant.id)
        assert error.value.status_code == 409
    async with vox_env.factory() as database:
        with pytest.raises(usage.UsageError) as error:
            await usage.admit_provider_stream(
                database,
                vox_env.user_id,
                vox_env.workspace_id,
                grant.call_id,
                stream_id=uuid4(),
                provider="synthetic",
                model="fixture",
                token_ceiling=100,
                voice_credit=grant,
                voice_connection_id=grant.connection_id,
                voice_worker_nonce=grant.worker_nonce,
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_grant_snapshots_prior_worker_final_counters_before_new_audio(vox_env, monkeypatch):
    fixture = await prepared(vox_env, monkeypatch)
    fixture.clock[0] += timedelta(milliseconds=100)
    async with vox_env.factory() as database:
        await usage.apply_usage_update(
            database,
            fixture.stream_id,
            UsageUpdate(
                event_id=uuid4(),
                sequence=1,
                cumulative=UsageCounters(speaking_ms=100, connected_ms=100),
            ),
        )
        await database.commit()
    async with vox_env.factory() as database:
        grant = await reserve(database, fixture)
        assert grant.base_sequence == 1 and grant.base_cumulative.speaking_ms == 100
        await database.commit()
    fixture.clock[0] += timedelta(milliseconds=20)
    async with vox_env.factory() as database:
        result = await usage.settle_voice_credit(
            database, grant, cumulative=UsageCounters(speaking_ms=120, connected_ms=120)
        )
        assert result.cumulative.speaking_ms == 120
        row = await database.get(VoxVoiceCredit, grant.id)
        assert row.settled_speaking_ms == 20
        await database.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["pause", "media", "cleanup", "generation", "session"])
async def test_new_credit_requires_all_current_authority_fences(vox_env, monkeypatch, revocation):
    fixture = await prepared(vox_env, monkeypatch)
    async with vox_env.factory() as database:
        if revocation == "pause":
            row = await database.get(User, vox_env.user_id)
            row.agent_paused = True
        elif revocation == "media":
            row = await database.get(VoxMediaConnection, fixture.intent.connection_id)
            row.status, row.revoked_at, row.reason = "revoked", fixture.clock[0], "authority-lost"
        elif revocation == "cleanup":
            row = await database.scalar(
                select(VoxMediaCleanup).where(
                    VoxMediaCleanup.connection_id == fixture.intent.connection_id
                )
            )
            row.status, row.requested_at, row.next_attempt_at = (
                "pending",
                fixture.clock[0],
                fixture.clock[0],
            )
        elif revocation == "generation":
            row = await database.scalar(select(VoxState).where(VoxState.user_id == vox_env.user_id))
            row.generation += 1
        else:
            row = await database.get(UserSession, vox_env.session_id)
            row.revoked_at = fixture.clock[0]
        await database.commit()
    async with vox_env.factory() as database:
        with pytest.raises(usage.UsageError) as error:
            await reserve(database, fixture)
        assert error.value.status_code == 409
        assert await database.scalar(select(func.count()).select_from(VoxVoiceCredit)) == 0


@pytest.mark.asyncio
async def test_credit_clips_local_month_boundary_and_does_not_hold_future_periods(
    vox_env, monkeypatch
):
    wall = datetime(2026, 10, 31, 23, 59, 59, 750_000, tzinfo=UTC)
    fixture = await prepared(vox_env, monkeypatch, wall=wall)
    async with vox_env.factory() as database:
        grant = await reserve(database, fixture)
        assert grant.granted_ms == 250
        assert (
            grant.expires_at
            == grant.day_end
            == grant.month_end
            == wall + timedelta(milliseconds=250)
        )
        await database.commit()
    fixture.clock[0] += timedelta(milliseconds=500)
    async with vox_env.factory() as database:
        status = await usage.budget_status(database, vox_env.user_id, vox_env.workspace_id)
        assert status.daily.reserved_voice_ms == status.monthly.reserved_voice_ms == 0
        assert status.remaining_voice_ms == 60_000
        old = await database.get(VoxVoiceCredit, grant.id)
        assert old.settled_at is None  # Period reset never fabricates a refund or measured speech.
        await usage.settle_voice_credit(
            database,
            grant,
            cumulative=UsageCounters(speaking_ms=250, idle_ms=250, connected_ms=500),
        )
        await database.commit()
    async with vox_env.factory() as database:
        current = await usage.budget_status(database, vox_env.user_id, vox_env.workspace_id)
        assert current.daily.speaking_ms == current.monthly.speaking_ms == 0
        prior_start, prior_end, _, _ = usage.local_bounds(wall, "UTC")
        prior = await usage._totals(database, vox_env.user_id, prior_start, prior_end)
        assert prior.speaking_ms == 250 and prior.reserved_voice_ms == 0


@pytest.mark.asyncio
async def test_0055_actual_postgres_catalog_and_guard_preserve_voice_evidence(vox_env, monkeypatch):
    fixture = await prepared(vox_env, monkeypatch)
    path = Path(__file__).parents[1] / "migrations/versions/0055_vox_voice_credit.py"
    spec = importlib.util.spec_from_file_location("voice_credit_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0055_vox_voice_credit"
    assert module.down_revision == "0054_vox_wake_commands"

    def ddl(connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, direction)()

    engine = vox_env.factory.kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: VoxVoiceCredit.__table__.drop(sync))
        await connection.run_sync(lambda sync: ddl(sync, "upgrade"))

        def parity(sync):
            catalog = inspect(sync)
            table = VoxVoiceCredit.__table__
            assert {c["name"] for c in catalog.get_columns(table.name)} == set(table.columns.keys())
            assert {c["name"] for c in catalog.get_indexes(table.name)} == {
                i.name for i in table.indexes
            }
            assert {c["name"] for c in catalog.get_check_constraints(table.name)} == {
                c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
            }
            assert {fk["referred_table"] for fk in catalog.get_foreign_keys(table.name)} == {
                "users",
                "workspaces",
                "vox_usage_streams",
            }

        await connection.run_sync(parity)
    async with vox_env.factory() as database:
        grant = await reserve(database, fixture)
        await database.commit()
    async with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="Voice quota evidence must be preserved"):
            async with connection.begin_nested():
                await connection.run_sync(lambda sync: ddl(sync, "downgrade"))
        assert (await connection.execute(select(VoxVoiceCredit.id))).scalar_one() == grant.id
        await connection.execute(VoxVoiceCredit.__table__.delete())
        await connection.run_sync(lambda sync: ddl(sync, "downgrade"))
        assert not await connection.run_sync(
            lambda sync: inspect(sync).has_table("vox_voice_credits")
        )
        await connection.run_sync(lambda sync: ddl(sync, "upgrade"))
        await connection.run_sync(parity)
