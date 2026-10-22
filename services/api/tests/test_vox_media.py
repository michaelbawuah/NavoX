"""Real database ownership/terminal boundaries for the unshipped Vox media ledger.

These tests never create provider rooms, sign tokens, capture audio, or treat
an internal lifecycle intent as a successful browser media connection.
"""

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from test_vox_calls import ROOT, instant, start
from test_vox_calls import vox_env as shared_vox_fixture

from navox.api.auth import hash_session_token
from navox.connectors.sync_state import utc
from navox.db.models import User, UserSession, Workspace, WorkspaceMembership
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.student import service as schedule
from navox.vox import media, service
from navox.vox.reconcile import CleanupReceipt, claim_cleanup, finish_cleanup

vox_env = shared_vox_fixture


async def begin(env, row=None, *, request_id=None, token_hash=None):
    if row is None:
        row, _ = await start(env)
    async with env.factory() as database:
        intent = await media.begin_connection(
            database,
            env.user_id,
            env.workspace_id,
            token_hash or env.token_hash,
            UUID(row["client_call_id"]),
            generation=row["generation"],
            request_id=request_id or uuid4(),
        )
    return row, intent


async def confirm(env, intent, *, token_hash=None):
    async with env.factory() as database:
        return await media.confirm_connection(
            database,
            env.user_id,
            env.workspace_id,
            token_hash or env.token_hash,
            intent.connection_id,
        )


async def cleanup_row(database, connection_id):
    return await database.scalar(
        select(VoxMediaCleanup).where(VoxMediaCleanup.connection_id == connection_id)
    )


@pytest.mark.asyncio
async def test_epoch_and_locators_are_server_owned_opaque_and_never_a_media_grant(vox_env):
    env = vox_env
    row, intent = await begin(env)
    assert intent.connection_id != UUID(row["id"])
    assert intent.reservation_id == UUID(row["id"])
    assert intent.client_call_id == UUID(row["client_call_id"])
    assert intent.generation == row["generation"]
    assert intent.status == "preparing"
    assert utc(intent.created_at) < utc(intent.expires_at) <= instant(row["expires_at"])
    identities = (intent.room_name, intent.client_identity, intent.agent_identity)
    assert len(set(identities)) == 3
    assert all(env.credentials["email"] not in value for value in identities)
    assert all(
        env.user_id.hex not in value.replace("-", "")
        and env.workspace_id.hex not in value.replace("-", "")
        for value in identities
    )
    assert all(env.token not in value and env.token_hash not in value for value in identities)
    assert not any(key in asdict(intent) for key in ("token", "audio", "transcript", "secret"))
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, intent.connection_id)
        job = await cleanup_row(database, intent.connection_id)
        assert saved is not None and job.status == "armed"
        assert job.owner_scope_id == intent.owner_scope_id
        assert (job.room_name, job.client_identity, job.agent_identity) == identities
        assert not VoxMediaCleanup.__table__.foreign_keys
        assert not {
            "user_id",
            "workspace_id",
            "auth_session_id",
            "audio",
            "transcript",
            "token",
            "secret",
        } & set(VoxMediaCleanup.__table__.columns.keys())
    confirmed = await confirm(env, intent)
    assert confirmed.status == "confirmed" and confirmed.connection_id == intent.connection_id
    response = await env.client.get(f"{ROOT}/context")
    assert response.status_code == 200 and response.json()["media_enabled"] is False
    assert response.json()["call"]["media_enabled"] is False
    assert "room_name" not in response.text and "participant" not in response.text


@pytest.mark.asyncio
async def test_exact_request_retry_never_allocates_or_extends_and_other_request_is_denied(vox_env):
    env = vox_env
    request_id = uuid4()
    row, first = await begin(env, request_id=request_id)
    _, retry = await begin(env, row, request_id=request_id)
    assert asdict(retry) == asdict(first)
    confirmed = await confirm(env, first)
    assert (await confirm(env, first)).connection_id == first.connection_id
    renewed = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/renew", json={"generation": row["generation"]}
    )
    assert renewed.status_code == 200
    _, retry = await begin(env, renewed.json(), request_id=request_id)
    assert retry.connection_id == first.connection_id
    assert retry.expires_at == confirmed.expires_at == first.expires_at
    with pytest.raises(service.VoxError) as error:
        await begin(env, row)
    assert error.value.status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxMediaConnection)) == 1
        assert await database.scalar(select(func.count()).select_from(VoxMediaCleanup)) == 1


@pytest.mark.asyncio
async def test_an_independent_login_cannot_adopt_confirm_or_revoke_a_connection(vox_env):
    env = vox_env
    request_id = uuid4()
    row, intent = await begin(env, request_id=request_id)
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers=dict(env.client.headers),
    ) as second:
        response = await second.post("/api/v1/auth/login", json=env.credentials)
        assert response.status_code == 200
        other_hash = hash_session_token(second.cookies.get(env.settings.session_cookie_name))
        with pytest.raises(service.VoxError) as error:
            await begin(env, row, request_id=request_id, token_hash=other_hash)
        assert error.value.status_code == 404
        with pytest.raises(service.VoxError) as error:
            await confirm(env, intent, token_hash=other_hash)
        assert error.value.status_code == 404
        async with env.factory() as database:
            with pytest.raises(service.VoxError) as error:
                await media.revoke_connection(
                    database, env.user_id, env.workspace_id, other_hash, intent.connection_id
                )
            assert error.value.status_code == 404
    assert (await confirm(env, intent)).status == "confirmed"
    async with env.factory() as database:
        assert (await cleanup_row(database, intent.connection_id)).status == "armed"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["hang-up", "logout", "cookie-replacement", "pause", "reset"])
@pytest.mark.parametrize("confirmed", [False, True])
async def test_real_terminal_paths_atomically_require_cleanup_and_late_confirm_stays_revoked(
    vox_env, terminal, confirmed
):
    env = vox_env
    row, intent = await begin(env)
    if confirmed:
        await confirm(env, intent)
    if terminal == "hang-up":
        response = await env.client.post(
            f"{ROOT}/{row['client_call_id']}/end", json={"generation": row["generation"]}
        )
        assert response.status_code == 204
    elif terminal == "logout":
        assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    elif terminal == "cookie-replacement":
        assert (
            await env.client.post("/api/v1/auth/login", json=env.credentials)
        ).status_code == 200
    elif terminal == "pause":
        response = await env.client.post("/api/v1/agent/pause")
        assert response.status_code == 200, response.text
    else:
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
        )
        assert response.status_code == 200, response.text
    async with env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        job = await cleanup_row(database, intent.connection_id)
        assert job.status == "pending" and job.connection_id == intent.connection_id
        assert job.owner_scope_id == intent.owner_scope_id
        assert job.room_name == intent.room_name and job.client_identity == intent.client_identity
        if terminal == "reset":
            assert connection is None
            assert await database.scalar(select(func.count()).select_from(VoxCall)) == 0
        else:
            assert connection.status == "revoked" and connection.revoked_at is not None
    with pytest.raises(service.VoxError) as error:
        await confirm(env, intent)
    assert error.value.status_code in {401, 404, 409, 410}
    async with env.factory() as database:
        job = await cleanup_row(database, intent.connection_id)
        assert job.status == "pending"
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        assert connection is None or connection.status == "revoked"


@pytest.mark.asyncio
async def test_reset_cleanup_intent_rolls_back_with_erasure_and_preserves_original_authority(
    vox_env,
):
    env = vox_env
    _, intent = await begin(env)
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.forget_vox_data(database, env.user_id, env.workspace_id)
        assert (await cleanup_row(database, intent.connection_id)).status == "pending"
        assert await database.get(VoxMediaConnection, intent.connection_id) is None
        await database.rollback()
    async with env.factory() as database:
        assert (await cleanup_row(database, intent.connection_id)).status == "armed"
        assert (await database.get(VoxMediaConnection, intent.connection_id)).status == "preparing"
    assert (await confirm(env, intent)).status == "confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("cascade", ["reservation", "session", "user"])
async def test_minimal_cleanup_ledger_survives_cascade_of_its_authority_rows(vox_env, cascade):
    env = vox_env
    row, intent = await begin(env)
    async with env.factory() as database:
        if cascade == "reservation":
            await database.execute(delete(VoxCall).where(VoxCall.id == UUID(row["id"])))
        elif cascade == "session":
            await database.execute(delete(UserSession).where(UserSession.id == env.session_id))
        else:
            await database.execute(delete(User).where(User.id == env.user_id))
        await database.commit()
    async with env.factory() as database:
        assert await database.get(VoxMediaConnection, intent.connection_id) is None
        job = await cleanup_row(database, intent.connection_id)
        assert job is not None
        assert (job.room_name, job.client_identity, job.agent_identity) == (
            intent.room_name,
            intent.client_identity,
            intent.agent_identity,
        )
        assert not VoxMediaCleanup.__table__.foreign_keys


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalidated",
    [
        "media-expiry",
        "reservation-expiry",
        "session-expiry",
        "session-revocation",
        "pause",
        "membership-loss",
        "workspace-kind",
    ],
)
async def test_bounded_sweeper_retires_abandoned_authority_without_browser_requests(
    vox_env, invalidated
):
    env = vox_env
    row, intent = await begin(env)
    await confirm(env, intent)
    now = datetime.now(UTC)
    async with env.factory() as database:
        if invalidated == "media-expiry":
            saved = await database.get(VoxMediaConnection, intent.connection_id)
            saved.created_at = now - timedelta(minutes=3)
            saved.expires_at = now - timedelta(seconds=1)
        elif invalidated == "reservation-expiry":
            saved = await database.get(VoxCall, UUID(row["id"]))
            saved.created_at = now - timedelta(minutes=3)
            saved.expires_at = now - timedelta(seconds=1)
        elif invalidated == "session-expiry":
            (await database.get(UserSession, env.session_id)).expires_at = now - timedelta(
                seconds=1
            )
        elif invalidated == "session-revocation":
            (await database.get(UserSession, env.session_id)).revoked_at = now
        elif invalidated == "pause":
            (await database.get(User, env.user_id)).agent_paused = True
        elif invalidated == "membership-loss":
            await database.execute(
                delete(WorkspaceMembership).where(
                    WorkspaceMembership.user_id == env.user_id,
                    WorkspaceMembership.workspace_id == env.workspace_id,
                )
            )
        else:
            (await database.get(Workspace, env.workspace_id)).workspace_type = "team"
        await database.commit()
    async with env.factory() as database:
        assert await media.sweep_connections(database, limit=1) == 1
    async with env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        assert connection.status == "revoked"
        assert (await cleanup_row(database, intent.connection_id)).status == "pending"
    async with env.factory() as database:
        assert await media.sweep_connections(database, limit=1) == 0
    with pytest.raises(service.VoxError):
        await confirm(env, intent)


@pytest.mark.asyncio
async def test_expired_media_epoch_cannot_be_revived_by_longer_reservation_renewal(vox_env):
    env = vox_env
    row, intent = await begin(env)
    now = datetime.now(UTC)
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, intent.connection_id)
        saved.created_at = now - timedelta(minutes=3)
        saved.expires_at = now - timedelta(seconds=1)
        await database.commit()
    response = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/renew", json={"generation": row["generation"]}
    )
    assert response.status_code == 200
    assert instant(response.json()["expires_at"]) > now
    with pytest.raises(service.VoxError) as error:
        await confirm(env, intent)
    assert error.value.status_code == 410
    async with env.factory() as database:
        assert (await database.get(VoxMediaConnection, intent.connection_id)).status == "revoked"
        assert (await cleanup_row(database, intent.connection_id)).status == "pending"


@pytest.mark.asyncio
async def test_unresolved_cleanup_blocks_replacement_even_after_owned_data_reset(vox_env):
    env = vox_env
    _, previous = await begin(env)
    response = await env.client.post(
        "/api/v1/student/data/reset",
        json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
    )
    assert response.status_code == 200
    row, _ = await start(env)
    async with env.factory() as database:
        state = await database.scalar(select(VoxState))
        assert state.id == previous.owner_scope_id
    with pytest.raises(service.VoxError) as error:
        await begin(env, row)
    assert error.value.status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxMediaConnection)) == 0
        job = await cleanup_row(database, previous.connection_id)
        assert job.status == "pending"
        # Model a trusted reconciler's completed revocation receipt, not provider I/O.
        job.status = "done"
        job.next_attempt_at = None
        job.completed_at = datetime.now(UTC)
        await database.commit()
    _, current = await begin(env, row)
    assert current.connection_id != previous.connection_id
    assert current.owner_scope_id == previous.owner_scope_id
    assert current.room_name != previous.room_name
    assert current.client_identity != previous.client_identity
    assert current.agent_identity != previous.agent_identity


@pytest.mark.asyncio
async def test_membership_loss_and_agent_pause_deny_new_authority(vox_env):
    env = vox_env
    row, _ = await start(env)
    async with env.factory() as database:
        await database.execute(
            delete(WorkspaceMembership).where(
                WorkspaceMembership.user_id == env.user_id,
                WorkspaceMembership.workspace_id == env.workspace_id,
            )
        )
        await database.commit()
    with pytest.raises(service.VoxError) as error:
        await begin(env, row)
    assert error.value.status_code in {403, 404}
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxMediaConnection)) == 0
        assert await database.scalar(select(func.count()).select_from(VoxMediaCleanup)) == 0


@pytest.mark.asyncio
async def test_late_provisioning_confirmation_rearms_exact_old_cleanup_without_touching_replacement(
    vox_env,
):
    env = vox_env
    request_id = uuid4()
    row, previous = await begin(env, request_id=request_id)
    async with env.factory() as database:
        await media.revoke_connection(
            database, env.user_id, env.workspace_id, env.token_hash, previous.connection_id
        )
    async with env.factory() as database:
        old_job = await cleanup_row(database, previous.connection_id)
        assert old_job.status == "pending"
        old_job.status = "done"
        old_job.next_attempt_at = None
        old_job.completed_at = datetime.now(UTC)
        await database.commit()
    with pytest.raises(service.VoxError) as error:
        await begin(env, row, request_id=request_id)
    assert error.value.status_code == 410
    _, current = await begin(env, row)
    await confirm(env, current)
    with pytest.raises(service.VoxError) as error:
        await confirm(env, previous)
    assert error.value.status_code == 410
    async with env.factory() as database:
        old_job = await cleanup_row(database, previous.connection_id)
        assert old_job.status == "pending" and old_job.completed_at is None
        assert old_job.room_name == previous.room_name
        current_job = await cleanup_row(database, current.connection_id)
        assert current_job.status == "armed" and current_job.room_name == current.room_name
        saved = await database.get(VoxMediaConnection, current.connection_id)
        assert saved.status == "confirmed"


def verified_receipt(claim):
    return CleanupReceipt(
        claim.connection_id,
        claim.claim_id,
        claim.room_name,
        claim.client_identity,
        claim.agent_identity,
        True,
        True,
        True,
    )


@pytest.mark.asyncio
async def test_trusted_provisioning_finalizer_requires_current_exact_epoch_without_extending_it(
    vox_env,
):
    env = vox_env
    _, intent = await begin(env)
    async with env.factory() as database:
        confirmed = await media.finalize_provision(database, intent)
    assert confirmed.status == "confirmed" and confirmed.connection_id == intent.connection_id
    assert confirmed.expires_at == intent.expires_at
    async with env.factory() as database:
        repeated = await media.finalize_provision(database, intent)
    assert asdict(repeated) == asdict(confirmed)


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", ["room", "reservation", "generation", "scope"])
async def test_modified_snapshot_cannot_confirm_or_redirect_another_cleanup_target(vox_env, forged):
    env = vox_env
    _, intent = await begin(env)
    field, value = {
        "room": ("room_name", "vx-" + uuid4().hex),
        "reservation": ("reservation_id", uuid4()),
        "generation": ("generation", intent.generation + 1),
        "scope": ("owner_scope_id", uuid4()),
    }[forged]
    with pytest.raises(service.VoxError):
        async with env.factory() as database:
            await media.finalize_provision(database, replace(intent, **{field: value}))
    async with env.factory() as database:
        saved = await database.get(VoxMediaConnection, intent.connection_id)
        job = await cleanup_row(database, intent.connection_id)
        assert saved.status != "confirmed"
        assert job.room_name == intent.room_name and job.owner_scope_id == intent.owner_scope_id
        assert (
            job.client_identity == intent.client_identity
            and job.agent_identity == intent.agent_identity
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["reset", "logout", "user-cascade"])
@pytest.mark.parametrize("cleanup_phase", ["claimed", "done"])
async def test_external_completion_after_erasure_or_logout_rearms_cleanup_and_fences_old_ack(
    vox_env, terminal, cleanup_phase
):
    env = vox_env
    _, intent = await begin(env)
    if terminal == "reset":
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
        )
        assert response.status_code == 200, response.text
    elif terminal == "logout":
        assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    else:
        async with env.factory() as database:
            await database.execute(delete(User).where(User.id == env.user_id))
            await database.commit()
    async with env.factory() as database:
        claim = await claim_cleanup(database)
    assert claim is not None and claim.connection_id == intent.connection_id
    receipt = verified_receipt(claim)
    if cleanup_phase == "done":
        async with env.factory() as database:
            assert await finish_cleanup(database, claim, receipt) is True
    with pytest.raises(service.VoxError) as error:
        async with env.factory() as database:
            await media.finalize_provision(database, intent)
    assert error.value.status_code == 410
    async with env.factory() as database:
        job = await cleanup_row(database, intent.connection_id)
        assert job.status == "pending" and job.claim_id is None and job.completed_at is None
        assert (job.room_name, job.client_identity, job.agent_identity) == (
            intent.room_name,
            intent.client_identity,
            intent.agent_identity,
        )
        saved = await database.get(VoxMediaConnection, intent.connection_id)
        assert saved is None or saved.status == "revoked"
    async with env.factory() as database:
        assert await finish_cleanup(database, claim, receipt) is False
    async with env.factory() as database:
        assert (await cleanup_row(database, intent.connection_id)).status == "pending"


@pytest.mark.asyncio
async def test_connection_quota_bounds_metadata_without_blocking_hang_up(vox_env, monkeypatch):
    env = vox_env
    monkeypatch.setattr(media, "MAX_CONNECTIONS_PER_CALL", 2)
    row, _ = await start(env)
    for _ in range(2):
        _, intent = await begin(env, row)
        async with env.factory() as database:
            await media.revoke_connection(
                database, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
            )
        async with env.factory() as database:
            claim = await claim_cleanup(database)
        assert claim is not None
        async with env.factory() as database:
            assert await finish_cleanup(database, claim, verified_receipt(claim)) is True
    with pytest.raises(service.VoxError) as error:
        await begin(env, row)
    assert error.value.status_code == 429
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxMediaConnection)) == 2
        assert await database.scalar(select(func.count()).select_from(VoxMediaCleanup)) == 2
    response = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/end", json={"generation": row["generation"]}
    )
    assert response.status_code == 204
