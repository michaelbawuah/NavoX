"""Current-source projection and bounded cleanup. Events carry no authority.

Stale delivery cannot replace a newer canonical revision. Cleanup removes derived
content only, never source-system data or another user's preference/grant.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeEmbedding,
    KnowledgeEntity,
    KnowledgeResource,
    KnowledgeResourceIndex,
)
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
    WorkspaceMembership,
)
from navox.knowledge.graph import sync_resource_graph
from navox.knowledge.indexing import index_connector_resource, trusted_read_capability
from navox.knowledge.jobs import KnowledgeResourceWork
from navox.knowledge.permissions import effective_read_capabilities, legacy_anchor_active
from navox.knowledge.search_contracts import utc_now


async def clear_derived_resource(
    database: AsyncSession, *, workspace_id: UUID, resource_id: UUID, now: datetime, retire: bool
) -> None:
    resource = await database.scalar(
        select(KnowledgeResource).where(
            KnowledgeResource.id == resource_id, KnowledgeResource.workspace_id == workspace_id
        )
    )
    if resource is None:
        return
    resource.normalized_text = None
    resource.title = None
    resource.canonical_url = None
    if retire:
        resource.deleted_at = now
    for model in (KnowledgeChunk, KnowledgeEmbedding):
        await database.execute(
            delete(model).where(
                model.workspace_id == workspace_id, model.resource_id == resource_id
            )
        )
    # Every anchor of this resource is scoped the same way, and the scoped
    # foreign keys cascade both outgoing and incoming edges. Removing only the
    # resource node would leave orphaned identity anchors behind.
    await database.execute(
        delete(KnowledgeEntity).where(
            KnowledgeEntity.workspace_id == workspace_id,
            KnowledgeEntity.source_resource_id == resource_id,
        )
    )
    index = await database.scalar(
        select(KnowledgeResourceIndex).where(
            KnowledgeResourceIndex.workspace_id == workspace_id,
            KnowledgeResourceIndex.resource_id == resource_id,
        )
    )
    if index:
        index.index_state = "STALE"
        index.source_content_hash = None
        index.text_length = 0
        index.chunk_count = 0
        index.structured_kind = None
        index.structured_at = None
        index.structured_until = None
    await database.flush()


async def process_resource_work(
    database: AsyncSession,
    *,
    payload: KnowledgeResourceWork,
    settings: Settings,
    delete_only: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> str:
    moment = now or utc_now()
    workspace_id, user_id, source_id = (
        UUID(payload.workspace_id),
        UUID(payload.user_id),
        UUID(payload.source_resource_id),
    )
    source = (
        await database.execute(
            select(
                ConnectorResource.connector_connection_id,
                ConnectorResource.resource_type,
                ConnectorResource.content_hash,
                ConnectorResource.deleted,
            ).where(
                ConnectorResource.id == source_id, ConnectorResource.workspace_id == workspace_id
            )
        )
    ).first()
    if source is None:
        return "ABSENT"
    connection = await database.scalar(
        select(ConnectorConnection)
        .where(
            ConnectorConnection.id == source[0],
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if connection is None:
        return "UNAVAILABLE"
    # Match connector authorization: connection before owner. No provider call holds these locks.
    user = await database.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    source = (
        await database.execute(
            select(
                ConnectorResource.connector_connection_id,
                ConnectorResource.resource_type,
                ConnectorResource.content_hash,
                ConnectorResource.deleted,
            ).where(
                ConnectorResource.id == source_id, ConnectorResource.workspace_id == workspace_id
            )
        )
    ).first()
    if source is None:
        return "ABSENT"
    definition = await database.get(
        ConnectorDefinition, connection.connector_definition_id, populate_existing=True
    )
    membership = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    capability = (
        trusted_read_capability(definition.connector_key, source[1], settings)
        if definition
        else None
    )
    eligible = (
        user is not None
        and not user.agent_paused
        and membership is not None
        and capability is not None
        and capability in effective_read_capabilities(definition, connection)
        and await legacy_anchor_active(database, connection, workspace_id)
    )
    projection = await database.scalar(
        select(KnowledgeResource.id).where(
            KnowledgeResource.workspace_id == workspace_id,
            KnowledgeResource.owner_user_id == user_id,
            KnowledgeResource.source_resource_id == source_id,
        )
    )
    if source[3] or not eligible:
        if projection is not None:
            await clear_derived_resource(
                database,
                workspace_id=workspace_id,
                resource_id=projection,
                now=moment,
                retire=bool(source[3] or connection.status == "DISCONNECTED"),
            )
        return "CLEANED" if projection is not None else "UNAVAILABLE"
    if delete_only:
        return "NOT_DELETED"
    if not settings.knowledge_enabled:
        return "DISABLED"
    if payload.expected_content_hash is not None and payload.expected_content_hash != source[2]:
        return "STALE_EVENT"
    # Canonical text is read only after current owned connection authorization.
    resource = await database.get(ConnectorResource, source_id, populate_existing=True)
    if resource is None:
        return "ABSENT"
    try:
        outcome = await index_connector_resource(
            database,
            connection=connection,
            resource=resource,
            now=moment,
            settings=settings,
            force=force,
        )
    except ValueError:
        return "UNAVAILABLE"
    # An old vector is not retained after source content or classification changes.
    await database.execute(
        delete(KnowledgeEmbedding).where(
            KnowledgeEmbedding.workspace_id == workspace_id,
            KnowledgeEmbedding.resource_id == outcome.resource_id,
            (KnowledgeEmbedding.source_content_hash != source[2])
            | (
                KnowledgeEmbedding.sensitivity
                != select(KnowledgeResource.sensitivity)
                .where(KnowledgeResource.id == outcome.resource_id)
                .scalar_subquery()
            ),
        )
    )
    await sync_resource_graph(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_id=outcome.resource_id,
        now=moment,
    )
    return outcome.state


async def cleanup_orphan_entities(database: AsyncSession, *, limit: int = 200) -> int:
    """Graph nodes have polymorphic provenance; purge disconnected cascade orphans.

    A resource node and every identity anchor scoped to it disappear together
    when their canonical resource row is gone, so no anchor can outlive the
    source authority that justified it.
    """
    if not 1 <= limit <= 200:
        raise ValueError("Cleanup batch is out of bounds")
    ids = list(
        await database.scalars(
            select(KnowledgeEntity.id)
            .where(
                ~select(KnowledgeResource.id)
                .where(
                    KnowledgeResource.id == KnowledgeEntity.source_resource_id,
                    KnowledgeResource.workspace_id == KnowledgeEntity.workspace_id,
                )
                .exists(),
            )
            .order_by(KnowledgeEntity.id)
            .limit(limit)
        )
    )
    if ids:
        await database.execute(delete(KnowledgeEntity).where(KnowledgeEntity.id.in_(ids)))
    return len(ids)
