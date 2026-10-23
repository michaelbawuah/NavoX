"""Deterministic operation counts and fail-closed frame admission, not latency tests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import begin, confirm

from navox.db.vox_usage import VoxUsageDelta, VoxUsageEvent, VoxVoiceCredit
from navox.vox import usage, worker_usage
from navox.vox.usage_schemas import UsagePolicy
from navox.vox.worker import WorkerAccessLost

vox_env = shared_vox_fixture


async def prepared(env, monkeypatch):
    _, intent = await begin(env)
    intent = await confirm(env, intent)
    clock = [1000.0]
    wall = datetime.now(UTC)
    original_sleep = worker_usage.asyncio.sleep

    async def now(_database):
        return wall + timedelta(seconds=clock[0] - 1000)

    async def sleep(seconds):
        clock[0] += seconds
        await original_sleep(0)

    monkeypatch.setattr(usage, "database_now", now)
    monkeypatch.setattr(worker_usage, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker_usage.asyncio, "sleep", sleep)
    authority = SimpleNamespace(user_id=env.user_id, workspace_id=env.workspace_id)

    class Check:
        factory = env.factory
        active = True
        frames = 0

        async def __call__(self):
            self.frames += 1
            if not self.active:
                raise WorkerAccessLost("Synthetic authority ended")
            return authority

        async def intent(self):
            await self()
            return authority, intent

    check = Check()
    return worker_usage.WorkerUsageHooks(check), check, clock, wall


@pytest.mark.asyncio
async def test_one_second_frame_union_batches_writes_but_keeps_every_frame_authority(
    vox_env, monkeypatch
):
    env = vox_env
    hooks, check, clock, _ = await prepared(env, monkeypatch)
    calls = {"reserve": 0, "settle": 0}
    reserve, settle = usage.reserve_voice_credit, usage.settle_voice_credit

    async def counted_reserve(*args, **kwargs):
        calls["reserve"] += 1
        return await reserve(*args, **kwargs)

    async def counted_settle(*args, **kwargs):
        calls["settle"] += 1
        return await settle(*args, **kwargs)

    monkeypatch.setattr(usage, "reserve_voice_credit", counted_reserve)
    monkeypatch.setattr(usage, "settle_voice_credit", counted_settle)
    for index in range(50):
        clock[0] = 1000 + index * 0.02
        await hooks.note_audio(20)
        await hooks.note_audio(20, direction="output")  # Duplex activity shares one interval.
        assert len(hooks._windows) <= 2
    assert calls == {"reserve": 1, "settle": 0}
    assert check.frames >= 100
    clock[0] = 1001
    await hooks.check_budget()
    assert calls == {"reserve": 2, "settle": 1}
    async with env.factory() as database:
        totals = await usage.call_totals(database, env.user_id, env.workspace_id, hooks._call_id)
        assert 999 <= totals.speaking_ms <= 1000
        assert totals.speaking_ms + totals.idle_ms == totals.connected_ms
        assert await database.scalar(select(func.count()).select_from(VoxUsageEvent)) == 1
    await hooks.close()


@pytest.mark.asyncio
async def test_last_twenty_ms_credit_stops_before_another_frame(vox_env, monkeypatch):
    env = vox_env
    hooks, _, clock, wall = await prepared(env, monkeypatch)
    await hooks._ensure_transport()
    async with env.factory() as database:
        await usage.update_policy(database, UsagePolicy(daily_voice_minutes=1), expected_revision=0)
        database.add(
            VoxUsageDelta(
                stream_id=hooks._transport_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                occurred_at=wall,
                speaking_ms=59_980,
                idle_ms=0,
                connected_ms=59_980,
                tokens=0,
                estimated_cost_usd=None,
                pricing_version=None,
            )
        )
        await database.commit()
    await hooks.note_audio(20)
    assert hooks._grant.granted_ms == 20
    clock[0] += 0.02
    with pytest.raises(usage.UsageError) as error:
        await hooks.note_audio(20)
    assert error.value.status_code == 429
    async with env.factory() as database:
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.daily.speaking_ms == 60_000
        assert status.remaining_voice_ms == 0
    await hooks.close()


@pytest.mark.asyncio
async def test_authority_loss_stops_next_frame_even_with_credit_remaining(vox_env, monkeypatch):
    hooks, check, clock, _ = await prepared(vox_env, monkeypatch)
    await hooks.note_audio(20)
    admitted = list(hooks._windows)
    check.active = False
    with pytest.raises(WorkerAccessLost):
        await hooks.note_audio(20)
    assert hooks._windows == admitted
    clock[0] += 0.02
    await hooks.close()  # Already-incurred accounting remains allowed after revocation.
    with pytest.raises(usage.UsageError):
        await hooks.note_audio(20)


@pytest.mark.asyncio
async def test_failed_close_keeps_uncertain_quota_without_inventing_speech(vox_env, monkeypatch):
    env = vox_env
    hooks, _, clock, _ = await prepared(env, monkeypatch)
    await hooks.note_audio(20)
    grant_id = hooks._grant.id
    clock[0] += 0.02

    async def unavailable(*_args, **_kwargs):
        raise usage.UsageError(503, "Synthetic accounting outage")

    monkeypatch.setattr(usage, "settle_voice_credit", unavailable)
    with pytest.raises(usage.UsageError):
        await hooks.close()
    assert hooks._closed
    clock[0] += 2
    async with env.factory() as database:
        row = await database.get(VoxVoiceCredit, grant_id)
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert row.settled_at is None and row.settled_speaking_ms is None
        assert status.daily.speaking_ms == 0
        assert status.daily.reserved_voice_ms == status.daily.uncertain_voice_ms == 1000
        assert status.remaining_voice_ms == 60 * 60_000 - 1000


@pytest.mark.asyncio
async def test_provider_begin_uses_own_final_credit_but_keeps_token_reserve_durable(
    vox_env, monkeypatch
):
    env = vox_env
    hooks, _, _, wall = await prepared(env, monkeypatch)
    await hooks._ensure_transport()
    async with env.factory() as database:
        await usage.update_policy(database, UsagePolicy(daily_voice_minutes=1), expected_revision=0)
        database.add(
            VoxUsageDelta(
                stream_id=hooks._transport_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                occurred_at=wall,
                speaking_ms=59_000,
                idle_ms=0,
                connected_ms=59_000,
                tokens=0,
                estimated_cost_usd=None,
                pricing_version=None,
            )
        )
        await database.commit()
    await hooks.check_budget()
    async with env.factory() as database:
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.remaining_voice_ms == 0 and status.daily.reserved_voice_ms == 1000
    stream = uuid4()
    await hooks.begin(stream, "synthetic", "fixture", 100)
    async with env.factory() as database:
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.daily.reserved_tokens == 100
    await hooks.finish(stream, {"input_tokens": 0, "output_tokens": 0}, True)
    await hooks.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["input", "output"])
async def test_same_direction_pcm_burst_counts_encoded_duration(vox_env, monkeypatch, direction):
    hooks, _, clock, _ = await prepared(vox_env, monkeypatch)
    for _ in range(5):
        await hooks.note_audio(20, direction=direction)
    clock[0] += 0.1
    await hooks.check_budget()
    async with vox_env.factory() as database:
        totals = await usage.call_totals(
            database, vox_env.user_id, vox_env.workspace_id, hooks._call_id
        )
        assert totals.speaking_ms == 100
    await hooks.close()


@pytest.mark.asyncio
async def test_grant_adopts_external_final_settlement_before_new_worker_audio(vox_env, monkeypatch):
    env = vox_env
    hooks, _, clock, _ = await prepared(env, monkeypatch)
    reserve = usage.reserve_voice_credit
    injected = False

    async def prior_worker_finishes(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            async with env.factory() as database:
                from navox.db.vox_usage import VoxUsageStream
                from navox.vox.usage_schemas import UsageCounters, UsageUpdate

                row = await database.get(VoxUsageStream, kwargs["stream_id"])
                await usage.apply_usage_update(
                    database,
                    row.id,
                    UsageUpdate(
                        event_id=uuid4(),
                        sequence=row.sequence + 1,
                        cumulative=UsageCounters(speaking_ms=100, connected_ms=100),
                    ),
                )
                await database.commit()
        return await reserve(*args, **kwargs)

    monkeypatch.setattr(usage, "reserve_voice_credit", prior_worker_finishes)
    await hooks.note_audio(20)
    assert hooks._grant.base_cumulative.speaking_ms == 100
    clock[0] += 0.02
    await hooks.check_budget()
    async with env.factory() as database:
        totals = await usage.call_totals(database, env.user_id, env.workspace_id, hooks._call_id)
        assert totals.speaking_ms == 120
        settled = list(
            await database.scalars(
                select(VoxVoiceCredit).where(VoxVoiceCredit.settled_at.is_not(None))
            )
        )
        assert len(settled) == 1 and settled[0].settled_speaking_ms == 20
    await hooks.close()
