"""SQL-observed races for Vox media epochs, with no external media resources.

SQLite cannot demonstrate these serialization properties; every PostgreSQL
case uses pg_blocking_pids to establish the intended interleaving first.
"""

import asyncio
from dataclasses import asdict
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy import func, select, text
from test_vox_calls import ROOT, instant, start
from test_vox_media import begin, cleanup_row
from test_vox_media import vox_env as shared_vox_fixture
from test_vox_postgres import cancel_pending, outcome, wait_for_lock
from test_vox_postgres import postgres_vox_env as shared_postgres_fixture

from navox.api import agent, auth
from navox.connectors.sync_state import database_now
from navox.db.models import User, UserSession, Workspace
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.student import service as schedule
from navox.vox import media, service

vox_env = shared_vox_fixture
postgres_vox_env = shared_postgres_fixture


async def terminal(env, database, boundary):
    if boundary == "reset":
        await schedule.reset_data(
            database,
            env.user_id,
            env.workspace_id,
            revision=0,
            request_id=uuid4(),
            settings=env.settings,
        )
    elif boundary == "logout":
        request = Request(
            {
                "type": "http",
                "headers": [
                    (b"cookie", f"{env.settings.session_cookie_name}={env.token}".encode())
                ],
            }
        )
        assert await auth.logout(request, Response(), database, env.settings) == {
            "status": "signed_out"
        }
    else:
        account = auth.CurrentAccount(
            await database.get(User, env.user_id),
            await database.get(Workspace, env.workspace_id),
        )
        result = await agent.set_agent_paused(True, current_account=account, database=database)
        assert result.paused is True


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [True, False])
async def test_parallel_connection_intents_never_create_two_current_media_epochs(
    postgres_vox_env, same_request
):
    env = postgres_vox_env
    row, _ = await start(env)
    first_request = uuid4()
    second_request = first_request if same_request else uuid4()
    async with (
        env.factory() as holder,
        env.factory() as first,
        env.factory() as second,
        env.factory() as observer,
    ):
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(holder, env.user_id, env.workspace_id, lock=True)
        tasks = [
            asyncio.create_task(
                outcome(
                    media.begin_connection(
                        database,
                        env.user_id,
                        env.workspace_id,
                        env.token_hash,
                        UUID(row["client_call_id"]),
                        generation=row["generation"],
                        request_id=request_id,
                    )
                )
            )
            for database, request_id in ((first, first_request), (second, second_request))
        ]
        try:
            await wait_for_lock(observer, [first_pid, second_pid], holder_pid)
            assert not any(task.done() for task in tasks)
            await holder.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
        finally:
            await holder.rollback()
            await cancel_pending(tasks)
    successes = [result for result in results if not isinstance(result, service.VoxError)]
    if same_request:
        assert len(successes) == 2 and asdict(successes[0]) == asdict(successes[1])
    else:
        failures = [result for result in results if isinstance(result, service.VoxError)]
        assert len(successes) == len(failures) == 1 and failures[0].status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxMediaConnection)) == 1
        assert await database.scalar(select(func.count()).select_from(VoxMediaCleanup)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["reset", "logout", "pause"])
@pytest.mark.parametrize("operation", ["begin", "confirm", "finalize", "renew"])
async def test_terminal_boundary_wins_over_queued_intent_or_late_confirmation(
    postgres_vox_env, boundary, operation
):
    env = postgres_vox_env
    if operation != "begin":
        row, intent = await begin(env)
    else:
        row, _ = await start(env)
        intent = None
    if operation == "renew":
        async with env.factory() as database:
            await media.confirm_connection(
                database, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
            )
    async with (
        env.factory() as eraser,
        env.factory() as writer,
        env.factory() as observer,
    ):
        # Retain stale ORM objects across the lock wait to catch identity-map reuse.
        stale_user = await writer.get(User, env.user_id)
        stale_session = await writer.get(UserSession, env.session_id)
        stale_call = await writer.get(VoxCall, UUID(row["id"]))
        stale_state = await writer.scalar(select(VoxState))
        stale_connection = (
            await writer.get(VoxMediaConnection, intent.connection_id) if intent else None
        )
        assert stale_user.agent_paused is False and stale_session.revoked_at is None
        assert stale_call.status == "reserved" and stale_state.generation == row["generation"]
        if stale_connection:
            assert stale_connection.status == ("confirmed" if operation == "renew" else "preparing")
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        eraser_pid = await eraser.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(eraser, env.user_id, env.workspace_id, lock=True)
        if operation == "finalize":
            command = media.finalize_provision(writer, intent)
        elif operation == "renew":
            command = media.renew_connection(
                writer,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                intent.connection_id,
                generation=row["generation"],
            )
        elif intent:
            command = media.confirm_connection(
                writer, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
            )
        else:
            command = media.begin_connection(
                writer,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                UUID(row["client_call_id"]),
                generation=row["generation"],
                request_id=uuid4(),
            )
        work = asyncio.create_task(outcome(command))
        try:
            await wait_for_lock(observer, [writer_pid], eraser_pid)
            assert not work.done()
            await terminal(env, eraser, boundary)
            result = await asyncio.wait_for(work, 15)
            assert isinstance(result, service.VoxError)
            if operation == "finalize":
                assert result.status_code == 410
            elif boundary == "logout":
                assert result.status_code == 401
            else:
                assert result.status_code in {404, 409, 410}
        finally:
            await eraser.rollback()
            await cancel_pending([work])
    async with env.factory() as database:
        assert (
            await database.scalar(
                select(func.count())
                .select_from(VoxMediaConnection)
                .where(VoxMediaConnection.status.in_(("preparing", "confirmed")))
            )
            == 0
        )
        if intent:
            assert (await cleanup_row(database, intent.connection_id)).status == "pending"
        else:
            assert await database.scalar(select(func.count()).select_from(VoxMediaCleanup)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["connection", "session"])
@pytest.mark.parametrize("operation", ["confirm", "finalize"])
async def test_confirmation_rechecks_database_time_after_observed_user_lock_wait(
    postgres_vox_env, expiry, operation
):
    env = postgres_vox_env
    row, _ = await start(env)
    if expiry == "connection":
        # Capture a genuinely short connection deadline, then renew only the
        # reservation. Mutating an already captured intent would test snapshot
        # tampering rather than expiry while waiting for the User lock.
        async with env.factory() as setup:
            deadline = await database_now(setup) + timedelta(seconds=3)
            (await setup.get(VoxCall, UUID(row["id"]))).expires_at = deadline
            await setup.commit()
    _, intent = await begin(env, row)
    if expiry == "connection":
        assert intent.expires_at == deadline
        renewed = await env.client.post(
            f"{ROOT}/{row['client_call_id']}/renew", json={"generation": row["generation"]}
        )
        assert renewed.status_code == 200, renewed.text
        assert instant(renewed.json()["expires_at"]) > deadline
    async with (
        env.factory() as holder,
        env.factory() as writer,
        env.factory() as observer,
    ):
        deadline = intent.expires_at
        if expiry == "session":
            deadline = await database_now(holder) + timedelta(seconds=1)
            (await holder.get(UserSession, env.session_id)).expires_at = deadline
            await holder.commit()
        stale_session = await writer.get(UserSession, env.session_id)
        stale_connection = await writer.get(VoxMediaConnection, intent.connection_id)
        assert (
            stale_connection.expires_at if expiry == "connection" else stale_session.expires_at
        ) == deadline
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(holder, env.user_id, env.workspace_id, lock=True)
        work = asyncio.create_task(
            outcome(
                media.finalize_provision(writer, intent)
                if operation == "finalize"
                else media.confirm_connection(
                    writer, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
                )
            )
        )
        try:
            await wait_for_lock(observer, [writer_pid], holder_pid)
            assert not work.done()
            async with asyncio.timeout(4):
                while await database_now(observer) <= deadline:
                    await asyncio.sleep(0.02)
            await holder.commit()
            result = await asyncio.wait_for(work, 15)
            assert isinstance(result, service.VoxError)
            assert result.status_code == (
                410 if operation == "finalize" or expiry == "connection" else 401
            )
        finally:
            await holder.rollback()
            await cancel_pending([work])
    async with env.factory() as database:
        await media.sweep_connections(database)
    async with env.factory() as database:
        assert (await database.get(VoxMediaConnection, intent.connection_id)).status == "revoked"
        assert (await cleanup_row(database, intent.connection_id)).status == "pending"


@pytest.mark.asyncio
async def test_media_renewal_waits_for_cleanup_lock_and_never_rearms_a_claim(postgres_vox_env):
    env = postgres_vox_env
    row, intent = await begin(env)
    async with env.factory() as database:
        await media.confirm_connection(
            database, env.user_id, env.workspace_id, env.token_hash, intent.connection_id
        )
    async with env.factory() as cleaner, env.factory() as writer, env.factory() as observer:
        cleaner_pid = await cleaner.scalar(text("SELECT pg_backend_pid()"))
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        cleanup = await cleaner.scalar(
            select(VoxMediaCleanup)
            .where(VoxMediaCleanup.connection_id == intent.connection_id)
            .with_for_update()
        )
        work = asyncio.create_task(
            outcome(
                media.renew_connection(
                    writer,
                    env.user_id,
                    env.workspace_id,
                    env.token_hash,
                    intent.connection_id,
                    generation=row["generation"],
                )
            )
        )
        try:
            await wait_for_lock(observer, [writer_pid], cleaner_pid)
            assert not work.done()
            now = await database_now(cleaner)
            # Explicit cleaner-state fixture; a claim can never become armed
            # merely because the browser heartbeat queued behind its row lock.
            cleanup.status = "claimed"
            cleanup.requested_at = now
            cleanup.next_attempt_at = now
            cleanup.claim_id = uuid4()
            cleanup.claim_expires_at = now + timedelta(seconds=30)
            await cleaner.commit()
            result = await asyncio.wait_for(work, 15)
            assert isinstance(result, service.VoxError) and result.status_code == 410
        finally:
            await cleaner.rollback()
            await cancel_pending([work])
    async with env.factory() as database:
        connection = await database.get(VoxMediaConnection, intent.connection_id)
        cleanup = await cleanup_row(database, intent.connection_id)
        assert connection.status == "revoked" and cleanup.status == "claimed"
        assert connection.expires_at == cleanup.connection_expires_at
