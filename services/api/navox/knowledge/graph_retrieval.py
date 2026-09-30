"""Bounded one-hop structural expansion of already retrieved connected evidence."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.knowledge import KnowledgeResource
from navox.knowledge.entities import IDENTITY_ENTITY_TYPE
from navox.knowledge.exclusions import ExclusionFilters
from navox.knowledge.graph import EntityView, resource_related
from navox.knowledge.retrieval import (
    RetrieverResult,
    _candidate_rows,
    _connected_evidence,
    _text_for,
    hash_fence,
)
from navox.knowledge.search_contracts import EvidenceResource, RelationshipView, RetrievalPlan

SEED_BOUND = 5
EXPANSION_BOUND = 100


def endpoint_key(view: EntityView) -> str:
    """Stable bundle key: a resource key, or an entity key for an identity anchor."""
    if view.entity_type == IDENTITY_ENTITY_TYPE:
        return f"{IDENTITY_ENTITY_TYPE}:{view.id}"
    return f"{view.resource_type}:{view.resource_id}"


@dataclass(frozen=True)
class GraphLink:
    seed_id: UUID
    edge_id: UUID
    revision: str
    relationship: RelationshipView


@dataclass(frozen=True)
class GraphEvidence:
    retrieval: RetrieverResult
    links: list[GraphLink] = field(default_factory=list)


async def graph_retriever(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    seeds: list[EvidenceResource],
    plan: RetrievalPlan,
    filters: ExclusionFilters,
    now: datetime,
) -> GraphEvidence:
    links: list[GraphLink] = []
    identifiers: set[UUID] = set()
    for seed in seeds[:SEED_BOUND]:
        related = await resource_related(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_id=seed.resource_id,
            now=now,
        )
        if related is None:
            continue
        nodes = {node.id: node for node in [related.origin, *related.entities]}
        by_resource: dict[UUID, EntityView] = {}
        for node in nodes.values():
            by_resource.setdefault(node.resource_id, node)
        for edge in related.relationships:
            child, parent = nodes.get(edge.from_entity_id), nodes.get(edge.to_entity_id)
            if child is None or parent is None:
                continue
            identifiers.update((child.resource_id, parent.resource_id))
            evidence_keys: list[str] = []
            for resource_id in edge.evidence_resource_ids or (edge.evidence_resource_id,):
                evidence_node = by_resource.get(resource_id)
                if evidence_node is None:
                    continue
                key = endpoint_key(evidence_node)
                if key not in evidence_keys:
                    evidence_keys.append(key)
            links.append(
                GraphLink(
                    seed.resource_id,
                    edge.id,
                    edge.source_version,
                    RelationshipView(
                        from_key=endpoint_key(child),
                        to_key=endpoint_key(parent),
                        kind=edge.relation_type,
                        evidence_keys=tuple(evidence_keys),
                    ),
                )
            )
    candidates, truncated = await _candidate_rows(
        database,
        workspace_id=workspace_id,
        plan=plan,
        filters=filters,
        eligible_ids=list(identifiers),
        structured_only=False,
        limit=EXPANSION_BOUND,
        match_terms=False,
    )
    permitted, stale = await hash_fence(database, workspace_id=workspace_id, candidates=candidates)
    searchable = [item for item in permitted if item.index_state == "INDEXED"]
    ids = [item.knowledge_resource_id for item in searchable]
    rows = (
        {
            row.id: row
            for row in await database.scalars(
                select(KnowledgeResource).where(
                    KnowledgeResource.id.in_(ids), KnowledgeResource.workspace_id == workspace_id
                )
            )
        }
        if ids
        else {}
    )
    chunks = await _text_for(database, ids)
    evidence = {
        item.key: _connected_evidence(
            item,
            rows[item.knowledge_resource_id],
            chunks.get(item.knowledge_resource_id, []),
            plan.terms,
        )
        for item in searchable
        if item.knowledge_resource_id in rows
    }
    return GraphEvidence(
        RetrieverResult(
            ranking=list(evidence),
            resources=evidence,
            candidates={item.key: item for item in searchable},
            examined=len(candidates),
            truncated=truncated or len(seeds) > SEED_BOUND,
            stale_dropped=stale,
        ),
        links,
    )


async def publish_graph(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    graph: GraphEvidence,
    now: datetime,
) -> tuple[set[str], list[RelationshipView]]:
    """Recheck the edge itself, not just each endpoint's independently valid content."""
    current: dict[UUID, dict[UUID, str]] = {}
    for seed_id in dict.fromkeys(link.seed_id for link in graph.links):
        related = await resource_related(
            database, workspace_id=workspace_id, user_id=user_id, resource_id=seed_id, now=now
        )
        current[seed_id] = (
            {edge.id: edge.source_version for edge in related.relationships} if related else {}
        )
    relationships: dict[tuple[str, str], RelationshipView] = {}
    for link in graph.links:
        if current[link.seed_id].get(link.edge_id) == link.revision:
            row = link.relationship
            relationships[(row.from_key, row.to_key)] = row
    keys = {
        key for relation in relationships.values() for key in (relation.from_key, relation.to_key)
    }
    return keys, list(relationships.values())
