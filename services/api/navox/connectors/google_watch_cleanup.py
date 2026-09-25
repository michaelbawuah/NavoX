"""Retire legacy Google watches after disconnect, with bounded durable retries."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.core.settings import Settings
from navox.db.models import Connection, ProviderEventSubscription
from navox.providers.google_oauth import (
    GoogleAccessTokenError,
    access_token_for_watch_cleanup,
)
from navox.providers.google_sources import SOURCE_SCOPES

GOOGLE_CALENDAR_STOP = "https://www.googleapis.com/calendar/v3/channels/stop"
GOOGLE_DRIVE_STOP = "https://www.googleapis.com/drive/v3/channels/stop"
DRIVE_READ_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
STALE_CLAIM_AFTER = timedelta(minutes=2)
# Permit clock skew and in-flight delivery after the provider's stated deadline.
EXPIRATION_GRACE = timedelta(minutes=5)


async def due_google_watches(database: AsyncSession, *, limit: int = 8) -> list[UUID]:
    """Include crashed claims; each candidate is conditionally claimed again."""
    now = datetime.now(UTC)
    cutoff = now - STALE_CLAIM_AFTER
    return list(
        await database.scalars(
            select(ProviderEventSubscription.id)
            .join(Connection, Connection.id == ProviderEventSubscription.connection_id)
            .where(
                ProviderEventSubscription.provider == "google",
                Connection.provider == "google",
                Connection.status == "disconnected",
                Connection.user_id == ProviderEventSubscription.user_id,
                Connection.workspace_id == ProviderEventSubscription.workspace_id,
                or_(
                    ProviderEventSubscription.source.in_(("calendar", "drive")),
                    (ProviderEventSubscription.source == "gmail")
                    & ProviderEventSubscription.expiration_confirmed_at.is_not(None)
                    & (ProviderEventSubscription.expiration_confirmed_at <= now)
                    & (ProviderEventSubscription.expires_at <= now - EXPIRATION_GRACE)
                    & (
                        ProviderEventSubscription.expiration_confirmed_at
                        < ProviderEventSubscription.expires_at
                    ),
                ),
                or_(
                    ProviderEventSubscription.status == "cancel_pending",
                    (ProviderEventSubscription.status == "cancelling")
                    & (ProviderEventSubscription.updated_at < cutoff),
                ),
            )
            .order_by(ProviderEventSubscription.updated_at, ProviderEventSubscription.id)
            .limit(limit)
        )
    )


async def cancel_google_watch(
    database: AsyncSession, *, subscription_id: UUID, settings: Settings
) -> bool:
    """Retire after confirmed expiry or a successful channel-specific stop.

    The disconnected credential has one purpose here: ending an existing watch.
    Neither its access token nor Google's error response enters diagnostics.
    """
    now = datetime.now(UTC)
    claimed = await database.scalar(
        update(ProviderEventSubscription)
        .where(
            ProviderEventSubscription.id == subscription_id,
            ProviderEventSubscription.provider == "google",
            or_(
                ProviderEventSubscription.status == "cancel_pending",
                (ProviderEventSubscription.status == "cancelling")
                & (ProviderEventSubscription.updated_at < now - STALE_CLAIM_AFTER),
            ),
        )
        .values(status="cancelling", updated_at=now)
        .returning(ProviderEventSubscription.id)
        .execution_options(synchronize_session=False)
    )
    await database.commit()
    if claimed is None:
        return False

    try:
        row = await database.get(ProviderEventSubscription, subscription_id)
        assert row is not None
        account = await database.get(Connection, row.connection_id)
        if (
            account is None
            or account.id != row.connection_id
            or account.user_id != row.user_id
            or account.workspace_id != row.workspace_id
            or account.provider != "google"
            or account.status != "disconnected"
            or row.source not in {"gmail", "calendar", "drive"}
        ):
            return False

        if (
            row.expiration_confirmed_at is not None
            and row.expires_at is not None
            and utc(row.expiration_confirmed_at) < utc(row.expires_at)
            and utc(row.expires_at) <= now - EXPIRATION_GRACE
        ):
            # Fence expiry against a concurrent renewal or reconnect in the same
            # SQL statement. This uses no OAuth credential and never stops the
            # mailbox-wide watch a newer connection may have registered.
            retired = await database.scalar(
                update(ProviderEventSubscription)
                .where(
                    ProviderEventSubscription.id == subscription_id,
                    ProviderEventSubscription.status == "cancelling",
                    ProviderEventSubscription.updated_at == now,
                    ProviderEventSubscription.expires_at == row.expires_at,
                    ProviderEventSubscription.expiration_confirmed_at
                    == row.expiration_confirmed_at,
                    select(Connection.id)
                    .where(
                        Connection.id == row.connection_id,
                        Connection.user_id == row.user_id,
                        Connection.workspace_id == row.workspace_id,
                        Connection.provider == "google",
                        Connection.status == "disconnected",
                    )
                    .exists(),
                )
                .values(status="cancelled", updated_at=datetime.now(UTC))
                .returning(ProviderEventSubscription.id)
                .execution_options(synchronize_session=False)
            )
            await database.commit()
            return retired is not None

        # Gmail stop is mailbox-wide. A concurrent OAuth callback could activate
        # a newer watch after a local account check. Await confirmed expiry.
        if (
            row.source == "gmail"
            or SOURCE_SCOPES.get(row.source, DRIVE_READ_SCOPE) not in account.granted_scopes
            or not row.resource_id
        ):
            return False

        token = await access_token_for_watch_cleanup(
            database, connection=account, settings=settings
        )
        # Token refresh may take time. Recheck local authority immediately before stop.
        await database.refresh(account)
        await database.refresh(row)
        if account.status != "disconnected" or row.status != "cancelling":
            return False
        url = {
            "calendar": GOOGLE_CALENDAR_STOP,
            "drive": GOOGLE_DRIVE_STOP,
        }[row.source]
        async with httpx.AsyncClient(
            timeout=15.0, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"id": row.channel_id, "resourceId": row.resource_id},
            )
            response.raise_for_status()
        # A second worker or authorization transition cannot confirm this claim.
        retired = await database.scalar(
            update(ProviderEventSubscription)
            .where(
                ProviderEventSubscription.id == subscription_id,
                ProviderEventSubscription.status == "cancelling",
                ProviderEventSubscription.updated_at == now,
                ProviderEventSubscription.connection_id == account.id,
            )
            .values(status="cancelled", updated_at=datetime.now(UTC))
            .returning(ProviderEventSubscription.id)
            .execution_options(synchronize_session=False)
        )
        await database.commit()
        return retired is not None
    except (GoogleAccessTokenError, httpx.HTTPError, ValueError):
        await database.rollback()
        return False
    finally:
        # Retry only after releasing the claim. A crashed process is recovered
        # by due_google_watches once its claim ages out.
        await database.execute(
            update(ProviderEventSubscription)
            .where(
                ProviderEventSubscription.id == subscription_id,
                ProviderEventSubscription.status == "cancelling",
                ProviderEventSubscription.updated_at == now,
            )
            .values(status="cancel_pending", updated_at=datetime.now(UTC))
        )
        await database.commit()
