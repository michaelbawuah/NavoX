"""Feed review and diffing. No source text becomes a model prompt or an action."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.lms_feed import LMS_FEED_MANIFEST, item_hash, item_key
from navox.connectors.secrets import SecretBroker
from navox.core.settings import Settings
from navox.db.lms_feeds import LmsFeed, LmsFeedItem, LmsFeedPreview, LmsFeedSource
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.student import StudentCourse
from navox.lms_feeds.schemas import (
    MAX_FEEDS,
    ConfirmCommand,
    FeedItem,
    ParsedFeed,
    PreviewCommand,
    SyncResult,
)
from navox.lms_feeds.transport import FeedError, FeedResponse, feed_domain


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class FeedAccessError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def reset_epoch(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID
) -> tuple[int, str | None]:
    predicate = (
        AuditEvent.user_id == user_id,
        AuditEvent.workspace_id == workspace_id,
        AuditEvent.event_type == "student.data.reset",
    )
    count = await database.scalar(select(func.count()).select_from(AuditEvent).where(*predicate))
    latest = await database.scalar(
        select(AuditEvent.id)
        .where(*predicate)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    # Count also catches resets sharing SQLite's second-resolution timestamp.
    return int(count or 0), str(latest) if latest else None


async def require_owner(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    active: bool,
    lock: bool = False,
) -> User:
    query = select(User).where(User.id == user_id)
    if lock:
        query = query.with_for_update()
    user = await database.scalar(query.execution_options(populate_existing=True))
    member = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    workspace = await database.get(Workspace, workspace_id, populate_existing=True)
    if (
        user is None
        or member is None
        or member.role != "owner"
        or workspace is None
        or workspace.workspace_type.casefold() != "personal"
    ):
        raise FeedAccessError(403, "Your personal workspace is required")
    if active and user.agent_paused:
        raise FeedAccessError(409, "Resume assistance before reading a calendar feed")
    return user


async def locked_feed(
    database: AsyncSession,
    *,
    feed_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    active: bool = False,
) -> LmsFeed:
    row = await database.scalar(
        select(LmsFeed).where(
            LmsFeed.id == feed_id,
            LmsFeed.user_id == user_id,
            LmsFeed.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise FeedAccessError(404, "Calendar feed not found")
    try:
        connection = await owned_connector(
            database,
            connection_id=row.connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=active,
            lock_authority=True,
        )
    except ConnectorAccessDenied:
        raise FeedAccessError(403, "Calendar feed access is no longer available") from None
    await require_owner(database, user_id=user_id, workspace_id=workspace_id, active=active)
    current = await database.scalar(
        select(LmsFeed)
        .where(LmsFeed.id == feed_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None or (
        active
        and (
            current.state != "active"
            or "academic.calendar.read" not in connection.authorized_capabilities
            or "academic.calendar.read" not in connection.provider_capabilities
        )
    ):
        raise FeedAccessError(409, "Calendar feed is paused or unavailable")
    return current


async def create_preview(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    command: PreviewCommand,
    parsed: ParsedFeed,
    fetched: FeedResponse,
    settings: Settings,
    now: datetime,
    expected_reset: tuple[int, str | None],
) -> tuple[LmsFeed, LmsFeedPreview]:
    await require_owner(
        database, user_id=user_id, workspace_id=workspace_id, active=True, lock=True
    )
    if await reset_epoch(database, user_id=user_id, workspace_id=workspace_id) != expected_reset:
        raise FeedAccessError(
            409, "Student data was reset while this calendar was loading. Start a fresh preview."
        )
    count = await database.scalar(
        select(func.count())
        .select_from(LmsFeed)
        .where(LmsFeed.user_id == user_id, LmsFeed.workspace_id == workspace_id)
    )
    if count is not None and count >= MAX_FEEDS:
        raise FeedAccessError(409, "Remove a calendar feed before adding another")
    definition_id = uuid5(NAMESPACE_URL, "navox:lms-calendar-feed:1.0.0")
    if await database.get(ConnectorDefinition, definition_id) is None:
        # The definition has a stable server-owned ID. Its registration is idempotent.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        insert = pg_insert if database.get_bind().dialect.name == "postgresql" else sqlite_insert
        statement = (
            insert(ConnectorDefinition)
            .values(
                id=definition_id,
                connector_key=LMS_FEED_MANIFEST.id,
                version=LMS_FEED_MANIFEST.version,
                display_name=LMS_FEED_MANIFEST.display_name,
                connector_class="POLLING",
                trust_level="NAVOX_FIRST_PARTY",
                manifest=LMS_FEED_MANIFEST.model_dump(mode="json", by_alias=True),
                active=True,
            )
            .on_conflict_do_nothing()
        )
        await database.execute(statement)
    connection = ConnectorConnection(
        id=uuid4(),
        connector_definition_id=definition_id,
        user_id=user_id,
        workspace_id=workspace_id,
        provider="lms_feed",
        external_account_id=str(uuid4()),
        display_name="Learning-system calendar",
        status="CONNECTED",
        health_state="CONNECTED",
        authorized_capabilities=["academic.calendar.read"],
        provider_capabilities=["academic.calendar.read"],
        config={"managed_by": "student.lms-feeds", "managed_by_source_workflow": True},
    )
    database.add(connection)
    await database.flush()
    await SecretBroker(settings).store(
        database,
        {"FEED_URL": command.url},
        connection_id=connection.id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    feed = LmsFeed(
        id=uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        connection_id=connection.id,
        domain=feed_domain(command.url),
        name="Calendar preview",
        platform=parsed.platform,
        timezone=command.timezone,
        all_day_due_time=command.all_day_due_time,
        state="preview",
        revision=1,
        course_matches={},
        etag=fetched.etag,
        last_modified=fetched.last_modified,
        failure_count=0,
    )
    database.add(feed)
    await database.flush()
    snapshot = parsed.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(
            [str(feed.id), snapshot, command.timezone, command.all_day_due_time],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    preview = LmsFeedPreview(
        id=uuid4(),
        feed_id=feed.id,
        preview_hash=digest,
        parsed=snapshot,
        expires_at=aware(now) + timedelta(minutes=30),
    )
    database.add(preview)
    await database.commit()
    return feed, preview


def model_feed_item(row: LmsFeedItem) -> FeedItem:
    return FeedItem.model_validate(row.payload)


async def owned_item(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    item_id: UUID,
    require_active: bool = True,
) -> LmsFeedItem:
    await require_owner(database, user_id=user_id, workspace_id=workspace_id, active=require_active)
    row = await database.scalar(
        select(LmsFeedItem).where(
            LmsFeedItem.id == item_id,
            LmsFeedItem.user_id == user_id,
            LmsFeedItem.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise FeedAccessError(404, "Calendar item not found")
    if not await item_sources_authorized(database, row, require_active=require_active):
        raise FeedAccessError(403, "Calendar item source is no longer authorized")
    if require_active and (row.cancelled or row.conflicted):
        raise FeedAccessError(409, "Review this calendar item's source before importing it")
    return row


async def item_sources_authorized(
    database: AsyncSession,
    item: LmsFeedItem,
    *,
    require_active: bool = False,
) -> bool:
    rows = list(
        await database.execute(
            select(LmsFeed, ConnectorConnection, ConnectorDefinition)
            .select_from(LmsFeedSource)
            .join(LmsFeed, LmsFeed.id == LmsFeedSource.feed_id)
            .join(ConnectorConnection, ConnectorConnection.id == LmsFeed.connection_id)
            .join(
                ConnectorDefinition,
                ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
            )
            .where(LmsFeedSource.item_id == item.id)
        )
    )
    # A canonical row can combine several sources. Deny mixed authority rather
    # than expose revoked source content through another feed's current grant.
    return bool(rows) and all(
        feed.user_id == item.user_id
        and feed.workspace_id == item.workspace_id
        and connection.user_id == item.user_id
        and connection.workspace_id == item.workspace_id
        and definition.active
        and connection.status in {"CONNECTED", "DEGRADED"}
        and "academic.calendar.read" in connection.authorized_capabilities
        and "academic.calendar.read" in connection.provider_capabilities
        and (not require_active or feed.state == "active")
        for feed, connection, definition in rows
    )


async def recompute_item(database: AsyncSession, item: LmsFeedItem, *, now: datetime) -> None:
    sources = list(
        await database.scalars(select(LmsFeedSource).where(LmsFeedSource.item_id == item.id))
    )
    if not sources:
        await database.delete(item)
        return
    present = [source for source in sources if source.present]
    if not present:
        if not item.cancelled:
            item.cancelled = True
            item.revision += 1
            item.updated_at = aware(now)
        return
    # Retain conflicts rather than inventing certainty from an arbitrary feed.
    candidates = [(source, FeedItem.model_validate(source.payload)) for source in present]
    candidates.sort(
        key=lambda pair: (
            pair[1].source_updated_at or datetime.min.replace(tzinfo=UTC),
            str(pair[0].feed_id),
        )
    )
    winner, payload = candidates[-1]
    data = payload.model_dump(mode="json")
    course_ids = {source.course_id for source in present}
    course_id = winner.course_id if len(course_ids) == 1 else None
    conflicted = len({source.payload_hash for source in present}) > 1 or len(course_ids) > 1
    cancelled = all(value.cancelled for _, value in candidates)
    if (item.payload, item.course_id, item.conflicted, item.cancelled) != (
        data,
        course_id,
        conflicted,
        cancelled,
    ):
        item.payload, item.course_id, item.conflicted, item.cancelled = (
            data,
            course_id,
            conflicted,
            cancelled,
        )
        item.revision += 1
        item.updated_at = aware(now)


async def apply_snapshot(
    database: AsyncSession,
    feed: LmsFeed,
    parsed: ParsedFeed,
    *,
    now: datetime,
) -> SyncResult:
    """Owner lock serializes cross-feed UID dedup; caller owns the feed lock."""
    mapped = {UUID(value) for value in feed.course_matches.values() if value}
    valid_courses = set(
        await database.scalars(
            select(StudentCourse.id).where(
                StudentCourse.id.in_(mapped),
                StudentCourse.user_id == feed.user_id,
                StudentCourse.workspace_id == feed.workspace_id,
                StudentCourse.archived.is_(False),
            )
        )
    )
    # Add/drop and student resets can invalidate a previously confirmed match.
    # Drop that match and ask again; never recreate a deleted course implicitly.
    feed.course_matches = {
        hint: value
        for hint, value in feed.course_matches.items()
        if value is None or UUID(value) in valid_courses
    }
    sources = list(
        await database.scalars(select(LmsFeedSource).where(LmsFeedSource.feed_id == feed.id))
    )
    current_items = list(
        await database.scalars(
            select(LmsFeedItem).where(
                LmsFeedItem.user_id == feed.user_id,
                LmsFeedItem.workspace_id == feed.workspace_id,
                or_(
                    LmsFeedItem.id.in_([source.item_id for source in sources]),
                    LmsFeedItem.identity_key.in_([item_key(item) for item in parsed.items]),
                ),
            )
        )
    )
    by_key = {item.identity_key: item for item in current_items}
    by_id = {item.id: item for item in current_items}
    by_item = {source.item_id: source for source in sources}
    source_keys = {by_id[source.item_id].identity_key for source in sources}
    new_keys = {item_key(item) for item in parsed.items} - source_keys
    if len(sources) + len(new_keys) > 10_000:
        raise FeedError("feed_history_limit")
    touched: dict[UUID, LmsFeedItem] = {}
    created = updated = cancelled = 0
    seen: set[UUID] = set()
    for payload in parsed.items:
        identity = item_key(payload)
        item = by_key.get(identity)
        if item is None:
            item = LmsFeedItem(
                id=uuid4(),
                user_id=feed.user_id,
                workspace_id=feed.workspace_id,
                identity_key=identity,
                course_id=None,
                payload=payload.model_dump(mode="json"),
                revision=1,
                cancelled=payload.cancelled,
                conflicted=False,
                updated_at=aware(now),
            )
            database.add(item)
            await database.flush()
            by_key[identity] = item
            created += 1
        matched = feed.course_matches.get(payload.course_hint or "")
        course_id = UUID(matched) if matched else None
        source = by_item.get(item.id)
        digest = item_hash(payload)
        if source is None:
            source = LmsFeedSource(
                id=uuid4(),
                feed_id=feed.id,
                item_id=item.id,
                course_id=course_id,
                payload=payload.model_dump(mode="json"),
                payload_hash=digest,
                present=True,
            )
            database.add(source)
        else:
            if (source.payload_hash, source.course_id, source.present) != (digest, course_id, True):
                updated += 1
            source.payload = payload.model_dump(mode="json")
            source.payload_hash, source.course_id, source.present = digest, course_id, True
        seen.add(item.id)
        touched[item.id] = item
    for source in sources:
        if source.item_id not in seen:
            item = by_id.get(source.item_id)
            if source.present:
                source.present = False
                cancelled += 1
            if item:
                touched[item.id] = item
    await database.flush()
    for item in touched.values():
        await recompute_item(database, item, now=now)
    return SyncResult(state="synced", created=created, updated=updated, cancelled=cancelled)


def next_sync(parsed: ParsedFeed, now: datetime) -> datetime:
    current = aware(now)
    near = any(
        item.due_at is not None
        and not item.cancelled
        and current <= item.due_at <= current + timedelta(hours=24)
        for item in parsed.items
    )
    return current + (timedelta(minutes=30) if near else timedelta(hours=2))


async def update_course_matches(
    database: AsyncSession,
    feed: LmsFeed,
    matches: dict[str, UUID | None],
    *,
    now: datetime,
) -> None:
    sources = list(
        await database.scalars(select(LmsFeedSource).where(LmsFeedSource.feed_id == feed.id))
    )
    known = {
        payload.course_hint
        for source in sources
        if (payload := FeedItem.model_validate(source.payload)).course_hint
    }
    if set(matches) != known:
        raise FeedAccessError(422, "Confirm every course match, including unmatched courses")
    for course_id in set(matches.values()) - {None}:
        course = await database.scalar(
            select(StudentCourse).where(
                StudentCourse.id == course_id,
                StudentCourse.user_id == feed.user_id,
                StudentCourse.workspace_id == feed.workspace_id,
                StudentCourse.archived.is_(False),
            )
        )
        if course is None:
            raise FeedAccessError(422, "Choose an active course from your timetable")
    feed.course_matches = {key: str(value) if value else None for key, value in matches.items()}
    feed.revision += 1
    feed.lease_token, feed.lease_until = None, None
    for source in sources:
        payload = FeedItem.model_validate(source.payload)
        source.course_id = matches.get(payload.course_hint or "")
    await database.flush()
    for item_id in {source.item_id for source in sources}:
        item = await database.get(LmsFeedItem, item_id)
        if item:
            await recompute_item(database, item, now=now)


async def confirm_preview(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    command: ConfirmCommand,
    now: datetime,
) -> LmsFeed:
    preview = await database.get(LmsFeedPreview, command.preview_token)
    if preview is None:
        raise FeedAccessError(404, "Calendar preview not found")
    feed = await locked_feed(
        database, feed_id=preview.feed_id, user_id=user_id, workspace_id=workspace_id
    )
    await database.refresh(preview)
    await require_owner(database, user_id=user_id, workspace_id=workspace_id, active=True)
    if re.search(r"https?://", command.name, re.I):
        raise FeedAccessError(422, "Use a short calendar name rather than a private link")
    digest = hashlib.sha256(
        json.dumps(command.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if preview.confirmed_at is not None:
        if preview.request_id != command.request_id or preview.confirm_hash != digest:
            raise FeedAccessError(409, "This calendar preview was already used")
        return feed
    if (
        feed.state != "preview"
        or aware(preview.expires_at) <= aware(now)
        or command.preview_hash != preview.preview_hash
    ):
        raise FeedAccessError(409, "Preview this calendar link again before connecting")
    parsed = ParsedFeed.model_validate(preview.parsed)
    if set(command.course_matches) != set(parsed.course_hints):
        raise FeedAccessError(422, "Confirm every course match, including unmatched courses")
    for course_id in set(command.course_matches.values()) - {None}:
        course = await database.scalar(
            select(StudentCourse).where(
                StudentCourse.id == course_id,
                StudentCourse.user_id == user_id,
                StudentCourse.workspace_id == workspace_id,
                StudentCourse.archived.is_(False),
            )
        )
        if course is None:
            raise FeedAccessError(422, "Choose an active course from your timetable")
    feed.course_matches = {
        key: str(value) if value else None for key, value in command.course_matches.items()
    }
    feed.name, feed.state = command.name, "active"
    feed.last_synced_at, feed.next_sync_at = aware(now), aware(now)
    feed.last_expanded_at = aware(now)
    feed.revision += 1
    await apply_snapshot(database, feed, parsed, now=now)
    preview.confirmed_at, preview.request_id, preview.confirm_hash = (
        aware(now),
        command.request_id,
        digest,
    )
    # The normalized canonical rows now hold the data; do not keep a second copy.
    preview.parsed = {}
    await database.commit()
    return feed


async def forget_feed(
    database: AsyncSession,
    *,
    feed_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    settings: Settings,
    now: datetime,
) -> None:
    feed = await locked_feed(database, feed_id=feed_id, user_id=user_id, workspace_id=workspace_id)
    connection_id = feed.connection_id
    await erase_feed_data(
        database, connection_id=connection_id, user_id=user_id, workspace_id=workspace_id, now=now
    )
    await SecretBroker(settings).delete_for_connection(
        database, connection_id=connection_id, user_id=user_id, workspace_id=workspace_id
    )
    connection = await database.get(ConnectorConnection, connection_id)
    if connection is not None:
        await database.delete(connection)
    await database.commit()


async def erase_feed_data(
    database: AsyncSession,
    *,
    connection_id: UUID,
    user_id: UUID,
    workspace_id: UUID,
    now: datetime,
) -> None:
    """Within an already-owned connector deletion transaction; never commits."""
    feed = await database.scalar(
        select(LmsFeed)
        .where(
            LmsFeed.connection_id == connection_id,
            LmsFeed.user_id == user_id,
            LmsFeed.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if feed is None:
        return
    from navox.vox.service import invalidate_active_context

    await invalidate_active_context(database, user_id, workspace_id)
    from navox.student_recovery.service import invalidate_recovery_previews

    await invalidate_recovery_previews(database, user_id, workspace_id)
    sources = list(
        await database.scalars(select(LmsFeedSource).where(LmsFeedSource.feed_id == feed.id))
    )
    item_ids = {source.item_id for source in sources}
    await database.execute(delete(LmsFeedSource).where(LmsFeedSource.feed_id == feed.id))
    await database.execute(delete(LmsFeedPreview).where(LmsFeedPreview.feed_id == feed.id))
    await database.delete(feed)
    await database.flush()
    remaining = set(
        await database.scalars(
            select(LmsFeedSource.item_id).where(LmsFeedSource.item_id.in_(item_ids))
        )
    )
    orphaned = list(item_ids - remaining)
    if orphaned:
        from navox.student_planning.service import forget_source_tasks

        for offset in range(0, len(orphaned), 5_000):
            await forget_source_tasks(
                database,
                user_id=user_id,
                workspace_id=workspace_id,
                item_ids=orphaned[offset : offset + 5_000],
            )
    for item_id in item_ids:
        item = await database.get(LmsFeedItem, item_id)
        if item:
            await recompute_item(database, item, now=now)


async def lock_all_feeds_for_reset(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID
) -> list[UUID]:
    """Conn (sorted) → membership → User, then reject a raced new connection.

    Call before taking the student User lock. A 409 must roll back the entire
    reset request; never lock a newly discovered connection while User is held.
    """
    predicate = (
        ConnectorConnection.user_id == user_id,
        ConnectorConnection.workspace_id == workspace_id,
        ConnectorConnection.provider == "lms_feed",
    )
    connections = list(
        await database.scalars(
            select(ConnectorConnection)
            .where(*predicate)
            .order_by(ConnectorConnection.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.user_id == user_id, WorkspaceMembership.workspace_id == workspace_id
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    await require_owner(
        database, user_id=user_id, workspace_id=workspace_id, active=False, lock=True
    )
    ids = [connection.id for connection in connections]
    current = set(await database.scalars(select(ConnectorConnection.id).where(*predicate)))
    if current != set(ids):
        raise FeedAccessError(409, "A calendar connection changed. Retry the student-data reset.")
    return ids


async def erase_all_owned_feeds(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    settings: Settings,
    connection_ids: list[UUID],
    now: datetime,
) -> None:
    """Atomic reset erasure under lock_all_feeds_for_reset's locks; no commit."""
    connections = list(
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.provider == "lms_feed",
            )
            .execution_options(populate_existing=True)
        )
    )
    if {connection.id for connection in connections} != set(connection_ids):
        raise FeedAccessError(409, "A calendar connection changed. Retry the student-data reset.")
    for connection in connections:
        if (
            connection.legacy_connection_id is not None
            or connection.config.get("managed_by") != "student.lms-feeds"
        ):
            raise FeedAccessError(409, "A calendar connection needs review before resetting data")
        await erase_feed_data(
            database,
            connection_id=connection.id,
            user_id=user_id,
            workspace_id=workspace_id,
            now=now,
        )
        await SecretBroker(settings).delete_for_connection(
            database, connection_id=connection.id, user_id=user_id, workspace_id=workspace_id
        )
        await database.delete(connection)
    await database.flush()


def feed_view(feed: LmsFeed) -> dict[str, object]:
    return {
        "id": str(feed.id),
        "name": feed.name,
        "domain": feed.domain,
        "platform": feed.platform,
        "timezone": feed.timezone,
        "all_day_due_time": feed.all_day_due_time,
        "state": feed.state,
        "revision": feed.revision,
        "last_synced_at": aware(feed.last_synced_at).isoformat() if feed.last_synced_at else None,
        "next_sync_at": aware(feed.next_sync_at).isoformat() if feed.next_sync_at else None,
        "last_error": feed.last_error,
        "course_matches": feed.course_matches,
    }
