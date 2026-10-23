"""Bounded, crash-recoverable media cleanup; no provider is enabled here.

Claims are committed before external I/O. Only a matching, unexpired claim and
explicit verification of both participant revocations and room absence may be
acknowledged. An absent room alone is not a token-revocation proof. No endpoint
or automatic worker invokes this module until a real cleaner is qualified.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.sync_state import database_now, utc
from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt

CLAIM_SECONDS = 30
PROVIDER_SECONDS = 10
MAX_ATTEMPTS = 2147483647


@dataclass(frozen=True)
class CleanupClaim:
    id: UUID
    connection_id: UUID
    claim_id: UUID
    expires_at: datetime
    room_name: str
    client_identity: str
    agent_identity: str


@dataclass(frozen=True)
class CleanupReceipt:
    connection_id: UUID
    claim_id: UUID
    room_name: str
    client_identity: str
    agent_identity: str
    client_revocation_verified: bool
    agent_revocation_verified: bool
    room_absence_verified: bool


class MediaCleaner(Protocol):
    async def revoke(self, claim: CleanupClaim) -> CleanupReceipt: ...


def _claim(row: VoxMediaCleanup) -> CleanupClaim:
    assert row.claim_id is not None and row.claim_expires_at is not None
    return CleanupClaim(
        row.id,
        row.connection_id,
        row.claim_id,
        utc(row.claim_expires_at),
        row.room_name,
        row.client_identity,
        row.agent_identity,
    )


async def claim_cleanup(database: AsyncSession, *, orphan_limit: int = 100) -> CleanupClaim | None:
    """Claim one due cleanup; rescue a bounded batch of cascade-orphaned intents."""
    if type(orphan_limit) is not int or not 1 <= orphan_limit <= 100:
        raise ValueError("Cleanup batch must contain between one and 100 records")
    now = await database_now(database)
    orphans = list(
        await database.scalars(
            select(VoxMediaCleanup)
            .where(
                VoxMediaCleanup.status == "armed",
                ~exists().where(VoxMediaConnection.id == VoxMediaCleanup.connection_id),
            )
            .order_by(VoxMediaCleanup.created_at, VoxMediaCleanup.id)
            .limit(orphan_limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
    for orphan in orphans:
        orphan.status = "pending"
        orphan.requested_at = now
        orphan.next_attempt_at = now
    await database.flush()
    row = await database.scalar(
        select(VoxMediaCleanup)
        .where(
            or_(
                (VoxMediaCleanup.status == "pending") & (VoxMediaCleanup.next_attempt_at <= now),
                (VoxMediaCleanup.status == "claimed") & (VoxMediaCleanup.claim_expires_at <= now),
            ),
            VoxMediaCleanup.attempts < MAX_ATTEMPTS,
        )
        .order_by(VoxMediaCleanup.created_at, VoxMediaCleanup.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if row is None:
        await database.commit()
        return None
    # An orphan flush or database wait must not consume the new claim's lifetime.
    now = await database_now(database)
    row.status = "claimed"
    row.claim_id = uuid4()
    row.claim_expires_at = now + timedelta(seconds=CLAIM_SECONDS)
    row.attempts += 1
    result = _claim(row)
    await database.commit()
    return result


def _verified(claim: CleanupClaim, receipt: CleanupReceipt | None) -> bool:
    return (
        isinstance(receipt, CleanupReceipt)
        and receipt.connection_id == claim.connection_id
        and receipt.claim_id == claim.claim_id
        and (receipt.room_name, receipt.client_identity, receipt.agent_identity)
        == (claim.room_name, claim.client_identity, claim.agent_identity)
        and receipt.client_revocation_verified is True
        and receipt.agent_revocation_verified is True
        and receipt.room_absence_verified is True
    )


async def finish_cleanup(
    database: AsyncSession,
    claim: CleanupClaim,
    receipt: CleanupReceipt | None,
) -> bool:
    """Acknowledge only this exact live attempt; uncertain outcomes stay retryable."""
    row = await database.scalar(
        select(VoxMediaCleanup)
        .where(VoxMediaCleanup.id == claim.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    unsettled = None
    if row is not None:
        unsettled = await database.scalar(
            select(VoxMediaProvisionAttempt.id)
            .where(
                VoxMediaProvisionAttempt.connection_id == row.connection_id,
                VoxMediaProvisionAttempt.status != "acknowledged",
            )
            .limit(1)
        )
    now = await database_now(database)
    if (
        row is None
        or row.status != "claimed"
        or row.claim_id != claim.claim_id
        or row.claim_expires_at is None
        or utc(row.claim_expires_at) <= now
        or row.connection_id != claim.connection_id
        or (row.room_name, row.client_identity, row.agent_identity)
        != (claim.room_name, claim.client_identity, claim.agent_identity)
    ):
        await database.commit()
        return False
    # A timed-out RPC may create a room or dispatch after today's absence check.
    # Without acknowledged completion, no finite guessed delay proves quiescence.
    verified = _verified(claim, receipt) and unsettled is None
    row.claim_id = None
    row.claim_expires_at = None
    if verified:
        row.status = "done"
        row.completed_at = now
        row.next_attempt_at = None
    else:
        row.status = "pending"
        row.next_attempt_at = now + timedelta(seconds=min(300, 5 * 2 ** min(row.attempts - 1, 6)))
    await database.commit()
    return verified


async def reconcile_one(
    factory: async_sessionmaker[AsyncSession],
    cleaner: MediaCleaner,
) -> bool:
    """Sweep ownership expiry, then perform at most one bounded external cleanup."""
    from navox.vox.commands import sweep_expired_wake_commands
    from navox.vox.media import sweep_connections

    # Independently retire short-lived private question payloads, even when
    # no provider cleanup is due or a voice worker has crashed.
    async with factory() as database:
        await sweep_expired_wake_commands(database)
    async with factory() as database:
        await sweep_connections(database, limit=100)
    async with factory() as database:
        claim = await claim_cleanup(database)
    if claim is None:
        return False
    receipt = None
    try:
        async with asyncio.timeout(PROVIDER_SECONDS):
            receipt = await cleaner.revoke(claim)
    except Exception:
        # No exception text, provider response, token or private context is stored.
        # Cancellation deliberately leaves the committed claim for crash recovery.
        pass
    async with factory() as database:
        return await finish_cleanup(database, claim, receipt)
