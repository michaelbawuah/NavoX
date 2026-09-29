"""Durable, bounded extraction and synthesis. Selection is never verification authority."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.runtime import GatewayUnavailable
from navox.core.settings import Settings
from navox.db.news import NewsClaim, NewsIntelligenceRun, NewsStoryItem
from navox.news.ai_context import NewsContext, run_news_task
from navox.news.ai_contracts import (
    EXTRACTION,
    SYNTHESIS,
    ClaimExtraction,
    NewsContextInput,
    NewsSynthesis,
)
from navox.news.contracts import NewsError
from navox.news.evidence import ClaimRead, admit_claim, refresh_verification
from navox.news.registry import catalog
from navox.news.stories import owned_story, record_version

MAX_RUNS_PER_HOUR = 4
PHASE_BUDGET = Decimal("0.025")


def claim_signature(claim: ClaimRead) -> str:
    return hashlib.sha256(claim.model_dump_json().encode()).hexdigest()


def source_versions(snapshot: NewsContextInput) -> dict[str, int]:
    return {str(item.id): item.revision for item in snapshot.items}


async def story_item_ids(
    database: AsyncSession, story_id: UUID, *, workspace_id: UUID, user_id: UUID
) -> tuple[UUID, ...]:
    ids = tuple(
        await database.scalars(
            select(NewsStoryItem.news_item_id)
            .where(
                NewsStoryItem.cluster_id == story_id,
                NewsStoryItem.workspace_id == workspace_id,
                NewsStoryItem.user_id == user_id,
            )
            .order_by(NewsStoryItem.news_item_id)
            .limit(13)
        )
    )
    if not 1 <= len(ids) <= 12:
        raise NewsError("invalid_evidence")
    return ids


async def locked_snapshot(database: AsyncSession, context: NewsContext) -> NewsContextInput:
    snapshot = await context.read_in_session(database, lock=True)
    if (
        context.story_id is None
        or await story_item_ids(
            database, context.story_id, workspace_id=context.workspace_id, user_id=context.user_id
        )
        != context.item_ids
    ):
        raise NewsError("source_changed")
    return snapshot


async def begin_run(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    story_id: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> tuple[UUID, NewsContext] | None:
    if not settings.news_feed_enabled or not settings.news_intelligence_enabled:
        return None
    async with factory() as database:
        await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
        ids = await story_item_ids(database, story_id, workspace_id=workspace_id, user_id=user_id)
    context = NewsContext(
        factory,
        settings,
        workspace_id=workspace_id,
        user_id=user_id,
        item_ids=ids,
        question="Extract the material claims in this story.",
        mode="intelligence",
        story_id=story_id,
    )
    async with factory() as database:
        snapshot = await locked_snapshot(database, context)
        story = await owned_story(
            database, story_id, workspace_id=workspace_id, user_id=user_id, lock=True
        )
        existing = await database.scalar(
            select(NewsIntelligenceRun).where(
                NewsIntelligenceRun.cluster_id == story.id,
                or_(
                    NewsIntelligenceRun.base_version == story.version,
                    NewsIntelligenceRun.result_version == story.version,
                ),
            )
        )
        if existing is not None:
            # A retry, crash or failed phase never blindly buys another provider request.
            return None
        now = datetime.now(UTC)
        count = await database.scalar(
            select(func.count())
            .select_from(NewsIntelligenceRun)
            .where(
                NewsIntelligenceRun.workspace_id == workspace_id,
                NewsIntelligenceRun.user_id == user_id,
                NewsIntelligenceRun.created_at >= now - timedelta(hours=1),
            )
        )
        if (count or 0) >= MAX_RUNS_PER_HOUR:
            raise NewsError("rate_limited")
        row = NewsIntelligenceRun(
            cluster_id=story.id,
            workspace_id=workspace_id,
            user_id=user_id,
            base_version=story.version,
            status="PROCESSING",
            source_snapshot=source_versions(snapshot),
            claim_snapshot={},
            trace_ids=[],
            created_at=now,
            expires_at=min(
                now + timedelta(minutes=30), *(item.expires_at for item in snapshot.items)
            ),
        )
        database.add(row)
        await database.commit()
        return row.id, context


async def current_run(
    database: AsyncSession, run_id: UUID, context: NewsContext
) -> NewsIntelligenceRun:
    current = await locked_snapshot(database, context)
    if context.initial is None or context.initial != current:
        raise NewsError("source_changed")
    row = await database.scalar(
        select(NewsIntelligenceRun)
        .where(
            NewsIntelligenceRun.id == run_id,
            NewsIntelligenceRun.workspace_id == context.workspace_id,
            NewsIntelligenceRun.user_id == context.user_id,
            NewsIntelligenceRun.status == "PROCESSING",
            NewsIntelligenceRun.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )
    if row is None or row.source_snapshot != source_versions(current):
        raise NewsError("source_changed")
    story = await owned_story(
        database, row.cluster_id, workspace_id=row.workspace_id, user_id=row.user_id
    )
    if story.version != (row.result_version or row.base_version):
        raise NewsError("source_changed")
    return row


async def mark_failed(
    factory: async_sessionmaker[AsyncSession], run_id: UUID, *, workspace_id: UUID, user_id: UUID
) -> None:
    async with factory() as database:
        row = await database.scalar(
            select(NewsIntelligenceRun)
            .where(
                NewsIntelligenceRun.id == run_id,
                NewsIntelligenceRun.workspace_id == workspace_id,
                NewsIntelligenceRun.user_id == user_id,
            )
            .with_for_update()
        )
        if row is not None and row.status == "PROCESSING":
            row.status, row.failure_code, row.selection = "UNAVAILABLE", "ai_unavailable", None
            await database.commit()


async def run_story_intelligence(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    story_id: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> str:
    run_id = None
    try:
        started = await begin_run(
            factory, settings, story_id, workspace_id=workspace_id, user_id=user_id
        )
        if started is None:
            return "SKIPPED"
        run_id, context = started
        runtime = await build_runtime(settings, factory)
        extraction_result, extraction, _ = await run_news_task(
            runtime, context, EXTRACTION, max_cost=PHASE_BUDGET
        )
        if not isinstance(extraction, ClaimExtraction):
            raise NewsError("invalid_evidence")
        async with factory() as database:
            row = await current_run(database, run_id, context)
            story = await owned_story(
                database, story_id, workspace_id=workspace_id, user_id=user_id
            )
            previous = set(
                await database.scalars(select(NewsClaim.id).where(NewsClaim.cluster_id == story.id))
            )
            admitted = set()
            definitions, now = catalog(settings), datetime.now(UTC)
            for span in extraction.claims:
                claim = await admit_claim(database, story, span, definitions, now=now)
                admitted.add(claim.id)
            # No automatic PRIMARY_RECORD, review signature or "strong" evidence is invented.
            if admitted - previous:
                story.version += 1
                await record_version(database, story, "CLAIMS_EXTRACTED", now=now)
            await refresh_verification(database, story, definitions, now=now)
            row.result_version = story.version
            row.trace_ids = [str(extraction_result.trace_id)]
            await database.commit()
        context = NewsContext(
            factory,
            settings,
            workspace_id=workspace_id,
            user_id=user_id,
            item_ids=context.item_ids,
            question="Select supported story sections and preserve uncertainty.",
            mode="intelligence",
            story_id=story_id,
        )
        synthesis_result, synthesis, _ = await run_news_task(
            runtime, context, SYNTHESIS, max_cost=PHASE_BUDGET
        )
        if not isinstance(synthesis, NewsSynthesis) or context.initial is None:
            raise NewsError("invalid_evidence")
        async with factory() as database:
            row = await current_run(database, run_id, context)
            story = await owned_story(
                database, story_id, workspace_id=workspace_id, user_id=user_id
            )
            story.version += 1
            await record_version(database, story, "SYNTHESIS_PUBLISHED", now=datetime.now(UTC))
            selected = {
                identifier for section in synthesis.sections for identifier in section.claim_ids
            }
            row.claim_snapshot = {
                str(claim.id): claim_signature(claim)
                for claim in context.initial.claims
                if claim.id in selected
            }
            row.selection = synthesis.model_dump(mode="json")
            row.trace_ids = [*row.trace_ids, str(synthesis_result.trace_id)]
            row.result_version, row.status = story.version, "READY"
            await database.commit()
        return "READY"
    except (
        NewsError,
        ContextDenied,
        AIProviderNotConfigured,
        GatewayUnavailable,
        IntegrityError,
        PermissionError,
        ValueError,
    ):
        if run_id is not None:
            await mark_failed(factory, run_id, workspace_id=workspace_id, user_id=user_id)
        return "UNAVAILABLE"
