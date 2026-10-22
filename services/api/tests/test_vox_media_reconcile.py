"""Crash recovery and exact-attempt acknowledgements without provider access."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from navox.connectors.sync_state import database_now
from navox.db.vox_media import VoxMediaCleanup
from navox.vox.reconcile import CleanupReceipt, claim_cleanup, finish_cleanup, reconcile_one


async def job(factory, *, status="pending"):
    async with factory() as database:
        now = await database_now(database)
        epoch = uuid4()
        value = VoxMediaCleanup(
            connection_id=epoch,
            owner_scope_id=uuid4(),
            room_name=f"vox-{epoch.hex}",
            client_identity=f"client-{epoch.hex}",
            agent_identity=f"agent-{epoch.hex}",
            status=status,
            connection_expires_at=now + timedelta(seconds=60),
            created_at=now,
            requested_at=None if status == "armed" else now,
            next_attempt_at=None if status == "armed" else now,
            attempts=0,
        )
        database.add(value)
        await database.commit()
        return value.id


def receipt(claim):
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
async def test_orphaned_intent_survives_owner_cascade_and_is_claimed_once(ai_database):
    identifier = await job(ai_database, status="armed")
    async with ai_database() as database:
        claim = await claim_cleanup(database)
    assert claim.id == identifier
    async with ai_database() as database:
        assert await claim_cleanup(database) is None
        assert await finish_cleanup(database, claim, receipt(claim)) is True
    async with ai_database() as database:
        row = await database.get(VoxMediaCleanup, identifier)
        assert row.status == "done" and row.completed_at is not None
        assert row.claim_id is row.claim_expires_at is row.next_attempt_at is None
        assert await finish_cleanup(database, claim, receipt(claim)) is False


@pytest.mark.asyncio
async def test_crashed_attempt_is_reclaimed_and_old_worker_cannot_acknowledge(ai_database):
    identifier = await job(ai_database)
    async with ai_database() as database:
        old = await claim_cleanup(database)
        row = await database.get(VoxMediaCleanup, identifier)
        row.claim_expires_at = await database_now(database) - timedelta(seconds=1)
        await database.commit()
    async with ai_database() as database:
        assert await finish_cleanup(database, old, receipt(old)) is False
        replacement = await claim_cleanup(database)
    assert replacement.id == old.id and replacement.claim_id != old.claim_id
    async with ai_database() as database:
        assert await finish_cleanup(database, old, receipt(old)) is False
        assert await finish_cleanup(database, replacement, receipt(replacement)) is True
        row = await database.get(VoxMediaCleanup, identifier)
        assert row.attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "connection_id",
        "claim_id",
        "room_name",
        "client_identity",
        "agent_identity",
        "client_revocation_verified",
        "agent_revocation_verified",
        "room_absence_verified",
    ],
)
async def test_partial_or_foreign_provider_receipt_never_completes_cleanup(ai_database, field):
    identifier = await job(ai_database)
    async with ai_database() as database:
        claim = await claim_cleanup(database)
        proof = receipt(claim)
        original = getattr(proof, field)
        wrong = False if type(original) is bool else uuid4() if field.endswith("_id") else "other"
        assert await finish_cleanup(database, claim, replace(proof, **{field: wrong})) is False
    async with ai_database() as database:
        row = await database.get(VoxMediaCleanup, identifier)
        assert row.status == "pending" and row.completed_at is None
        assert row.claim_id is row.claim_expires_at is None
        assert row.next_attempt_at > row.requested_at
        assert await claim_cleanup(database) is None


@pytest.mark.asyncio
async def test_late_provisioning_invalidates_a_live_cleanup_claim(ai_database):
    identifier = await job(ai_database)
    async with ai_database() as database:
        old = await claim_cleanup(database)
        row = await database.get(VoxMediaCleanup, identifier)
        # Same transaction used by late-confirm revocation: the old claim loses
        # authority even if its remote request eventually reports success.
        row.status = "pending"
        row.claim_id = row.claim_expires_at = None
        row.next_attempt_at = await database_now(database)
        await database.commit()
    async with ai_database() as database:
        assert await finish_cleanup(database, old, receipt(old)) is False
        current = await claim_cleanup(database)
        assert current.claim_id != old.claim_id
        assert await finish_cleanup(database, current, receipt(current)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "timeout", "partial"])
async def test_reconciler_releases_database_locks_before_io_and_preserves_uncertainty(
    ai_database,
    monkeypatch,
    failure,
):
    identifier = await job(ai_database)
    monkeypatch.setattr("navox.vox.reconcile.PROVIDER_SECONDS", 0.01)

    class Cleaner:
        async def revoke(self, claim):
            # Read the committed claim from an entirely separate transaction.
            async with ai_database() as observer:
                stored = await observer.get(VoxMediaCleanup, identifier)
                assert stored.status == "claimed" and stored.claim_id == claim.claim_id
            if failure == "exception":
                raise RuntimeError("synthetic provider secret must not be retained")
            if failure == "timeout":
                await asyncio.sleep(1)
            return replace(receipt(claim), client_revocation_verified=False)

    assert await reconcile_one(ai_database, Cleaner()) is False
    async with ai_database() as database:
        row = await database.get(VoxMediaCleanup, identifier)
        assert row.status == "pending" and row.attempts == 1
        assert set(row.__table__.columns.keys()).isdisjoint({"error", "token", "transcript"})


@pytest.mark.asyncio
async def test_reconciler_handles_at_most_one_external_cleanup_per_invocation(ai_database):
    await job(ai_database)
    await job(ai_database)
    calls = []

    class Cleaner:
        async def revoke(self, claim):
            calls.append(claim)
            return receipt(claim)

    assert await reconcile_one(ai_database, Cleaner()) is True
    assert len(calls) == 1
    async with ai_database() as database:
        rows = list(await database.scalars(select(VoxMediaCleanup)))
        assert sorted(row.status for row in rows) == ["done", "pending"]


@pytest.mark.asyncio
async def test_cancellation_leaves_claim_for_recovery_instead_of_claiming_success(ai_database):
    identifier = await job(ai_database)
    entered = asyncio.Event()

    class Cleaner:
        async def revoke(self, claim):
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(reconcile_one(ai_database, Cleaner()))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with ai_database() as database:
        row = await database.get(VoxMediaCleanup, identifier)
        assert row.status == "claimed" and row.completed_at is None
