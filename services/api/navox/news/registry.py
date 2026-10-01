"""Operator-defined catalog and owner-scoped activation. Feed content cannot alter it."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.news import NewsContentRights, NewsItem, NewsSource, NewsSourceFeed
from navox.news.contracts import FeedType, NewsError, SourceDefinition
from navox.news.rights import Operation, policy_digest, policy_for, require_operation


def catalog(settings: Settings) -> dict[str, SourceDefinition]:
    try:
        definitions = [
            SourceDefinition.model_validate(item) for item in settings.news_source_catalog
        ]
    except ValueError:
        raise NewsError("invalid_source") from None
    result = {definition.key: definition for definition in definitions}
    if len(result) != len(definitions):
        raise NewsError("invalid_source")
    return result


async def owned_source(
    database: AsyncSession,
    source_id: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
    lock: bool = False,
) -> NewsSource:
    query = (
        select(NewsSource)
        .where(
            NewsSource.id == source_id,
            NewsSource.workspace_id == workspace_id,
            NewsSource.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    source = await database.scalar(query)
    if source is None:
        raise NewsError("source_unavailable")
    return source


async def current_rights(database: AsyncSession, source: NewsSource) -> NewsContentRights:
    rights = await database.scalar(
        select(NewsContentRights)
        .where(
            NewsContentRights.source_id == source.id,
            NewsContentRights.version == source.rights_version,
        )
        .execution_options(populate_existing=True)
    )
    if rights is None:
        raise NewsError("rights_denied")
    return rights


async def activate_source(
    database: AsyncSession,
    definition: SourceDefinition,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> NewsSource:
    """The caller obtains definition from trusted deployment configuration, never request data."""
    if definition.feed_type not in {FeedType.RSS, FeedType.ATOM}:
        raise NewsError("invalid_source")
    require_operation(Operation.METADATA, definition.rights)
    if not definition.rights.reviewed_at <= now < definition.rights.expires_at:
        raise NewsError("rights_expired")
    source = await database.scalar(
        select(NewsSource)
        .where(
            NewsSource.workspace_id == workspace_id,
            NewsSource.user_id == user_id,
            NewsSource.source_key == definition.key,
        )
        .with_for_update()
    )
    if source is None:
        source = NewsSource(
            workspace_id=workspace_id, user_id=user_id, source_key=definition.key, rights_version=0
        )
        database.add(source)
    changed = source.config_digest != definition.fingerprint
    if source.id is not None and not changed:
        previous = await current_rights(database, source)
        changed = previous.revoked_at is not None
    for field in (
        "name",
        "domain",
        "source_type",
        "region",
        "language",
        "identity_verified",
        "independence_group",
    ):
        setattr(source, field, getattr(definition, field))
    source.config_digest = definition.fingerprint
    source.status = "active"
    if changed:
        source.rights_version += 1
    await database.flush()
    if changed:
        database.add(
            NewsContentRights(
                source_id=source.id,
                version=source.rights_version,
                policy=definition.rights.model_dump(mode="json"),
                policy_digest=policy_digest(definition.rights),
            )
        )
    feed = await database.scalar(
        select(NewsSourceFeed).where(NewsSourceFeed.source_id == source.id)
    )
    if feed is None:
        feed = NewsSourceFeed(source_id=source.id)
        database.add(feed)
    feed.feed_type = definition.feed_type
    feed.endpoint_reference = definition.endpoint
    feed.category = definition.category
    feed.poll_interval_seconds = definition.poll_interval_seconds
    await database.flush()
    return source


def require_definition(
    source: NewsSource, definitions: dict[str, SourceDefinition]
) -> SourceDefinition:
    definition = definitions.get(source.source_key)
    if source.status != "active" or definition is None:
        raise NewsError("source_unavailable")
    if definition.fingerprint != source.config_digest:
        raise NewsError("source_changed")
    return definition


async def revoke_rights(database: AsyncSession, source: NewsSource, *, now: datetime) -> None:
    """Immediate content deletion; dependent derived rows must cascade or be invalidated."""
    rows = await database.scalars(
        select(NewsContentRights).where(NewsContentRights.source_id == source.id)
    )
    for row in rows:
        row.revoked_at = now
    source.status = "disabled"
    await database.execute(delete(NewsItem).where(NewsItem.source_id == source.id))


async def require_current_policy(
    database: AsyncSession,
    source: NewsSource,
    *,
    now: datetime,
) -> NewsContentRights:
    row = await current_rights(database, source)
    require_operation(Operation.METADATA, policy_for(row, now=now))
    return row
