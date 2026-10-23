"""Synthetic callback/clock tests; no external inference or audio transport."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_vox_calls import vox_env as _shared_vox_env
from test_vox_media import begin, confirm

from navox.db.vox_usage import VoxUsageEvent
from navox.vox import usage, worker_usage
from navox.vox.usage_schemas import UsageCounters

vox_env = _shared_vox_env


def test_aggregate_counts_never_masquerade_as_text_pricing():
    counters, complete = worker_usage.normalize_usage({"input_tokens": 10, "output_tokens": 3})
    assert complete and counters.total_tokens == 13
    assert counters.unclassified_input == 10 and counters.input_text == 0
    assert (
        usage.estimate(
            counters,
            {"provider": "synthetic", "model": "fixture", "version": "fixture"},
            "provider",
        )
        is None
    )
    counters, complete = worker_usage.normalize_usage({"input_audio_tokens": 4})
    assert not complete and counters.input_audio == 4
    counters, complete = worker_usage.normalize_usage(
        {
            "input_tokens": 10,
            "output_tokens": 0,
            "input_text_tokens": 4,
            "input_audio_tokens": 6,
            "input_cached_tokens": 2,
        }
    )
    assert complete and counters.unclassified_input == 10
    assert counters.input_text == counters.input_audio == 0
    with pytest.raises(usage.UsageError):
        worker_usage.normalize_usage({"input_tokens": -1})


@pytest.mark.asyncio
async def test_worker_hook_reserves_before_attempt_and_records_idempotent_final_usage(
    vox_env, monkeypatch
):
    env = vox_env
    call, intent = await begin(env)
    intent = await confirm(env, intent)
    authority = SimpleNamespace(user_id=env.user_id, workspace_id=env.workspace_id)

    class Check:
        factory = env.factory

        async def __call__(self):
            return authority

        async def intent(self):
            return authority, intent

    hooks = worker_usage.WorkerUsageHooks(Check())
    stream = uuid4()
    await hooks.begin(stream, "synthetic", "fixture", 100)
    async with env.factory() as database:
        assert (
            await usage.budget_status(database, env.user_id, env.workspace_id)
        ).daily.reserved_tokens == 100
    counts = {"input_tokens": 10, "output_tokens": 3}
    await hooks.finish(stream, counts, True)
    await hooks.finish(stream, counts, True)
    async with env.factory() as database:
        status = await usage.budget_status(database, env.user_id, env.workspace_id)
        assert status.daily.tokens == 13 and status.daily.reserved_tokens == 0
        assert status.daily.estimated_cost_usd is None
        assert (
            await database.scalar(
                select(func.count())
                .select_from(VoxUsageEvent)
                .where(VoxUsageEvent.stream_id == stream)
            )
            == 1
        )
    await hooks.close()


@pytest.mark.asyncio
async def test_server_activity_clock_unions_overlap_and_records_room_idle_separately(
    vox_env, monkeypatch
):
    env = vox_env
    call, intent = await begin(env)
    intent = await confirm(env, intent)
    clock = [1000.0]
    created = datetime.fromisoformat(call["created_at"].replace("Z", "+00:00"))

    async def now(_):
        return created + timedelta(seconds=clock[0] - 1000)

    monkeypatch.setattr(usage, "database_now", now)
    monkeypatch.setattr(worker_usage, "monotonic", lambda: clock[0])
    authority = SimpleNamespace(user_id=env.user_id, workspace_id=env.workspace_id)

    class Check:
        factory = env.factory

        async def __call__(self):
            return authority

        async def intent(self):
            return authority, intent

    hooks = worker_usage.WorkerUsageHooks(Check())
    await hooks.check_budget()
    await hooks.note_audio(100)
    await hooks.note_audio(100, direction="output")  # Duplex shares the same interval.
    clock[0] += 1
    await hooks.check_budget()
    async with env.factory() as database:
        totals = await usage.call_totals(database, env.user_id, env.workspace_id, UUID(call["id"]))
        assert 99 <= totals.speaking_ms <= 100
        assert 899 <= totals.idle_ms <= 900
        assert 999 <= totals.connected_ms <= 1000
        assert totals.speaking_ms + totals.idle_ms == totals.connected_ms
        assert totals.estimated_cost_usd is None
    await hooks.close()


def test_content_free_schema_ignores_no_private_payload():
    assert "transcript" not in UsageCounters.model_fields
    assert "audio" not in UsageCounters.model_fields
