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
from navox.db.news import NewsPreference, NewsStory, NewsStoryPreference, NewsStoryVersion
from navox.db.session import get_session_factory
from navox.news.clustering import search_terms
from navox.news.contracts import Category, Contract, NewsError, NewsItemRead, stored_utc
from navox.news.evidence import ClaimRead, claim_views
from navox.news.registry import catalog
from navox.news.research import ChangesRead, CoverageRead, TimelineRead, changes, coverage, timeline
from navox.news.stories import StoryRead, StoryUpdate, owned_story, story_view
from navox.news.synthesis import StorySummary, summary_view

router = APIRouter(prefix="/news", tags=["news"])


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
        for story in rows:
            pref = await database.get(NewsStoryPreference, story.id)
            if (pref and pref.dismissed) or (mode == "saved" and not (pref and pref.saved)):
                continue
            try:
                results.append(await story_view(database, story, definitions, now=now))
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
        # Baseline lists use publication/update recency; calibrated importance/trend ranking
        # is introduced only with its evaluation, not an invented certainty score.
        return results[:50]
    except NewsError as error:
        raise news_failure(error) from None


@router.get("")
@router.get("/top")
async def top(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[StoryRead]:
    return await feed("top", account, database, settings)


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
    await owned_story(
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
