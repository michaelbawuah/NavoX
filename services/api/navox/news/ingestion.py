"""One reviewed feed per ingestion transaction, with terminal, content-free receipts."""

import hashlib
import json
from datetime import datetime, timedelta
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.news import (
    NewsClaim,
    NewsClaimEvidence,
    NewsContentRights,
    NewsIngestionReceipt,
    NewsItem,
    NewsSource,
    NewsSourceFeed,
)
from navox.news.api_feeds import fetch_api_items
from navox.news.contracts import (
    Category,
    FeedType,
    NewsError,
    NewsItemInput,
    NewsItemRead,
    SourceDefinition,
    SourceType,
    stored_utc,
)
from navox.news.feeds import fetch_feed, parse_feed
from navox.news.images import permitted_image
from navox.news.registry import (
    current_rights,
    owned_source,
    require_current_policy,
    require_definition,
)
from navox.news.rights import (
    Operation,
    policy_for,
    require_item_retention,
    require_operation,
    retention_deadline,
)


async def store_item(
    database: AsyncSession,
    source: NewsSource,
    rights: NewsContentRights,
    definition: SourceDefinition,
    item: NewsItemInput,
    *,
    now: datetime,
) -> NewsItem:
    policy = policy_for(rights, now=now)
    require_operation(Operation.METADATA, policy)
    if urlsplit(item.canonical_url).hostname not in definition.article_domains:
        raise NewsError("invalid_item")
    if item.published_at > now + timedelta(minutes=5):
        raise NewsError("invalid_item")
    # Hash only permitted data. A digest of forbidden content is not a workaround for rights.
    permitted = item.model_dump(mode="json")
    if not policy.snippet_storage_allowed:
        permitted["description"] = None
    image = permitted_image(item.image, definition, policies=(policy,))
    if image is None:
        permitted.pop("image", None)
    else:
        permitted["image"] = image.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(permitted, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    row = await database.scalar(
        select(NewsItem).where(
            NewsItem.source_id == source.id,
            NewsItem.external_id == item.external_id,
        )
    )
    if row is not None and row.content_digest == digest:
        # Repeated polls do not reset retention or create false corrections.
        row.last_observed_at = now
        return row
    if (
        row is not None
        and row.updated_at is not None
        and (item.updated_at is None or item.updated_at < stored_utc(row.updated_at))
    ):
        # An older feed response must not undo a published correction.
        return row
    values = item.model_dump()
    values["description"] = permitted["description"]
    values["image"] = permitted.get("image")
    values["categories"] = [category.value for category in item.categories]
    if row is None:
        row = NewsItem(
            workspace_id=source.workspace_id,
            user_id=source.user_id,
            source_id=source.id,
            revision=0,
        )
        database.add(row)
    else:
        # Old extracted text cannot inherit a new revision's retention deadline.
        await database.execute(delete(NewsClaim).where(NewsClaim.origin_item_id == row.id))
    for name, value in values.items():
        setattr(row, name, value)
    row.rights_profile_id = rights.id
    row.content_digest = digest
    row.retrieved_at = now
    row.last_observed_at = now
    row.expires_at = retention_deadline(now, policy)
    row.revision += 1
    await database.flush()
    return row


async def ingest_source(
    database: AsyncSession,
    client: httpx.AsyncClient,
    definitions: dict[str, SourceDefinition],
    *,
    source_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    request_id: UUID,
    now: datetime,
    settings: Settings | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
) -> NewsIngestionReceipt:
    source = await owned_source(
        database, source_id, workspace_id=workspace_id, user_id=user_id, lock=True
    )
    definition = require_definition(source, definitions)
    rights = await require_current_policy(database, source, now=now)
    previous = await database.scalar(
        select(NewsIngestionReceipt).where(
            NewsIngestionReceipt.source_id == source.id,
            NewsIngestionReceipt.request_id == request_id,
        )
    )
    if previous is not None:
        return previous
    feed = await database.scalar(
        select(NewsSourceFeed).where(NewsSourceFeed.source_id == source.id)
    )
    if feed is None or feed.endpoint_reference != definition.endpoint:
        raise NewsError("source_changed")
    if feed.last_started_at is not None and now < (
        stored_utc(feed.last_started_at) + timedelta(seconds=feed.poll_interval_seconds)
    ):
        raise NewsError("refresh_too_soon")
    feed.last_started_at = now
    receipt = NewsIngestionReceipt(
        source_id=source.id,
        request_id=request_id,
        status="RUNNING",
        stored_count=0,
        rejected_count=0,
    )
    database.add(receipt)
    await database.flush()
    try:
        started = monotonic()
        if definition.feed_type == FeedType.API:
            if settings is None:
                raise NewsError("invalid_source")
            items, rejected = await fetch_api_items(
                database,
                settings,
                definition,
                workspace_id=workspace_id,
                user_id=user_id,
                transport=api_transport,
            )
        else:
            data = await fetch_feed(client, definition)
            items, rejected = parse_feed(data, definition)
        completed_at = now + timedelta(seconds=monotonic() - started)
        # A permission may expire while the network request is in flight.
        policy_for(rights, now=completed_at)
        receipt.rejected_count = rejected
        for item in items:
            try:
                await store_item(database, source, rights, definition, item, now=completed_at)
                receipt.stored_count += 1
            except NewsError:
                receipt.rejected_count += 1
        receipt.status = "COMPLETED"
        feed.health_status = "healthy" if not receipt.rejected_count else "partial"
        feed.last_success_at = now
        feed.last_error_code = None
    except NewsError as error:
        receipt.status = "FAILED"
        receipt.error_code = error.code
        feed.health_status = "unavailable"
        feed.last_error_at = now
        feed.last_error_code = error.code
    await database.flush()
    return receipt


async def item_view(
    database: AsyncSession,
    item: NewsItem,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> NewsItemRead:
    source = await owned_source(
        database, item.source_id, workspace_id=item.workspace_id, user_id=item.user_id
    )
    definition = require_definition(source, definitions)
    current = await current_rights(database, source)
    original = await database.get(NewsContentRights, item.rights_profile_id, populate_existing=True)
    if original is None or original.source_id != source.id:
        raise NewsError("rights_denied")
    policies = (policy_for(original, now=now), policy_for(current, now=now))
    require_operation(Operation.METADATA, *policies)
    require_item_retention(item, *policies, now=now)
    return NewsItemRead(
        id=item.id,
        source_id=source.id,
        source_name=source.name,
        source_type=SourceType(source.source_type),
        rights_profile_id=item.rights_profile_id,
        external_id=item.external_id,
        headline=item.headline,
        canonical_url=item.canonical_url,
        author=item.author,
        published_at=stored_utc(item.published_at),
        updated_at=stored_utc(item.updated_at) if item.updated_at else None,
        event_started_at=stored_utc(item.event_started_at) if item.event_started_at else None,
        event_ended_at=stored_utc(item.event_ended_at) if item.event_ended_at else None,
        description=item.description if all(p.snippet_storage_allowed for p in policies) else None,
        image=permitted_image(item.image, definition, policies=policies),
        categories=tuple(Category(value) for value in item.categories),
        language=item.language,
        region=item.region,
        retrieved_at=stored_utc(item.retrieved_at),
        last_observed_at=stored_utc(item.last_observed_at),
        expires_at=stored_utc(item.expires_at),
        revision=item.revision,
    )


async def visible_items(
    database: AsyncSession,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    limit: int = 100,
) -> list[NewsItemRead]:
    rows = await database.scalars(
        select(NewsItem)
        .where(
            NewsItem.workspace_id == workspace_id,
            NewsItem.user_id == user_id,
            NewsItem.expires_at > now,
        )
        .order_by(NewsItem.published_at.desc(), NewsItem.id)
        .limit(min(limit, 200))
    )
    result = []
    for row in rows:
        try:
            result.append(await item_view(database, row, definitions, now=now))
        except NewsError:
            continue
    return result


async def purge_unavailable(
    database: AsyncSession,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    source_id: UUID | None = None,
    after: UUID | None = None,
    batch_size: int = 200,
) -> int:
    """Also runs while serving is disabled: revocation/retention cannot depend on a UI flag."""
    query = select(NewsItem).where(
        NewsItem.workspace_id == workspace_id, NewsItem.user_id == user_id
    )
    if source_id is not None:
        query = query.where(NewsItem.source_id == source_id)
    purged = 0
    while True:
        page = query.where(NewsItem.id > after) if after is not None else query
        rows = list(await database.scalars(page.order_by(NewsItem.id).limit(batch_size)))
        for row in rows:
            try:
                view = await item_view(database, row, definitions, now=now)
                # Narrowed permission removes previously stored snippets as well.
                if view.description is None:
                    row.description = None
                if view.image is None:
                    row.image = None
                original = await database.get(NewsContentRights, row.rights_profile_id)
                source = await database.get(NewsSource, row.source_id)
                if original is None or source is None:
                    raise NewsError("rights_denied")
                current = await current_rights(database, source)
                policies = (policy_for(original, now=now), policy_for(current, now=now))
                if (
                    not all(p.summary_generation_allowed for p in policies)
                    or view.description is None
                ):
                    await database.execute(
                        delete(NewsClaim).where(NewsClaim.origin_item_id == row.id)
                    )
                    await database.execute(
                        delete(NewsClaimEvidence).where(NewsClaimEvidence.news_item_id == row.id)
                    )
            except NewsError:
                await database.delete(row)
                purged += 1
        await database.flush()
        if len(rows) < batch_size:
            return purged
        after = rows[-1].id
