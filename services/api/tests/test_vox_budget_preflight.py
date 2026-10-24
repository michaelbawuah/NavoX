"""Budget denial is explicit before media allocation, and reversible without cleanup fiction."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_vox_calls import ROOT, start
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_provider import FakeProvider

from navox.api.vox import media_provider
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt
from navox.db.vox_usage import VoxUsageStream
from navox.vox import readiness, usage
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.usage_schemas import UsageCounters, UsagePolicy, UsageUpdate

vox_env = shared_vox_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit", ["daily_tokens", "monthly_tokens", "daily_voice_minutes", "monthly_voice_minutes"]
)
async def test_budget_denial_creates_no_media_and_policy_reset_allows_new_attempt(
    vox_env, monkeypatch, limit
):
    env = vox_env
    # This test owns budget/database/HTTP behavior; configuration qualification
    # remains covered by its dedicated suite. No actual provider is contacted.
    monkeypatch.setattr(readiness, "load_inference_config", lambda _: VoxInferenceConfig())
    monkeypatch.setattr(readiness, "checkpoint_cipher", lambda _: object())
    monkeypatch.setattr(readiness, "route_credential", lambda *_: None)
    provider = FakeProvider()
    env.app.dependency_overrides[media_provider] = lambda: provider
    call, _ = await start(env)
    async with env.factory() as database:
        token_stream, transport_stream = uuid4(), uuid4()
        await usage.admit_provider_stream(
            database,
            env.user_id,
            env.workspace_id,
            UUID(call["id"]),
            stream_id=token_stream,
            provider="fixture",
            model="synthetic",
            token_ceiling=100,
        )
        await usage.start_usage_stream(
            database,
            env.user_id,
            env.workspace_id,
            UUID(call["id"]),
            stream_id=transport_stream,
        )
        transport = await database.get(VoxUsageStream, transport_stream)
        # Synthetic historical connection; no live usage counter is reset.
        transport.created_at -= timedelta(seconds=61)
        await database.flush()
        for stream, counters in (
            (token_stream, UsageCounters(input_text=100)),
            (transport_stream, UsageCounters(speaking_ms=60_000, connected_ms=60_000)),
        ):
            await usage.apply_usage_update(
                database, stream, UsageUpdate(event_id=uuid4(), sequence=1, cumulative=counters)
            )
            await usage.finish_usage_stream(database, stream, usage_complete=True)
        await usage.update_policy(
            database, UsagePolicy(**{limit: 100 if "tokens" in limit else 1}), expected_revision=0
        )
        await database.commit()
    path = f"{ROOT}/{call['client_call_id']}/media"
    body = {"generation": call["generation"], "request_id": str(uuid4())}
    denied = await env.client.post(path, json=body)
    assert denied.status_code == 429
    assert denied.json()["detail"]["code"] == "vox_allowance_exhausted_before_media"
    assert denied.headers["cache-control"] == "private, no-store"
    assert provider.intents == []
    async with env.factory() as database:
        for model in (VoxMediaConnection, VoxMediaCleanup, VoxMediaProvisionAttempt):
            assert await database.scalar(select(model)) is None
        await usage.update_policy(database, UsagePolicy(), expected_revision=1)
        await database.commit()
    admitted = await env.client.post(path, json=body)
    assert admitted.status_code == 200, admitted.text
    assert len(provider.intents) == 1
