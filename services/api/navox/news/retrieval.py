"""Bounded query retrieval over owned, currently permitted news, never the open web."""

import re
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsContentRights, NewsItem, NewsSource, NewsStory, NewsStoryItem
from navox.news.clustering import search_terms
from navox.news.contracts import Contract, NewsError, NewsItemRead, SourceDefinition
from navox.news.evidence import permitted_summary_item
from navox.news.registry import current_rights, require_definition
from navox.news.rights import Operation, policy_for, require_operation
from navox.news.stories import owned_story


class NewsIntent(StrEnum):
    CURRENT_NEWS = "CURRENT_NEWS"
    TRENDING = "TRENDING"
    X_TRENDS = "X_TRENDS"
    STORY_QUESTION = "STORY_QUESTION"
    VERIFY_CLAIM = "VERIFY_CLAIM"
    TIMELINE = "TIMELINE"
    BACKGROUND = "BACKGROUND"
    COVERAGE_COMPARISON = "COVERAGE_COMPARISON"
    WHATS_CHANGED = "WHATS_CHANGED"
    DEEP_RESEARCH = "DEEP_RESEARCH"


class Freshness(StrEnum):
    REALTIME = "REALTIME"
    FRESH = "FRESH"
    RECENT = "RECENT"
    HISTORICAL = "HISTORICAL"


class Depth(StrEnum):
    QUICK = "QUICK"
    STANDARD = "STANDARD"
    DEEP = "DEEP"


class RetrievalPlan(Contract):
    intent: NewsIntent = NewsIntent.CURRENT_NEWS
    freshness: Freshness = Freshness.FRESH
    depth: Depth = Depth.STANDARD

    @model_validator(mode="after")
    def historical_scope(self) -> "RetrievalPlan":
        if self.freshness == Freshness.HISTORICAL and self.intent not in {
            NewsIntent.TIMELINE,
            NewsIntent.BACKGROUND,
        }:
            raise ValueError("Historical evidence is not a current-news answer")
        return self

    @property
    def freshness_seconds(self) -> int:
        return {
            Freshness.REALTIME: 60,
            Freshness.FRESH: 1800,
            Freshness.RECENT: 86400,
            Freshness.HISTORICAL: 31536000,
        }[self.freshness]

    @property
    def item_limit(self) -> int:
        return {Depth.QUICK: 4, Depth.STANDARD: 8, Depth.DEEP: 12}[self.depth]

    @property
    def candidate_limit(self) -> int:
        return {Depth.QUICK: 100, Depth.STANDARD: 200, Depth.DEEP: 400}[self.depth]


class RetrievalResult(Contract):
    items: tuple[NewsItemRead, ...] = Field(default=(), max_length=12)
    refresh_source_ids: tuple[UUID, ...] = Field(default=(), max_length=4)
    examined: int = Field(ge=0, le=400)
    limited: bool
    scope: str = "owned_permitted_items"
    resolved_reference: bool = False


STOP_WORDS = frozenset(
    (
        "a an and are about any as at be been by can could did do does for from happened "
        "has have how in into is it latest me more news of on or please recent report reports "
        "show some tell than that the their them there these they this those to today was were "
        "what what's when where which who why will with would you your"
    ).split()
)


def query_terms(question: str) -> tuple[str, ...]:
    return tuple(sorted(search_terms(question) - STOP_WORDS))[:10]


def numbered_reference(question: str) -> int | None:
    matches = re.findall(r"(?:#|\b(?:number|item|story)\s+)([0-9]+)\b", question.casefold())
    if not matches:
        return None
    if len(set(matches)) != 1 or len(matches[0]) > 3 or int(matches[0]) < 1:
        raise NewsError("invalid_reference")
    return int(matches[0])


async def select_evidence(
    database: AsyncSession,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    question: str,
    plan: RetrievalPlan,
    now: datetime,
    story_id: UUID | None = None,
    reference_item_id: UUID | None = None,
    previous_item_ids: tuple[UUID, ...] = (),
) -> RetrievalResult:
    """Refresh candidates are identifiers only; this function performs no network I/O."""
    plan = RetrievalPlan.model_validate(plan)
    user = await database.get(User, user_id, populate_existing=True)
    member = await database.get(WorkspaceMembership, (workspace_id, user_id))
    if user is None or member is None or user.agent_paused:
        raise NewsError("conversation_unavailable")
    if story_id is not None:
        await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    if reference_item_id is not None:
        await permitted_summary_item(
            database,
            reference_item_id,
            definitions,
            workspace_id=workspace_id,
            user_id=user_id,
            now=now,
        )
        reference = await database.get(NewsStoryItem, reference_item_id)
        if reference is None or (story_id is not None and story_id != reference.cluster_id):
            raise NewsError("invalid_reference")
        story_id = reference.cluster_id
        await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    permissions, snippet_profiles, policy_limited = await search_permissions(
        database, definitions, workspace_id=workspace_id, user_id=user_id, now=now
    )
    if not permissions:
        return RetrievalResult(examined=0, limited=policy_limited)
    query = (
        select(NewsItem)
        .join(NewsStoryItem, NewsStoryItem.news_item_id == NewsItem.id)
        .join(NewsStory, NewsStory.id == NewsStoryItem.cluster_id)
        .where(
            NewsItem.workspace_id == workspace_id,
            NewsItem.user_id == user_id,
            NewsStory.workspace_id == workspace_id,
            NewsStory.user_id == user_id,
            NewsStory.suppressed.is_(False),
            NewsItem.expires_at > now,
            NewsStoryItem.item_revision == NewsItem.revision,
            NewsItem.published_at <= now + timedelta(minutes=5),
        )
    )
    query = query.where(or_(*permissions))
    terms = query_terms(question)
    if story_id is not None:
        query = query.where(NewsStory.id == story_id)
    elif terms:
        query = query.where(
            or_(
                *(
                    or_(
                        NewsItem.headline.ilike(
                            "%"
                            + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                            + "%",
                            escape="\\",
                        ),
                        and_(
                            NewsItem.rights_profile_id.in_(snippet_profiles),
                            NewsItem.description.ilike(
                                "%"
                                + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                                + "%",
                                escape="\\",
                            ),
                        ),
                    )
                    for term in terms
                )
            )
        )
    rows = list(
        await database.scalars(
            query.order_by(NewsItem.published_at.desc(), NewsItem.id).limit(
                plan.candidate_limit + 1
            )
        )
    )
    limited = policy_limited or len(rows) > plan.candidate_limit
    views = []
    for row in rows[: plan.candidate_limit]:
        try:
            _, view, _ = await permitted_summary_item(
                database, row.id, definitions, workspace_id=workspace_id, user_id=user_id, now=now
            )
            if (
                terms
                and story_id is None
                and not (set(terms) & search_terms(view.headline + " " + (view.description or "")))
            ):
                continue
            views.append(view)
        except NewsError:
            continue
    reference_order = {value: index for index, value in enumerate(previous_item_ids)}

    def rank(view: NewsItemRead) -> tuple[int, int, float, str]:
        priority = reference_order.get(view.id, 1000) if not terms else 0
        score = 2 * len(set(terms) & search_terms(view.headline))
        score += len(set(terms) & search_terms(view.description or ""))
        return priority, -score, -view.published_at.timestamp(), view.id.hex

    views.sort(key=rank)
    cutoff = now - timedelta(seconds=plan.freshness_seconds)
    fresh = tuple(
        view for view in views if cutoff <= view.last_observed_at <= now + timedelta(minutes=5)
    )
    stale_sources = tuple(
        dict.fromkeys(view.source_id for view in views if view.last_observed_at < cutoff)
    )
    if reference_item_id is not None and not any(view.id == reference_item_id for view in views):
        raise NewsError("invalid_reference")
    return RetrievalResult(
        items=fresh[: plan.item_limit],
        refresh_source_ids=stale_sources[:4],
        examined=min(len(rows), plan.candidate_limit),
        limited=limited or len(fresh) > plan.item_limit or len(stale_sources) > 4,
        resolved_reference=reference_item_id is not None,
    )


async def search_permissions(
    database: AsyncSession,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> tuple[list[ColumnElement[bool]], list[UUID], bool]:
    """Authorize source policies before running any predicate over stored article text."""
    clauses: list[ColumnElement[bool]] = []
    snippets: list[UUID] = []
    limited = False
    remaining = 1000
    sources = list(
        await database.scalars(
            select(NewsSource)
            .where(
                NewsSource.workspace_id == workspace_id,
                NewsSource.user_id == user_id,
                NewsSource.source_key.in_(tuple(definitions)),
            )
            .order_by(NewsSource.id)
            .with_for_update(read=True)
        )
    )
    for source in sources:
        try:
            require_definition(source, definitions)
            current = policy_for(await current_rights(database, source), now=now)
            require_operation(Operation.METADATA, current)
            require_operation(Operation.SUMMARY, current)
        except NewsError:
            continue
        rows = list(
            await database.scalars(
                select(NewsContentRights)
                .where(
                    NewsContentRights.source_id == source.id,
                )
                .order_by(NewsContentRights.version.desc())
                .limit(remaining + 1)
                .with_for_update(read=True)
            )
        )
        limited = limited or len(rows) > remaining
        examined = rows[:remaining]
        remaining -= len(examined)
        for row in examined:
            try:
                original = policy_for(row, now=now)
                require_operation(Operation.METADATA, original)
                require_operation(Operation.SUMMARY, original)
            except NewsError:
                continue
            cutoff = now - timedelta(days=min(original.retention_days, current.retention_days))
            clauses.append(
                and_(NewsItem.rights_profile_id == row.id, NewsItem.retrieved_at > cutoff)
            )
            if original.snippet_storage_allowed and current.snippet_storage_allowed:
                snippets.append(row.id)
        if remaining == 0:
            limited = True
            break
    return clauses, snippets, limited
