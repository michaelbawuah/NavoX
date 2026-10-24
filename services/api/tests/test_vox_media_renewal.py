"""Renewed live epochs retain short initial admission and terminal cleanup fences."""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from test_vox_calls import ROOT
from test_vox_media import begin, cleanup_row, confirm
from test_vox_media import vox_env as shared_vox_fixture

from navox.api.vox import media_provider
from navox.connectors.sync_state import utc
from navox.db.models import UserSession, WorkspaceMembership
from navox.db.vox_commands import VoxWakeCommand
from navox.db.vox_media import VoxMediaConnection
from navox.vox import media, service
from navox.vox.provider import LiveKitCloudConfig, LiveKitCloudProvider, MediaUnavailable
from navox.vox.schemas import VoxLease

vox_env = shared_vox_fixture


def clock(monkeypatch):
    current = [datetime.now(UTC)]

    async def now(_database):
        return current[0]

    monkeypatch.setattr(media, "database_now", now)
    monkeypatch.setattr(service, "database_now", now)
    return current


async def renew(env, intent, *, generation=None, token_hash=None):
    async with env.factory() as database:
        return await media.renew_connection(
            database,
            env.user_id,
            env.workspace_id,
            token_hash or env.token_hash,
            intent.connection_id,
            generation=intent.generation if generation is None else generation,
        )


async def reservation_renew(env, row):
    async with env.factory() as database:
        return await service.renew(
            database,
            env.user_id,
            env.workspace_id,
            env.token_hash,
            UUID(row["client_call_id"]),
            VoxLease(generation=row["generation"]),
        )


@pytest.mark.asyncio
async def test_heartbeat_extends_same_epoch_and_cleanup_atomically_with_fresh_call(
    vox_env, monkeypatch
):
    env = vox_env
    now = clock(monkeypatch)
    created = now[0]
    row, original = await begin(env)
    await confirm(env, original)
    now[0] += timedelta(seconds=30)
    call = await reservation_renew(env, row)
    updated = await renew(env, original)
    assert updated.expires_at > original.expires_at
    assert updated.expires_at <= now[0] + timedelta(seconds=120)
    assert updated.expires_at <= call.expires_at
    assert {k: v for k, v in asdict(updated).items() if k not in {"expires_at", "status"}} == {
        k: v for k, v in asdict(original).items() if k not in {"expires_at", "status"}
    }
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, original.connection_id)
        cleanup = await cleanup_row(database, original.connection_id)
        assert utc(saved.expires_at) == utc(cleanup.connection_expires_at) == updated.expires_at
        assert cleanup.status == "armed" and cleanup.requested_at is None
        assert utc(saved.created_at) == created
    now[0] += timedelta(seconds=60)
    await reservation_renew(env, row)
    latest = await renew(env, original)
    now[0] += timedelta(seconds=40)  # Original admission expired; renewed epoch is still live.
    assert original.expires_at < now[0] < latest.expires_at
    async with env.factory() as database:
        old_completion = await media.finalize_provision(database, original)
    assert old_completion.expires_at == latest.expires_at  # Finalizer never advances a lease.


@pytest.mark.asyncio
async def test_renewed_epoch_cannot_mint_join_tokens_after_initial_window(vox_env, monkeypatch):
    env = vox_env
    now = clock(monkeypatch)
    row, original = await begin(env)
    await confirm(env, original)
    now[0] += timedelta(seconds=90)
    await reservation_renew(env, row)
    live = await renew(env, original)
    now[0] += timedelta(seconds=40)
    assert live.expires_at > now[0] > original.created_at + timedelta(seconds=120)
    with pytest.raises(service.VoxError) as error:
        await begin(env, row, request_id=(await _request_id(env, original.connection_id)))
    assert error.value.status_code == 409
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, original.connection_id)
        cleanup = await cleanup_row(database, original.connection_id)
        assert saved.status == "confirmed" and cleanup.status == "armed"
        assert utc(saved.expires_at) == live.expires_at


async def _request_id(env, connection_id):
    async with env.factory() as database:
        return (await database.get(VoxMediaConnection, connection_id)).request_id


@pytest.mark.asyncio
async def test_media_heartbeat_http_returns_new_deadline_without_new_token(vox_env, monkeypatch):
    env = vox_env
    now = clock(monkeypatch)
    row, original = await begin(env)
    await confirm(env, original)
    now[0] += timedelta(seconds=30)
    await reservation_renew(env, row)
    env.app.dependency_overrides[media_provider] = lambda: object()
    path = f"{ROOT}/{row['client_call_id']}/media/{original.connection_id}/renew"
    result = await env.client.post(path, json={"generation": original.generation})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "private, no-store"
    binding = result.json()
    assert set(binding) == {
        "connection_id",
        "client_call_id",
        "generation",
        "room_name",
        "participant_identity",
        "agent_identity",
        "expires_at",
    }
    assert (
        datetime.fromisoformat(binding["expires_at"].replace("Z", "+00:00")) > original.expires_at
    )
    assert "token" not in binding and "token_expires_at" not in binding


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", ["preparing", "expired-media", "expired-call", "logout", "membership"]
)
async def test_invalid_or_unconfirmed_authority_never_renews(vox_env, monkeypatch, state):
    env = vox_env
    now = clock(monkeypatch)
    row, original = await begin(env)
    if state != "preparing":
        await confirm(env, original)
    if state == "expired-media":
        now[0] += timedelta(seconds=90)
        await reservation_renew(env, row)
        now[0] += timedelta(seconds=31)
    elif state == "expired-call":
        now[0] += timedelta(seconds=121)
    elif state == "logout":
        assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    elif state == "membership":
        async with env.factory() as database:
            await database.execute(
                delete(WorkspaceMembership).where(
                    WorkspaceMembership.user_id == env.user_id,
                    WorkspaceMembership.workspace_id == env.workspace_id,
                )
            )
            await database.commit()
    with pytest.raises(service.VoxError):
        await renew(env, original)
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, original.connection_id)
        assert utc(saved.expires_at) == original.expires_at
        if state == "preparing":
            assert saved.status == "preparing"
        if state in {"expired-media", "expired-call", "logout"}:
            assert saved.status == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_state", ["missing", "pending", "claimed", "done"])
async def test_cleanup_already_started_cannot_be_rearmed_by_heartbeat(
    vox_env, monkeypatch, cleanup_state
):
    env = vox_env
    now = clock(monkeypatch)
    _, original = await begin(env)
    await confirm(env, original)
    async with env.factory() as database:
        job = await cleanup_row(database, original.connection_id)
        if cleanup_state == "missing":
            await database.delete(job)
        else:
            job.status = cleanup_state
            job.requested_at = now[0]
            job.next_attempt_at = None if cleanup_state == "done" else now[0]
            if cleanup_state == "claimed":
                job.claim_id = uuid4()
                job.claim_expires_at = now[0] + timedelta(seconds=30)
            if cleanup_state == "done":
                job.completed_at = now[0]
        await database.commit()
    with pytest.raises(service.VoxError) as error:
        await renew(env, original)
    assert error.value.status_code == 410
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, original.connection_id)
        job = await cleanup_row(database, original.connection_id)
        assert saved.status == "revoked" and job.status != "armed"
        assert utc(saved.expires_at) == utc(job.connection_expires_at) == original.expires_at


@pytest.mark.asyncio
async def test_session_cap_and_wrong_generation_never_expand_live_authority(vox_env, monkeypatch):
    env = vox_env
    now = clock(monkeypatch)
    row, original = await begin(env)
    await confirm(env, original)
    with pytest.raises(service.VoxError):
        await renew(env, original, generation=original.generation + 1)
    with pytest.raises(service.VoxError):
        await renew(env, original, generation=True)
    now[0] += timedelta(seconds=30)
    cap = now[0] + timedelta(seconds=100)
    async with env.factory() as database:
        (await database.get(UserSession, env.session_id)).expires_at = cap
        await database.commit()
    await reservation_renew(env, row)
    bounded = await renew(env, original)
    assert bounded.expires_at == cap


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["hang-up", "reset", "logout"])
async def test_old_provider_completion_after_renewal_never_restores_revoked_epoch(
    vox_env, monkeypatch, terminal
):
    env = vox_env
    now = clock(monkeypatch)
    row, original = await begin(env)
    await confirm(env, original)
    now[0] += timedelta(seconds=30)
    await reservation_renew(env, row)
    live = await renew(env, original)
    if terminal == "hang-up":
        result = await env.client.post(
            f"{ROOT}/{row['client_call_id']}/end", json={"generation": original.generation}
        )
    elif terminal == "reset":
        result = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
        )
    else:
        result = await env.client.post("/api/v1/auth/logout")
    assert result.status_code in {200, 204}, result.text
    async with env.factory() as database:
        job = await cleanup_row(database, original.connection_id)
        deadline = utc(job.connection_expires_at)
        assert deadline == live.expires_at
    with pytest.raises(service.VoxError):
        async with env.factory() as database:
            await media.finalize_provision(database, original)
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, original.connection_id)
        job = await cleanup_row(database, original.connection_id)
        assert saved is None or saved.status == "revoked"
        assert job.status == "pending" and utc(job.connection_expires_at) == deadline


@pytest.mark.asyncio
async def test_terminal_transition_erases_queued_encrypted_wake_command(vox_env):
    env = vox_env
    _, original = await begin(env)
    await confirm(env, original)
    now = datetime.now(UTC)
    async with env.factory() as database:
        database.add(
            VoxWakeCommand(
                connection_id=original.connection_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                request_id=uuid4(),
                digest="d" * 64,
                encrypted_text="synthetic-ciphertext",
                created_at=now,
                expires_at=now + timedelta(seconds=30),
            )
        )
        await database.commit()
    async with env.factory() as database:
        await media.revoke_connection(
            database, env.user_id, env.workspace_id, env.token_hash, original.connection_id
        )
    async with env.factory() as database:
        assert await database.scalar(select(VoxWakeCommand.id)) is None


def test_renewed_live_lease_never_extends_initial_token_admission(monkeypatch):
    from navox.vox import provider as module

    base = datetime.now(UTC)
    current = [base + timedelta(seconds=30)]

    class Clock:
        @staticmethod
        def now(_timezone):
            return current[0]

    monkeypatch.setattr(module, "datetime", Clock)
    original = media.ConnectionIntent(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        0,
        "confirmed",
        base,
        base + timedelta(seconds=150),
        "vx-" + uuid4().hex,
        "vc-" + uuid4().hex,
        "va-" + uuid4().hex,
    )
    provider = LiveKitCloudProvider(
        LiveKitCloudConfig("wss://synthetic.livekit.cloud", "synthetic", "s" * 32)
    )
    token = provider.participant_token(original)
    import base64
    import json

    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert claims["exp"] == int((base + timedelta(seconds=120)).timestamp())
    assert provider.participant_token_expires_at(original) < original.expires_at
    current[0] = base + timedelta(seconds=121)
    with pytest.raises(MediaUnavailable):
        provider.participant_token(replace(original, expires_at=base + timedelta(seconds=210)))
