"""Source-cited structural graph. Stored nodes and edges never grant access.

Only exact canonical parent identities and exact source-asserted container roles
create links. No name matching, model inference or source text is persisted in
graph rows: person/account identities are stored as tenant-scoped digests and
their display names are rebuilt from the current authorized source document.
Traversal is one hop and bounded; both ends, both asserted facts and their
source revisions are checked again before publication.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from navox.connectors.normalization import canonical_resource_to_source_document
from navox.db.knowledge import (
    KnowledgeEntity,
    KnowledgeRelationship,
    KnowledgeResource,
    KnowledgeResourceIndex,
)
from navox.db.models import ConnectorResource, User
from navox.intelligence.contracts import SourceDocument
from navox.knowledge.contracts import aware_utc, stored_utc
from navox.knowledge.entities import (
    COMPUTED_KINDS,
    IDENTITY_CLAIM_BOUND,
    IDENTITY_ENTITY_TYPE,
    ROLE_RELATION_KINDS,
    IdentityClaim,
    claims_for,
    combined_revision,
    display_name,
    identity_entity_key,
    identity_revision,
    node_supports_roles,
    node_type,
    resource_entity_key,
)
from navox.knowledge.exclusions import load_exclusions
from navox.knowledge.indexing import canonical_contract
from navox.knowledge.permissions import can_view_resource
from navox.knowledge.search_contracts import utc_now
from navox.knowledge.service import _require_current_account

GRAPH_SCAN_BOUND = 100
GRAPH_RESULT_BOUND = 20
SAME_THREAD_BOUND = 20
IDENTITY_SCAN_BOUND = 200
IDENTITY_NEIGHBOUR_BOUND = 20
RESOLUTION_BOUND = 5

COVERAGE_BOUNDED = "BOUNDED_SOURCE_STRUCTURE"
COVERAGE_RESOLUTION = "BOUNDED_SOURCE_IDENTITY"

_RELATION_ORDER = {
    "PART_OF": 0,
    "BELONGS_TO": 1,
    "SAME_THREAD": 2,
    "SENT": 3,
    "RECEIVED": 4,
    "ORGANIZED": 5,
    "ATTENDED": 6,
    "SAME_SOURCE_IDENTITY": 7,
}

ResolutionState = Literal["RESOLVED", "POSSIBLE_MATCH", "AMBIGUOUS", "DISTINCT"]
ResolutionBasis = Literal[
    "EXACT_SOURCE_IDENTITY",
    "DIFFERENT_SOURCE_IDENTITY",
    "NAME_ONLY_CANDIDATE",
    "NO_SHARED_SIGNAL",
]


@dataclass(frozen=True)
class Authority:
    """Current snapshot of one source resource's authority and revision fence."""

    resource_id: UUID
    owner_id: UUID
    connection_id: UUID
    external_id: str
    parent_id: str | None
    source_type: str
    provider: str
    content_hash: str
    source_resource_id: UUID
    node_type: str
    revision: str


@dataclass(frozen=True)
class Projection:
    """Whitelisted identity claims rebuilt from one current canonical document."""

    node_type: str
    claims: tuple[IdentityClaim, ...]
    truncated: bool
    identity_revision: str


@dataclass(frozen=True)
class Loaded:
    """One currently viewable entity, with the fence material it came from."""

    entity_id: UUID
    entity_key: str
    version: str | None
    view: EntityView
    authority: Authority
    claim: IdentityClaim | None = None
    anchor_claims: tuple[IdentityClaim, ...] | None = None
    anchor_revision: str | None = None


@dataclass(frozen=True)
class VerifiedClaims:
    claims: tuple[IdentityClaim, ...]
    identity_revision: str


@dataclass(frozen=True)
class Neighbour:
    """One bounded, currently meaningful neighbour of the origin entity."""

    loaded: Loaded
    relationship: RelationshipView
    edge_id: UUID
    edge: KnowledgeRelationship | None = None
    computed: str | None = None


class EntityView(BaseModel):
    id: UUID
    resource_id: UUID
    entity_type: str
    resource_type: str
    state: str
    title: str | None
    canonical_url: str | None


class RelationshipView(BaseModel):
    id: UUID
    from_entity_id: UUID
    to_entity_id: UUID
    relation_type: str
    evidence_resource_id: UUID
    # Both asserted facts are cited when a relation needs two anchors of proof.
    evidence_resource_ids: tuple[UUID, ...] = ()
    source_version: str
    state: str


class RelatedView(BaseModel):
    origin: EntityView
    entities: list[EntityView]
    relationships: list[RelationshipView]
    depth: int = 1
    # Always describes a bounded structural subset, never implies full coverage.
    coverage: str = COVERAGE_BOUNDED
    limit: int = GRAPH_RESULT_BOUND
    truncated: bool = False
    partial_reasons: tuple[str, ...] = ()


class ResolutionEvidence(BaseModel):
    """One permitted anchor of a resolution statement; never a raw address."""

    entity_id: UUID
    resource_id: UUID
    source_type: str
    source_version: str | None
    title: str | None


class EntityResolution(BaseModel):
    entity_id: UUID
    state: ResolutionState
    basis: ResolutionBasis
    explanation: str
    evidence: tuple[ResolutionEvidence, ...]


class ResolutionView(BaseModel):
    subject: EntityView
    state: ResolutionState
    candidates: tuple[EntityResolution, ...]
    coverage: str = COVERAGE_RESOLUTION
    truncated: bool = False
    limit: int = RESOLUTION_BOUND


async def _authority(
    database: AsyncSession, workspace_id: UUID, user_id: UUID, resource_id: UUID, now: datetime
) -> Authority | None:
    row = (
        await database.execute(
            select(
                KnowledgeResource.owner_user_id,
                KnowledgeResource.source_connection_id,
                KnowledgeResource.external_resource_id,
                KnowledgeResource.source_type,
                ConnectorResource.external_parent_id,
                ConnectorResource.content_hash,
                KnowledgeResourceIndex.source_content_hash,
                KnowledgeResourceIndex.index_state,
                ConnectorResource.id,
                ConnectorResource.provider,
                ConnectorResource.resource_type,
            )
            .join(
                ConnectorResource,
                (ConnectorResource.id == KnowledgeResource.source_resource_id)
                & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id)
                & (
                    ConnectorResource.connector_connection_id
                    == KnowledgeResource.source_connection_id
                )
                & (ConnectorResource.external_id == KnowledgeResource.external_resource_id),
            )
            .join(
                KnowledgeResourceIndex,
                (KnowledgeResourceIndex.resource_id == KnowledgeResource.id)
                & (KnowledgeResourceIndex.workspace_id == KnowledgeResource.workspace_id),
            )
            .where(
                KnowledgeResource.id == resource_id,
                KnowledgeResource.workspace_id == workspace_id,
                KnowledgeResource.deleted_at.is_(None),
                ConnectorResource.deleted.is_(False),
            )
        )
    ).first()
    if row is None or not row[5] or row[5] != row[6] or row[7] == "STALE":
        return None
    if not await can_view_resource(
        database, resource_id=resource_id, workspace_id=workspace_id, user_id=user_id, now=now
    ):
        return None
    filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)
    if filters.excludes(
        source_type=row[3],
        resource_id=resource_id,
        source_connection_id=row[1],
        external_parent_id=row[4],
    ):
        return None
    # Parent moves, provider reclassification and a changed content hash all
    # invalidate an edge. Identity metadata is fenced separately per resource.
    revision = sha256(
        repr((str(row[1]), row[2], row[3], row[9], row[10], row[4], row[5])).encode()
    ).hexdigest()
    return Authority(
        resource_id=resource_id,
        owner_id=row[0],
        connection_id=row[1],
        external_id=row[2],
        parent_id=row[4],
        source_type=row[3],
        provider=row[9],
        content_hash=row[5],
        source_resource_id=row[8],
        node_type=node_type(row[3], row[10]),
        revision=revision,
    )


def _source_document(resource: ConnectorResource, connection_id: UUID) -> SourceDocument | None:
    """Canonical SourceDocument of a stored row, or None when unusable."""
    try:
        return canonical_resource_to_source_document(
            canonical_contract(resource), provenance_connection_id=connection_id
        )
    except (ValueError, TypeError, ValidationError):
        return None


async def _projection(
    database: AsyncSession, workspace_id: UUID, authority: Authority
) -> Projection:
    """Rebuild whitelisted identity claims. Called only after current authority."""
    if not node_supports_roles(authority.node_type):
        return Projection(authority.node_type, (), False, authority.revision)
    resource = await database.get(
        ConnectorResource, authority.source_resource_id, populate_existing=True
    )
    if (
        resource is None
        or resource.workspace_id != workspace_id
        or resource.connector_connection_id != authority.connection_id
        or resource.external_id != authority.external_id
        or resource.content_hash != authority.content_hash
    ):
        return Projection(authority.node_type, (), False, identity_revision(authority.revision, ()))
    document = _source_document(resource, authority.connection_id)
    if document is None:
        return Projection(authority.node_type, (), False, identity_revision(authority.revision, ()))
    extracted = claims_for(document, authority.node_type, workspace_id=workspace_id)
    return Projection(
        node_type=authority.node_type,
        claims=extracted.claims,
        truncated=extracted.truncated,
        identity_revision=identity_revision(authority.revision, extracted.claims),
    )


def _claim_for_key(projection: Projection, key: str, resource_id: UUID) -> IdentityClaim | None:
    for claim in projection.claims:
        if identity_entity_key(resource_id, claim) == key:
            return claim
    return None


async def _upsert_entity(
    database: AsyncSession, workspace_id: UUID, authority: Authority
) -> KnowledgeEntity:
    key = resource_entity_key(authority.resource_id)
    rows = list(
        await database.scalars(
            select(KnowledgeEntity)
            .where(
                KnowledgeEntity.workspace_id == workspace_id,
                KnowledgeEntity.canonical_key == key,
            )
            .order_by(KnowledgeEntity.created_at, KnowledgeEntity.id)
            .execution_options(populate_existing=True)
        )
    )
    entity = rows[0] if rows else None
    for duplicate in rows[1:]:
        await database.delete(duplicate)
    if entity is None:
        entity = KnowledgeEntity(
            id=uuid4(),
            workspace_id=workspace_id,
            entity_type=authority.node_type,
            canonical_key=key,
            display_name="",
            state="RESOLVED",
            source_type=authority.source_type,
            source_resource_id=authority.resource_id,
            source_version=authority.revision,
        )
        database.add(entity)
    else:
        entity.entity_type = authority.node_type
        entity.source_version = authority.revision
        entity.source_type = authority.source_type
        entity.display_name = ""
        entity.state = "RESOLVED"
    await database.flush()
    return entity


async def _upsert_identity_entity(
    database: AsyncSession,
    workspace_id: UUID,
    authority: Authority,
    claim: IdentityClaim,
    version: str,
) -> KnowledgeEntity:
    """One source-scoped identity anchor. Only a tenant-scoped digest is stored."""
    key = identity_entity_key(authority.resource_id, claim)
    entity = await database.scalar(
        select(KnowledgeEntity)
        .where(
            KnowledgeEntity.workspace_id == workspace_id,
            KnowledgeEntity.entity_type == IDENTITY_ENTITY_TYPE,
            KnowledgeEntity.canonical_key == key,
        )
        .order_by(KnowledgeEntity.created_at, KnowledgeEntity.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if entity is None:
        entity = KnowledgeEntity(
            id=uuid4(),
            workspace_id=workspace_id,
            entity_type=IDENTITY_ENTITY_TYPE,
            canonical_key=key,
            display_name="",
            state="RESOLVED",
            source_type=authority.source_type,
            source_resource_id=authority.resource_id,
            source_version=version,
        )
        database.add(entity)
    else:
        entity.display_name = ""
        entity.state = "RESOLVED"
        entity.source_type = authority.source_type
        entity.source_version = version
    await database.flush()
    return entity


async def _parent_authority(
    database: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    child: Authority,
    now: datetime,
) -> Authority | None:
    parent_ids = list(
        await database.scalars(
            select(KnowledgeResource.id)
            .where(
                KnowledgeResource.workspace_id == workspace_id,
                KnowledgeResource.owner_user_id == user_id,
                KnowledgeResource.source_connection_id == child.connection_id,
                KnowledgeResource.external_resource_id == child.parent_id,
                KnowledgeResource.deleted_at.is_(None),
            )
            .order_by(KnowledgeResource.id)
            .limit(2)
        )
    )
    if len(parent_ids) != 1:
        return None
    return await _authority(database, workspace_id, user_id, parent_ids[0], now)


async def sync_resource_graph(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    now: datetime | None = None,
) -> UUID | None:
    """Internal owner-only projection, transaction owned by the caller.

    Serialize graph mutations for an owner. Only links between that owner's
    current resources in the same connection are eligible for structural edges.
    Every anchor and edge owned by this resource is rebuilt deterministically, so
    a repeated sync cannot duplicate them and a withdrawn assertion disappears.
    """
    moment = aware_utc(now) if now else utc_now()
    await database.scalar(select(User.id).where(User.id == user_id).with_for_update())
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    child = await _authority(database, workspace_id, user_id, resource_id, moment)
    if child is None or child.owner_id != user_id:
        return None
    entity = await _upsert_entity(database, workspace_id, child)
    projection = await _projection(database, workspace_id, child)
    claimed: list[tuple[IdentityClaim, KnowledgeEntity]] = []
    for claim in projection.claims:
        anchor = await _upsert_identity_entity(
            database, workspace_id, child, claim, projection.identity_revision
        )
        claimed.append((claim, anchor))
    keep_keys = {identity_entity_key(child.resource_id, claim) for claim, _ in claimed}
    anchored = list(
        await database.scalars(
            select(KnowledgeEntity).where(
                KnowledgeEntity.workspace_id == workspace_id,
                KnowledgeEntity.source_resource_id == resource_id,
                KnowledgeEntity.entity_type == IDENTITY_ENTITY_TYPE,
            )
        )
    )
    for row in anchored:
        if row.canonical_key not in keep_keys:
            await database.delete(row)
    # Only this resource's own assertions are rebuilt. Incoming edges owned by
    # another source are re-derived from that source's own current authority.
    own_ids = [entity.id, *[anchor.id for _, anchor in claimed]]
    await database.execute(
        delete(KnowledgeRelationship).where(
            KnowledgeRelationship.workspace_id == workspace_id,
            KnowledgeRelationship.from_entity_id.in_(own_ids),
        )
    )
    if child.parent_id and child.parent_id != child.external_id:
        parent = await _parent_authority(database, workspace_id, user_id, child, moment)
        if parent is not None:
            parent_entity = await _upsert_entity(database, workspace_id, parent)
            database.add(
                KnowledgeRelationship(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    from_entity_id=entity.id,
                    to_entity_id=parent_entity.id,
                    relation_type="PART_OF",
                    state="RESOLVED",
                    evidence_resource_id=resource_id,
                    source_version=child.revision,
                    valid_from=moment,
                )
            )
            if child.node_type == "COURSE_WORK" and parent.node_type == "COURSE":
                database.add(
                    KnowledgeRelationship(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        from_entity_id=entity.id,
                        to_entity_id=parent_entity.id,
                        relation_type="BELONGS_TO",
                        state="RESOLVED",
                        evidence_resource_id=resource_id,
                        source_version=child.revision,
                        valid_from=moment,
                    )
                )
    for claim, anchor in claimed:
        database.add(
            KnowledgeRelationship(
                id=uuid4(),
                workspace_id=workspace_id,
                from_entity_id=anchor.id,
                to_entity_id=entity.id,
                relation_type=claim.relation,
                state="RESOLVED",
                evidence_resource_id=resource_id,
                source_version=projection.identity_revision,
                valid_from=moment,
            )
        )
    await database.flush()
    return entity.id


async def _entity(
    database: AsyncSession, workspace_id: UUID, entity_id: UUID
) -> KnowledgeEntity | None:
    result: KnowledgeEntity | None = await database.scalar(
        select(KnowledgeEntity)
        .options(
            load_only(
                KnowledgeEntity.id,
                KnowledgeEntity.workspace_id,
                KnowledgeEntity.entity_type,
                KnowledgeEntity.canonical_key,
                KnowledgeEntity.state,
                KnowledgeEntity.source_resource_id,
                KnowledgeEntity.source_version,
            )
        )
        .where(
            KnowledgeEntity.id == entity_id,
            KnowledgeEntity.workspace_id == workspace_id,
            KnowledgeEntity.state == "RESOLVED",
        )
        .execution_options(populate_existing=True)
    )

    return result


async def _view(
    database: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    entity: KnowledgeEntity,
    now: datetime,
) -> Loaded | None:
    """Current authority check first; titles and names are read only afterwards."""
    authority = await _authority(database, workspace_id, user_id, entity.source_resource_id, now)
    if authority is None:
        return None
    if entity.entity_type == IDENTITY_ENTITY_TYPE:
        projection = await _projection(database, workspace_id, authority)
        if projection.identity_revision != entity.source_version:
            return None
        claim = _claim_for_key(projection, entity.canonical_key, authority.resource_id)
        if claim is None:
            return None
        return Loaded(
            entity_id=entity.id,
            entity_key=entity.canonical_key,
            version=entity.source_version,
            view=EntityView(
                id=entity.id,
                resource_id=authority.resource_id,
                entity_type=IDENTITY_ENTITY_TYPE,
                resource_type=authority.source_type,
                state="RESOLVED",
                title=claim.display_name,
                canonical_url=None,
            ),
            authority=authority,
            claim=claim,
            anchor_claims=projection.claims,
            anchor_revision=projection.identity_revision,
        )
    if entity.canonical_key != resource_entity_key(authority.resource_id):
        return None
    if entity.entity_type != authority.node_type:
        return None
    if entity.source_version != authority.revision:
        return None
    row = (
        await database.execute(
            select(KnowledgeResource.title, KnowledgeResource.canonical_url).where(
                KnowledgeResource.id == authority.resource_id,
                KnowledgeResource.workspace_id == workspace_id,
            )
        )
    ).one()
    return Loaded(
        entity_id=entity.id,
        entity_key=entity.canonical_key,
        version=entity.source_version,
        view=EntityView(
            id=entity.id,
            resource_id=authority.resource_id,
            entity_type=authority.node_type,
            resource_type=authority.source_type,
            state="RESOLVED",
            title=row[0],
            canonical_url=row[1],
        ),
        authority=authority,
    )


async def _load(
    database: AsyncSession, workspace_id: UUID, user_id: UUID, entity_id: UUID, now: datetime
) -> Loaded | None:
    entity = await _entity(database, workspace_id, entity_id)
    if entity is None:
        return None
    return await _view(database, workspace_id, user_id, entity, now)


async def _load_resource(
    database: AsyncSession, workspace_id: UUID, user_id: UUID, resource_id: UUID, now: datetime
) -> Loaded | None:
    entity_id = await database.scalar(
        select(KnowledgeEntity.id)
        .where(
            KnowledgeEntity.workspace_id == workspace_id,
            KnowledgeEntity.canonical_key == resource_entity_key(resource_id),
        )
        .order_by(KnowledgeEntity.created_at, KnowledgeEntity.id)
        .limit(1)
    )
    if entity_id is None:
        return None
    return await _load(database, workspace_id, user_id, entity_id, now)


async def _current(
    database: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    loaded: Loaded,
    now: datetime,
) -> Loaded | None:
    """Second, independent read of the same fences, including identity metadata."""
    authority = await _authority(database, workspace_id, user_id, loaded.authority.resource_id, now)
    if authority is None or authority != loaded.authority:
        return None
    if loaded.view.entity_type != IDENTITY_ENTITY_TYPE:
        return loaded
    projection = await _projection(database, workspace_id, authority)
    claim = _claim_for_key(projection, loaded.entity_key, authority.resource_id)
    if claim is None or projection.identity_revision != loaded.version:
        return None
    return replace(
        loaded,
        authority=authority,
        claim=claim,
        anchor_claims=projection.claims,
        anchor_revision=projection.identity_revision,
    )


async def _anchor_claims(
    database: AsyncSession, workspace_id: UUID, loaded: Loaded
) -> VerifiedClaims:
    """Whitelisted claims of the anchor resource, verified as currently stored."""
    if loaded.anchor_claims is not None and loaded.anchor_revision is not None:
        return VerifiedClaims(loaded.anchor_claims, loaded.anchor_revision)
    projection = await _projection(database, workspace_id, loaded.authority)
    if not projection.claims:
        return VerifiedClaims((), projection.identity_revision)
    anchors = list(
        await database.scalars(
            select(KnowledgeEntity)
            .where(
                KnowledgeEntity.workspace_id == workspace_id,
                KnowledgeEntity.source_resource_id == loaded.authority.resource_id,
                KnowledgeEntity.entity_type == IDENTITY_ENTITY_TYPE,
            )
            .order_by(KnowledgeEntity.id)
            .limit(IDENTITY_CLAIM_BOUND + 1)
        )
    )
    expected = {
        identity_entity_key(loaded.authority.resource_id, claim) for claim in projection.claims
    }
    if (
        len(anchors) > IDENTITY_CLAIM_BOUND
        or {row.canonical_key for row in anchors} != expected
        or any(row.source_version != projection.identity_revision for row in anchors)
    ):
        # A stale or inconsistent anchor set proves nothing, so it resolves nothing.
        return VerifiedClaims((), projection.identity_revision)
    return VerifiedClaims(projection.claims, projection.identity_revision)


def _containment_holds(
    source: Loaded, target: Loaded, edge: KnowledgeRelationship, *, belongs_to: bool
) -> bool:
    child, parent = source, target
    if child.authority.node_type == IDENTITY_ENTITY_TYPE:
        return False
    if parent.authority.node_type == IDENTITY_ENTITY_TYPE:
        return False
    if belongs_to and (
        child.authority.node_type != "COURSE_WORK" or parent.authority.node_type != "COURSE"
    ):
        return False
    if (
        child.authority.connection_id != parent.authority.connection_id
        or not child.authority.parent_id
        or child.authority.parent_id != parent.authority.external_id
        or child.authority.resource_id == parent.authority.resource_id
    ):
        return False
    return (
        edge.evidence_resource_id == child.authority.resource_id
        and edge.source_version == child.authority.revision
    )


def _persisted_edge_holds(edge: KnowledgeRelationship, source: Loaded, target: Loaded) -> bool:
    """Re-derive the meaning of one persisted edge from current authorities."""
    if edge.relation_type == "PART_OF":
        return _containment_holds(source, target, edge, belongs_to=False)
    if edge.relation_type == "BELONGS_TO":
        return _containment_holds(source, target, edge, belongs_to=True)
    if edge.relation_type in ROLE_RELATION_KINDS:
        if source.claim is None or source.claim.relation != edge.relation_type:
            return False
        if target.view.entity_type == IDENTITY_ENTITY_TYPE:
            return False
        if source.authority.resource_id != target.authority.resource_id:
            return False
        if target.authority.node_type != source.claim.container:
            return False
        return (
            edge.evidence_resource_id == target.authority.resource_id
            and edge.source_version == source.version
        )
    return False


def _ordered_pair(first: Loaded, second: Loaded) -> tuple[Loaded, Loaded]:
    return (first, second) if str(first.entity_id) <= str(second.entity_id) else (second, first)


def _computed_edge_id(kind: str, first: UUID, second: UUID) -> UUID:
    left, right = sorted((str(first), str(second)))
    return uuid5(NAMESPACE_URL, f"navox-knowledge:{kind}:{left}:{right}")


def _computed_neighbour(origin: Loaded, other: Loaded, kind: str, version: str) -> Neighbour:
    source, target = _ordered_pair(origin, other)
    edge_id = _computed_edge_id(kind, source.entity_id, target.entity_id)
    return Neighbour(
        loaded=other,
        edge_id=edge_id,
        computed=kind,
        relationship=RelationshipView(
            id=edge_id,
            from_entity_id=source.entity_id,
            to_entity_id=target.entity_id,
            relation_type=kind,
            evidence_resource_id=source.authority.resource_id,
            evidence_resource_ids=(source.authority.resource_id, target.authority.resource_id),
            source_version=version,
            state="RESOLVED",
        ),
    )


async def _computed_neighbours(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    origin: Loaded,
    now: datetime,
    seen: set[UUID],
) -> tuple[list[Neighbour], tuple[str, ...]]:
    """Bounded structural relations whose evidence is two asserted facts.

    They are derived on read and never persisted against a single anchor, so a
    relation exists only while both asserted facts still hold on both anchors.
    """
    neighbours: list[Neighbour] = []
    partial: list[str] = []
    authority = origin.authority
    if authority.node_type == "EMAIL" and authority.parent_id:
        sibling_ids = list(
            await database.scalars(
                select(KnowledgeResource.id)
                .join(
                    ConnectorResource,
                    (ConnectorResource.id == KnowledgeResource.source_resource_id)
                    & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id),
                )
                .where(
                    KnowledgeResource.workspace_id == workspace_id,
                    KnowledgeResource.owner_user_id == user_id,
                    KnowledgeResource.source_connection_id == authority.connection_id,
                    ConnectorResource.connector_connection_id == authority.connection_id,
                    ConnectorResource.external_parent_id == authority.parent_id,
                    ConnectorResource.deleted.is_(False),
                    KnowledgeResource.deleted_at.is_(None),
                    KnowledgeResource.id != authority.resource_id,
                )
                .order_by(KnowledgeResource.id)
                .limit(SAME_THREAD_BOUND + 1)
            )
        )
        if len(sibling_ids) > SAME_THREAD_BOUND:
            partial.append("THREAD_NEIGHBOURS_TRUNCATED")
        visited_threads: set[UUID] = set()
        for sibling_id in sibling_ids[:SAME_THREAD_BOUND]:
            if len(neighbours) >= GRAPH_RESULT_BOUND:
                partial.append("RELATIONSHIP_BOUND_REACHED")
                break
            if sibling_id in visited_threads or sibling_id in seen:
                continue
            other = await _load_resource(database, workspace_id, user_id, sibling_id, now)
            if other is None:
                continue
            if (
                other.authority.node_type != "EMAIL"
                or other.authority.connection_id != authority.connection_id
                or other.authority.parent_id != authority.parent_id
                or other.authority.resource_id == authority.resource_id
            ):
                continue
            visited_threads.add(sibling_id)
            seen.add(sibling_id)
            seen.add(other.entity_id)
            neighbours.append(
                _computed_neighbour(
                    origin,
                    other,
                    "SAME_THREAD",
                    combined_revision(authority.revision, other.authority.revision, "SAME_THREAD"),
                )
            )
    verified = await _anchor_claims(database, workspace_id, origin)
    claims = verified.claims
    if claims:
        # The other anchor of a shared assertion is the sibling identity anchor
        # for an identity origin, and the sibling resource anchor for a resource
        # origin. Rows of the origin's own resource are never a second anchor.
        identity_origin = origin.view.entity_type == IDENTITY_ENTITY_TYPE
        rows = list(
            await database.scalars(
                select(KnowledgeEntity)
                .join(
                    KnowledgeResource,
                    (KnowledgeResource.id == KnowledgeEntity.source_resource_id)
                    & (KnowledgeResource.workspace_id == KnowledgeEntity.workspace_id),
                )
                .where(
                    KnowledgeEntity.workspace_id == workspace_id,
                    KnowledgeEntity.entity_type == IDENTITY_ENTITY_TYPE,
                    KnowledgeEntity.source_resource_id != origin.authority.resource_id,
                    KnowledgeResource.workspace_id == workspace_id,
                    KnowledgeResource.owner_user_id == user_id,
                    KnowledgeResource.deleted_at.is_(None),
                )
                .order_by(KnowledgeEntity.id)
                .limit(IDENTITY_SCAN_BOUND + 1)
            )
        )
        if len(rows) > IDENTITY_SCAN_BOUND:
            partial.append("IDENTITY_SCAN_TRUNCATED")
        own_keys = {claim.identity_key for claim in claims}
        anchors_seen: set[UUID] = set()
        for row in rows[:IDENTITY_SCAN_BOUND]:
            if len(neighbours) >= max(GRAPH_RESULT_BOUND, IDENTITY_NEIGHBOUR_BOUND):
                partial.append("RELATIONSHIP_BOUND_REACHED")
                break
            if not any(
                row.canonical_key.endswith(f":{claim.kind}:{claim.digest}") for claim in claims
            ):
                continue
            anchor = await _load(database, workspace_id, user_id, row.id, now)
            if anchor is None or anchor.authority.resource_id in anchors_seen:
                continue
            other_claims = await _anchor_claims(database, workspace_id, anchor)
            if not own_keys & {claim.identity_key for claim in other_claims.claims}:
                continue
            anchors_seen.add(anchor.authority.resource_id)
            other = anchor
            if not identity_origin:
                # Cite and relate the sibling resource, not its identity anchor.
                other = await _load_resource(
                    database, workspace_id, user_id, anchor.authority.resource_id, now
                )
                if other is None:
                    continue
            if other.entity_id in seen:
                continue
            seen.add(other.entity_id)
            neighbours.append(
                _computed_neighbour(
                    origin,
                    other,
                    "SAME_SOURCE_IDENTITY",
                    combined_revision(
                        verified.identity_revision,
                        other_claims.identity_revision,
                        "SAME_SOURCE_IDENTITY",
                    ),
                )
            )
    return neighbours, tuple(partial)


async def _computed_edge_holds(
    database: AsyncSession,
    workspace_id: UUID,
    neighbour: Neighbour,
    fresh: dict[UUID, Loaded],
) -> bool:
    kind = neighbour.computed
    if kind is None or kind not in COMPUTED_KINDS:
        return False
    source = fresh.get(neighbour.relationship.from_entity_id)
    target = fresh.get(neighbour.relationship.to_entity_id)
    if source is None or target is None:
        return False
    if source.authority.resource_id == target.authority.resource_id:
        return False
    if kind == "SAME_THREAD":
        if source.authority.node_type != "EMAIL" or target.authority.node_type != "EMAIL":
            return False
        if (
            not source.authority.parent_id
            or source.authority.parent_id != target.authority.parent_id
            or source.authority.connection_id != target.authority.connection_id
        ):
            return False
        return neighbour.relationship.source_version == combined_revision(
            source.authority.revision, target.authority.revision, kind
        )
    left = await _anchor_claims(database, workspace_id, source)
    right = await _anchor_claims(database, workspace_id, target)
    shared = {claim.identity_key for claim in left.claims} & {
        claim.identity_key for claim in right.claims
    }
    if not shared:
        return False
    return neighbour.relationship.source_version == combined_revision(
        left.identity_revision, right.identity_revision, kind
    )


async def _neighbour_holds(
    database: AsyncSession,
    workspace_id: UUID,
    neighbour: Neighbour,
    fresh: dict[UUID, Loaded],
) -> bool:
    if neighbour.computed is not None:
        return await _computed_edge_holds(database, workspace_id, neighbour, fresh)
    edge = neighbour.edge
    source = fresh.get(neighbour.relationship.from_entity_id)
    target = fresh.get(neighbour.relationship.to_entity_id)
    if edge is None or source is None or target is None:
        return False
    return _persisted_edge_holds(edge, source, target)


def _sorted_neighbours(neighbours: list[Neighbour]) -> list[Neighbour]:
    ordered = sorted(
        neighbours,
        key=lambda item: (
            _RELATION_ORDER.get(item.relationship.relation_type, 99),
            str(item.relationship.from_entity_id),
            str(item.relationship.to_entity_id),
            str(item.edge_id),
        ),
    )
    unique: list[Neighbour] = []
    seen_edges: set[UUID] = set()
    for item in ordered:
        if item.edge_id in seen_edges:
            continue
        seen_edges.add(item.edge_id)
        unique.append(item)
    return unique


async def _neighbours_of(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    origin: Loaded,
    now: datetime,
) -> tuple[list[Neighbour], tuple[str, ...]]:
    partial: list[str] = []
    candidates = list(
        await database.scalars(
            select(KnowledgeRelationship)
            .where(
                KnowledgeRelationship.workspace_id == workspace_id,
                KnowledgeRelationship.state == "RESOLVED",
                or_(
                    KnowledgeRelationship.from_entity_id == origin.entity_id,
                    KnowledgeRelationship.to_entity_id == origin.entity_id,
                ),
            )
            .order_by(KnowledgeRelationship.id)
            .limit(GRAPH_SCAN_BOUND + 1)
        )
    )
    if len(candidates) > GRAPH_SCAN_BOUND:
        partial.append("EDGE_SCAN_TRUNCATED")
        candidates = candidates[:GRAPH_SCAN_BOUND]
    neighbours: list[Neighbour] = []
    seen: set[UUID] = {origin.entity_id}
    seen_edges: set[tuple[UUID, str]] = set()
    for edge in candidates:
        if edge.valid_from and stored_utc(edge.valid_from) > now:
            continue
        if edge.valid_until and stored_utc(edge.valid_until) <= now:
            continue
        other_id = (
            edge.to_entity_id if edge.from_entity_id == origin.entity_id else edge.from_entity_id
        )
        if (other_id, edge.relation_type) in seen_edges:
            continue
        other = await _load(database, workspace_id, user_id, other_id, now)
        if other is None:
            continue
        source, target = (
            (origin, other) if edge.from_entity_id == origin.entity_id else (other, origin)
        )
        if not _persisted_edge_holds(edge, source, target):
            continue
        seen_edges.add((other_id, edge.relation_type))
        seen.add(other_id)
        neighbours.append(
            Neighbour(
                loaded=other,
                edge_id=edge.id,
                edge=edge,
                relationship=RelationshipView(
                    id=edge.id,
                    from_entity_id=edge.from_entity_id,
                    to_entity_id=edge.to_entity_id,
                    relation_type=edge.relation_type,
                    evidence_resource_id=edge.evidence_resource_id,
                    evidence_resource_ids=(edge.evidence_resource_id,),
                    source_version=edge.source_version or "",
                    state="RESOLVED",
                ),
            )
        )
        if len(neighbours) >= GRAPH_RESULT_BOUND:
            partial.append("RELATIONSHIP_BOUND_REACHED")
            break
    computed, computed_partial = await _computed_neighbours(
        database, workspace_id=workspace_id, user_id=user_id, origin=origin, now=now, seen=seen
    )
    return _sorted_neighbours([*neighbours, *computed]), tuple([*partial, *computed_partial])


async def get_entity(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    entity_id: UUID,
    now: datetime | None = None,
) -> EntityView | None:
    moment = aware_utc(now) if now else utc_now()
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    loaded = await _load(database, workspace_id, user_id, entity_id, moment)
    if loaded is None:
        return None
    fence = aware_utc(now) if now else utc_now()
    current = await _current(database, workspace_id, user_id, loaded, fence)
    if current is None or current.authority != loaded.authority:
        return None
    return loaded.view


async def related_entities(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    entity_id: UUID,
    now: datetime | None = None,
) -> RelatedView | None:
    moment = aware_utc(now) if now else utc_now()
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    origin = await _load(database, workspace_id, user_id, entity_id, moment)
    if origin is None:
        return None
    neighbours, partial = await _neighbours_of(
        database, workspace_id=workspace_id, user_id=user_id, origin=origin, now=moment
    )
    # A second metadata-only pass protects previously loaded titles, identities
    # and edges if a source was revoked, moved or reindexed while reading.
    loaded_by_id = {origin.entity_id: origin}
    for neighbour in neighbours:
        loaded_by_id[neighbour.loaded.entity_id] = neighbour.loaded
    fresh: dict[UUID, Loaded] = {}
    fence = aware_utc(now) if now else utc_now()
    for loaded_id, loaded in sorted(loaded_by_id.items(), key=lambda item: str(item[0])):
        current = await _current(database, workspace_id, user_id, loaded, fence)
        if current is not None:
            fresh[loaded_id] = current
    if origin.entity_id not in fresh:
        return None
    kept: list[Neighbour] = []
    for neighbour in neighbours:
        if neighbour.loaded.entity_id not in fresh:
            continue
        if not await _neighbour_holds(database, workspace_id, neighbour, fresh):
            continue
        kept.append(neighbour)
    views: list[EntityView] = []
    seen_entities: set[UUID] = set()
    for neighbour in kept:
        if neighbour.loaded.entity_id in seen_entities:
            continue
        seen_entities.add(neighbour.loaded.entity_id)
        views.append(neighbour.loaded.view)
    return RelatedView(
        origin=origin.view,
        entities=views,
        relationships=[neighbour.relationship for neighbour in kept],
        truncated=bool(partial),
        partial_reasons=partial,
    )


async def resource_related(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    now: datetime | None = None,
) -> RelatedView | None:
    entity_id = await database.scalar(
        select(KnowledgeEntity.id)
        .where(
            KnowledgeEntity.workspace_id == workspace_id,
            KnowledgeEntity.canonical_key == resource_entity_key(resource_id),
        )
        .order_by(KnowledgeEntity.created_at, KnowledgeEntity.id)
        .limit(1)
    )
    if entity_id is None:
        return None
    return await related_entities(
        database, workspace_id=workspace_id, user_id=user_id, entity_id=entity_id, now=now
    )


def _bounded_targets(target_ids: tuple[UUID, ...], subject_id: UUID) -> tuple[list[UUID], bool]:
    ordered: list[UUID] = []
    for target in target_ids:
        if target == subject_id or target in ordered:
            continue
        ordered.append(target)
    return ordered[:RESOLUTION_BOUND], len(ordered) > RESOLUTION_BOUND


def _resolution_evidence(loaded: Loaded) -> ResolutionEvidence:
    """Cite the permitted anchor only; titles were read after current authority."""
    return ResolutionEvidence(
        entity_id=loaded.entity_id,
        resource_id=loaded.authority.resource_id,
        source_type=loaded.authority.source_type,
        source_version=loaded.authority.revision,
        title=loaded.view.title,
    )


async def _compare(
    database: AsyncSession, *, workspace_id: UUID, left: Loaded, right: Loaded
) -> EntityResolution:
    """Exact source assertions first; a display name can only ever be a candidate."""
    left_claims = await _anchor_claims(database, workspace_id, left)
    right_claims = await _anchor_claims(database, workspace_id, right)
    left_keys = {claim.identity_key for claim in left_claims.claims}
    right_keys = {claim.identity_key for claim in right_claims.claims}
    evidence = (_resolution_evidence(left), _resolution_evidence(right))
    if left_keys and right_keys:
        if left_keys & right_keys:
            return EntityResolution(
                entity_id=right.entity_id,
                state="RESOLVED",
                basis="EXACT_SOURCE_IDENTITY",
                explanation=(
                    "Both records assert the same source account identity for the same provider."
                ),
                evidence=evidence,
            )
        return EntityResolution(
            entity_id=right.entity_id,
            state="DISTINCT",
            basis="DIFFERENT_SOURCE_IDENTITY",
            explanation=(
                "The records assert different explicit source identities. That statement is "
                "about these source assertions, not about whether one person holds both."
            ),
            evidence=evidence,
        )
    left_label = display_name(left.view.title)
    right_label = display_name(right.view.title)
    if (
        left_label is not None
        and right_label is not None
        and left_label.casefold() == right_label.casefold()
    ):
        return EntityResolution(
            entity_id=right.entity_id,
            state="POSSIBLE_MATCH",
            basis="NAME_ONLY_CANDIDATE",
            explanation=(
                "Both records show the same display name and neither asserts an explicit source "
                "identity, so this is an unconfirmed name candidate."
            ),
            evidence=evidence,
        )
    return EntityResolution(
        entity_id=right.entity_id,
        state="AMBIGUOUS",
        basis="NO_SHARED_SIGNAL",
        explanation="No permitted evidence establishes or excludes a shared source identity here.",
        evidence=evidence,
    )


def _aggregate_state(states: list[ResolutionState]) -> ResolutionState:
    if not states:
        return "AMBIGUOUS"
    unresolved = [state for state in states if state in {"POSSIBLE_MATCH", "AMBIGUOUS"}]
    if "AMBIGUOUS" in unresolved or len(unresolved) > 1:
        return "AMBIGUOUS"
    if unresolved:
        return "POSSIBLE_MATCH"
    return "RESOLVED" if "RESOLVED" in states else "DISTINCT"


async def resolve_entity(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    entity_id: UUID,
    target_ids: tuple[UUID, ...],
    now: datetime | None = None,
) -> ResolutionView | None:
    """Bounded read-only comparison of source-anchored entity identities.

    Every candidate is resolved from the current authority of both anchors and
    nothing here mutates, merges or promotes an identity.
    """
    moment = aware_utc(now) if now else utc_now()
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    subject = await _load(database, workspace_id, user_id, entity_id, moment)
    if subject is None:
        return None
    ordered, truncated = _bounded_targets(target_ids, subject.entity_id)
    fence = aware_utc(now) if now else utc_now()
    results: list[EntityResolution] = []
    for target_id in ordered:
        other = await _load(database, workspace_id, user_id, target_id, moment)
        if other is None:
            continue
        current_subject = await _current(database, workspace_id, user_id, subject, fence)
        current_other = await _current(database, workspace_id, user_id, other, fence)
        if current_subject is None or current_other is None:
            continue
        results.append(
            await _compare(
                database, workspace_id=workspace_id, left=current_subject, right=current_other
            )
        )
    return ResolutionView(
        subject=subject.view,
        state=_aggregate_state([result.state for result in results]),
        candidates=tuple(results),
        truncated=truncated,
    )
