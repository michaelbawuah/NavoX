"""Permission-aware keyword and structured retrieval over the knowledge index.

Order of operations matters and is enforced here: candidate identities are
collected first, current authority is checked before any stored text is read,
and authority plus source revision are checked again before publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import hypot, isfinite
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeEmbedding,
    KnowledgeResource,
    KnowledgeResourceIndex,
    KnowledgeResourcePermission,
)
from navox.db.models import ConnectorResource
from navox.knowledge.contracts import stored_utc
from navox.knowledge.exclusions import ExclusionFilters
from navox.knowledge.permissions import can_view_resource
from navox.knowledge.rrf import RRF_K, reciprocal_rank_fusion
from navox.knowledge.search_contracts import (
    SEMANTIC_REASON_NAMESPACE,
    SEMANTIC_REASON_NO_VECTORS,
    SEMANTIC_REASON_OUTDATED,
    SEMANTIC_REASON_VECTOR_INVALID,
    EvidenceExcerpt,
    EvidenceResource,
    RetrievalPlan,
    SemanticKey,
    StructuredFact,
)

CANDIDATE_BOUND = 200
RANK_BOUND = 100
CHUNK_BOUND = 600
EXCERPTS_PER_RESOURCE = 3

# Authority labels come from shipped code that knows the domain owner, never
# from stored content.
STRUCTURED_AUTHORITY: dict[str, str] = {
    "calendar.event": "Calendar (source system)",
    "canvas.assignment": "Canvas (source system)",
    "task.due": "Task (source system)",
}
STRUCTURED_FACT_LABELS: dict[str, str] = {
    "calendar.event": "Event start",
    "canvas.assignment": "Due",
    "task.due": "Due",
}
# Intents whose query is a date or state window, so an extra topical constraint
# would remove the rows the caller asked for.
UNTYPED_STRUCTURED_INTENTS = frozenset({"TIMELINE", "OPERATIONAL_STATE"})


@dataclass(frozen=True)
class RankedCandidate:
    """One connected candidate identity with its rank-list key."""

    key: str
    knowledge_resource_id: UUID
    source_type: str
    source_connection_id: UUID
    external_resource_id: str
    external_parent_id: str | None
    source_resource_id: UUID | None
    indexed_content_hash: str | None
    index_state: str
    structured_kind: str | None = None
    structured_at: datetime | None = None
    structured_until: datetime | None = None


@dataclass
class RetrievalOutcome:
    resources: list[EvidenceResource] = field(default_factory=list)
    facts: list[StructuredFact] = field(default_factory=list)
    examined: int = 0
    truncated: bool = False
    stale_dropped: int = 0


@dataclass(frozen=True)
class RetrieverResult:
    """One retriever's ordered keys, evidence and connected candidate identities."""

    ranking: list[str]
    resources: dict[str, EvidenceResource]
    candidates: dict[str, RankedCandidate]
    examined: int
    truncated: bool
    stale_dropped: int = 0
    # Authorized candidates this phase cannot search because the connector never
    # retained their content, keyed so overlapping retrievers do not double count.
    not_searchable_keys: frozenset[str] = frozenset()
    facts: list[StructuredFact] = field(default_factory=list)


@dataclass(frozen=True)
class SemanticEvidence:
    """Honest outcome of one semantic retrieval over already-authorized rows.

    ``available`` only means an exact-namespace ranking was produced. When it is
    false, ``reference`` carries a non-sensitive reason code and callers return
    permission-safe lexical and structured results with explicit coverage.
    """

    ranking: list[str] = field(default_factory=list)
    resources: dict[str, EvidenceResource] = field(default_factory=dict)
    candidates: dict[str, RankedCandidate] = field(default_factory=dict)
    available: bool = False
    reference: str | None = None
    key: SemanticKey | None = None
    scored: int = 0
    namespace_matched: int = 0
    namespace_other: int = 0
    # Authorized current resources in this namespace whose stored vector was
    # invalid or unreadable, and authorized current resources with no vector.
    unusable: int = 0
    missing: int = 0
    # Namespace-matching vectors whose stored revision or classification no
    # longer matches the current row. Counted, never scored.
    outdated: int = 0
    vector_truncated: bool = False
    examined: int = 0
    truncated: bool = False
    stale_dropped: int = 0


def resource_key(source_type: str, resource_id: UUID) -> str:
    return f"{source_type}:{resource_id}"


def _term_score(terms: tuple[str, ...], title: str | None, text: str | None) -> int:
    folded_title = (title or "").casefold()
    folded_text = (text or "").casefold()
    score = 0
    for term in terms:
        if term in folded_title:
            score += 3
        if term in folded_text:
            score += 1
    return score


def _topical(terms: tuple[str, ...], *values: str | None) -> bool:
    """Whether any term appears in the supplied stored text."""
    if not terms:
        return True
    haystack = " ".join(value for value in values if value).casefold()
    return any(term in haystack for term in terms)


def connected_facts(candidate: RankedCandidate, row: KnowledgeResource) -> list[StructuredFact]:
    """Typed facts for a structured connected resource, with trusted labels."""
    kind = candidate.structured_kind
    if kind is None or candidate.structured_at is None:
        return []
    authority = STRUCTURED_AUTHORITY.get(kind, "Source system")
    label = STRUCTURED_FACT_LABELS.get(kind, "Date")
    facts = [
        StructuredFact(
            fact_id=f"{kind}:{row.id}:at",
            label=label,
            value=candidate.structured_at.isoformat(),
            source_type=candidate.source_type,
            resource_id=row.id,
            source_updated_at=row.source_updated_at,
            authority=authority,
        )
    ]
    if candidate.structured_until is not None:
        facts.append(
            StructuredFact(
                fact_id=f"{kind}:{row.id}:until",
                label="Event end",
                value=candidate.structured_until.isoformat(),
                source_type=candidate.source_type,
                resource_id=row.id,
                source_updated_at=row.source_updated_at,
                authority=authority,
            )
        )
    return facts


def _excerpts(
    chunks: list[KnowledgeChunk], terms: tuple[str, ...], *, title: str | None
) -> tuple[EvidenceExcerpt, ...]:
    ranked: list[tuple[int, KnowledgeChunk]] = []
    for chunk in chunks:
        body = (chunk.text_content or "").casefold()
        hits = sum(1 for term in terms if term in body)
        ranked.append((hits, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].chunk_index))
    chosen = [chunk for hits, chunk in ranked if hits > 0][:EXCERPTS_PER_RESOURCE]
    if not chosen:
        chosen = [chunk for _, chunk in ranked[:1]]
    excerpts: list[EvidenceExcerpt] = []
    for chunk in chosen:
        body = chunk.text_content or ""
        if not body:
            continue
        excerpts.append(
            EvidenceExcerpt(
                text=body,
                start=0,
                end=len(body),
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
            )
        )
    del title
    return tuple(excerpts)


async def eligible_identities(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    limit: int = CANDIDATE_BOUND,
) -> tuple[list[UUID], bool]:
    """Authorize resource identities before any content predicate runs.

    Only identities that currently pass ``can_view_resource`` may take part in
    text matching, counting or truncation, so a query can never probe for
    private rows. The scan itself is bounded and reports its own truncation.
    """
    # Only rows carrying a live VIEW grant naming this caller, this workspace or
    # the public principal can ever be visible, so the bounded scan is expressed
    # over the caller's own grant rows. Private rows can therefore never change
    # a match count or the truncation report.
    granted = (
        select(KnowledgeResourcePermission.resource_id)
        .where(
            KnowledgeResourcePermission.workspace_id == workspace_id,
            KnowledgeResourcePermission.permission == "VIEW",
            KnowledgeResourcePermission.revoked_at.is_(None),
            or_(
                and_(
                    KnowledgeResourcePermission.principal_type == "USER",
                    KnowledgeResourcePermission.principal_id == user_id,
                ),
                and_(
                    KnowledgeResourcePermission.principal_type == "WORKSPACE",
                    KnowledgeResourcePermission.principal_id == workspace_id,
                ),
                KnowledgeResourcePermission.principal_type == "PUBLIC",
            ),
        )
        .scalar_subquery()
    )
    identifiers = list(
        await database.scalars(
            select(KnowledgeResource.id)
            .where(
                KnowledgeResource.workspace_id == workspace_id,
                KnowledgeResource.deleted_at.is_(None),
                KnowledgeResource.id.in_(granted),
            )
            .order_by(KnowledgeResource.id)
            .limit(limit + 1)
        )
    )
    truncated = len(identifiers) > limit
    eligible: list[UUID] = []
    for resource_id in identifiers[:limit]:
        if await can_view_resource(
            database,
            resource_id=resource_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=now,
        ):
            eligible.append(resource_id)
    return eligible, truncated


async def hash_fence(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    candidates: list[RankedCandidate],
) -> tuple[list[RankedCandidate], int]:
    """Drop candidates whose canonical revision moved since they were indexed.

    This runs before any stored text is read, so a changed source can never be
    served from a stale derived representation.
    """
    if not candidates:
        return [], 0
    identifiers = [c.source_resource_id for c in candidates if c.source_resource_id is not None]
    rows = (
        await database.execute(
            select(ConnectorResource.id, ConnectorResource.content_hash).where(
                ConnectorResource.workspace_id == workspace_id,
                ConnectorResource.id.in_(identifiers),
            )
        )
    ).all()
    live = {row[0]: row[1] for row in rows}
    fresh: list[RankedCandidate] = []
    dropped = 0
    for candidate in candidates:
        if (
            candidate.source_resource_id is None
            or candidate.indexed_content_hash is None
            or live.get(candidate.source_resource_id) != candidate.indexed_content_hash
        ):
            dropped += 1
            continue
        fresh.append(candidate)
    return fresh, dropped


async def _candidate_rows(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    plan: RetrievalPlan,
    filters: ExclusionFilters,
    eligible_ids: list[UUID],
    structured_only: bool,
    limit: int,
    match_terms: bool = True,
) -> tuple[list[RankedCandidate], bool]:
    if not eligible_ids:
        return [], False
    query = (
        select(
            KnowledgeResource.id,
            KnowledgeResource.source_type,
            KnowledgeResource.source_connection_id,
            KnowledgeResource.external_resource_id,
            ConnectorResource.external_parent_id,
            ConnectorResource.id,
            KnowledgeResourceIndex.source_content_hash,
            KnowledgeResourceIndex.index_state,
            KnowledgeResourceIndex.structured_kind,
            KnowledgeResourceIndex.structured_at,
            KnowledgeResourceIndex.structured_until,
        )
        .join(KnowledgeResourceIndex, KnowledgeResourceIndex.resource_id == KnowledgeResource.id)
        .outerjoin(
            ConnectorResource,
            (ConnectorResource.id == KnowledgeResource.source_resource_id)
            & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id),
        )
        .where(
            KnowledgeResource.workspace_id == workspace_id,
            KnowledgeResource.deleted_at.is_(None),
            KnowledgeResource.id.in_(eligible_ids),
        )
    )
    if structured_only:
        query = query.where(KnowledgeResourceIndex.structured_at.is_not(None))
        query = query.order_by(KnowledgeResourceIndex.structured_at, KnowledgeResource.id)
    elif match_terms:
        clauses = []
        for term in plan.terms:
            clauses.append(KnowledgeResource.title.ilike(f"%{term}%"))
            clauses.append(KnowledgeResource.normalized_text.ilike(f"%{term}%"))
        if clauses:
            query = query.where(or_(*clauses))
        query = query.order_by(KnowledgeResourceIndex.updated_at.desc(), KnowledgeResource.id)
    else:
        # Semantic recall is bounded by recency, never by a keyword predicate:
        # a vector nearest-neighbour must not require a lexical hit.
        query = query.order_by(KnowledgeResourceIndex.updated_at.desc(), KnowledgeResource.id)
    if plan.date_range is not None:
        # A date range is a global filter over the source's own instant: the
        # typed structured window when the source has one, otherwise the
        # canonical creation time. It is never inferred from stored text.
        instant = func.coalesce(
            KnowledgeResourceIndex.structured_at, KnowledgeResource.source_created_at
        )
        if plan.date_range.start is not None:
            query = query.where(instant >= plan.date_range.start)
        if plan.date_range.end is not None:
            query = query.where(instant < plan.date_range.end)
    if plan.source_ids:
        query = query.where(KnowledgeResource.source_connection_id.in_(plan.source_ids))
    if plan.types:
        query = query.where(KnowledgeResource.source_type.in_([t.value for t in plan.types]))
    if filters.resource_ids:
        query = query.where(KnowledgeResource.id.notin_(filters.resource_ids))
    if filters.source_ids:
        query = query.where(KnowledgeResource.source_connection_id.notin_(filters.source_ids))
    if filters.types:
        query = query.where(KnowledgeResource.source_type.notin_(filters.types))
    rows = list((await database.execute(query.limit(limit + 1))).all())
    truncated = len(rows) > limit
    candidates: list[RankedCandidate] = []
    for (
        resource_id,
        source_type,
        source_connection_id,
        external_resource_id,
        external_parent_id,
        source_resource_id,
        source_content_hash,
        index_state,
        structured_kind,
        structured_at,
        structured_until,
    ) in rows[:limit]:
        if filters.folders and source_connection_id is not None and external_parent_id:
            if (source_connection_id, external_parent_id) in filters.folders:
                continue
        candidates.append(
            RankedCandidate(
                key=resource_key(source_type, resource_id),
                knowledge_resource_id=resource_id,
                source_type=source_type,
                source_connection_id=source_connection_id,
                external_resource_id=external_resource_id,
                external_parent_id=external_parent_id,
                source_resource_id=source_resource_id,
                indexed_content_hash=source_content_hash,
                index_state=index_state,
                structured_kind=structured_kind,
                structured_at=structured_at,
                structured_until=structured_until,
            )
        )
    return candidates, truncated


async def _text_for(
    database: AsyncSession, resource_ids: list[UUID]
) -> dict[UUID, list[KnowledgeChunk]]:
    if not resource_ids:
        return {}
    chunks = list(
        await database.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.resource_id.in_(resource_ids))
            .order_by(KnowledgeChunk.resource_id, KnowledgeChunk.chunk_index)
            .limit(CHUNK_BOUND)
        )
    )
    grouped: dict[UUID, list[KnowledgeChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.resource_id, []).append(chunk)
    return grouped


def _native_key(resource: EvidenceResource) -> str:
    return resource.key


async def fulltext_retriever(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    filters: ExclusionFilters,
    now: datetime,
) -> RetrieverResult:
    eligible, authority_truncated = await eligible_identities(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
    )
    candidates, content_truncated = await _candidate_rows(
        database,
        workspace_id=workspace_id,
        plan=plan,
        filters=filters,
        eligible_ids=eligible,
        structured_only=False,
        limit=CANDIDATE_BOUND,
    )
    truncated = authority_truncated or content_truncated
    permitted = candidates
    authorized = len(permitted)
    permitted, stale = await hash_fence(database, workspace_id=workspace_id, candidates=permitted)
    searchable = [item for item in permitted if item.index_state == "INDEXED"]
    not_searchable_keys = frozenset(item.key for item in permitted if item.index_state != "INDEXED")
    if not searchable:
        return RetrieverResult(
            ranking=[],
            resources={},
            candidates={},
            examined=authorized,
            truncated=truncated,
            stale_dropped=stale,
            not_searchable_keys=not_searchable_keys,
        )
    rows = list(
        await database.scalars(
            select(KnowledgeResource).where(
                KnowledgeResource.id.in_([c.knowledge_resource_id for c in searchable])
            )
        )
    )
    by_id = {row.id: row for row in rows}
    chunks = await _text_for(database, [c.knowledge_resource_id for c in searchable])
    scored: list[tuple[int, RankedCandidate]] = []
    for candidate in searchable:
        row = by_id.get(candidate.knowledge_resource_id)
        if row is None:
            continue
        if not _topical(plan.terms, row.title, row.normalized_text):
            continue
        scored.append(
            (
                _term_score(plan.terms, row.title, row.normalized_text),
                candidate,
            )
        )
    scored.sort(key=lambda item: (-item[0], item[1].key))
    ranking: list[str] = []
    resources: dict[str, EvidenceResource] = {}
    selected: dict[str, RankedCandidate] = {}
    for _, candidate in scored[:RANK_BOUND]:
        row = by_id.get(candidate.knowledge_resource_id)
        if row is None:
            continue
        ranking.append(candidate.key)
        selected[candidate.key] = candidate
        resources[candidate.key] = _connected_evidence(
            candidate, row, chunks.get(row.id, []), plan.terms
        )
    return RetrieverResult(
        ranking=ranking,
        resources=resources,
        candidates=selected,
        examined=authorized,
        truncated=truncated,
        stale_dropped=stale,
        not_searchable_keys=not_searchable_keys,
    )


def _connected_evidence(
    candidate: RankedCandidate,
    row: KnowledgeResource,
    chunks: list[KnowledgeChunk],
    terms: tuple[str, ...],
) -> EvidenceResource:
    return EvidenceResource(
        source_type=candidate.source_type,
        resource_id=row.id,
        title=row.title,
        excerpts=_excerpts(chunks, terms, title=row.title),
        canonical_url=row.canonical_url,
        source_updated_at=row.source_updated_at,
        source_version=row.source_version,
        fresh_until=stored_utc(row.fresh_until) if row.fresh_until else None,
        indexed_at=stored_utc(row.indexed_at) if row.indexed_at else None,
        provenance={
            "connection_id": str(candidate.source_connection_id),
            "external_resource_id": candidate.external_resource_id,
            "sensitivity": row.sensitivity,
        },
        origin="CONNECTED",
    )


async def structured_retriever(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    filters: ExclusionFilters,
    now: datetime,
) -> RetrieverResult:
    eligible, authority_truncated = await eligible_identities(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
    )
    candidates, content_truncated = await _candidate_rows(
        database,
        workspace_id=workspace_id,
        plan=plan,
        filters=filters,
        eligible_ids=eligible,
        structured_only=True,
        limit=CANDIDATE_BOUND,
    )
    truncated = authority_truncated or content_truncated
    permitted = candidates
    authorized = len(permitted)
    permitted, stale = await hash_fence(database, workspace_id=workspace_id, candidates=permitted)
    searchable = [item for item in permitted if item.index_state == "INDEXED"]
    not_searchable_keys = frozenset(item.key for item in permitted if item.index_state != "INDEXED")
    resources: dict[str, EvidenceResource] = {}
    ranking: list[str] = []
    selected: dict[str, RankedCandidate] = {}
    facts: list[StructuredFact] = []
    if searchable:
        rows = list(
            await database.scalars(
                select(KnowledgeResource).where(
                    KnowledgeResource.id.in_([c.knowledge_resource_id for c in searchable])
                )
            )
        )
        by_id = {row.id: row for row in rows}
        chunks = await _text_for(database, [c.knowledge_resource_id for c in searchable])
        # A date or state window is the query itself; every other query still has
        # to match the stored topic so unrelated typed rows are not returned.
        require_topic = plan.intent.value not in UNTYPED_STRUCTURED_INTENTS
        for candidate in searchable[:RANK_BOUND]:
            row = by_id.get(candidate.knowledge_resource_id)
            if row is None:
                continue
            if require_topic and not _topical(plan.terms, row.title, row.normalized_text):
                continue
            ranking.append(candidate.key)
            selected[candidate.key] = candidate
            evidence = _connected_evidence(candidate, row, chunks.get(row.id, []), plan.terms)
            resources[candidate.key] = evidence
            facts.extend(connected_facts(candidate, row))
    return RetrieverResult(
        ranking=ranking,
        resources=resources,
        candidates=selected,
        examined=authorized,
        truncated=truncated,
        stale_dropped=stale,
        not_searchable_keys=not_searchable_keys,
        facts=facts,
    )


async def publication_check(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    candidates: dict[str, RankedCandidate],
    filters: ExclusionFilters,
) -> tuple[set[str], int]:
    """Re-authorize, re-exclude and re-fence evidence immediately before returning it.

    Current parent, type and source scope are read from the database rather than
    trusted from the earlier candidate copy, so a moved source or a new exclusion
    cannot slip through on stale metadata. Returns the keys that are still
    permitted and the number dropped by authority, exclusion or revision drift.
    """
    kept: set[str] = set()
    dropped = 0
    for key, candidate in candidates.items():
        scope = (
            await database.execute(
                select(
                    KnowledgeResource.source_type,
                    KnowledgeResource.source_connection_id,
                    KnowledgeResource.deleted_at,
                    ConnectorResource.external_parent_id,
                )
                .outerjoin(
                    ConnectorResource,
                    (ConnectorResource.id == KnowledgeResource.source_resource_id)
                    & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id),
                )
                .where(
                    KnowledgeResource.id == candidate.knowledge_resource_id,
                    KnowledgeResource.workspace_id == workspace_id,
                )
                .execution_options(populate_existing=True)
            )
        ).first()
        if scope is None or scope[2] is not None:
            dropped += 1
            continue
        if filters.excludes(
            source_type=scope[0],
            resource_id=candidate.knowledge_resource_id,
            source_connection_id=scope[1],
            external_parent_id=scope[3],
        ):
            dropped += 1
            continue
        if not await can_view_resource(
            database,
            resource_id=candidate.knowledge_resource_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=now,
        ):
            dropped += 1
            continue
        live_hash = await database.scalar(
            select(ConnectorResource.content_hash)
            .where(
                ConnectorResource.id == candidate.source_resource_id,
                ConnectorResource.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            candidate.source_resource_id is None
            or live_hash is None
            or candidate.indexed_content_hash is None
            or live_hash != candidate.indexed_content_hash
        ):
            dropped += 1
            continue
        kept.add(key)
    return kept, dropped


def fuse(*rankings: list[str], limit: int) -> list[tuple[str, float]]:
    """Fuse retriever ranks. Rank order is the only cross-retriever signal."""
    return reciprocal_rank_fusion(list(rankings), k=RRF_K, limit=limit)


SEMANTIC_SIMILARITY_FLOOR = 0.0
# Hard cap on vector rows read for one request, so one caller cannot force an
# unbounded scan of the vector table. Truncation is reported as partial coverage.
SEMANTIC_VECTOR_BOUND = 5_000


def parse_stored_vector(raw: object, dimension: int) -> tuple[float, ...] | None:
    """Read one persisted vector strictly, or return ``None`` when unusable.

    Bools, strings, NaN, infinities and a wrong dimension are rejected instead of
    being coerced, so a corrupt row produces honest incomplete coverage rather
    than an exception or a malformed score.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != dimension:
        return None
    values: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        try:
            number = float(item)
        except OverflowError:
            return None
        if not isfinite(number):
            return None
        values.append(number)
    return tuple(values)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    """Cosine over finite vectors of the same dimension; anything else is omitted.

    Both sides are scaled to unit length with ``hypot`` before the dot product,
    so huge or tiny finite inputs cannot overflow, underflow or collapse to a
    zero vector through squaring.
    """
    if not left or len(left) != len(right):
        return None
    if not all(isfinite(value) for value in left) or not all(isfinite(value) for value in right):
        return None
    left_scale, right_scale = max(map(abs, left)), max(map(abs, right))
    if left_scale == 0.0 or right_scale == 0.0:
        return None
    left = tuple(value / left_scale for value in left)
    right = tuple(value / right_scale for value in right)
    left_norm = hypot(*left)
    right_norm = hypot(*right)
    if left_norm <= 0.0 or right_norm <= 0.0 or not isfinite(left_norm + right_norm):
        return None
    value = sum(
        (first / left_norm) * (second / right_norm)
        for first, second in zip(left, right, strict=True)
    )
    return value if isfinite(value) else None


def _semantic_excerpts(
    chunks: list[KnowledgeChunk], matched_chunk: int
) -> tuple[EvidenceExcerpt, ...]:
    """The matched chunk first, then its stored neighbours in index order."""
    ordered = sorted(
        chunks, key=lambda chunk: (chunk.chunk_index != matched_chunk, chunk.chunk_index)
    )
    excerpts: list[EvidenceExcerpt] = []
    for chunk in ordered[:EXCERPTS_PER_RESOURCE]:
        body = chunk.text_content or ""
        if not body:
            continue
        excerpts.append(
            EvidenceExcerpt(
                text=body,
                start=0,
                end=len(body),
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
            )
        )
    return tuple(excerpts)


async def semantic_retriever(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    filters: ExclusionFilters,
    now: datetime,
    key: SemanticKey,
    query_vector: tuple[float, ...],
    limit: int = CANDIDATE_BOUND,
) -> SemanticEvidence:
    """Rank authorized connected resources by exact-namespace cosine similarity.

    Order of work: authority, exclusions and the canonical revision fence run
    first, then current classification, chunk existence and the stored vector
    revision are applied inside the vector query itself. A forbidden, moved,
    reclassified or rebuilt resource therefore never has its vector JSON read or
    scored. Only the exact ``key`` namespace participates; other namespaces and
    outdated vectors are counted for honest coverage and never mixed.
    """
    if (
        len(query_vector) != key.dimension
        or not any(query_vector)
        or not all(isfinite(value) for value in query_vector)
    ):
        return SemanticEvidence(reference=SEMANTIC_REASON_VECTOR_INVALID, key=key)
    eligible, authority_truncated = await eligible_identities(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
    )
    candidates, content_truncated = await _candidate_rows(
        database,
        workspace_id=workspace_id,
        plan=plan,
        filters=filters,
        eligible_ids=eligible,
        structured_only=False,
        limit=limit,
        match_terms=False,
    )
    truncated = authority_truncated or content_truncated
    authorized = len(candidates)
    permitted, stale = await hash_fence(database, workspace_id=workspace_id, candidates=candidates)
    searchable = [item for item in permitted if item.index_state == "INDEXED"]
    if not searchable:
        return SemanticEvidence(
            available=True,
            reference=SEMANTIC_REASON_NO_VECTORS,
            key=key,
            examined=authorized,
            truncated=truncated,
            stale_dropped=stale,
        )
    identifiers = [item.knowledge_resource_id for item in searchable]
    by_resource_id = {item.knowledge_resource_id: item for item in searchable}
    exact = (
        KnowledgeEmbedding.workspace_id == workspace_id,
        KnowledgeEmbedding.provider == key.provider,
        KnowledgeEmbedding.model == key.model,
        KnowledgeEmbedding.registry_revision == key.registry_revision,
        KnowledgeEmbedding.embedding_version == key.embedding_version,
        KnowledgeEmbedding.dimension == key.dimension,
    )
    index_join = and_(
        KnowledgeResourceIndex.resource_id == KnowledgeEmbedding.resource_id,
        KnowledgeResourceIndex.workspace_id == KnowledgeEmbedding.workspace_id,
    )
    resource_join = and_(
        KnowledgeResource.id == KnowledgeEmbedding.resource_id,
        KnowledgeResource.workspace_id == KnowledgeEmbedding.workspace_id,
    )
    chunk_join = and_(
        KnowledgeChunk.resource_id == KnowledgeEmbedding.resource_id,
        KnowledgeChunk.workspace_id == KnowledgeEmbedding.workspace_id,
        KnowledgeChunk.chunk_index == KnowledgeEmbedding.chunk_index,
    )
    # Only vectors that still match the current index revision, the current
    # trusted classification and an existing current chunk are loaded at all.
    rows = (
        await database.execute(
            select(
                KnowledgeEmbedding.resource_id,
                KnowledgeEmbedding.chunk_index,
                KnowledgeEmbedding.vector,
            )
            .join(KnowledgeResourceIndex, index_join)
            .join(KnowledgeResource, resource_join)
            .join(KnowledgeChunk, chunk_join)
            .where(
                *exact,
                KnowledgeEmbedding.resource_id.in_(identifiers),
                KnowledgeEmbedding.source_content_hash
                == KnowledgeResourceIndex.source_content_hash,
                KnowledgeEmbedding.sensitivity == KnowledgeResource.sensitivity,
                KnowledgeResource.deleted_at.is_(None),
            )
            .order_by(KnowledgeEmbedding.resource_id, KnowledgeEmbedding.chunk_index)
            .limit(SEMANTIC_VECTOR_BOUND + 1)
        )
    ).all()
    vector_truncated = len(rows) > SEMANTIC_VECTOR_BOUND
    rows = rows[:SEMANTIC_VECTOR_BOUND]
    namespace_other = int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeEmbedding)
            .where(
                KnowledgeEmbedding.workspace_id == workspace_id,
                KnowledgeEmbedding.resource_id.in_(identifiers),
                or_(
                    KnowledgeEmbedding.provider != key.provider,
                    KnowledgeEmbedding.model != key.model,
                    KnowledgeEmbedding.registry_revision != key.registry_revision,
                    KnowledgeEmbedding.embedding_version != key.embedding_version,
                    KnowledgeEmbedding.dimension != key.dimension,
                ),
            )
        )
        or 0
    )
    # Counted without reading vector JSON: rows in this namespace whose stored
    # revision, classification or chunk no longer matches the current resource.
    outdated = int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeEmbedding)
            .outerjoin(KnowledgeResourceIndex, index_join)
            .outerjoin(KnowledgeResource, resource_join)
            .outerjoin(KnowledgeChunk, chunk_join)
            .where(
                *exact,
                KnowledgeEmbedding.resource_id.in_(identifiers),
                or_(
                    KnowledgeResourceIndex.id.is_(None),
                    KnowledgeEmbedding.source_content_hash
                    != KnowledgeResourceIndex.source_content_hash,
                    KnowledgeResource.id.is_(None),
                    KnowledgeEmbedding.sensitivity != KnowledgeResource.sensitivity,
                    KnowledgeResource.deleted_at.is_not(None),
                    KnowledgeChunk.id.is_(None),
                ),
            )
        )
        or 0
    )
    best: dict[str, tuple[float, int]] = {}
    scored = 0
    unusable = 0
    for resource_id, chunk_index, raw_vector in rows:
        candidate = by_resource_id.get(resource_id)
        if candidate is None:
            continue
        stored = parse_stored_vector(raw_vector, key.dimension)
        similarity = None if stored is None else cosine_similarity(stored, query_vector)
        if similarity is None:
            unusable += 1
            continue
        scored += 1
        current = best.get(candidate.key)
        if current is None or similarity > current[0]:
            best[candidate.key] = (similarity, chunk_index)
    ranked = [
        (candidate_key, similarity, chunk_index)
        for candidate_key, (similarity, chunk_index) in best.items()
        if similarity > SEMANTIC_SIMILARITY_FLOOR
    ]
    ranked.sort(key=lambda row: (-row[1], row[0]))
    covered = set(best)
    missing = len(by_resource_id) - len(covered)
    truncated = truncated or vector_truncated
    if not ranked:
        if namespace_other and not rows and not outdated:
            reference = SEMANTIC_REASON_NAMESPACE
        elif outdated and not covered:
            reference = SEMANTIC_REASON_OUTDATED
        else:
            reference = SEMANTIC_REASON_NO_VECTORS
        return SemanticEvidence(
            available=True,
            reference=reference,
            key=key,
            scored=0,
            namespace_matched=len(rows),
            namespace_other=namespace_other,
            unusable=unusable,
            missing=missing,
            outdated=outdated,
            vector_truncated=vector_truncated,
            examined=authorized,
            truncated=truncated,
            stale_dropped=stale,
        )
    by_key = {item.key: item for item in searchable}
    selected: dict[str, RankedCandidate] = {}
    ranking: list[str] = []
    matched_chunks: dict[str, int] = {}
    for candidate_key, _, _chunk_index in ranked[:RANK_BOUND]:
        candidate = by_key.get(candidate_key)
        if candidate is None:
            continue
        ranking.append(candidate_key)
        selected[candidate_key] = candidate
    matched_chunks = {row[0]: row[2] for row in ranked}
    chunks = await _text_for(database, [item.knowledge_resource_id for item in selected.values()])
    rows_by_id = {
        row.id: row
        for row in await database.scalars(
            select(KnowledgeResource).where(
                KnowledgeResource.id.in_([item.knowledge_resource_id for item in selected.values()])
            )
        )
    }
    resources: dict[str, EvidenceResource] = {}
    for candidate_key in ranking:
        candidate = selected[candidate_key]
        row = rows_by_id.get(candidate.knowledge_resource_id)
        if row is None:
            continue
        matched_chunk = matched_chunks[candidate_key]
        resources[candidate_key] = EvidenceResource(
            source_type=candidate.source_type,
            resource_id=row.id,
            title=row.title,
            excerpts=_semantic_excerpts(chunks.get(row.id, []), matched_chunk),
            canonical_url=row.canonical_url,
            source_updated_at=row.source_updated_at,
            source_version=row.source_version,
            fresh_until=stored_utc(row.fresh_until) if row.fresh_until else None,
            indexed_at=stored_utc(row.indexed_at) if row.indexed_at else None,
            provenance={
                "connection_id": str(candidate.source_connection_id),
                "external_resource_id": candidate.external_resource_id,
                "sensitivity": row.sensitivity,
            },
            origin="CONNECTED",
        )
    return SemanticEvidence(
        ranking=ranking,
        resources=resources,
        candidates=selected,
        available=True,
        reference=None,
        key=key,
        scored=scored,
        namespace_matched=len(rows),
        namespace_other=namespace_other,
        unusable=unusable,
        missing=missing,
        outdated=outdated,
        vector_truncated=vector_truncated,
        examined=authorized,
        truncated=truncated,
        stale_dropped=stale,
    )
