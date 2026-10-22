"""Actual PG session fences; SQLite cannot qualify the production worker lock."""

import asyncio

import pytest
from sqlalchemy import select, text
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import confirm
from test_vox_worker import assignment, prepared

from navox.db.models import User
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost
from navox.vox.worker_livekit import exclusive_epoch

vox_env = shared_vox_fixture


@pytest.fixture
def postgres_factory(ai_database):
    if ai_database.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL session advisory locks")
    return ai_database


@pytest.mark.asyncio
async def test_duplicate_epoch_cannot_enter_until_holder_releases_even_across_commit(
    postgres_factory,
):
    engine = postgres_factory.kw["bind"]
    value = assignment()
    async with exclusive_epoch(engine, value):
        with pytest.raises(WorkerAccessLost, match="already has"):
            async with exclusive_epoch(engine, value):
                pytest.fail("Duplicate worker must not connect to RTC")
        # An unrelated epoch is independent; no global or User lock is retained.
        async with exclusive_epoch(engine, assignment()):
            pass
    async with exclusive_epoch(engine, value):
        pass


@pytest.mark.asyncio
async def test_exception_releases_physical_session_lock_and_never_retains_user_row(vox_env):
    engine = vox_env.factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL advisory and NOWAIT row locks")
    value = assignment()
    with pytest.raises(RuntimeError, match="injected bounded"):
        async with exclusive_epoch(engine, value):
            async with vox_env.factory() as observer:
                await asyncio.wait_for(
                    observer.scalar(
                        select(User).where(User.id == vox_env.user_id).with_for_update(nowait=True)
                    ),
                    2,
                )
                # The advisory lock's connection committed its transaction before
                # RTC/provider work; another connection observes no idle tx there.
                active = await observer.scalar(
                    text("""
                    SELECT count(*) FROM pg_stat_activity
                    WHERE state = 'idle in transaction'
                    AND query LIKE 'SELECT pg_try_advisory_lock%'
                """)
                )
                assert active == 0
            raise RuntimeError("injected bounded worker failure")
    async with exclusive_epoch(engine, value):
        pass


@pytest.mark.asyncio
async def test_lost_lock_session_cannot_keep_using_fresh_pooled_authority_after_takeover(vox_env):
    engine = vox_env.factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL backend termination and session advisory locks")
    intent, base = await prepared(vox_env)
    await confirm(vox_env, intent)
    async with exclusive_epoch(engine, base.assignment) as connection:
        original = DatabaseAuthority(vox_env.factory, base.assignment, connection)
        await original()
        pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        await connection.commit()
        async with vox_env.factory() as killer:
            assert await killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            await killer.commit()
        # The database epoch still exists and a pooled connection still sees it.
        await base()
        async with exclusive_epoch(engine, base.assignment) as successor_connection:
            successor = DatabaseAuthority(vox_env.factory, base.assignment, successor_connection)
            await successor()
            with pytest.raises(WorkerAccessLost, match="connection ended"):
                await original()
            # Sticky failure also prevents SQLAlchemy transparently reconnecting.
            with pytest.raises(WorkerAccessLost, match="connection ended"):
                await original()
            await original.retire()
            await successor()
