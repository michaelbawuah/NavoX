"""Database outbox reconciliation; Temporal payloads contain no private links."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity

from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings, get_settings
from navox.db.lms_feeds import LmsFeed, LmsFeedPreview, LmsFeedSource
from navox.db.models import Connection, ConnectionCredential, ConnectorConnection
from navox.db.session import get_session_factory
from navox.lms_feeds.parser import parse_bounded
from navox.lms_feeds.schemas import ParsedFeed, Platform, SyncResult
from navox.lms_feeds.service import (
    FeedAccessError,
    apply_snapshot,
    aware,
    locked_feed,
    next_sync,
)
from navox.lms_feeds.transport import FeedError, FeedFetcher


async def sync_feed(
    factory: async_sessionmaker[AsyncSession],
    *,
    feed_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    settings: Settings,
    fetcher: FeedFetcher | None = None,
    now: datetime | None = None,
) -> SyncResult:
    if not getattr(settings, "student_lms_feeds_enabled", False):
        return SyncResult(state="disabled")
    current = aware(now or datetime.now(UTC))
    token = uuid4()
    async with factory() as database:
        try:
            feed = await locked_feed(
                database, feed_id=feed_id, user_id=user_id, workspace_id=workspace_id, active=True
            )
        except FeedAccessError:
            return SyncResult(state="superseded")
        if feed.lease_until and aware(feed.lease_until) > current:
            return SyncResult(state="superseded")
        if feed.next_sync_at and aware(feed.next_sync_at) > current:
            return SyncResult(state="superseded")
        with await SecretBroker(settings).lease(
            database,
            connection_id=feed.connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            purpose="sync.read",
            names={"FEED_URL"},
        ) as lease:
            private_url = lease.get("FEED_URL")
        feed.lease_token, feed.lease_until = token, current + timedelta(seconds=45)
        revision, timezone, due_time = feed.revision, feed.timezone, feed.all_day_due_time
        connection = await database.get(ConnectorConnection, feed.connection_id)
        credential_id = connection.credential_reference if connection else None
        generation = connection.sync_generation if connection else None
        platform = cast(Platform, feed.platform)
        etag, modified = feed.etag, feed.last_modified
        if feed.last_expanded_at is None or aware(feed.last_expanded_at).date() != current.date():
            # A 304 cannot extend a recurrence projection's rolling horizon.
            etag, modified = None, None
        await database.commit()
    error: FeedError | None = None
    parsed: ParsedFeed | None = None
    fetched = None
    try:
        fetched = await (fetcher or FeedFetcher()).fetch(
            private_url, etag=etag, last_modified=modified
        )
        if fetched.status == 304 and not (etag or modified):
            raise FeedError("unexpected_not_modified")
        if fetched.status == 200:
            parsed = await parse_bounded(
                fetched.content,
                timezone=timezone,
                all_day_due_time=due_time,
                now=current,
                private_url=private_url,
                platform=platform,
            )
    except FeedError as failure:
        error = failure
    finally:
        private_url = ""
    async with factory() as database:
        try:
            feed = await locked_feed(
                database, feed_id=feed_id, user_id=user_id, workspace_id=workspace_id, active=True
            )
        except FeedAccessError:
            return SyncResult(state="superseded")
        if feed.revision != revision or feed.lease_token != token:
            return SyncResult(state="superseded")
        connection = await database.get(ConnectorConnection, feed.connection_id)
        if (
            connection is None
            or connection.credential_reference != credential_id
            or connection.sync_generation != generation
        ):
            feed.lease_token, feed.lease_until = None, None
            feed.next_sync_at = current + timedelta(minutes=1)
            await database.commit()
            return SyncResult(state="superseded")
        feed.lease_token, feed.lease_until = None, None
        if not getattr(settings, "student_lms_feeds_enabled", False):
            await database.commit()
            return SyncResult(state="disabled")
        if error is not None:
            auth_failure = error.status in {401, 403, 404}
            code = "feed_link_expired" if auth_failure else error.code
            feed.failure_count = feed.failure_count + 1 if feed.last_error == code else 1
            feed.last_error = code
            feed.next_sync_at = current + timedelta(hours=2)
            if auth_failure and feed.failure_count >= 3:
                feed.state, feed.next_sync_at = "paused", None
                feed.revision += 1
            await database.commit()
            return SyncResult(state="paused" if feed.state == "paused" else "failed")
        if fetched is None:
            return SyncResult(state="failed")
        feed.failure_count, feed.last_error = 0, None
        feed.last_synced_at = current
        feed.etag = fetched.etag or feed.etag
        feed.last_modified = fetched.last_modified or feed.last_modified
        if parsed is not None:
            try:
                result = await apply_snapshot(database, feed, parsed, now=current)
            except FeedError as capacity_error:
                feed.state, feed.last_error, feed.next_sync_at = "paused", capacity_error.code, None
                feed.revision += 1
                await database.commit()
                return SyncResult(state="paused")
            feed.next_sync_at = next_sync(parsed, current)
            feed.last_expanded_at = current
        else:
            # A 304 still reevaluates the near-deadline cadence from saved rows.
            rows = await database.scalars(
                select(LmsFeedSource).where(
                    LmsFeedSource.feed_id == feed.id, LmsFeedSource.present.is_(True)
                )
            )
            from navox.lms_feeds.schemas import FeedItem

            near = any(
                (payload := FeedItem.model_validate(source.payload)).due_at is not None
                and not payload.cancelled
                and current <= payload.due_at <= current + timedelta(hours=24)
                for source in rows
            )
            feed.next_sync_at = current + (timedelta(minutes=30) if near else timedelta(hours=2))
            result = SyncResult(state="unchanged")
        await database.commit()
        return result


async def reconcile_feeds(
    factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    current = aware(now or datetime.now(UTC))
    async with factory() as database:
        expired = list(
            await database.execute(
                select(LmsFeed.id, LmsFeed.user_id, LmsFeed.workspace_id)
                .join(LmsFeedPreview, LmsFeedPreview.feed_id == LmsFeed.id)
                .where(LmsFeed.state == "preview", LmsFeedPreview.expires_at <= current)
                .limit(20)
            )
        )
    for feed_id, user_id, workspace_id in expired:
        async with factory() as database:
            await expire_preview(
                database, feed_id=feed_id, user_id=user_id, workspace_id=workspace_id, now=current
            )
    if not getattr(settings, "student_lms_feeds_enabled", False):
        return 0
    async with factory() as database:
        pending = list(
            await database.execute(
                select(LmsFeed.id, LmsFeed.user_id, LmsFeed.workspace_id)
                .where(
                    LmsFeed.state == "active",
                    LmsFeed.next_sync_at <= current,
                    or_(LmsFeed.lease_until.is_(None), LmsFeed.lease_until <= current),
                )
                .order_by(LmsFeed.next_sync_at, LmsFeed.id)
                .limit(20)
            )
        )
    semaphore = asyncio.Semaphore(2)

    async def one(feed_id: UUID, user_id: UUID, workspace_id: UUID) -> None:
        async with semaphore:
            try:
                await sync_feed(
                    factory,
                    feed_id=feed_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    settings=settings,
                    now=current,
                )
            except (FeedAccessError, SecretBrokerError):
                return

    await asyncio.gather(*(one(*row) for row in pending))
    return len(pending)


async def expire_preview(
    database: AsyncSession, *, feed_id: UUID, user_id: UUID, workspace_id: UUID, now: datetime
) -> None:
    """Erase an expired unconfirmed preview even after membership is revoked.

    This path cannot fetch, decrypt or publish data. Only the new, private
    broker-only connection created for this exact preview can be removed.
    """
    feed = await database.scalar(
        select(LmsFeed).where(
            LmsFeed.id == feed_id, LmsFeed.user_id == user_id, LmsFeed.workspace_id == workspace_id
        )
    )
    if feed is None:
        return
    connection = await database.scalar(
        select(ConnectorConnection)
        .where(
            ConnectorConnection.id == feed.connection_id,
            ConnectorConnection.user_id == user_id,
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.provider == "lms_feed",
            ConnectorConnection.legacy_connection_id.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if connection is None or connection.config.get("managed_by") != "student.lms-feeds":
        return
    await database.refresh(feed, with_for_update=True)
    preview = await database.scalar(
        select(LmsFeedPreview)
        .where(LmsFeedPreview.feed_id == feed.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        feed.state != "preview"
        or preview is None
        or preview.confirmed_at is not None
        or aware(preview.expires_at) > aware(now)
    ):
        return
    if await database.scalar(
        select(LmsFeedSource.id).where(LmsFeedSource.feed_id == feed.id).limit(1)
    ):
        return  # Confirmed/derived data must use the normal serialized erasure path.
    credential_id = connection.credential_reference
    connection.credential_reference = None
    await database.execute(delete(LmsFeedPreview).where(LmsFeedPreview.id == preview.id))
    await database.delete(feed)
    await database.flush()
    await database.delete(connection)
    await database.flush()
    if credential_id:
        native = await database.scalar(
            select(ConnectorConnection.id)
            .where(ConnectorConnection.credential_reference == credential_id)
            .limit(1)
        )
        legacy = await database.scalar(
            select(Connection.id).where(Connection.credential_reference == credential_id).limit(1)
        )
        if native is None and legacy is None:
            await database.execute(
                delete(ConnectionCredential).where(ConnectionCredential.id == credential_id)
            )
    await database.commit()


@activity.defn
async def reconcile_lms_feeds_activity() -> int:
    return await reconcile_feeds(get_session_factory(), settings=get_settings())
