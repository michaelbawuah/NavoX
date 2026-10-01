"""Temporal activities re-read scope and authority; workflow IDs are not grants."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from temporalio import activity

from navox.core.settings import get_settings
from navox.db.knowledge import KnowledgeEmbeddingRequest, KnowledgeResource, KnowledgeResourceIndex
from navox.db.models import ConnectorConnection, ConnectorResource
from navox.db.session import get_session_factory
from navox.knowledge.ask import purge_expired_history
from navox.knowledge.embeddings import (
    EMBEDDING_VERSION,
    SemanticDisabled,
    SemanticQuotaExceeded,
    SemanticRequestReplay,
    SemanticUnavailable,
    embed_resource,
)
from navox.knowledge.jobs import KnowledgeEmbeddingWork, KnowledgePage, KnowledgeResourceWork
from navox.knowledge.lifecycle import cleanup_orphan_entities, process_resource_work

PAGE_SIZE = 50


@activity.defn
async def knowledge_source_page_activity(after: str | None) -> KnowledgePage:
    # Includes paused/disconnected owners and disabled feature: cleanup still runs.
    async with get_session_factory()() as database:
        query = select(
            ConnectorResource.id,
            ConnectorResource.workspace_id,
            ConnectorConnection.user_id,
            ConnectorResource.content_hash,
        ).join(
            ConnectorConnection,
            (ConnectorConnection.id == ConnectorResource.connector_connection_id)
            & (ConnectorConnection.workspace_id == ConnectorResource.workspace_id),
        )
        if after is not None:
            query = query.where(ConnectorResource.id > UUID(after))
        rows = (await database.execute(query.order_by(ConnectorResource.id).limit(PAGE_SIZE))).all()
        await cleanup_orphan_entities(database)
        await purge_expired_history(database)
        await database.commit()
        return KnowledgePage(
            resources=[
                KnowledgeResourceWork(str(row[1]), str(row[2]), str(row[0]), row[3]) for row in rows
            ],
            after=str(rows[-1][0]) if len(rows) == PAGE_SIZE else None,
        )


async def _process(
    payload: KnowledgeResourceWork, *, delete_only: bool = False, force: bool = False
) -> str:
    async with get_session_factory()() as database:
        result = await process_resource_work(
            database, payload=payload, settings=get_settings(), delete_only=delete_only, force=force
        )
        await database.commit()
        return result


@activity.defn
async def knowledge_resource_activity(payload: KnowledgeResourceWork) -> str:
    return await _process(payload)


@activity.defn
async def knowledge_delete_activity(payload: KnowledgeResourceWork) -> str:
    return await _process(payload, delete_only=True)


@activity.defn
async def knowledge_reindex_activity(payload: KnowledgeResourceWork) -> str:
    return await _process(payload, force=True)


@activity.defn
async def knowledge_embedding_activity(payload: KnowledgeEmbeddingWork) -> str:
    # Each chunk has a durable request id. Activity retries cannot buy twice.
    async with get_session_factory()() as database:
        try:
            report = await embed_resource(
                database,
                workspace_id=UUID(payload.workspace_id),
                user_id=UUID(payload.user_id),
                resource_id=UUID(payload.resource_id),
                request_id=UUID(payload.request_id),
                chunk_index=payload.chunk_index,
                settings=get_settings(),
            )
        except SemanticQuotaExceeded:
            return "QUOTA_EXCEEDED"
        except SemanticRequestReplay:
            return "ALREADY_RESERVED"
        except (SemanticDisabled, SemanticUnavailable):
            return "UNAVAILABLE"
        return report.status


@activity.defn
async def knowledge_pending_embeddings_activity(
    payload: KnowledgeResourceWork,
) -> list[KnowledgeEmbeddingWork]:
    """An enabled index queues a bounded chunk batch; execution rechecks all authority.

    Deterministic per-revision IDs make reconciliation/retries incapable of paying
    twice. Namespace upgrades use explicit reindex embedding jobs with new IDs.
    """
    settings = get_settings()
    if not settings.knowledge_enabled or not settings.knowledge_semantic_enabled:
        return []
    workspace_id, user_id = UUID(payload.workspace_id), UUID(payload.user_id)
    async with get_session_factory()() as database:
        row = (
            await database.execute(
                select(
                    KnowledgeResource.id,
                    KnowledgeResourceIndex.source_content_hash,
                    KnowledgeResourceIndex.chunk_count,
                )
                .join(
                    KnowledgeResourceIndex,
                    KnowledgeResourceIndex.resource_id == KnowledgeResource.id,
                )
                .where(
                    KnowledgeResource.workspace_id == workspace_id,
                    KnowledgeResource.owner_user_id == user_id,
                    KnowledgeResource.source_resource_id == UUID(payload.source_resource_id),
                    KnowledgeResource.deleted_at.is_(None),
                    KnowledgeResourceIndex.index_state == "INDEXED",
                )
            )
        ).first()
        if row is None or row[1] != payload.expected_content_hash:
            return []
        pending: list[KnowledgeEmbeddingWork] = []
        for chunk_index in range(min(row[2], 200)):
            request_id = uuid5(
                NAMESPACE_URL,
                f"navox:knowledge:{workspace_id}:{user_id}:{row[0]}:{row[1]}:{chunk_index}:{EMBEDDING_VERSION}",
            )
            exists = await database.scalar(
                select(KnowledgeEmbeddingRequest.id).where(
                    KnowledgeEmbeddingRequest.workspace_id == workspace_id,
                    KnowledgeEmbeddingRequest.user_id == user_id,
                    KnowledgeEmbeddingRequest.request_id == request_id,
                )
            )
            if exists is None:
                pending.append(
                    KnowledgeEmbeddingWork(
                        str(workspace_id), str(user_id), str(row[0]), str(request_id), chunk_index
                    )
                )
                if len(pending) >= min(settings.knowledge_semantic_hourly_quota, 20):
                    break
        return pending
