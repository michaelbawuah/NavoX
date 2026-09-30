"""Apply qualified relation selections under current operator-reviewed source policy."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import NewsClaim, NewsClaimEvidence, NewsSource, NewsStory
from navox.news.ai_contracts import NewsRelations
from navox.news.contracts import SourceDefinition
from navox.news.evidence import EvidenceReview, review_evidence
from navox.news.registry import require_definition
from navox.news.verification import EvidenceKind, Relationship


def relations_enabled(definitions: dict[str, SourceDefinition], *, now: datetime) -> bool:
    return any(
        definition.evidence_policy is not None
        and definition.evidence_policy.reviewed_at <= now < definition.evidence_policy.expires_at
        for definition in definitions.values()
    )


async def apply_relations(
    database: AsyncSession,
    story: NewsStory,
    proposals: NewsRelations,
    definitions: dict[str, SourceDefinition],
    *,
    trace_id: UUID,
    now: datetime,
) -> int:
    """No model field can grant strong, primary, independent or retraction authority.

    The caller must have executed the qualified RELATIONS gateway task and locked
    its unchanged owned NewsContext in this transaction. review_evidence independently
    enforces rights, exact spans and current story membership at the write boundary.
    """
    from navox.db.news import NewsItem

    applied = 0
    for proposal in proposals.relations:
        claim = await database.scalar(
            select(NewsClaim)
            .where(
                NewsClaim.id == proposal.claim_id,
                NewsClaim.cluster_id == story.id,
                NewsClaim.workspace_id == story.workspace_id,
                NewsClaim.user_id == story.user_id,
            )
            .execution_options(populate_existing=True)
        )
        item = await database.scalar(
            select(NewsItem)
            .where(
                NewsItem.id == proposal.span.item_id,
                NewsItem.workspace_id == story.workspace_id,
                NewsItem.user_id == story.user_id,
            )
            .execution_options(populate_existing=True)
        )
        if claim is None or item is None:
            continue
        existing = await database.scalar(
            select(NewsClaimEvidence).where(
                NewsClaimEvidence.claim_id == claim.id,
                NewsClaimEvidence.news_item_id == item.id,
            )
        )
        if (
            existing is not None
            and existing.item_revision == proposal.span.item_revision
            and existing.review_reference
            and not existing.review_reference.startswith("auto-rel:")
        ):
            # A model proposal cannot replace a current explicit trusted review.
            continue
        source = await database.get(NewsSource, item.source_id, populate_existing=True)
        if source is None:
            continue
        definition = require_definition(source, definitions)
        policy = definition.evidence_policy
        if policy is None or not policy.reviewed_at <= now < policy.expires_at:
            continue
        relationship = Relationship(proposal.relationship)
        if policy.role in {"INTERESTED_PARTY", "SOCIAL", "SYNDICATED"}:
            # These roles may be quoted but cannot become supporting authority.
            relationship = Relationship.ATTRIBUTES
        await review_evidence(
            database,
            claim,
            proposal.span,
            EvidenceReview(
                relationship=relationship,
                kind=EvidenceKind(policy.role),
                strength="strong" if policy.strong_evidence_allowed else "weak",
                origin_groups=policy.origin_groups,
                reference=f"auto-rel:{policy.review_reference}:{trace_id}",
            ),
            definitions,
            now=now,
        )
        applied += 1
    return applied
