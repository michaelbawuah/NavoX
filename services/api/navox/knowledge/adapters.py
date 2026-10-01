"""Native read adapters for authoritative owned domains (SPEC-002/004/006).

Commitments, subscriptions and News stay authoritative in their own stores.
These adapters read them through their existing owned services and current
rights checks and return typed evidence references. Nothing is persisted in the
connected-source knowledge tables and no connector row or URL is fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.models import Commitment
from navox.db.news import NewsStory
from navox.knowledge.exclusions import ExclusionFilters
from navox.knowledge.planner import date_range_covers
from navox.knowledge.search_contracts import (
    EvidenceExcerpt,
    EvidenceResource,
    RetrievalPlan,
    StructuredFact,
)
from navox.news.contracts import NewsError
from navox.news.registry import catalog
from navox.news.stories import owned_story, story_view
from navox.subscriptions import service as subscription_service

NATIVE_LIMIT = 25
COMMITMENT_TYPE = "COMMITMENT"
SUBSCRIPTION_TYPE = "SUBSCRIPTION"
NEWS_TYPE = "NEWS_STORY"
COMMITMENT_AUTHORITY = "SPEC-002 commitments"
SUBSCRIPTION_AUTHORITY = "SPEC-004 subscriptions"
NEWS_AUTHORITY = "SPEC-006 News"
NATIVE_TYPES = (COMMITMENT_TYPE, SUBSCRIPTION_TYPE, NEWS_TYPE)


@dataclass(frozen=True)
class NativeEvidence:
    resources: tuple[EvidenceResource, ...]
    facts: tuple[StructuredFact, ...]
    truncated: bool


def _matches(terms: tuple[str, ...], *values: object) -> bool:
    if not terms:
        return True
    haystack = " ".join(value for value in values if isinstance(value, str) and value).casefold()
    return any(term in haystack for term in terms)


def _excerpt(text: str | None, *, limit: int = 400) -> EvidenceExcerpt | None:
    if not text:
        return None
    span = text.strip()
    if not span:
        return None
    return EvidenceExcerpt(text=span[:limit], start=0, end=min(len(span), limit))


def _money(amount: Decimal | None, currency: str | None) -> str:
    if amount is None:
        return "unknown amount"
    rendered = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{rendered} {currency}" if currency else rendered


def domain_requested(plan: RetrievalPlan, domain: str, filters: ExclusionFilters) -> bool:
    """Whether a native domain may contribute to this request.

    A connected-source filter names connection UUIDs, which native domains are
    not, so it excludes them rather than silently ignoring the filter.
    """
    if plan.source_ids:
        return False
    if plan.types and domain not in {resource_type.value for resource_type in plan.types}:
        return False
    return not filters.excludes(source_type=domain)


async def _commitment_evidence(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    limit: int,
) -> tuple[list[EvidenceResource], list[StructuredFact], bool]:
    rows = list(
        await database.scalars(
            select(Commitment)
            .where(Commitment.workspace_id == workspace_id, Commitment.user_id == user_id)
            .order_by(Commitment.due_at.is_(None), Commitment.due_at, Commitment.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
    )
    truncated = len(rows) > limit
    resources: list[EvidenceResource] = []
    facts: list[StructuredFact] = []
    for row in rows[:limit]:
        if not _matches(plan.terms, row.title, row.description, row.commitment_type):
            continue
        if plan.date_range is not None and not date_range_covers(plan.date_range, row.due_at):
            continue
        excerpt = _excerpt(row.description)
        resources.append(
            EvidenceResource(
                source_type=COMMITMENT_TYPE,
                resource_id=row.id,
                title=row.title,
                excerpts=(excerpt,) if excerpt else (),
                source_updated_at=row.updated_at,
                provenance={"authority": COMMITMENT_AUTHORITY, "status": row.status},
                origin="NATIVE",
            )
        )
        if row.due_at is not None:
            facts.append(
                StructuredFact(
                    fact_id=f"commitment:{row.id}:due_at",
                    label="Due",
                    value=row.due_at.isoformat(),
                    source_type=COMMITMENT_TYPE,
                    resource_id=row.id,
                    source_updated_at=row.updated_at,
                    authority=COMMITMENT_AUTHORITY,
                )
            )
    return resources, facts, truncated


async def _subscription_evidence(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    limit: int,
) -> tuple[list[EvidenceResource], list[StructuredFact], bool]:
    rows = await subscription_service.list_subscriptions(
        database, workspace_id=workspace_id, user_id=user_id
    )
    truncated = len(rows) > limit
    resources: list[EvidenceResource] = []
    facts: list[StructuredFact] = []
    for row in rows[:limit]:
        # Re-read current values: a caller's identity-map copy may be stale.
        await database.refresh(row)
        if not _matches(plan.terms, row.name, row.plan_name, row.obligation_type):
            continue
        if plan.date_range is not None and not date_range_covers(
            plan.date_range, row.next_renewal_at
        ):
            continue
        excerpt = _excerpt(
            f"{_money(row.billing_amount, row.billing_currency)} "
            f"{row.billing_interval} · {row.status}"
        )
        resources.append(
            EvidenceResource(
                source_type=SUBSCRIPTION_TYPE,
                resource_id=row.id,
                title=row.plan_name or row.name,
                excerpts=(excerpt,) if excerpt else (),
                source_updated_at=row.updated_at,
                source_version=str(row.revision),
                provenance={"authority": SUBSCRIPTION_AUTHORITY, "status": row.status},
                origin="NATIVE",
            )
        )
        if row.next_renewal_at is not None:
            facts.append(
                StructuredFact(
                    fact_id=f"subscription:{row.id}:next_renewal_at",
                    label="Next renewal",
                    value=row.next_renewal_at.isoformat(),
                    source_type=SUBSCRIPTION_TYPE,
                    resource_id=row.id,
                    source_updated_at=row.updated_at,
                    authority=SUBSCRIPTION_AUTHORITY,
                )
            )
    return resources, facts, truncated


async def _news_evidence(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    now: datetime,
    settings: Settings | None,
    limit: int,
) -> tuple[list[EvidenceResource], list[StructuredFact], bool]:
    """News evidence through the owned story service and its current rights checks.

    The trusted catalog supplies source definitions, ``owned_story`` enforces
    ownership and suppression, and ``story_view`` applies the original/current
    rights intersection, retention windows and URL validation. Anything the News
    service refuses is simply absent here.
    """
    # Search must not bypass the operator's News flag: direct story_view sits
    # below the API's require_feed gate.
    if settings is None or not settings.news_feed_enabled:
        return [], [], False
    try:
        definitions = catalog(settings)
    except NewsError:
        return [], [], False
    if not definitions:
        return [], [], False
    identifiers = list(
        await database.scalars(
            select(NewsStory.id)
            .where(
                NewsStory.workspace_id == workspace_id,
                NewsStory.user_id == user_id,
                NewsStory.suppressed.is_(False),
            )
            .order_by(NewsStory.last_updated_at.desc(), NewsStory.id)
            .limit(limit + 1)
            .execution_options(populate_existing=True)
        )
    )
    truncated = len(identifiers) > limit
    resources: list[EvidenceResource] = []
    facts: list[StructuredFact] = []
    for story_id in identifiers[:limit]:
        try:
            story = await owned_story(
                database, story_id, workspace_id=workspace_id, user_id=user_id
            )
            view = await story_view(database, story, definitions, now=now)
        except NewsError:
            continue
        if not _matches(plan.terms, view.headline, view.description, view.category.value):
            continue
        moment = view.event_started_at or view.published_at
        if plan.date_range is not None and not date_range_covers(plan.date_range, moment):
            continue
        excerpt = _excerpt(view.description)
        # Cite the story's own anchor item, not whichever source is newest.
        anchor = next((item for item in view.sources if item.id == story.anchor_item_id), None)
        resources.append(
            EvidenceResource(
                source_type=NEWS_TYPE,
                resource_id=view.id,
                title=view.headline,
                excerpts=(excerpt,) if excerpt else (),
                canonical_url=anchor.canonical_url if anchor else None,
                source_updated_at=view.last_updated_at,
                source_version=str(view.version),
                provenance={
                    "authority": NEWS_AUTHORITY,
                    # Verification and lifecycle are distinct News fields.
                    "verification_status": view.verification_status.value,
                    "lifecycle_status": view.lifecycle_status,
                    "source_name": anchor.source_name if anchor else "",
                    # Display basis only: this does not assert summary permission,
                    # which Ask must establish through permitted_summary_item.
                    "content_basis": "stored_snippet" if excerpt else "metadata",
                },
                origin="NATIVE",
            )
        )
        facts.append(
            StructuredFact(
                fact_id=f"news:{view.id}:published_at",
                label="Published",
                value=view.published_at.isoformat(),
                source_type=NEWS_TYPE,
                resource_id=view.id,
                source_updated_at=view.last_updated_at,
                authority=NEWS_AUTHORITY,
            )
        )
    return resources, facts, truncated


async def native_evidence(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    filters: ExclusionFilters,
    now: datetime,
    settings: Settings | None = None,
    limit: int = NATIVE_LIMIT,
) -> NativeEvidence:
    """Collect bounded native evidence. Every read re-checks current authority."""
    resources: list[EvidenceResource] = []
    facts: list[StructuredFact] = []
    truncated = False
    if domain_requested(plan, COMMITMENT_TYPE, filters):
        found, found_facts, cut = await _commitment_evidence(
            database, workspace_id=workspace_id, user_id=user_id, plan=plan, limit=limit
        )
        resources.extend(found)
        facts.extend(found_facts)
        truncated = truncated or cut
    if domain_requested(plan, SUBSCRIPTION_TYPE, filters):
        found, found_facts, cut = await _subscription_evidence(
            database, workspace_id=workspace_id, user_id=user_id, plan=plan, limit=limit
        )
        resources.extend(found)
        facts.extend(found_facts)
        truncated = truncated or cut
    if domain_requested(plan, NEWS_TYPE, filters):
        found, found_facts, cut = await _news_evidence(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            plan=plan,
            now=now,
            settings=settings,
            limit=limit,
        )
        resources.extend(found)
        facts.extend(found_facts)
        truncated = truncated or cut
    return NativeEvidence(resources=tuple(resources), facts=tuple(facts), truncated=truncated)
