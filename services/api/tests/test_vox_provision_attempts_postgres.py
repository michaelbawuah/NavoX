"""SQL-observed single-attempt and cleanup fencing across API replicas."""

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select, text
from test_vox_calls import ROOT
from test_vox_media import begin, cleanup_row
from test_vox_media import vox_env as shared_vox_fixture
from test_vox_media_reconcile import receipt
from test_vox_postgres import cancel_pending, outcome, wait_for_lock
from test_vox_postgres import postgres_vox_env as shared_postgres_fixture

from navox.db.models import User
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt
from navox.vox import admission, media, provision, service
from navox.vox.reconcile import claim_cleanup, finish_cleanup

vox_env = shared_vox_fixture
postgres_vox_env = shared_postgres_fixture


async def retire(env, intent):
    async with env.factory() as database:
        await media.revoke_connection(
            database, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
        )


@pytest.mark.asyncio
async def test_concurrent_replicas_commit_only_one_physical_attempt(postgres_vox_env):
    env = postgres_vox_env
    _, intent = await begin(env)
    async with (
        env.factory() as holder,
        env.factory() as first,
        env.factory() as second,
        env.factory() as observer,
    ):
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        await holder.scalar(
            select(VoxMediaCleanup)
            .where(VoxMediaCleanup.connection_id == intent.connection_id)
            .with_for_update()
        )
        tasks = [
            asyncio.create_task(outcome(provision.begin_attempt(database, intent)))
            for database in (first, second)
        ]
        try:
            await wait_for_lock(observer, [first_pid, second_pid], holder_pid)
            assert not any(task.done() for task in tasks)
            await holder.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
        finally:
            await holder.rollback()
            await cancel_pending(tasks)
    successes = [value for value in results if isinstance(value, UUID)]
    failures = [value for value in results if isinstance(value, service.VoxError)]
    assert len(successes) == len(failures) == 1 and failures[0].status_code == 409
    async with env.factory() as database:
        stored = (await database.scalars(select(VoxMediaProvisionAttempt))).one()
        assert stored.id == successes[0] and stored.status == "in-flight"
        assert (await database.get(VoxMediaConnection, intent.connection_id)).status == "preparing"
        assert (await cleanup_row(database, intent.connection_id)).status == "armed"


@pytest.mark.asyncio
async def test_replica_retry_neither_dispatches_twice_nor_retires_the_active_holder(
    postgres_vox_env, monkeypatch
):
    env = postgres_vox_env
    _, intent = await begin(env)
    entered, release = asyncio.Event(), asyncio.Event()

    class Provider:
        calls = 0

        async def provision(self, value):
            assert value.connection_id == intent.connection_id
            self.calls += 1
            entered.set()
            await release.wait()

    provider = Provider()
    original = admission._ACTIVE
    first = asyncio.create_task(
        admission.provision(
            env.factory, provider, intent, env.user_id, env.workspace_id, env.token_hash
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # A second process has no access to the first process's in-memory tasks.
        monkeypatch.setattr(admission, "_ACTIVE", {})
        with pytest.raises(service.VoxError) as error:
            await admission.provision(
                env.factory, provider, intent, env.user_id, env.workspace_id, env.token_hash
            )
        assert error.value.status_code == 409 and provider.calls == 1
        async with env.factory() as database:
            assert (
                await database.get(VoxMediaConnection, intent.connection_id)
            ).status == "preparing"
            assert (await cleanup_row(database, intent.connection_id)).status == "armed"
            assert (
                await database.scalar(select(func.count()).select_from(VoxMediaProvisionAttempt))
                == 1
            )
        release.set()
        confirmed = await asyncio.wait_for(first, 5)
        assert confirmed.status == "confirmed" and provider.calls == 1
        async with env.factory() as database:
            attempt = (await database.scalars(select(VoxMediaProvisionAttempt))).one()
            assert attempt.status == "acknowledged"
            assert (await cleanup_row(database, intent.connection_id)).status == "armed"
    finally:
        release.set()
        await cancel_pending([first])
        original.pop(intent.connection_id, None)


@pytest.mark.asyncio
async def test_completed_cleanup_wins_over_a_queued_new_attempt(postgres_vox_env):
    env = postgres_vox_env
    _, intent = await begin(env)
    await retire(env, intent)
    async with env.factory() as database:
        claim = await claim_cleanup(database)
    async with (
        env.factory() as finisher,
        env.factory() as starter,
        env.factory() as observer,
    ):
        # Populate the queued caller's identity map before the cleanup commit.
        stale = await starter.get(VoxMediaCleanup, claim.id)
        assert stale.status == "claimed"
        finisher_pid = await finisher.scalar(text("SELECT pg_backend_pid()"))
        starter_pid = await starter.scalar(text("SELECT pg_backend_pid()"))
        await finisher.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.id == claim.id).with_for_update()
        )
        task = asyncio.create_task(outcome(provision.begin_attempt(starter, intent)))
        try:
            await wait_for_lock(observer, [starter_pid], finisher_pid)
            assert not task.done()
            assert await finish_cleanup(finisher, claim, receipt(claim)) is True
            result = await asyncio.wait_for(task, 5)
            assert isinstance(result, service.VoxError) and result.status_code == 410
        finally:
            await finisher.rollback()
            await cancel_pending([task])
    async with env.factory() as database:
        assert (await cleanup_row(database, intent.connection_id)).status == "done"
        assert (
            await database.scalar(select(func.count()).select_from(VoxMediaProvisionAttempt)) == 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in-flight", "uncertain"])
async def test_queued_cleanup_ack_cannot_cross_an_unsettled_attempt(postgres_vox_env, status):
    env = postgres_vox_env
    _, intent = await begin(env)
    async with env.factory() as database:
        attempt_id = await provision.begin_attempt(database, intent)
        if status == "uncertain":
            await provision.finish_attempt(
                database, intent.connection_id, attempt_id, acknowledged=False
            )
    await retire(env, intent)
    async with env.factory() as database:
        claim = await claim_cleanup(database)
    async with (
        env.factory() as holder,
        env.factory() as finisher,
        env.factory() as observer,
    ):
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        finisher_pid = await finisher.scalar(text("SELECT pg_backend_pid()"))
        await holder.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.id == claim.id).with_for_update()
        )
        task = asyncio.create_task(finish_cleanup(finisher, claim, receipt(claim)))
        try:
            await wait_for_lock(observer, [finisher_pid], holder_pid)
            assert not task.done()
            await holder.commit()
            assert await asyncio.wait_for(task, 5) is False
        finally:
            await holder.rollback()
            await cancel_pending([task])
    async with env.factory() as database:
        cleanup = await cleanup_row(database, intent.connection_id)
        assert cleanup.status == "pending" and cleanup.completed_at is None
        assert (await database.get(VoxMediaProvisionAttempt, attempt_id)).status == status


@pytest.mark.asyncio
async def test_owner_erasure_does_not_resolve_an_unknown_remote_outcome(
    postgres_vox_env,
):
    env = postgres_vox_env
    row, intent = await begin(env)
    async with env.factory() as database:
        attempt_id = await provision.begin_attempt(database, intent)
        await provision.finish_attempt(
            database, intent.connection_id, attempt_id, acknowledged=False
        )
        await database.execute(delete(User).where(User.id == env.user_id))
        await database.commit()
        unknown = await database.get(VoxMediaProvisionAttempt, attempt_id)
        before = (
            unknown.id,
            unknown.connection_id,
            unknown.status,
            unknown.started_at,
            unknown.finished_at,
        )
        assert await database.get(VoxMediaConnection, intent.connection_id) is None
    async with env.factory() as database:
        claim = await claim_cleanup(database)
        assert claim.connection_id == intent.connection_id
        assert await finish_cleanup(database, claim, receipt(claim)) is False
        saved = await database.get(VoxMediaProvisionAttempt, attempt_id)
        assert (
            saved.id,
            saved.connection_id,
            saved.status,
            saved.started_at,
            saved.finished_at,
        ) == before
        assert (await cleanup_row(database, intent.connection_id)).status == "pending"
    # The account and its browser reservation really were erased, not simply revoked.
    assert (await env.client.get(f"{ROOT}/context")).status_code == 401
    assert UUID(row["client_call_id"]) == intent.client_call_id
