"""Read-only news intelligence plus explicit, private reading preferences."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.news import news_failure, require_feed
from navox.db.news import (
    NewsClaim,
    NewsItem,
    NewsPreference,
    NewsStory,
    NewsStoryPreference,
    NewsStoryVersion,
)
from navox.db.session import get_session_factory
from navox.news.clustering import search_terms
from navox.news.contracts import Category, Contract, NewsError, NewsItemRead, stored_utc
from navox.news.evidence import ClaimRead, claim_views, evaluate_claim
from navox.news.importance import current_importance
from navox.news.ranking import observed_trend_signals
from navox.news.registry import catalog
from navox.news.research import ChangesRead, CoverageRead, TimelineRead, changes, coverage, timeline
from navox.news.stories import StoryRead, StoryUpdate, owned_story, story_view
from navox.news.synthesis import StorySummary, summary_view

router = APIRouter(prefix="/news", tags=["news"])

RELATED_STORY_LIMIT = 5


class Preferences(Contract):
    categories: tuple[Category, ...] = Field(default=(), max_length=5)
    topics: tuple[str, ...] = Field(default=(), max_length=30)
    entities: tuple[str, ...] = Field(default=(), max_length=30)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    region: str = Field(default="world", min_length=2, max_length=64)
    reading_history_enabled: bool = Field(default=False, strict=True)

    @field_validator("topics", "entities")
    @classmethod
    def bounded_labels(cls, labels: tuple[str, ...]) -> tuple[str, ...]:
        result = tuple(dict.fromkeys(label.strip() for label in labels))
        if any(
            not label or len(label) > 100 or any(ord(c) < 32 for c in label) for label in result
        ):
            raise ValueError("Choose short topic or entity names")
        return result


class PreferencesPatch(Contract):
    categories: tuple[Category, ...] | None = Field(default=None, max_length=5)
    topics: tuple[str, ...] | None = Field(default=None, max_length=30)
    entities: tuple[str, ...] | None = Field(default=None, max_length=30)
    language: str | None = Field(default=None, max_length=32)
    region: str | None = Field(default=None, max_length=64)
    reading_history_enabled: bool | None = Field(default=None, strict=True)


class Toggle(Contract):
    enabled: bool = Field(strict=True)


async def read_preferences(
    account: CurrentAccountDependency, database: DatabaseSession
) -> Preferences:
    row = await database.get(NewsPreference, (account.workspace.id, account.user.id))
    if row is None:
        return Preferences()
    return Preferences.model_validate({key: getattr(row, key) for key in Preferences.model_fields})


@router.get("/preferences")
async def preferences(account: CurrentAccountDependency, database: DatabaseSession) -> Preferences:
    return await read_preferences(account, database)


@router.patch("/preferences")
async def update_preferences(
    command: PreferencesPatch,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Preferences:
    require_origin(request, settings.web_origin)
    current = await read_preferences(account, database)
    try:
        merged = Preferences.model_validate(
            current.model_dump() | command.model_dump(exclude_none=True)
        )
    except ValueError:
        raise HTTPException(422, "Choose valid news preferences.") from None
    row = await database.get(NewsPreference, (account.workspace.id, account.user.id))
    if row is None:
        row = NewsPreference(workspace_id=account.workspace.id, user_id=account.user.id)
        database.add(row)
    for key, value in merged.model_dump(mode="json").items():
        setattr(row, key, value)
    await database.commit()
    return merged


async def feed(
    mode: Literal["top", "for-you", "trending", "saved"],
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    category: Category | None = None,
) -> list[StoryRead]:
    require_feed(settings)
    try:
        definitions, now = catalog(settings), datetime.now(UTC)
        query = select(NewsStory).where(
            NewsStory.workspace_id == account.workspace.id,
            NewsStory.user_id == account.user.id,
            NewsStory.suppressed.is_(False),
        )
        if category is not None:
            query = query.where(NewsStory.primary_category == category)
        rows = await database.scalars(
            query.order_by(NewsStory.last_updated_at.desc(), NewsStory.id).limit(100)
        )
        results = []
        trend_keys = {}
        importance_scores = {}
        for story in rows:
            pref = await database.get(NewsStoryPreference, story.id)
            if (pref and pref.dismissed) or (mode == "saved" and not (pref and pref.saved)):
                continue
            try:
                view = await story_view(database, story, definitions, now=now)
                results.append(view)
                if mode == "top":
                    importance_scores[view.id] = await current_importance(
                        database, story, definitions, now=now
                    )
                if mode == "trending":
                    signals = await observed_trend_signals(database, story, view.sources, now=now)
                    trend_keys[view.id] = signals.sort_key
            except NewsError:
                continue
        if mode == "for-you":
            interests = await read_preferences(account, database)
            terms = set().union(
                *(search_terms(value) for value in (*interests.topics, *interests.entities))
            )
            results.sort(
                key=lambda story: (
                    story.followed,
                    story.category in interests.categories,
                    len(search_terms(story.headline) & terms),
                    story.last_updated_at,
                ),
                reverse=True,
            )
        elif mode == "trending":
            results.sort(key=lambda story: trend_keys[story.id])
        elif (
            mode == "top"
            and results
            and all(importance_scores[story.id] is not None for story in results)
        ):
            results.sort(
                key=lambda story: (
                    -(importance_scores[story.id] or 0),
                    -story.last_updated_at.timestamp(),
                    story.id.hex,
                )
            )
            results = [
                story.model_copy(update={"ranking_basis": "REVIEWED_IMPORTANCE"})
                for story in results
            ]
        # Missing current reviewed inputs keep the whole candidate set in recency order.
        # Trending uses only observed activity and source independence, never a truth score.
        return results[:50]
    except NewsError as error:
        raise news_failure(error) from None


@router.get("")
@router.get("/top")
async def top(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[StoryRead]:
    return await feed("top", account, database, settings)


@router.get("/trending")
async def trending(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[StoryRead]:
    return await feed("trending", account, database, settings)


@router.get("/for-you")
async def for_you(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[StoryRead]:
    return await feed("for-you", account, database, settings)


@router.get("/saved")
async def saved(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[StoryRead]:
    return await feed("saved", account, database, settings)


@router.get("/categories/{category}")
async def category_news(
    category: Category,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[StoryRead]:
    return await feed("top", account, database, settings, category)


@router.get("/stories/{story_id}")
async def story_detail(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> StoryRead:
    require_feed(settings)
    try:
        story = await owned_story(
            database, story_id, workspace_id=account.workspace.id, user_id=account.user.id
        )
        return await story_view(database, story, catalog(settings), now=datetime.now(UTC))
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/stories/{story_id}/sources")
async def story_sources(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> tuple[NewsItemRead, ...]:
    return (await story_detail(story_id, account, database, settings)).sources


@router.get("/stories/{story_id}/claims")
async def story_claims(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[ClaimRead]:
    await story_detail(story_id, account, database, settings)
    story = await owned_story(
        database, story_id, workspace_id=account.workspace.id, user_id=account.user.id
    )
    return await claim_views(database, story, catalog(settings), now=datetime.now(UTC))


async def related_story_views(
    story: NewsStory,
    database: DatabaseSession,
    settings: SettingsDependency,
    now: datetime,
) -> list[StoryRead]:
    """Other stories this owner may read that share a currently admitted claim digest.

    Matching uses stored claim digests only: no semantic similarity, no embeddings and
    no generated text. Ranking is shared-claim count, then publication time, then id.
    """
    definitions = catalog(settings)
    current_ids = [view.id for view in await claim_views(database, story, definitions, now=now)]
    if not current_ids:
        return []
    digests = set(
        await database.scalars(
            select(NewsClaim.text_digest).where(
                NewsClaim.cluster_id == story.id,
                NewsClaim.workspace_id == story.workspace_id,
                NewsClaim.user_id == story.user_id,
                NewsClaim.id.in_(current_ids),
            )
        )
    )
    if not digests:
        return []
    shared: dict[UUID, set[str]] = {}
    claims = await database.scalars(
        select(NewsClaim)
        .where(
            NewsClaim.workspace_id == story.workspace_id,
            NewsClaim.user_id == story.user_id,
            NewsClaim.cluster_id != story.id,
            NewsClaim.text_digest.in_(sorted(digests)),
        )
        .order_by(NewsClaim.cluster_id, NewsClaim.id)
        .limit(500)
    )
    for claim in claims:
        try:
            await evaluate_claim(database, claim, definitions, now=now)
        except NewsError:
            # A claim whose origin revision changed, or whose rights were withdrawn,
            # no longer describes something this owner may currently read.
            continue
        shared.setdefault(claim.cluster_id, set()).add(claim.text_digest)
    if not shared:
        return []
    candidates = list(
        await database.scalars(
            select(NewsStory).where(
                NewsStory.id.in_(sorted(shared)),
                NewsStory.workspace_id == story.workspace_id,
                NewsStory.user_id == story.user_id,
                NewsStory.suppressed.is_(False),
            )
        )
    )
    anchor_times: dict[UUID, datetime] = {}
    anchors = await database.execute(
        select(NewsItem.id, NewsItem.published_at).where(
            NewsItem.id.in_([candidate.anchor_item_id for candidate in candidates])
        )
    )
    for item_id, published_at in anchors:
        anchor_times[item_id] = stored_utc(published_at)
    ordered = sorted(
        (candidate for candidate in candidates if candidate.anchor_item_id in anchor_times),
        key=lambda candidate: (
            -len(shared[candidate.id]),
            -anchor_times[candidate.anchor_item_id].timestamp(),
            candidate.id.hex,
        ),
    )
    results: list[StoryRead] = []
    for candidate in ordered:
        if len(results) >= RELATED_STORY_LIMIT:
            break
        try:
            results.append(await story_view(database, candidate, definitions, now=now))
        except NewsError:
            continue
    return results


@router.get("/stories/{story_id}/related")
async def story_related(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[StoryRead]:
    require_feed(settings)
    try:
        story = await owned_story(
            database, story_id, workspace_id=account.workspace.id, user_id=account.user.id
        )
        return await related_story_views(story, database, settings, datetime.now(UTC))
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/stories/{story_id}/updates")
async def story_updates(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    since_version: int = Query(default=0, ge=0),
) -> list[StoryUpdate]:
    await story_detail(story_id, account, database, settings)
    rows = await database.scalars(
        select(NewsStoryVersion)
        .where(NewsStoryVersion.cluster_id == story_id, NewsStoryVersion.version > since_version)
        .order_by(NewsStoryVersion.version.desc())
        .limit(50)
    )
    return [
        StoryUpdate(
            version=row.version,
            change_kind=row.change_kind,
            generated_at=stored_utc(row.generated_at),
        )
        for row in rows
    ]


@router.post("/stories/{story_id}/{operation}")
async def story_preference(
    story_id: UUID,
    operation: Literal["save", "dismiss", "follow"],
    command: Toggle,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, bool]:
    require_origin(request, settings.web_origin)
    await story_detail(story_id, account, database, settings)
    story = await owned_story(
        database, story_id, workspace_id=account.workspace.id, user_id=account.user.id, lock=True
    )
    row = await database.get(NewsStoryPreference, story_id)
    if row is None:
        row = NewsStoryPreference(
            cluster_id=story_id,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            saved=False,
            dismissed=False,
            followed=False,
        )
        database.add(row)
    field = {"save": "saved", "dismiss": "dismissed", "follow": "followed"}[operation]
    if operation == "follow":
        if command.enabled and not row.followed:
            row.last_read_version = story.version
        elif not command.enabled:
            row.last_read_version = None
    setattr(row, field, command.enabled)
    await database.commit()
    return {"saved": row.saved, "dismissed": row.dismissed, "followed": row.followed}


@router.get("/stories/{story_id}/summary")
async def story_summary(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> StorySummary:
    require_feed(settings)
    try:
        return await summary_view(
            database,
            get_session_factory(),
            settings,
            story_id,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
        )
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/stories/{story_id}/timeline")
async def story_timeline(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> "TimelineRead":
    require_feed(settings)
    if not settings.news_deep_research_enabled:
        raise HTTPException(503, "Story timelines are not available yet.")
    try:
        return await timeline(
            database,
            story_id,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
            limit=limit,
        )
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/stories/{story_id}/coverage")
async def story_coverage(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> "CoverageRead":
    require_feed(settings)
    if not settings.news_coverage_comparison_enabled:
        raise HTTPException(503, "Source comparison is not available yet.")
    try:
        return await coverage(
            database,
            story_id,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
            limit=limit,
        )
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/stories/{story_id}/changes")
async def story_changes(
    story_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    since_version: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> "ChangesRead":
    require_feed(settings)
    try:
        return await changes(
            database,
            story_id,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
            since_version=since_version,
            limit=limit,
        )
    except NewsError as error:
        raise news_failure(error) from None
    except ValueError:
        raise HTTPException(422, "Choose a valid story version and limit.") from None
