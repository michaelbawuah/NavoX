"""Owned story projections with live permission checks and content-free change history."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import (
    NewsClaim,
    NewsItem,
    NewsStory,
    NewsStoryItem,
    NewsStoryPreference,
    NewsStoryVersion,
)
from navox.news.clustering import duplicate_keys
from navox.news.contracts import (
    Category,
    Contract,
    NewsError,
    NewsItemRead,
    SourceDefinition,
    Verification,
    stored_utc,
)
from navox.news.ingestion import item_view


class StoryRead(Contract):
    id: UUID
    headline: str
    description: str | None
    category: Category
    verification_status: Verification
    lifecycle_status: str
    source_count: int
    published_at: datetime
    event_started_at: datetime | None
    last_updated_at: datetime
    retrieved_at: datetime
    version: int
    sources: tuple[NewsItemRead, ...]
    saved: bool
    followed: bool
    evidence_pending: bool


class StoryUpdate(Contract):
    version: int
    change_kind: str
    generated_at: datetime


async def owned_story(
    database: AsyncSession, story_id: UUID, *, workspace_id: UUID, user_id: UUID, lock: bool = False
) -> NewsStory:
    query = select(NewsStory).where(
        NewsStory.id == story_id,
        NewsStory.workspace_id == workspace_id,
        NewsStory.user_id == user_id,
        NewsStory.suppressed.is_(False),
    )
    if lock:
        query = query.with_for_update()
    story = await database.scalar(query.execution_options(populate_existing=True))
    if story is None:
        raise NewsError("story_unavailable")
    return story


async def record_version(
    database: AsyncSession, story: NewsStory, kind: str, *, now: datetime
) -> None:
    items = list(
        await database.scalars(select(NewsStoryItem).where(NewsStoryItem.cluster_id == story.id))
    )
    claims = list(await database.scalars(select(NewsClaim).where(NewsClaim.cluster_id == story.id)))
    database.add(
        NewsStoryVersion(
            cluster_id=story.id,
            version=story.version,
            change_kind=kind,
            source_snapshot={str(item.news_item_id): item.item_revision for item in items},
            claim_states={str(claim.id): claim.verification_status for claim in claims},
            generated_at=now,
        )
    )
    story.last_updated_at = now
    await database.flush()


async def index_item(
    database: AsyncSession,
    item: NewsItem,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> NewsStory:
    view = await item_view(database, item, definitions, now=now)
    url_key, copy_key = duplicate_keys(view)
    member = await database.get(NewsStoryItem, item.id)
    story: NewsStory | None
    if member is not None:
        story = await owned_story(
            database,
            member.cluster_id,
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            lock=True,
        )
        if member.item_revision != item.revision:
            member.item_revision, member.url_digest, member.copy_digest = (
                item.revision,
                url_key,
                copy_key,
            )
            claims = await database.scalars(
                select(NewsClaim).where(NewsClaim.cluster_id == story.id)
            )
            for claim in claims:
                # Recompute before any next publication. Historical states remain in versions.
                claim.verification_status = Verification.UNCONFIRMED
                claim.last_evaluated_at = None
            story.version += 1
            story.lifecycle_status = "DEVELOPING"
            await record_version(database, story, "SOURCE_UPDATED", now=now)
        return story
    candidates = (
        select(NewsStoryItem)
        .join(NewsItem, NewsItem.id == NewsStoryItem.news_item_id)
        .where(
            NewsStoryItem.workspace_id == item.workspace_id,
            NewsStoryItem.user_id == item.user_id,
            NewsItem.language == item.language,
            or_(
                NewsStoryItem.url_digest == url_key,
                (NewsStoryItem.copy_digest == copy_key)
                & (NewsItem.published_at >= view.published_at - timedelta(days=3))
                & (NewsItem.published_at <= view.published_at + timedelta(days=3))
                if copy_key
                else false(),
            ),
        )
        .order_by(NewsStoryItem.joined_at, NewsStoryItem.news_item_id)
        .limit(100)
    )
    story = None
    for match in await database.scalars(candidates):
        candidate = await database.get(NewsItem, match.news_item_id)
        if candidate is None or candidate.revision != match.item_revision:
            continue
        try:
            existing = await item_view(database, candidate, definitions, now=now)
            if (
                view.event_started_at
                and existing.event_started_at
                and view.event_started_at != existing.event_started_at
            ):
                continue
            story = await owned_story(
                database,
                match.cluster_id,
                workspace_id=item.workspace_id,
                user_id=item.user_id,
                lock=True,
            )
            break
        except NewsError:
            continue
    if story is None:
        story = NewsStory(
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            anchor_item_id=item.id,
            primary_category=(view.categories or (Category.WORLD,))[0],
            region=item.region,
            language=item.language,
            started_at=item.event_started_at,
            last_updated_at=now,
            version=1,
            lifecycle_status="DISCOVERED",
        )
        database.add(story)
        await database.flush()
        decision, change = "CREATE_NEW", "DISCOVERED"
    else:
        story.version += 1
        decision, change = "EXACT_DUPLICATE", "SOURCE_ADDED"
    database.add(
        NewsStoryItem(
            news_item_id=item.id,
            cluster_id=story.id,
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            item_revision=item.revision,
            url_digest=url_key,
            copy_digest=copy_key,
            decision=decision,
            joined_at=now,
        )
    )
    await database.flush()
    await record_version(database, story, change, now=now)
    return story


async def index_source(
    database: AsyncSession,
    source_id: UUID,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> int:
    rows = await database.scalars(
        select(NewsItem)
        .where(
            NewsItem.source_id == source_id,
            NewsItem.workspace_id == workspace_id,
            NewsItem.user_id == user_id,
            NewsItem.expires_at > now,
        )
        .order_by(NewsItem.published_at.desc())
        .limit(200)
    )
    count = 0
    for row in rows:
        try:
            await index_item(database, row, definitions, now=now)
            count += 1
        except NewsError:
            continue
    return count


async def story_items(
    database: AsyncSession,
    story: NewsStory,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> list[NewsItemRead]:
    items = await database.scalars(
        select(NewsItem)
        .join(NewsStoryItem, NewsStoryItem.news_item_id == NewsItem.id)
        .where(
            NewsStoryItem.cluster_id == story.id,
            NewsItem.workspace_id == story.workspace_id,
            NewsItem.user_id == story.user_id,
        )
        .order_by(NewsItem.published_at.desc(), NewsItem.id)
        .limit(100)
    )
    result = []
    for row in items:
        try:
            result.append(await item_view(database, row, definitions, now=now))
        except NewsError:
            continue
    return result


async def story_view(
    database: AsyncSession,
    story: NewsStory,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> StoryRead:
    from navox.news.evidence import claim_views

    items = await story_items(database, story, definitions, now=now)
    anchor = next((item for item in items if item.id == story.anchor_item_id), None)
    if anchor is None or story.suppressed:
        raise NewsError("story_unavailable")
    claims = await claim_views(database, story, definitions, now=now)
    states = {claim.status for claim in claims}
    status = Verification.UNCONFIRMED
    for severe in (Verification.RETRACTED, Verification.CONTRADICTED, Verification.DISPUTED):
        if severe in states:
            status = severe
            break
    else:
        if (
            states
            and states <= {Verification.VERIFIED, Verification.CORROBORATED}
            and any(claim.text == anchor.headline for claim in claims)
        ):
            status = (
                Verification.CORROBORATED
                if Verification.CORROBORATED in states
                else Verification.VERIFIED
            )
        elif states == {Verification.ATTRIBUTED}:
            status = Verification.ATTRIBUTED
        elif story.lifecycle_status == "DEVELOPING":
            status = Verification.DEVELOPING
    preference = await database.get(NewsStoryPreference, story.id)
    return StoryRead(
        id=story.id,
        headline=anchor.headline,
        description=anchor.description,
        category=Category(story.primary_category),
        verification_status=status,
        lifecycle_status=story.lifecycle_status,
        source_count=len({item.source_id for item in items}),
        published_at=anchor.published_at,
        event_started_at=anchor.event_started_at,
        last_updated_at=stored_utc(story.last_updated_at),
        retrieved_at=max(item.last_observed_at for item in items),
        version=story.version,
        sources=tuple(items),
        saved=bool(preference and preference.saved),
        followed=bool(preference and preference.followed),
        evidence_pending=not claims,
    )
