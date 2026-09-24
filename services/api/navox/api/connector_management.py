"""Authenticated SPEC-003 catalogue, connection overview, and explicit controls."""

from secrets import token_urlsafe
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connections import (
    GOOGLE_ACCOUNT_BINDING_SCOPES,
    GOOGLE_ALLOWED_SCOPES,
    OAUTH_ATTEMPT_TTL,
    GoogleAuthorizationStartResponse,
    authorization_url,
    google_vault,
    hash_value,
    start_google_authorization,
)
from navox.api.intelligence_sync import SyncRequest, sync_source
from navox.connectors.management import (
    CATALOG,
    CatalogEntry,
    ConnectionView,
    catalog_entry,
    connection_views,
    transition_connection,
)
from navox.connectors.sync_state import database_now
from navox.db.models import Connection, OAuthAuthorizationAttempt

router = APIRouter(tags=["connector management"])


class ManagementCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class ManagedSyncCommand(ManagementCommand):
    source: Literal["gmail", "calendar", "snapshot"]


def require_origin(request: Request, web_origin: str) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != web_origin.rstrip("/"):
        raise HTTPException(403, "Untrusted request origin")


@router.get("/connectors", response_model=list[CatalogEntry])
async def list_connectors(current_account: CurrentAccountDependency) -> list[CatalogEntry]:
    del current_account
    return list(CATALOG)


@router.get("/connectors/{connector_id}", response_model=CatalogEntry)
async def describe_connector(
    connector_id: str, current_account: CurrentAccountDependency
) -> CatalogEntry:
    del current_account
    return catalog_entry(connector_id)


@router.post("/connectors/{connector_id}/connect", response_model=GoogleAuthorizationStartResponse)
async def connect_catalog_entry(
    connector_id: str,
    payload: ManagementCommand,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> GoogleAuthorizationStartResponse:
    del payload
    require_origin(request, settings.web_origin)
    entry = catalog_entry(connector_id)
    if entry.availability != "available" or entry.id != "google-workspace":
        raise HTTPException(409, "Use the connector-specific setup flow")
    return await start_google_authorization(current_account, database, settings)


@router.get("/connections", response_model=list[ConnectionView])
async def list_connections(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> list[ConnectionView]:
    return await connection_views(
        database,
        workspace_id=current_account.workspace.id,
        user_id=current_account.user.id,
        agent_paused=current_account.user.agent_paused,
    )


async def _detail(
    connection_id: UUID, current_account: CurrentAccountDependency, database: DatabaseSession
) -> ConnectionView:
    views = await connection_views(
        database,
        workspace_id=current_account.workspace.id,
        user_id=current_account.user.id,
        agent_paused=current_account.user.agent_paused,
        connection_id=connection_id,
    )
    if len(views) != 1:
        raise HTTPException(404, "Connection not found")
    return views[0]


@router.get("/connections/{connection_id}", response_model=ConnectionView)
async def describe_connection(
    connection_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> ConnectionView:
    return await _detail(connection_id, current_account, database)


@router.post("/connections/{connection_id}/pause", response_model=ConnectionView)
async def pause_connection(
    connection_id: UUID,
    payload: ManagementCommand,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ConnectionView:
    require_origin(request, settings.web_origin)
    await transition_connection(
        database,
        workspace_id=current_account.workspace.id,
        user_id=current_account.user.id,
        connection_id=connection_id,
        operation="pause",
        request_id=payload.request_id,
    )
    return await _detail(connection_id, current_account, database)


@router.post("/connections/{connection_id}/resume", response_model=ConnectionView)
async def resume_connection(
    connection_id: UUID,
    payload: ManagementCommand,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> ConnectionView:
    require_origin(request, settings.web_origin)
    await transition_connection(
        database,
        workspace_id=current_account.workspace.id,
        user_id=current_account.user.id,
        connection_id=connection_id,
        operation="resume",
        request_id=payload.request_id,
    )
    return await _detail(connection_id, current_account, database)


@router.post("/connections/{connection_id}/sync", status_code=202)
async def sync_connection(
    connection_id: UUID,
    payload: ManagedSyncCommand,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    require_origin(request, settings.web_origin)
    view = await _detail(connection_id, current_account, database)
    if view.connector_id == "generic-import" and payload.source == "snapshot":
        if not any(source.id == "snapshot" and source.can_sync for source in view.sources):
            raise HTTPException(409, "Snapshot processing is paused, active, or unavailable")
        from navox.api.imports import queue_snapshot
        from navox.connectors.authorization import owned_connector

        row = await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=current_account.workspace.id,
            user_id=current_account.user.id,
            require_active=True,
        )
        status = await queue_snapshot(database, row, payload.request_id, settings)
        return {"dispatch_status": status}
    if view.connector_id != "google-workspace" or payload.source == "snapshot":
        raise HTTPException(409, "Manual sync is not enabled for this connector yet")
    if view.health in {"PAUSED", "DISCONNECTED"}:
        raise HTTPException(409, "Resume or reconnect before syncing")
    # Keep the established source-scope, provider configuration, quota and
    # identifiers-only Temporal dispatch checks in one place.
    return await sync_source(
        SyncRequest(
            connection_id=connection_id, source=payload.source, request_id=payload.request_id
        ),
        current_account,
        database,
        settings,
    )


@router.post(
    "/connections/{connection_id}/reauthorize", response_model=GoogleAuthorizationStartResponse
)
async def reauthorize_connection(
    connection_id: UUID,
    payload: ManagementCommand,
    request: Request,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> GoogleAuthorizationStartResponse:
    del payload
    require_origin(request, settings.web_origin)
    connection = await database.scalar(
        select(Connection).where(
            Connection.id == connection_id,
            Connection.workspace_id == current_account.workspace.id,
            Connection.user_id == current_account.user.id,
            Connection.provider == "google",
        )
    )
    if connection is None:
        raise HTTPException(404, "Connection not found")
    if connection.status in {"disconnected", "revoked"}:
        raise HTTPException(409, "Use a new connection for this account")
    google_vault(settings)
    # Reconnect re-requests only existing permissions plus account binding.
    # It must not silently add Gmail, Calendar or send access.
    scopes = tuple(
        sorted(
            (set(connection.granted_scopes) & GOOGLE_ALLOWED_SCOPES)
            | set(GOOGLE_ACCOUNT_BINDING_SCOPES)
        )
    )
    state, verifier = token_urlsafe(32), token_urlsafe(64)
    now = await database_now(database)
    database.add(
        OAuthAuthorizationAttempt(
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            provider="google",
            purpose="reconnect",
            connection_id=connection_id,
            requested_scopes=list(scopes),
            state_hash=hash_value(state),
            code_verifier=verifier,
            expires_at=now + OAUTH_ATTEMPT_TTL,
        )
    )
    await database.commit()
    return GoogleAuthorizationStartResponse(
        authorization_url=authorization_url(
            state, verifier, settings, scopes=scopes, include_granted_scopes=True
        ),
        requested_scopes=list(scopes),
    )
