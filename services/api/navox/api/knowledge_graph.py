"""Read-only, current-authority connected graph endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.knowledge import knowledge_enabled
from navox.knowledge.graph import (
    EntityView,
    RelatedView,
    ResolutionView,
    get_entity,
    related_entities,
    resolve_entity,
    resource_related,
)
from navox.knowledge.service import KnowledgeUnavailable

router = APIRouter(tags=["knowledge"])


@router.get("/knowledge/resources/{resource_id}/related", response_model=RelatedView)
async def related_resource(
    resource_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RelatedView:
    knowledge_enabled(settings.knowledge_enabled)
    try:
        result = await resource_related(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            resource_id=resource_id,
        )
    except KnowledgeUnavailable:
        raise HTTPException(403, "Connected knowledge is unavailable") from None
    if result is None:
        raise HTTPException(404, "Related knowledge is unavailable")
    return result


@router.get("/knowledge/entities/{entity_id}", response_model=EntityView)
async def entity_detail(
    entity_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> EntityView:
    knowledge_enabled(settings.knowledge_enabled)
    try:
        result = await get_entity(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            entity_id=entity_id,
        )
    except KnowledgeUnavailable:
        raise HTTPException(403, "Connected knowledge is unavailable") from None
    if result is None:
        raise HTTPException(404, "Related knowledge is unavailable")
    return result


@router.get("/knowledge/entities/{entity_id}/related", response_model=RelatedView)
async def related_entity(
    entity_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RelatedView:
    knowledge_enabled(settings.knowledge_enabled)
    try:
        result = await related_entities(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            entity_id=entity_id,
        )
    except KnowledgeUnavailable:
        raise HTTPException(403, "Connected knowledge is unavailable") from None
    if result is None:
        raise HTTPException(404, "Related knowledge is unavailable")
    return result


@router.get("/knowledge/entities/{entity_id}/resolution", response_model=ResolutionView)
async def entity_resolution(
    entity_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    target_id: Annotated[list[UUID], Query(min_length=1, max_length=64)],
) -> ResolutionView:
    """Compare source-anchored identities. Read-only; no merge or promote."""
    knowledge_enabled(settings.knowledge_enabled)
    try:
        result = await resolve_entity(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            entity_id=entity_id,
            target_ids=tuple(target_id),
        )
    except KnowledgeUnavailable:
        raise HTTPException(403, "Connected knowledge is unavailable") from None
    if result is None:
        raise HTTPException(404, "Related knowledge is unavailable")
    return result
