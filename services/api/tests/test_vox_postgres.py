"""SQL-observed PostgreSQL serialization of private Vox ownership leases.

The existing NAVOX_CONNECTOR_TEST_DSN fixture isolates every test in its own
schema. SQLite skips these tests: scheduled tasks cannot prove row locking.
"""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import Request, Response
from sqlalchemy import func, select, text
from test_vox_calls import ROOT, context, instant, start, vox_env  # noqa: F401

from navox.api import auth
from navox.connectors.sync_state import database_now
from navox.db.models import UserSession
from navox.db.vox import VoxCall, VoxState
from navox.student import service as schedule
from navox.vox import service
from navox.vox.schemas import VoxLease, VoxStart


@pytest_asyncio.fixture
async def postgres_vox_env(vox_env):  # noqa: F811 - imported shared pytest fixture
    if vox_env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires real PostgreSQL User row locks and pg_blocking_pids")
    return vox_env


async def wait_for_lock(observer, pids: list[int], holder_pid: int) -> None:
    async with asyncio.timeout(10):
        while True:
            blockers = [
                await observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": pid})
                for pid in pids
            ]
            if all(blockers) and any(holder_pid in values for values in blockers):
                return
            await asyncio.sleep(0.01)


async def outcome(operation):
    try:
        return await operation
    except service.VoxError as error:
        return error


async def cancel_pending(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [True, False])
async def test_parallel_starts_serialize_and_never_allocate_two_reservations(
    postgres_vox_env, same_request
):
    env = postgres_vox_env
    previous, _ = await start(env)
    assert (
        await env.client.post(f"{ROOT}/{previous['client_call_id']}/end", json={"generation": 0})
    ).status_code == 204
    generation = (await context(env))["generation"]
    first_input = VoxStart(client_call_id=uuid4(), generation=generation, mode="off-record")
    second_input = (
        first_input if same_request else first_input.model_copy(update={"client_call_id": uuid4()})
    )
    async with (
        env.factory() as holder,
        env.factory() as first,
        env.factory() as second,
        env.factory() as observer,
    ):
        # Retain identities through the wait, rather than relying on weak identity-map entries.
        stale_first = await first.scalar(select(VoxState))
        stale_second = await second.scalar(select(VoxState))
        assert stale_first.generation == stale_second.generation == generation
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(holder, env.user_id, env.workspace_id, lock=True)
        tasks = [
            asyncio.create_task(
                outcome(
                    service.start(first, env.user_id, env.workspace_id, env.token_hash, first_input)
                )
            ),
            asyncio.create_task(
                outcome(
                    service.start(
                        second, env.user_id, env.workspace_id, env.token_hash, second_input
                    )
                )
            ),
        ]
        try:
            await wait_for_lock(observer, [first_pid, second_pid], holder_pid)
            assert not any(task.done() for task in tasks)
            await holder.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
        finally:
            await holder.rollback()
            await cancel_pending(tasks)
    if same_request:
        assert results[0].model_dump() == results[1].model_dump()
    else:
        successes = [result for result in results if not isinstance(result, service.VoxError)]
        errors = [result for result in results if isinstance(result, service.VoxError)]
        assert len(successes) == len(errors) == 1 and errors[0].status_code == 409
    async with env.factory() as database:
        calls = list(await database.scalars(select(VoxCall).where(VoxCall.status == "reserved")))
        assert len(calls) == 1 and calls[0].generation == generation


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["reset", "logout"])
@pytest.mark.parametrize("operation", ["start", "renew"])
async def test_reset_or_logout_wins_over_queued_work_with_stale_orm_authority(
    postgres_vox_env, boundary, operation
):
    env = postgres_vox_env
    row, _ = await start(env)
    async with (
        env.factory() as eraser,
        env.factory() as writer,
        env.factory() as observer,
    ):
        stale_call = await writer.get(VoxCall, UUID(row["id"]))
        stale_state = await writer.scalar(select(VoxState))
        stale_session = await writer.get(UserSession, env.session_id)
        assert stale_call.status == "reserved" and stale_state.generation == 0
        assert stale_session.revoked_at is None
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        eraser_pid = await eraser.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(eraser, env.user_id, env.workspace_id, lock=True)
        if operation == "start":
            command = service.start(
                writer,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                VoxStart(client_call_id=uuid4(), generation=0, mode="off-record"),
            )
        else:
            command = service.renew(
                writer,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                UUID(row["client_call_id"]),
                VoxLease(generation=0),
            )
        work = asyncio.create_task(outcome(command))
        try:
            await wait_for_lock(observer, [writer_pid], eraser_pid)
            assert not work.done()
            if boundary == "reset":
                await schedule.reset_data(
                    eraser,
                    env.user_id,
                    env.workspace_id,
                    revision=0,
                    request_id=uuid4(),
                    settings=env.settings,
                )
            else:
                request = Request(
                    {
                        "type": "http",
                        "headers": [
                            (b"cookie", f"{env.settings.session_cookie_name}={env.token}".encode())
                        ],
                    }
                )
                assert await auth.logout(request, Response(), eraser, env.settings) == {
                    "status": "signed_out"
                }
            result = await asyncio.wait_for(work, 15)
            assert isinstance(result, service.VoxError)
            assert result.status_code == (
                401 if boundary == "logout" else 409 if operation == "start" else 404
            )
        finally:
            await eraser.rollback()
            await cancel_pending([work])
    async with env.factory() as database:
        state = await database.scalar(select(VoxState))
        assert state.generation > row["generation"]
        assert (
            await database.scalar(
                select(func.count()).select_from(VoxCall).where(VoxCall.status == "reserved")
            )
            == 0
        )
        if boundary == "reset":
            assert await database.scalar(select(func.count()).select_from(VoxCall)) == 0
        else:
            call = await database.get(VoxCall, UUID(row["id"]))
            assert call.status == "ended" and call.end_reason == "sign-out"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expiry", "operation"), [("auth", "start"), ("auth", "renew"), ("lease", "renew")]
)
async def test_expiry_during_user_lock_wait_uses_fresh_database_clock(
    postgres_vox_env, expiry, operation
):
    env = postgres_vox_env
    row, _ = await start(env)
    if operation == "start":
        assert (
            await env.client.post(f"{ROOT}/{row['client_call_id']}/end", json={"generation": 0})
        ).status_code == 204
    generation = (await context(env))["generation"]
    async with (
        env.factory() as holder,
        env.factory() as writer,
        env.factory() as observer,
    ):
        deadline = await database_now(holder) + timedelta(seconds=1)
        if expiry == "auth":
            session = await holder.get(UserSession, env.session_id)
            session.expires_at = deadline
        else:
            call = await holder.get(VoxCall, UUID(row["id"]))
            call.expires_at = deadline
        await holder.commit()
        stale_session = await writer.get(UserSession, env.session_id)
        stale_call = await writer.get(VoxCall, UUID(row["id"]))
        assert (stale_session.expires_at if expiry == "auth" else stale_call.expires_at) == deadline
        writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        await schedule.owner(holder, env.user_id, env.workspace_id, lock=True)
        if operation == "start":
            command = service.start(
                writer,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                VoxStart(client_call_id=uuid4(), generation=generation, mode="off-record"),
            )
        else:
            command = service.renew(
                writer,
                env.user_id,
                env.workspace_id,
                env.token_hash,
                UUID(row["client_call_id"]),
                VoxLease(generation=generation),
            )
        work = asyncio.create_task(outcome(command))
        try:
            await wait_for_lock(observer, [writer_pid], holder_pid)
            assert not work.done()
            # Use server time on both sides of the wait; now()/transaction time is insufficient.
            async with asyncio.timeout(3):
                while await database_now(observer) <= deadline:
                    await asyncio.sleep(0.02)
            await holder.commit()
            result = await asyncio.wait_for(work, 15)
            assert isinstance(result, service.VoxError)
            assert result.status_code == (401 if expiry == "auth" else 410)
        finally:
            await holder.rollback()
            await cancel_pending([work])
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(VoxCall)) == 1
        if operation == "start":
            assert (
                await database.scalar(
                    select(func.count()).select_from(VoxCall).where(VoxCall.status == "reserved")
                )
                == 0
            )
        else:
            call = await database.get(VoxCall, UUID(row["id"]))
            if expiry == "auth":
                assert call.expires_at == instant(row["expires_at"])
            else:
                assert call.status == "ended" and call.end_reason == "expired"
                assert call.expires_at == deadline and call.ended_at >= deadline
                state = await database.scalar(select(VoxState))
                assert state.generation > row["generation"]
