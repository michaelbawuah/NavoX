"""Bounded feed work with owner, pause, feature and permission checks at execution."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, exists, or_, select, update
from temporalio import activity

from navox.core.settings import Settings, get_settings
from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsConversation, NewsConversationTurn, NewsIntelligenceRun, NewsSource
from navox.db.session import get_session_factory
from navox.news.cluster_contracts import active_policy
from navox.news.contracts import NewsError, SourceDefinition
from navox.news.ingestion import ingest_source, purge_unavailable
from navox.news.jobs import NewsConversationWork, NewsSourcePage, NewsSourceWork, NewsWorkResult
from navox.news.registry import catalog, owned_source
from navox.news.semantic_clustering import (
    NEWS_CLUSTER_ACTIVITY_BOUND,
    semantic_clusterer,
    vectorize_source,
)
from navox.news.stories import index_source

PAGE_SIZE = 100


def clustering_deferred(
    settings: Settings, definitions: dict[str, SourceDefinition], *, now: datetime
) -> bool:
    """Whether ingestion must leave unresolved items to the clustering activity."""
    if not settings.news_semantic_clustering_enabled:
        return False
    return active_policy(settings=settings, definitions=definitions, now=now).policy is not None


def should_defer(
    payload: NewsSourceWork,
    settings: Settings,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> bool:
    """Deferral needs both an explicit workflow payload flag and a live policy.

    The flag is recorded in workflow history with the payload, so a payload from
    before this field existed keeps the safe default and never defers. Settings
    are re-checked here, so a replaced or expired policy cannot defer either.
    """
    return payload.defer and clustering_deferred(settings, definitions, now=now)


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
        try:
            defer = clustering_deferred(
                get_settings(), catalog(get_settings()), now=datetime.now(UTC)
            )
        except NewsError:
            defer = False
        return NewsSourcePage(
            sources=[
                NewsSourceWork(
                    str(row.id), str(row.workspace_id), str(row.user_id), str(uuid4()), defer=defer
                )
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
                    settings=settings,
                )
            deferred = receipt.status == "COMPLETED" and should_defer(
                payload, settings, definitions, now=datetime.now(UTC)
            )
            result = NewsWorkResult(receipt.status, receipt.stored_count, purged, deferred)
            # When this exact payload asked for the reviewed semantic pipeline,
            # unresolved items are left to the dedicated bounded clustering
            # activity. The flag is in workflow history, so the workflow can
            # always guarantee the separate non-paid exact recovery afterwards.
            if receipt.status == "COMPLETED" and not deferred:
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
async def news_clustering_activity(payload: NewsSourceWork) -> NewsWorkResult:
    """Bounded vector backfill plus first-membership clustering for one source.

    No News row lock is held across a provider call: the source is read without a
    lock and the read transaction is committed before any paid work, so the lock
    order stays user -> item -> source. Every run ends with exact dedupe/new-story
    indexing, and the result reports whether unresolved rows still need the
    separate non-paid exact recovery activity. Cancellation always propagates.
    """
    settings, factory = get_settings(), get_session_factory()
    definitions = catalog(settings)
    if not settings.news_feed_enabled:
        return NewsWorkResult("paused")
    async with factory() as database:
        try:
            # Deliberately unlocked: holding a source lock across a paid provider
            # call is what the lock-order and lock-duration rules forbid.
            source = await owned_source(
                database,
                UUID(payload.source_id),
                workspace_id=UUID(payload.workspace_id),
                user_id=UUID(payload.user_id),
                lock=False,
            )
        except NewsError:
            return NewsWorkResult("unavailable")
        workspace_id, user_id, source_id = source.workspace_id, source.user_id, source.id
        now = datetime.now(UTC)
        user = await database.get(User, user_id, populate_existing=True)
        member = await database.get(WorkspaceMembership, (workspace_id, user_id))
        paused = user is None or member is None or user.agent_paused
        # Release every read lock/snapshot before the gateway is touched.
        await database.commit()
        if paused:
            return NewsWorkResult("paused")
        clusterer = semantic_clusterer(settings, definitions=definitions, now=now)
        if clusterer is not None:
            try:
                await vectorize_source(
                    database,
                    source_id,
                    definitions,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    clusterer=clusterer,
                    now=now,
                )
                await index_source(
                    database,
                    source_id,
                    definitions,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    now=now,
                    clustering=clusterer,
                    unindexed_only=True,
                    limit=NEWS_CLUSTER_ACTIVITY_BOUND,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A provider, policy or validation failure stays inside this
                # activity; the exact pass below and the workflow's recovery
                # activity remain responsible for finishing the rows.
                await database.rollback()
        indexed = 0
        try:
            indexed = await index_source(
                database,
                source_id,
                definitions,
                workspace_id=workspace_id,
                user_id=user_id,
                now=datetime.now(UTC),
            )
            await database.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            await database.rollback()
            # The separate exact recovery activity runs with a fresh session.
            return NewsWorkResult("COMPLETED", stored_count=0, deferred=True)
        return NewsWorkResult("COMPLETED", stored_count=indexed)


@activity.defn
async def news_exact_index_activity(payload: NewsSourceWork) -> NewsWorkResult:
    """Non-paid exact dedupe/new-story indexing, safe to run after any failure.

    This activity never builds an AI runtime and never buys an embedding, so it
    can run when the gateway is missing, misconfigured or failing. It exists so a
    deferred item can never stay invisible because the clustering activity
    raised, timed out or was cancelled after ingestion, and it purges unavailable
    content first, exactly like ordinary ingestion.
    """
    settings, factory = get_settings(), get_session_factory()
    if not settings.news_feed_enabled:
        return NewsWorkResult("paused")
    definitions = catalog(settings)
    async with factory() as database:
        try:
            source = await owned_source(
                database,
                UUID(payload.source_id),
                workspace_id=UUID(payload.workspace_id),
                user_id=UUID(payload.user_id),
                lock=False,
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
        try:
            indexed = await index_source(
                database,
                source.id,
                definitions,
                workspace_id=source.workspace_id,
                user_id=source.user_id,
                now=datetime.now(UTC),
            )
        except NewsError:
            indexed = 0
        await database.commit()
        return NewsWorkResult("COMPLETED", stored_count=indexed, purged_count=purged)


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
