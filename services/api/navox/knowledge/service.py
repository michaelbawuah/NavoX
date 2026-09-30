"""Connected search orchestration for SPEC-007 phases 2/3.

Search never performs external actions and never generates an answer. Every call
re-reads membership, re-checks authority before reading stored text, and checks
authority, exclusions and the source revision again before returning anything.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from navox.core.settings import Settings
from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeResource,
    KnowledgeResourceIndex,
    KnowledgeResourcePermission,
)
from navox.db.models import ConnectorResource, User, WorkspaceMembership
from navox.knowledge.adapters import native_evidence
from navox.knowledge.contracts import Permission, ResourceType, aware_utc, stored_utc
from navox.knowledge.exclusions import (
    ExclusionFilters,
    load_exclusions,
)
from navox.knowledge.permissions import (
    RESOURCE_AUTHORITY_COLUMNS,
    can_view_resource,
)
from navox.knowledge.planner import interpret, suggested_followups
from navox.knowledge.recent import record_recent_search
from navox.knowledge.retrieval import (
    CANDIDATE_BOUND,
    RANK_BOUND,
    RetrieverResult,
    SemanticEvidence,
    fulltext_retriever,
    fuse,
    publication_check,
    structured_retriever,
)
from navox.knowledge.search_contracts import (
    SEMANTIC_REASON_MISSING,
    SEMANTIC_REASON_NAMESPACE,
    SEMANTIC_REASON_NO_VECTORS,
    SEMANTIC_REASON_OUTDATED,
    SEMANTIC_REASON_PARTIAL,
    AnswerState,
    Coverage,
    EvidenceBundleV1,
    EvidenceResource,
    ResourceDetail,
    ResourceDetailChunk,
    RetrieverMode,
    SearchMode,
    SearchRequest,
    SearchResponse,
    StructuredFact,
    utc_now,
)

UNAVAILABLE_MODES: tuple[RetrieverMode, ...] = (RetrieverMode.SEMANTIC, RetrieverMode.GRAPH)
EMPTY_RETRIEVER = RetrieverResult(
    ranking=[], resources={}, candidates={}, examined=0, truncated=False
)

# Non-sensitive reason codes for coverage this phase cannot serve.
REASON_NO_CONTENT = "SOURCE_CONTENT_NOT_RETAINED"
REASON_SEMANTIC = "SEMANTIC_UNAVAILABLE"
REASON_GRAPH = "GRAPH_UNAVAILABLE"
REASON_NATIVE_FILTERED = "CONNECTED_SOURCE_FILTER_EXCLUDES_NATIVE_DOMAINS"


class KnowledgeUnavailable(ValueError):
    """Raised when the requesting account cannot use connected search."""


async def _require_current_account(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> None:
    user = await database.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    member = await database.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    if user is None or member is None or user.agent_paused:
        raise KnowledgeUnavailable("Search is unavailable for this account")


def _native_truncated(native_resources: int, limit: int) -> bool:
    return native_resources > limit


async def search_knowledge(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request: SearchRequest,
    now: datetime | None = None,
    settings: Settings | None = None,
    record_recent: bool = True,
    semantic: SemanticEvidence | None = None,
) -> SearchResponse:
    """Run one bounded, permission-filtered search.

    ``semantic`` carries the ranking of an already-paid, already-fenced semantic
    retrieval. It only adds ranks: authority, exclusions and the revision fence
    are re-checked here for every candidate, exactly as for lexical results.
    """
    moment = aware_utc(now) if now is not None else utc_now()
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    plan = interpret(request)
    filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)

    fulltext = EMPTY_RETRIEVER
    structured = EMPTY_RETRIEVER
    if RetrieverMode.FULLTEXT in plan.retrievers:
        fulltext = await fulltext_retriever(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            plan=plan,
            filters=filters,
            now=moment,
        )
    if RetrieverMode.STRUCTURED in plan.retrievers:
        structured = await structured_retriever(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            plan=plan,
            filters=filters,
            now=moment,
        )
    # Native domains answer ordinary keyword queries too, not only typed ones.
    native = await native_evidence(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        plan=plan,
        filters=filters,
        now=moment,
        settings=settings,
    )

    # Import at call time: graph read authority shares this module's account guard.
    from navox.knowledge.graph_retrieval import graph_retriever, publish_graph

    graph = None
    if RetrieverMode.GRAPH in plan.retrievers:
        seeds = list(
            {
                resource.key: resource
                for resource in [
                    *fulltext.resources.values(),
                    *structured.resources.values(),
                    *(semantic.resources.values() if semantic is not None else []),
                ]
            }.values()
        )
        graph = await graph_retriever(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            seeds=seeds,
            plan=plan,
            filters=filters,
            now=moment,
        )
    graph_result = graph.retrieval if graph is not None else EMPTY_RETRIEVER
    native_resources = {resource.key: resource for resource in native.resources}
    native_ranking = [resource.key for resource in native.resources]
    semantic_resources = semantic.resources if semantic is not None else {}
    semantic_mask = [key for key in (semantic.ranking if semantic is not None else []) if key]
    rankings = [
        ranking
        for ranking in (
            fulltext.ranking,
            structured.ranking,
            native_ranking,
            semantic_mask,
            graph_result.ranking,
        )
        if ranking
    ]
    fused = fuse(*rankings, limit=RANK_BOUND) if rankings else []
    candidates = {
        **fulltext.candidates,
        **structured.candidates,
        **graph_result.candidates,
        **(semantic.candidates if semantic is not None else {}),
    }

    # Publication re-checks also apply to native evidence, current exclusions and
    # the requesting account, so nothing removed mid-flight is returned.
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    published_filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)
    permitted_keys, stale_dropped = await publication_check(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        now=moment,
        candidates=candidates,
        filters=published_filters,
    )
    rechecked_native = await native_evidence(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        plan=plan,
        filters=published_filters,
        now=moment,
        settings=settings,
    )
    # Use the rechecked native values and facts, never the earlier copies, so a
    # changed revision, text or right cannot leak stale native evidence.
    allowed_native = {resource.key: resource for resource in rechecked_native.resources}

    graph_keys, graph_relationships = (
        await publish_graph(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            graph=graph,
            now=moment if now is not None else utc_now(),
        )
        if graph is not None
        else (set(), [])
    )
    independent_keys = set(fulltext.resources) | set(structured.resources) | set(semantic_resources)
    ordered: list[EvidenceResource] = []
    for key, _score in fused:
        if key in candidates and key not in permitted_keys:
            continue
        if key in graph_result.resources and key not in independent_keys and key not in graph_keys:
            continue
        if key in native_resources and key not in allowed_native:
            continue
        found = (
            fulltext.resources.get(key)
            or structured.resources.get(key)
            or semantic_resources.get(key)
            or graph_result.resources.get(key)
            or allowed_native.get(key)
        )
        if found is None:
            continue
        ordered.append(found)
    page = ordered[request.offset : request.offset + request.limit]

    from navox.knowledge.conflicts import detect_conflicts

    conflicts = await detect_conflicts(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_ids=[resource.resource_id for resource in page if resource.origin == "CONNECTED"],
        now=moment if now is not None else None,
    )
    final_keys, final_stale = await publication_check(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        now=moment if now is not None else utc_now(),
        candidates=candidates,
        filters=await load_exclusions(database, workspace_id=workspace_id, user_id=user_id),
    )
    page = [
        resource
        for resource in page
        if resource.origin != "CONNECTED" or resource.key in final_keys
    ]
    page_ids = {resource.resource_id for resource in page}
    conflicts = tuple(
        conflict
        for conflict in conflicts
        if all(value.resource_id in page_ids for value in conflict.values)
    )
    stale_dropped += final_stale
    fact_keys = {resource.key for resource in page}
    facts = [
        fact
        for fact in [*structured.facts, *rechecked_native.facts]
        if resource_key_of(fact) in fact_keys
    ]
    not_searchable = len(fulltext.not_searchable_keys | structured.not_searchable_keys)
    partial_reasons: list[str] = []
    if any(
        resource.fresh_until is not None and stored_utc(resource.fresh_until) <= moment
        for resource in page
    ):
        partial_reasons.append("SOURCE_STALE")
    if not_searchable > 0:
        partial_reasons.append(REASON_NO_CONTENT)
    partial_reasons.extend(_semantic_reasons(semantic))
    partial_reasons.append("GRAPH_BOUNDED_SOURCE_STRUCTURE" if graph is not None else REASON_GRAPH)
    if plan.source_ids:
        partial_reasons.append(REASON_NATIVE_FILTERED)
    truncated = (
        fulltext.truncated
        or structured.truncated
        or native.truncated
        or graph_result.truncated
        or (semantic.truncated if semantic is not None else False)
        or (request.offset + request.limit < len(ordered))
    )
    from navox.knowledge.source_health import source_issues

    issues = await source_issues(
        database, workspace_id=workspace_id, user_id=user_id, plan=plan, filters=published_filters
    )
    if issues:
        partial_reasons.append("SOURCE_UNAVAILABLE")
    coverage = Coverage(
        source_issues=issues,
        candidate_bound=CANDIDATE_BOUND,
        examined=fulltext.examined
        + structured.examined
        + graph_result.examined
        + (semantic.examined if semantic is not None else 0),
        returned=len(page),
        truncated=truncated,
        stale_dropped=stale_dropped
        + fulltext.stale_dropped
        + structured.stale_dropped
        + graph_result.stale_dropped
        + (semantic.stale_dropped if semantic is not None else 0),
        exclusions_applied=published_filters.count,
        not_searchable=not_searchable,
        partial_reasons=tuple(partial_reasons),
    )
    trace_id = uuid4().hex
    if record_recent:
        await record_recent_search(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            mode=plan.mode,
            query=request.query,
            result_count=len(page),
            now=moment,
        )
        await database.commit()
    return SearchResponse(
        interpreted_mode=plan.mode,
        intent=plan.intent,
        results=tuple(page),
        structured_facts=tuple(facts),
        conflicts=conflicts,
        relationships=tuple(
            relation
            for relation in graph_relationships
            if relation.from_key in fact_keys and relation.to_key in fact_keys
        ),
        answer=None,
        answer_state=(
            AnswerState.UNAVAILABLE if plan.mode is SearchMode.ASK else AnswerState.NOT_REQUESTED
        ),
        suggested_followups=suggested_followups(plan, returned=len(page)),
        trace_id=trace_id,
        session_id=None,
        coverage=coverage,
        unavailable_modes=tuple(
            mode
            for mode in UNAVAILABLE_MODES
            if not (mode is RetrieverMode.SEMANTIC and semantic is not None and semantic.available)
            and not (mode is RetrieverMode.GRAPH and graph is not None)
        ),
        exclusion_count=published_filters.count,
    )


def _semantic_reasons(semantic: SemanticEvidence | None) -> list[str]:
    """Non-sensitive coverage codes for this request's semantic path."""
    if semantic is None or not semantic.available:
        return [REASON_SEMANTIC]
    reasons: list[str] = []
    if semantic.reference == SEMANTIC_REASON_NAMESPACE or (
        semantic.namespace_other and not semantic.namespace_matched
    ):
        reasons.append(SEMANTIC_REASON_NAMESPACE)
    elif semantic.reference == SEMANTIC_REASON_OUTDATED or semantic.outdated:
        reasons.append(SEMANTIC_REASON_OUTDATED)
    elif semantic.reference == SEMANTIC_REASON_NO_VECTORS:
        reasons.append(SEMANTIC_REASON_NO_VECTORS)
    elif semantic.reference is not None:
        reasons.append(REASON_SEMANTIC)
    if semantic.missing:
        # Authorized current resources with no usable vector in this namespace.
        reasons.append(SEMANTIC_REASON_MISSING)
    if (
        semantic.unusable
        or semantic.vector_truncated
        or semantic.truncated
        or semantic.stale_dropped
        or (semantic.namespace_other and semantic.namespace_matched)
    ):
        reasons.append(SEMANTIC_REASON_PARTIAL)
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped


def resource_key_of(fact: StructuredFact) -> str:
    return f"{fact.source_type}:{fact.resource_id}"


def _permission_snapshot_id(*, workspace_id: UUID, user_id: UUID, trace_id: str) -> str:
    """Opaque, collision-resistant snapshot label tied to the retrieval trace.

    It grants nothing and is not derived by truncating the trace, so the trace
    identifier is never partially leaked or lost.
    """
    digest = sha256(f"{trace_id}:{workspace_id}:{user_id}:{uuid4().hex}".encode()).hexdigest()
    return f"ps-{digest[:32]}"


def evidence_bundle(
    *,
    response: SearchResponse,
    query: str,
    workspace_id: UUID,
    user_id: UUID,
) -> EvidenceBundleV1:
    """Provider-independent bundle for later SPEC-008 consumers."""
    return EvidenceBundleV1(
        query=query,
        resources=response.results,
        structured_facts=response.structured_facts,
        relationships=response.relationships,
        conflicts=response.conflicts,
        freshness_summary={
            "resources": len(response.results),
            "structured_facts": len(response.structured_facts),
        },
        permission_snapshot_id=_permission_snapshot_id(
            workspace_id=workspace_id,
            user_id=user_id,
            trace_id=response.trace_id,
        ),
        retrieval_trace_id=response.trace_id,
    )


def _excluded(
    filters: ExclusionFilters,
    *,
    source_type: str,
    resource_id: UUID,
    source_connection_id: UUID | None,
    external_parent_id: str | None,
) -> bool:
    return filters.excludes(
        source_type=source_type,
        resource_id=resource_id,
        source_connection_id=source_connection_id,
        external_parent_id=external_parent_id,
    )


async def load_resource_detail(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    now: datetime | None = None,
) -> ResourceDetail | None:
    """Resource detail for one currently permitted, current-revision resource.

    Authority, exclusions and the source revision are all checked before any
    stored text is read and again before the detail is returned, so a denied or
    changed resource never yields a cached title, URL or chunk.
    """
    moment = aware_utc(now) if now is not None else utc_now()
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)

    authority = (
        await database.execute(
            select(
                KnowledgeResource.id,
                KnowledgeResource.source_type,
                KnowledgeResource.source_connection_id,
                KnowledgeResource.external_resource_id,
                KnowledgeResource.deleted_at,
                ConnectorResource.external_parent_id,
            )
            .outerjoin(
                ConnectorResource,
                (ConnectorResource.id == KnowledgeResource.source_resource_id)
                & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id),
            )
            .where(
                KnowledgeResource.id == resource_id,
                KnowledgeResource.workspace_id == workspace_id,
            )
        )
    ).first()
    if authority is None:
        return None
    (
        _resource_id,
        source_type,
        source_connection_id,
        _external_resource_id,
        deleted_at,
        external_parent_id,
    ) = authority
    if deleted_at is not None:
        return None
    if _excluded(
        filters,
        source_type=source_type,
        resource_id=resource_id,
        source_connection_id=source_connection_id,
        external_parent_id=external_parent_id,
    ):
        return None
    if not await _authorized(database, workspace_id, user_id, resource_id, moment):
        return None
    index_row = await database.scalar(
        select(KnowledgeResourceIndex).where(
            KnowledgeResourceIndex.resource_id == resource_id,
            KnowledgeResourceIndex.workspace_id == workspace_id,
        )
    )
    if index_row is None or not await _revision_current(
        database, workspace_id=workspace_id, resource_id=resource_id, index_row=index_row
    ):
        return None
    # The derived chunks belong to exactly this stored revision.
    chunks_hash = index_row.source_content_hash
    chunks_state = index_row.index_state
    chunks: list[KnowledgeChunk] = []
    if chunks_state == "INDEXED":
        chunks = list(
            await database.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.resource_id == resource_id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        )
    resource = await database.scalar(
        select(KnowledgeResource)
        .options(
            load_only(
                *RESOURCE_AUTHORITY_COLUMNS,
                KnowledgeResource.title,
                KnowledgeResource.canonical_url,
                KnowledgeResource.sensitivity,
                KnowledgeResource.source_updated_at,
                KnowledgeResource.source_version,
                KnowledgeResource.fresh_until,
                KnowledgeResource.indexed_at,
            )
        )
        .where(
            KnowledgeResource.id == resource_id,
            KnowledgeResource.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if resource is None:
        return None
    # Publication re-check: authority, exclusions and the revision fence again.
    if not await _authorized(database, workspace_id, user_id, resource_id, moment):
        return None
    # Re-read the index state, so a rebuild between chunk load and publication
    # cannot serve chunks that belong to a different revision.
    fresh_index = await database.scalar(
        select(KnowledgeResourceIndex)
        .where(
            KnowledgeResourceIndex.resource_id == resource_id,
            KnowledgeResourceIndex.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        fresh_index is None
        or fresh_index.index_state != chunks_state
        or fresh_index.source_content_hash != chunks_hash
    ):
        return None
    if not await _revision_current(
        database,
        workspace_id=workspace_id,
        resource_id=resource_id,
        index_row=fresh_index,
        expected_hash=chunks_hash,
    ):
        return None
    published_filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)
    current_scope = (
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
                KnowledgeResource.id == resource_id,
                KnowledgeResource.workspace_id == workspace_id,
            )
        )
    ).first()
    if current_scope is None or current_scope[2] is not None:
        return None
    if _excluded(
        published_filters,
        source_type=current_scope[0],
        resource_id=resource_id,
        source_connection_id=current_scope[1],
        external_parent_id=current_scope[3],
    ):
        return None
    grants = int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeResourcePermission)
            .where(
                KnowledgeResourcePermission.resource_id == resource_id,
                KnowledgeResourcePermission.permission == Permission.VIEW.value,
            )
        )
        or 0
    )
    return ResourceDetail(
        resource_id=resource.id,
        source_type=ResourceType(resource.source_type),
        title=resource.title,
        canonical_url=resource.canonical_url,
        sensitivity=resource.sensitivity,
        source_updated_at=resource.source_updated_at,
        source_version=resource.source_version,
        fresh_until=resource.fresh_until,
        indexed_at=resource.indexed_at,
        index_state=index_row.index_state,
        structured_kind=index_row.structured_kind,
        structured_at=index_row.structured_at,
        chunks=tuple(
            ResourceDetailChunk(
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
                page_number=chunk.page_number,
                text_content=chunk.text_content,
                token_count=chunk.token_count,
            )
            for chunk in chunks
        ),
        provenance={
            "connection_id": str(resource.source_connection_id),
            "external_resource_id": resource.external_resource_id,
            "view_grants": str(grants),
            "owner_user_id": str(resource.owner_user_id),
        },
    )


async def _authorized(
    database: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    now: datetime,
) -> bool:
    return await can_view_resource(
        database,
        resource_id=resource_id,
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
    )


async def _revision_current(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    resource_id: UUID,
    index_row: KnowledgeResourceIndex,
    expected_hash: str | None = None,
) -> bool:
    """Every state requires a stored revision that still matches the source.

    A missing or stale hash denies, including for ``NO_CONTENT``: the detail must
    not describe a record whose canonical revision moved.
    """
    if index_row.source_content_hash is None:
        return False
    if expected_hash is not None and index_row.source_content_hash != expected_hash:
        return False
    live = (
        await database.execute(
            select(ConnectorResource.content_hash)
            .join(
                KnowledgeResource,
                (KnowledgeResource.source_resource_id == ConnectorResource.id)
                & (KnowledgeResource.workspace_id == ConnectorResource.workspace_id),
            )
            .where(
                KnowledgeResource.id == resource_id,
                KnowledgeResource.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if live is None:
        return False
    return bool(live[0] == index_row.source_content_hash)
