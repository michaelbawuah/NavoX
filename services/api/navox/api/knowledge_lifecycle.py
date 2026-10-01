"""Explicit owner refresh through existing connector dispatch and quotas."""

from typing import Literal
from uuid import UUID, uuid5

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import ManagedSyncCommand, require_origin, sync_connection
from navox.api.knowledge import knowledge_enabled
from navox.db.knowledge import KnowledgeResource
from navox.db.models import ConnectorConnection, ConnectorDefinition, ConnectorResource
from navox.knowledge.contracts import stored_utc
from navox.knowledge.exclusions import load_exclusions
from navox.knowledge.permissions import can_view_resource
from navox.knowledge.planner import interpret
from navox.knowledge.search_contracts import (
    Freshness,
    LiveSearchRequest,
    SearchRefresh,
    SearchResponse,
    utc_now,
)
from navox.knowledge.service import KnowledgeUnavailable, search_knowledge

router = APIRouter(tags=["knowledge"])


class RefreshCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class RefreshView(BaseModel):
    resource_id: UUID
    state: Literal["REFRESHING", "ERROR"]
    detail: str


@router.post(
    "/knowledge/resources/{resource_id}/refresh", response_model=RefreshView, status_code=202
)
async def refresh_resource(
    resource_id: UUID,
    payload: RefreshCommand,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> RefreshView:
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    workspace_id, user_id = account.workspace.id, account.user.id
    row = (
        await database.execute(
            select(
                KnowledgeResource.source_connection_id,
                KnowledgeResource.source_type,
                KnowledgeResource.source_read_capability,
                ConnectorResource.external_parent_id,
            )
            .join(
                ConnectorResource,
                (ConnectorResource.id == KnowledgeResource.source_resource_id)
                & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id),
            )
            .where(
                KnowledgeResource.id == resource_id,
                KnowledgeResource.workspace_id == workspace_id,
                KnowledgeResource.owner_user_id == user_id,
            )
        )
    ).first()
    if row is None or not await can_view_resource(
        database, resource_id=resource_id, workspace_id=workspace_id, user_id=user_id, now=utc_now()
    ):
        raise HTTPException(404, "Resource refresh is unavailable")
    filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)
    if filters.excludes(
        source_type=row[1],
        resource_id=resource_id,
        source_connection_id=row[0],
        external_parent_id=row[3],
    ):
        raise HTTPException(404, "Resource refresh is unavailable")
    connection = await database.get(ConnectorConnection, row[0], populate_existing=True)
    if connection is None:
        raise HTTPException(404, "Resource refresh is unavailable")
    definition = await database.get(ConnectorDefinition, connection.connector_definition_id)
    if definition is None:
        raise HTTPException(404, "Resource refresh is unavailable")
    source: Literal["gmail", "calendar", "snapshot", "canvas", "resources"] = "resources"
    target = connection.id
    if connection.legacy_connection_id is not None:
        target = connection.legacy_connection_id
        if row[2] == "communication.messages.read":
            source = "gmail"
        elif row[2] == "calendar.events.read":
            source = "calendar"
        else:
            raise HTTPException(409, "This source does not support live refresh")
    elif definition.connector_key == "canvas-lms":
        source = "canvas"
    elif definition.connector_key == "generic-import":
        raise HTTPException(409, "Import a new snapshot to refresh this source")
    # Existing management path performs fresh ownership, source scope, cooldown,
    # provider configuration and dispatch checks. No arbitrary URL/tool is accepted.
    status = await sync_connection(
        target,
        ManagedSyncCommand(request_id=payload.request_id, source=source),
        request,
        account,
        database,
        settings,
    )
    queued = status.get("dispatch_status", status.get("status")) == "queued"
    return RefreshView(
        resource_id=resource_id,
        state="REFRESHING" if queued else "ERROR",
        detail="Source refresh queued; results remain cached until sync finishes."
        if queued
        else "Source refresh could not be queued; cached results may be incomplete.",
    )


@router.post("/search/live", response_model=SearchResponse)
async def live_search(
    payload: LiveSearchRequest,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SearchResponse:
    """Freshness-sensitive search refreshes at most three relevant owned sources.

    Only already permitted matching resources select sources. No broad account
    refresh, arbitrary URL, provider generation, or source activation is possible.
    GET and ordinary search remain cached/read-only. Connector dispatch owns the
    durable request identity, authority checks, active-job suppression and quota.
    """
    require_origin(request, settings.web_origin)
    knowledge_enabled(settings.knowledge_enabled)
    try:
        result = await search_knowledge(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload.search,
            settings=settings,
        )
        if interpret(payload.search).freshness is Freshness.CACHED:
            return result.model_copy(update={"refresh": SearchRefresh(state="NOT_NEEDED")})
        moment = utc_now()
        targets: dict[tuple[UUID, str], UUID] = {}
        for resource in result.results:
            if resource.origin != "CONNECTED" or (
                resource.fresh_until is not None and stored_utc(resource.fresh_until) > moment
            ):
                continue
            row = (
                await database.execute(
                    select(
                        KnowledgeResource.source_connection_id,
                        KnowledgeResource.source_read_capability,
                    ).where(
                        KnowledgeResource.id == resource.resource_id,
                        KnowledgeResource.workspace_id == account.workspace.id,
                        KnowledgeResource.owner_user_id == account.user.id,
                        KnowledgeResource.deleted_at.is_(None),
                    )
                )
            ).first()
            if row is not None and row[0] is not None:
                targets.setdefault((row[0], row[1]), resource.resource_id)
        queued = unavailable = 0
        for (connection_id, capability), resource_id in list(targets.items())[:3]:
            try:
                refreshed = await refresh_resource(
                    resource_id,
                    RefreshCommand(
                        request_id=uuid5(payload.request_id, f"{connection_id}:{capability}")
                    ),
                    request,
                    account,
                    database,
                    settings,
                )
                if refreshed.state == "REFRESHING":
                    queued += 1
                else:
                    unavailable += 1
            except HTTPException as error:
                if error.status_code not in {403, 404, 409, 429, 502, 503}:
                    raise
                unavailable += 1
        summary = SearchRefresh(
            state=(
                "PARTIAL"
                if queued and (unavailable or len(targets) > 3)
                else "REFRESHING"
                if queued
                else "UNAVAILABLE"
                if targets
                else "NOT_NEEDED"
            ),
            queued=queued,
            unavailable=unavailable,
            bounded=len(targets) > 3,
        )
        # Dispatch yields to external workflow infrastructure. Recompute results
        # afterward so revoked, moved or updated evidence is never republished.
        current = await search_knowledge(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            request=payload.search,
            settings=settings,
            record_recent=False,
        )
        return current.model_copy(update={"refresh": summary})
    except KnowledgeUnavailable:
        raise HTTPException(403, "Connected search is unavailable") from None
