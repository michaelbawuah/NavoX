"""Bounded feed work with owner, pause, feature and permission checks at execution."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, exists, or_, select, update
from temporalio import activity

from navox.core.settings import get_settings
from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsConversation, NewsConversationTurn, NewsIntelligenceRun, NewsSource
from navox.db.session import get_session_factory
from navox.news.contracts import NewsError
from navox.news.ingestion import ingest_source, purge_unavailable
from navox.news.jobs import NewsConversationWork, NewsSourcePage, NewsSourceWork, NewsWorkResult
from navox.news.registry import catalog, owned_source
from navox.news.stories import index_source

PAGE_SIZE = 100


@activity.defn
async def news_sources_activity(after: str | None) -> NewsSourcePage:
    # Include disabled sources and paused owners: retention must still run for them.
    async with get_session_factory()() as database:
        if after is None:
            now = datetime.now(UTC)
            await database.execute(
                delete(NewsConversation).where(NewsConversation.expires_at <= now)
            )
            await database.execute(
                delete(NewsConversationTurn).where(NewsConversationTurn.expires_at <= now)
            )
            await database.execute(
                update(NewsConversationTurn)
                .where(
                    NewsConversationTurn.status == "PROCESSING",
                    NewsConversationTurn.created_at < now - timedelta(minutes=5),
                )
                .values(status="UNAVAILABLE", failure_code="news_unavailable")
            )
            await database.execute(
                update(NewsIntelligenceRun)
                .where(
                    NewsIntelligenceRun.status == "PROCESSING",
                    NewsIntelligenceRun.created_at < now - timedelta(minutes=10),
                )
                .values(status="UNAVAILABLE", failure_code="ai_unavailable")
            )
            await database.execute(
                update(NewsIntelligenceRun)
                .where(
                    NewsIntelligenceRun.expires_at <= now,
                )
                .values(selection=None, claim_snapshot={})
            )
            await database.commit()
        query = select(NewsSource).order_by(NewsSource.id).limit(PAGE_SIZE)
        if after is not None:
            query = query.where(NewsSource.id > UUID(after))
        sources = list(await database.scalars(query))
        return NewsSourcePage(
            sources=[
                NewsSourceWork(str(row.id), str(row.workspace_id), str(row.user_id), str(uuid4()))
                for row in sources
            ],
            after=str(sources[-1].id) if len(sources) == PAGE_SIZE else None,
        )


@activity.defn
async def news_conversation_activity(payload: NewsConversationWork) -> None:
    from navox.news.conversations import finish_question

    await finish_question(
        get_session_factory(),
        get_settings(),
        UUID(payload.turn_id),
        workspace_id=UUID(payload.workspace_id),
        user_id=UUID(payload.user_id),
    )


@activity.defn
async def ingest_news_source_activity(payload: NewsSourceWork) -> NewsWorkResult:
    settings = get_settings()
    definitions = catalog(settings)
    async with get_session_factory()() as database:
        try:
            source = await owned_source(
                database,
                UUID(payload.source_id),
                workspace_id=UUID(payload.workspace_id),
                user_id=UUID(payload.user_id),
                lock=True,
            )
        except NewsError:
            return NewsWorkResult("unavailable")
        now = datetime.now(UTC)
        purged = await purge_unavailable(
            database,
            definitions,
            workspace_id=source.workspace_id,
            user_id=source.user_id,
            source_id=source.id,
            now=now,
        )
        user = await database.get(User, source.user_id, populate_existing=True)
        member = await database.get(WorkspaceMembership, (source.workspace_id, source.user_id))
        if not settings.news_feed_enabled or user is None or member is None or user.agent_paused:
            await database.commit()
            return NewsWorkResult("paused", purged_count=purged)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                receipt = await ingest_source(
                    database,
                    client,
                    definitions,
                    source_id=source.id,
                    workspace_id=source.workspace_id,
                    user_id=source.user_id,
                    request_id=UUID(payload.request_id),
                    now=now,
                )
            result = NewsWorkResult(receipt.status, receipt.stored_count, purged)
            if receipt.status == "COMPLETED":
                await index_source(
                    database,
                    source.id,
                    definitions,
                    workspace_id=source.workspace_id,
                    user_id=source.user_id,
                    now=datetime.now(UTC),
                )
        except NewsError as error:
            result = NewsWorkResult(error.code, purged_count=purged)
        await database.commit()
        return result


@activity.defn
async def news_intelligence_activity(payload: NewsSourceWork) -> int:
    """Spend nothing unless separately enabled; source work contains only identifiers."""
    from navox.db.news import NewsStory, NewsStoryItem
    from navox.news.intelligence import run_story_intelligence

    settings, factory = get_settings(), get_session_factory()
    if not settings.news_feed_enabled or not settings.news_intelligence_enabled:
        return 0
    workspace_id, user_id, source_id = (
        UUID(payload.workspace_id),
        UUID(payload.user_id),
        UUID(payload.source_id),
    )
    from navox.db.news import NewsItem

    async with factory() as database:
        identifiers = list(
            await database.scalars(
                select(NewsStoryItem.cluster_id)
                .join(NewsItem, NewsItem.id == NewsStoryItem.news_item_id)
                .join(NewsStory, NewsStory.id == NewsStoryItem.cluster_id)
                .where(
                    NewsItem.source_id == source_id,
                    NewsItem.workspace_id == workspace_id,
                    NewsItem.user_id == user_id,
                    NewsStory.suppressed.is_(False),
                    ~exists(
                        select(NewsIntelligenceRun.id).where(
                            NewsIntelligenceRun.cluster_id == NewsStory.id,
                            or_(
                                NewsIntelligenceRun.base_version == NewsStory.version,
                                NewsIntelligenceRun.result_version == NewsStory.version,
                            ),
                        )
                    ),
                )
                .distinct()
                .order_by(NewsStoryItem.cluster_id)
                .limit(4)
            )
        )
    published = 0
    for identifier in identifiers:
        result = await run_story_intelligence(
            factory, settings, identifier, workspace_id=workspace_id, user_id=user_id
        )
        published += result == "READY"
    return published


@activity.defn
async def prepare_news_conversation_activity(payload: NewsConversationWork) -> list[NewsSourceWork]:
    """Return at most four already-owned sources; queries never enter Temporal history."""
    from navox.news.conversation_retrieval import refresh_work_for_turn

    settings = get_settings()
    if not settings.news_feed_enabled or not settings.news_chat_enabled:
        return []
    async with get_session_factory()() as database:
        try:
            return await refresh_work_for_turn(
                database,
                settings,
                UUID(payload.turn_id),
                workspace_id=UUID(payload.workspace_id),
                user_id=UUID(payload.user_id),
                now=datetime.now(UTC),
            )
        except (NewsError, ValueError):
            return []
