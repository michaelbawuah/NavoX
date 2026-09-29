"""Grounded claim admission and independently reviewed evidence, with no public truth setter."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import (
    NewsClaim,
    NewsClaimEvidence,
    NewsContentRights,
    NewsItem,
    NewsSource,
    NewsStory,
    NewsStoryItem,
)
from navox.news.clustering import digest_text, duplicate_keys, normalized_text
from navox.news.contracts import (
    Contract,
    NewsError,
    NewsItemRead,
    SourceDefinition,
    SourceType,
    Verification,
)
from navox.news.ingestion import item_view
from navox.news.registry import current_rights
from navox.news.rights import Operation, policy_for, require_operation
from navox.news.stories import record_version
from navox.news.verification import EvidenceFact, EvidenceKind, Relationship, verify


class ClaimSpan(Contract):
    item_id: UUID
    item_revision: int = Field(ge=1)
    field: Literal["headline", "description"]
    start: int = Field(ge=0, le=4000)
    end: int = Field(gt=0, le=4000)
    claim_type: Literal["EVENT", "NUMBER", "STATEMENT", "FORECAST", "OTHER"] = "STATEMENT"


class EvidenceReview(Contract):
    """Trusted adjudication input; deliberately absent from model schemas and public API."""

    relationship: Relationship
    kind: EvidenceKind
    strength: Literal["weak", "strong"]
    origin_groups: frozenset[str] = Field(default=frozenset(), max_length=20)
    reference: str = Field(min_length=8, max_length=128)


class ClaimRead(Contract):
    id: UUID
    text: str
    attributed_to: str | None
    status: Verification
    independent_supports: int
    independent_contradictions: int
    source_ids: tuple[UUID, ...]
    reason: str


async def permitted_summary_item(
    database: AsyncSession,
    item_id: UUID,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> tuple[NewsItem, NewsItemRead, NewsSource]:
    item = await database.scalar(
        select(NewsItem).where(
            NewsItem.id == item_id,
            NewsItem.workspace_id == workspace_id,
            NewsItem.user_id == user_id,
        )
    )
    if item is None:
        raise NewsError("item_unavailable")
    view = await item_view(database, item, definitions, now=now)
    source = await database.get(NewsSource, item.source_id)
    original = await database.get(NewsContentRights, item.rights_profile_id)
    if source is None or original is None:
        raise NewsError("rights_denied")
    current = await current_rights(database, source)
    require_operation(
        Operation.SUMMARY, policy_for(original, now=now), policy_for(current, now=now)
    )
    return item, view, source


def quote_at(view: NewsItemRead, span: ClaimSpan) -> str:
    text = view.headline if span.field == "headline" else view.description
    if (
        view.revision != span.item_revision
        or text is None
        or not 0 <= span.start < span.end <= len(text)
    ):
        raise NewsError("invalid_evidence")
    quote = text[span.start : span.end].strip()
    if span.field == "headline" and (span.start != 0 or span.end != len(text)):
        raise NewsError("invalid_evidence")
    if span.field == "description":
        before, after = text[: span.start].rstrip(), text[span.end :]
        if before and before[-1] not in ".!?":
            raise NewsError("invalid_evidence")
        if after and (not quote or quote[-1] not in ".!?" or not after[0].isspace()):
            raise NewsError("invalid_evidence")
    if not quote or len(quote) > 2000:
        raise NewsError("invalid_evidence")
    # No arbitrary generated paraphrase is accepted as an established fact.
    return quote


async def admit_claim(
    database: AsyncSession,
    story: NewsStory,
    span: ClaimSpan,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> NewsClaim:
    item, view, source = await permitted_summary_item(
        database,
        span.item_id,
        definitions,
        workspace_id=story.workspace_id,
        user_id=story.user_id,
        now=now,
    )
    member = await database.get(NewsStoryItem, item.id)
    if member is None or member.cluster_id != story.id or member.item_revision != item.revision:
        raise NewsError("invalid_evidence")
    quote = quote_at(view, span)
    digest = digest_text(normalized_text(quote))
    existing = await database.scalar(
        select(NewsClaim).where(
            NewsClaim.cluster_id == story.id,
            NewsClaim.origin_item_id == item.id,
            NewsClaim.text_digest == digest,
        )
    )
    if existing is not None:
        return existing
    claim = NewsClaim(
        cluster_id=story.id,
        workspace_id=story.workspace_id,
        user_id=story.user_id,
        origin_item_id=item.id,
        origin_revision=item.revision,
        claim_text=quote,
        text_digest=digest,
        claim_type=span.claim_type,
        attributed_to=source.name,
        verification_status=Verification.UNCONFIRMED,
        first_seen_at=now,
    )
    database.add(claim)
    await database.flush()
    database.add(
        NewsClaimEvidence(
            claim_id=claim.id,
            news_item_id=item.id,
            source_id=source.id,
            workspace_id=story.workspace_id,
            user_id=story.user_id,
            item_revision=item.revision,
            quote_field=span.field,
            quote_start=span.start,
            quote_end=span.end,
            relationship=Relationship.ATTRIBUTES,
            evidence_kind=EvidenceKind.UNREVIEWED,
            evidence_strength="weak",
            provenance_group=source.independence_group,
            independence_group=source.independence_group,
            origin_groups=[],
        )
    )
    await database.flush()
    return claim


async def review_evidence(
    database: AsyncSession,
    claim: NewsClaim,
    span: ClaimSpan,
    review: EvidenceReview,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> NewsClaimEvidence:
    item, view, source = await permitted_summary_item(
        database,
        span.item_id,
        definitions,
        workspace_id=claim.workspace_id,
        user_id=claim.user_id,
        now=now,
    )
    quote_at(view, span)
    # Company statements and social posts cannot acquire primary-evidence authority.
    if review.kind == EvidenceKind.PRIMARY_RECORD and source.source_type not in {
        SourceType.GOVERNMENT,
        SourceType.RESEARCH,
        SourceType.WIRE_SERVICE,
    }:
        raise NewsError("invalid_evidence")
    if review.relationship == Relationship.RETRACTS:
        origin = await database.get(NewsItem, claim.origin_item_id)
        if origin is None or origin.source_id != source.id:
            raise NewsError("invalid_evidence")
    if review.kind == EvidenceKind.SYNDICATED and not review.origin_groups:
        raise NewsError("invalid_evidence")
    evidence = await database.scalar(
        select(NewsClaimEvidence).where(
            NewsClaimEvidence.claim_id == claim.id, NewsClaimEvidence.news_item_id == item.id
        )
    )
    if evidence is None:
        evidence = NewsClaimEvidence(
            claim_id=claim.id,
            news_item_id=item.id,
            source_id=source.id,
            workspace_id=claim.workspace_id,
            user_id=claim.user_id,
        )
        database.add(evidence)
    evidence.item_revision = item.revision
    evidence.quote_field, evidence.quote_start, evidence.quote_end = (
        span.field,
        span.start,
        span.end,
    )
    evidence.relationship, evidence.evidence_kind = review.relationship, review.kind
    evidence.evidence_strength = review.strength
    evidence.provenance_group, evidence.independence_group = (
        source.independence_group,
        source.independence_group,
    )
    evidence.origin_groups = sorted(review.origin_groups)
    evidence.review_reference, evidence.reviewed_at = review.reference, now
    await database.flush()
    return evidence


async def evaluate_claim(
    database: AsyncSession,
    claim: NewsClaim,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
    freshness_seconds: int = 86400,
) -> ClaimRead:
    origin, _, _ = await permitted_summary_item(
        database,
        claim.origin_item_id,
        definitions,
        workspace_id=claim.workspace_id,
        user_id=claim.user_id,
        now=now,
    )
    if origin.revision != claim.origin_revision:
        raise NewsError("stale_evidence")
    facts = []
    for row in await database.scalars(
        select(NewsClaimEvidence).where(NewsClaimEvidence.claim_id == claim.id)
    ):
        try:
            item, view, source = await permitted_summary_item(
                database,
                row.news_item_id,
                definitions,
                workspace_id=claim.workspace_id,
                user_id=claim.user_id,
                now=now,
            )
            span = ClaimSpan.model_validate(
                dict(
                    item_id=item.id,
                    item_revision=row.item_revision,
                    field=row.quote_field,
                    start=row.quote_start,
                    end=row.quote_end,
                )
            )
            quote_at(view, span)
            fact = EvidenceFact(
                item_id=item.id,
                source_id=source.id,
                source_type=SourceType(source.source_type),
                identity_verified=source.identity_verified,
                relationship=Relationship(row.relationship),
                kind=EvidenceKind(row.evidence_kind),
                strong=row.evidence_strength == "strong",
                reviewed=bool(row.reviewed_at and row.review_reference),
                independence_group=source.independence_group,
                origin_groups=frozenset(row.origin_groups),
                copy_digest=duplicate_keys(view)[1],
                fresh=view.last_observed_at >= now - timedelta(seconds=freshness_seconds),
                origin_withdrawal=source.id == origin.source_id,
            )
            facts.append(fact)
        except (NewsError, ValueError):
            continue
    result = verify(facts)
    return ClaimRead(
        id=claim.id,
        text=claim.claim_text,
        attributed_to=claim.attributed_to,
        status=result.status,
        independent_supports=result.independent_supports,
        independent_contradictions=result.independent_contradictions,
        source_ids=tuple(dict.fromkeys(fact.source_id for fact in facts)),
        reason=result.reason,
    )


async def claim_views(
    database: AsyncSession,
    story: NewsStory,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> list[ClaimRead]:
    rows = await database.scalars(
        select(NewsClaim)
        .where(
            NewsClaim.cluster_id == story.id,
            NewsClaim.workspace_id == story.workspace_id,
            NewsClaim.user_id == story.user_id,
        )
        .order_by(NewsClaim.first_seen_at, NewsClaim.id)
        .limit(50)
    )
    result = []
    for row in rows:
        try:
            result.append(await evaluate_claim(database, row, definitions, now=now))
        except NewsError:
            continue
    return result


async def refresh_verification(
    database: AsyncSession,
    story: NewsStory,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> bool:
    views = {view.id: view for view in await claim_views(database, story, definitions, now=now)}
    changed = False
    for row in await database.scalars(select(NewsClaim).where(NewsClaim.cluster_id == story.id)):
        view = views.get(row.id)
        status = view.status if view else Verification.UNCONFIRMED
        changed = changed or row.verification_status != status
        row.verification_status, row.last_evaluated_at = status, now
    if changed:
        story.version += 1
        states = {view.status for view in views.values()}
        story.lifecycle_status = (
            "RETRACTED"
            if Verification.RETRACTED in states
            else ("DISPUTED" if Verification.DISPUTED in states else "ACTIVE")
        )
        await record_version(database, story, "VERIFICATION_CHANGED", now=now)
    return changed
