"""Render saved selections only after rechecking current ownership, evidence and rights."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.context import ContextDenied
from navox.core.settings import Settings
from navox.db.news import NewsIntelligenceRun
from navox.news.ai_context import NewsContext
from navox.news.ai_contracts import SYNTHESIS, NewsSynthesis, validate_news_output
from navox.news.contracts import Contract, NewsError, Verification, stored_utc
from navox.news.intelligence import claim_signature, source_versions
from navox.news.stories import owned_story


class SummaryFact(Contract):
    claim_id: UUID
    text: str
    status: Verification
    attributed_to: str | None
    source_name: str
    source_url: str


class SummarySection(Contract):
    heading: Literal["what_happened", "why_it_matters", "what_is_unclear", "latest_development"]
    facts: tuple[SummaryFact, ...]


class StorySummary(Contract):
    status: Literal["READY", "PENDING", "UNAVAILABLE", "SOURCES_CHANGED"]
    headline: str | None = None
    headline_source_url: str | None = None
    headline_attribution: str | None = None
    # A selected publisher headline is not an independent verification judgment.
    headline_status: Literal["ATTRIBUTED"] = "ATTRIBUTED"
    sections: tuple[SummarySection, ...] = ()
    as_of: datetime | None = None
    actions_executed: Literal[False] = False


async def summary_view(
    database: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    story_id: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> StorySummary:
    story = await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    row = await database.scalar(
        select(NewsIntelligenceRun)
        .where(
            NewsIntelligenceRun.cluster_id == story_id,
            NewsIntelligenceRun.workspace_id == workspace_id,
            NewsIntelligenceRun.user_id == user_id,
        )
        .order_by(NewsIntelligenceRun.created_at.desc(), NewsIntelligenceRun.base_version.desc())
        .limit(1)
    )
    if row is None:
        return StorySummary(status="UNAVAILABLE")
    if row.status == "PROCESSING" and stored_utc(row.created_at) <= now < stored_utc(
        row.expires_at
    ):
        return StorySummary(status="PENDING")
    if row.status != "READY" or row.selection is None:
        return StorySummary(status="UNAVAILABLE")
    try:
        if now >= stored_utc(row.expires_at) or story.version != row.result_version:
            raise NewsError("stale_evidence")
        context = NewsContext(
            factory,
            settings,
            workspace_id=workspace_id,
            user_id=user_id,
            item_ids=tuple(
                sorted((UUID(key) for key in row.source_snapshot), key=lambda key: key.int)
            ),
            question="Render the current source-backed summary.",
            mode="intelligence",
            story_id=story_id,
        )
        snapshot = await context.read_in_session(database)
        if source_versions(snapshot) != row.source_snapshot:
            raise NewsError("stale_evidence")
        selection = validate_news_output(SYNTHESIS, row.selection, snapshot)
        if not isinstance(selection, NewsSynthesis):
            raise NewsError("invalid_evidence")
        claims = {claim.id: claim for claim in snapshot.claims}
        selected = {
            identifier for section in selection.sections for identifier in section.claim_ids
        }
        if {
            str(identifier): claim_signature(claims[identifier]) for identifier in selected
        } != row.claim_snapshot:
            raise NewsError("stale_evidence")
        items = {item.id: item for item in snapshot.items}
        # Claim origin is recovered from the database, never chosen by generated text.
        from navox.db.news import NewsClaim

        origin_rows = await database.execute(
            select(NewsClaim.id, NewsClaim.origin_item_id).where(
                NewsClaim.id.in_(selected),
                NewsClaim.workspace_id == workspace_id,
                NewsClaim.user_id == user_id,
                NewsClaim.cluster_id == story_id,
            )
        )
        origins: dict[UUID, UUID] = {identifier: origin_id for identifier, origin_id in origin_rows}
        sections = []
        for section in selection.sections:
            facts = []
            for identifier in section.claim_ids:
                claim, origin = claims[identifier], items[origins[identifier]]
                facts.append(
                    SummaryFact(
                        claim_id=identifier,
                        text=claim.text,
                        status=claim.status,
                        attributed_to=claim.attributed_to,
                        source_name=origin.source_name,
                        source_url=origin.canonical_url,
                    )
                )
            sections.append(SummarySection(heading=section.heading, facts=tuple(facts)))
        headline = items[selection.headline_item_id]
        return StorySummary(
            status="READY",
            headline=headline.headline,
            headline_source_url=headline.canonical_url,
            headline_attribution=headline.source_name,
            sections=tuple(sections),
            as_of=stored_utc(row.created_at),
        )
    except (ContextDenied, NewsError, ValueError, KeyError):
        return StorySummary(status="SOURCES_CHANGED")
