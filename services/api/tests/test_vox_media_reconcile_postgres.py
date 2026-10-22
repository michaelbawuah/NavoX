"""Actual PostgreSQL locks around cleanup claims and the external-I/O boundary."""

import asyncio

import pytest
from sqlalchemy import select
from test_vox_media_reconcile import job, receipt

from navox.db.vox_media import VoxMediaCleanup
from navox.vox.reconcile import claim_cleanup, reconcile_one


@pytest.fixture
def postgres_factory(ai_database):
    if ai_database.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL SKIP LOCKED and NOWAIT")
    return ai_database


@pytest.mark.asyncio
async def test_another_worker_skips_a_row_locked_by_a_crashed_claim_transaction(postgres_factory):
    first = await job(postgres_factory)
    second = await job(postgres_factory)
    async with postgres_factory() as holder, postgres_factory() as worker:
        await holder.scalar(
            select(VoxMediaCleanup).where(VoxMediaCleanup.id == first).with_for_update()
        )
        claim = await asyncio.wait_for(claim_cleanup(worker), 2)
        assert claim.id == second
        await holder.rollback()
    async with postgres_factory() as worker:
        remaining = await claim_cleanup(worker)
        assert remaining.id == first and remaining.claim_id != claim.claim_id


@pytest.mark.asyncio
async def test_external_cleanup_callback_can_take_the_claim_row_lock_without_waiting(
    postgres_factory,
):
    identifier = await job(postgres_factory)

    class Cleaner:
        async def revoke(self, claim):
            async with postgres_factory() as observer:
                row = await observer.scalar(
                    select(VoxMediaCleanup)
                    .where(VoxMediaCleanup.id == identifier)
                    .with_for_update(nowait=True)
                )
                assert row.claim_id == claim.claim_id and row.status == "claimed"
            return receipt(claim)

    assert await reconcile_one(postgres_factory, Cleaner()) is True
