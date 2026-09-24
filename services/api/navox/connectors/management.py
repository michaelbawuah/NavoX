"""Content-free connection projections and owner-scoped management transitions.

Catalogue availability is explicit: an adapter existing in source does not make
its public setup path certified. Reads here perform no provider or model I/O.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.builtin.google import ensure_google_connector_connection, google_capabilities
from navox.connectors.sync_state import database_now, utc
from navox.db.models import (
    AuditEvent,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorImportSnapshot,
    ConnectorSyncRun,
    User,
    WorkspaceMembership,
)
from navox.intelligence.source_cooldown import source_cooldown
from navox.providers.google_sources import CALENDAR_READ_SCOPE, GMAIL_READ_SCOPE

Health = Literal[
    "CONNECTED", "DEGRADED", "AUTH_EXPIRED", "RATE_LIMITED", "SYNC_FAILED", "PAUSED", "DISCONNECTED"
]
Freshness = Literal["never_synced", "fresh", "stale", "unknown"]
FRESHNESS_WINDOW = timedelta(hours=1)
SOURCE_CAPABILITIES = {
    "gmail": (GMAIL_READ_SCOPE, "communication.messages.read", "Gmail"),
    "calendar": (CALENDAR_READ_SCOPE, "calendar.events.read", "Google Calendar"),
}
CAPABILITY_LABELS = {
    "communication.messages.read": "Read Gmail messages",
    "communication.messages.changed": "Receive Gmail change notifications",
    "communication.messages.send": "Send email only through exact-action approval",
    "calendar.events.read": "Read calendar events",
    "calendar.events.changed": "Receive calendar change notifications",
    "academic.courses.read": "Read active courses",
    "academic.assignments.read": "Read assignments and due dates",
    "academic.submissions.read": "Read submission state",
    "academic.announcements.read": "Read announcements",
    "imports.calendar.read": "Read imported calendar events",
    "imports.tabular.read": "Read imported CSV rows",
    "imports.json.read": "Read imported JSON records",
}


class CatalogEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    name: str
    category: str
    description: str
    availability: Literal["available", "setup_pending", "planned"]
    setup_label: str
    authentication: str
    read_capabilities: list[str]


CATALOG = (
    CatalogEntry(
        id="google-workspace",
        name="Google Workspace",
        category="Productivity",
        description="Gmail and Calendar, with separately approved read access.",
        availability="available",
        setup_label="Connect Google",
        authentication="Google OAuth",
        read_capabilities=["communication.messages.read", "calendar.events.read"],
    ),
    CatalogEntry(
        id="canvas-lms",
        name="Canvas LMS",
        category="Education",
        description="Courses, assignments, submission state, announcements and calendar context.",
        availability="setup_pending",
        setup_label="Setup not enabled yet",
        authentication="Institution-approved OAuth",
        read_capabilities=[
            "academic.courses.read",
            "academic.assignments.read",
            "academic.submissions.read",
            "academic.announcements.read",
        ],
    ),
    CatalogEntry(
        id="generic-import",
        name="File imports",
        category="Data",
        description="Preview and process encrypted, immutable ICS, CSV and JSON snapshots.",
        availability="available",
        setup_label="Import a file",
        authentication="None",
        read_capabilities=["imports.calendar.read", "imports.tabular.read", "imports.json.read"],
    ),
    CatalogEntry(
        id="generic-rest-api",
        name="Custom REST API",
        category="Universal protocols",
        description="Configuration-driven access for compatible services beyond the app catalogue.",
        availability="setup_pending",
        setup_label="Setup not enabled yet",
        authentication="API token or none",
        read_capabilities=[],
    ),
    CatalogEntry(
        id="mcp",
        name="MCP server",
        category="Universal protocols",
        description="Planned: discover resources and tools behind NavoX permission checks.",
        availability="planned",
        setup_label="Adapter pending",
        authentication="Server dependent",
        read_capabilities=[],
    ),
)


class PermissionView(BaseModel):
    name: str
    label: str
    mode: Literal["read", "write", "event"]


class SourceView(BaseModel):
    id: str
    name: str
    authorized: bool
    health: Health
    last_synced_at: datetime | None
    freshness: Freshness
    syncing: bool
    retry_at: datetime | None
    can_sync: bool


class ConnectionView(BaseModel):
    id: UUID
    connector_id: str
    name: str
    account_label: str | None
    health: Health
    agent_paused: bool
    permissions: list[PermissionView]
    sources: list[SourceView]
    can_pause: bool
    can_resume: bool
    can_reauthorize: bool
    # Never imply the unfinished disconnect/delete-data path is supported.
    can_disconnect: bool = False
    can_delete_data: bool = False


def catalog_entry(identifier: str) -> CatalogEntry:
    for entry in CATALOG:
        if entry.id == identifier:
            return entry
    raise HTTPException(404, "Connector not found")


def freshness(value: datetime | None, now: datetime) -> Freshness:
    if value is None:
        return "never_synced"
    if utc(value) > now + timedelta(minutes=1):
        return "unknown"
    return "fresh" if now - utc(value) <= FRESHNESS_WINDOW else "stale"


def _permissions(names: list[str] | frozenset[str]) -> list[PermissionView]:
    result = []
    for name in sorted(set(names)):
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", name) or len(name) > 160:
            continue
        mode: Literal["read", "write", "event"] = "write"
        if name.endswith(".read"):
            mode = "read"
        elif name.endswith(".changed"):
            mode = "event"
        result.append(PermissionView(name=name, label=CAPABILITY_LABELS.get(name, name), mode=mode))
    return result


def _health(row: ConnectorConnection | None, now: datetime, *, fallback: Health) -> Health:
    if row is None:
        return fallback
    if row.status in {"DISCONNECTED", "PAUSED"} or row.paused_at is not None:
        return "PAUSED" if row.status != "DISCONNECTED" else "DISCONNECTED"
    if row.health_state == "AUTH_EXPIRED" or row.last_error_code in {
        "AUTH_EXPIRED",
        "AUTH_REVOKED",
    }:
        return "AUTH_EXPIRED"
    if row.retry_not_before and utc(row.retry_not_before) > now:
        return "RATE_LIMITED" if row.last_error_code == "RATE_LIMITED" else "DEGRADED"
    if row.health_state in {"DEGRADED", "SYNC_FAILED", "RATE_LIMITED"}:
        return "SYNC_FAILED" if row.health_state == "SYNC_FAILED" else "DEGRADED"
    return fallback


def _syncing(row: ConnectorConnection | None, now: datetime) -> bool:
    return bool(
        row
        and row.sync_lease_token
        and row.sync_lease_expires_at
        and utc(row.sync_lease_expires_at) > now
    )


async def connection_views(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    agent_paused: bool,
    connection_id: UUID | None = None,
) -> list[ConnectionView]:
    """Bounded owner-scoped overview. Do not serialize ORM rows or connector config."""
    now = await database_now(database)
    legacy_query = select(Connection).where(
        Connection.workspace_id == workspace_id,
        Connection.user_id == user_id,
        Connection.provider == "google",
    )
    native_query = select(ConnectorConnection).where(
        ConnectorConnection.workspace_id == workspace_id,
        ConnectorConnection.user_id == user_id,
    )
    if connection_id:
        legacy_query = legacy_query.where(Connection.id == connection_id)
        native_query = native_query.where(
            (ConnectorConnection.id == connection_id)
            | (ConnectorConnection.legacy_connection_id == connection_id)
        )
    legacy = list(
        await database.scalars(
            legacy_query.order_by(Connection.created_at, Connection.id).limit(201)
        )
    )
    native = list(await database.scalars(native_query.order_by(ConnectorConnection.id).limit(601)))
    if len(legacy) > 200 or len(native) > 600:
        raise HTTPException(409, "Connection overview limit exceeded; narrow to one connection")
    definitions = {
        row.id: row
        for row in await database.scalars(
            select(ConnectorDefinition).where(
                ConnectorDefinition.id.in_({row.connector_definition_id for row in native})
            )
        )
    }
    result: list[ConnectionView] = []
    for account in legacy:
        linked = [
            row
            for row in native
            if row.legacy_connection_id == account.id and row.provider == "google"
        ]
        health: Health = "CONNECTED"
        if account.status in {"disconnected", "revoked"}:
            health = "DISCONNECTED"
        elif account.status == "paused" or any(row.paused_at for row in linked):
            health = "PAUSED"
        elif account.credential_reference is None or account.status in {
            "needs_reauthorization",
            "auth_expired",
        }:
            health = "AUTH_EXPIRED"
        elif account.status != "active":
            health = "DEGRADED"
        sources: list[SourceView] = []
        for source, (scope, _, label) in SOURCE_CAPABILITIES.items():
            row = next(
                (
                    r
                    for r in linked
                    if definitions.get(r.connector_definition_id)
                    and definitions[r.connector_definition_id].connector_key == f"google-{source}"
                ),
                None,
            )
            # Calendar's registered identifier is google-calendar, Gmail's google-gmail.
            state = health if health != "CONNECTED" else _health(row, now, fallback=health)
            cooldown = await source_cooldown(database, account.id, source)
            retry = (
                utc(row.retry_not_before)
                if row and row.retry_not_before and utc(row.retry_not_before) > now
                else None
            )
            if cooldown:
                source_retry = now + timedelta(seconds=int(cooldown["retry_after_seconds"]))
                retry = max(retry, source_retry) if retry else source_retry
                if health == "CONNECTED":
                    state = "RATE_LIMITED"
            last = utc(row.last_synced_at) if row and row.last_synced_at else None
            authorized = scope in account.granted_scopes
            syncing = state not in {"PAUSED", "DISCONNECTED", "AUTH_EXPIRED"} and _syncing(row, now)
            sources.append(
                SourceView(
                    id=source,
                    name=label,
                    authorized=authorized,
                    health=state,
                    last_synced_at=last,
                    freshness=freshness(last, now),
                    syncing=syncing,
                    retry_at=retry,
                    can_sync=bool(
                        authorized
                        and not agent_paused
                        and not syncing
                        and retry is None
                        and health == "CONNECTED"
                        and state != "AUTH_EXPIRED"
                    ),
                )
            )
        if health == "CONNECTED":
            statuses = {s.health for s in sources if s.authorized}
            for candidate in ("AUTH_EXPIRED", "SYNC_FAILED", "RATE_LIMITED", "DEGRADED"):
                if candidate in statuses:
                    health = candidate
                    break
        result.append(
            ConnectionView(
                id=account.id,
                connector_id="google-workspace",
                name="Google Workspace",
                account_label=account.external_email,
                health=health,
                agent_paused=agent_paused,
                permissions=_permissions(google_capabilities(account.granted_scopes)),
                sources=sources,
                can_pause=health not in {"PAUSED", "DISCONNECTED"},
                can_resume=health == "PAUSED",
                can_reauthorize=health != "DISCONNECTED",
            )
        )
    # Google compatibility/source mirrors are represented by their original account ID.
    for row in native:
        if row.provider == "google":
            continue
        definition = definitions.get(row.connector_definition_id)
        entry = next((e for e in CATALOG if definition and e.id == definition.connector_key), None)
        state = _health(row, now, fallback="CONNECTED")
        if definition is None or not definition.active:
            state = "DEGRADED" if state not in {"PAUSED", "DISCONNECTED"} else state
        last = utc(row.last_synced_at) if row.last_synced_at else None
        snapshot = (
            await database.get(ConnectorImportSnapshot, row.id)
            if entry and entry.id == "generic-import"
            else None
        )
        is_snapshot = bool(
            snapshot and snapshot.workspace_id == workspace_id and snapshot.user_id == user_id
        )
        from navox.connectors.import_parser import IMPORT_CAPABILITIES

        snapshot_allowed = (
            is_snapshot
            and IMPORT_CAPABILITIES.get(str(row.config.get("format")))
            in row.authorized_capabilities
        )
        retry = (
            utc(row.retry_not_before)
            if row.retry_not_before and utc(row.retry_not_before) > now
            else None
        )
        is_canvas = bool(
            entry and entry.id == "canvas-lms" and definition and definition.version == "1.1.0"
        )
        canvas_allowed = bool(
            is_canvas
            and row.credential_reference
            and "academic.courses.read" in row.authorized_capabilities
        )
        result.append(
            ConnectionView(
                id=row.id,
                connector_id=entry.id if entry else "private-connector",
                name=entry.name if entry else "Private connector",
                account_label=row.display_name if is_snapshot or is_canvas else None,
                health=state,
                agent_paused=agent_paused,
                permissions=_permissions(row.authorized_capabilities),
                sources=[
                    SourceView(
                        id="snapshot" if is_snapshot else "canvas" if is_canvas else "resources",
                        name="Imported snapshot"
                        if is_snapshot
                        else "Canvas academic context"
                        if is_canvas
                        else "Connected resources",
                        authorized=bool(snapshot_allowed)
                        if is_snapshot
                        else canvas_allowed
                        if is_canvas
                        else True,
                        health=state,
                        last_synced_at=last,
                        freshness="fresh" if is_snapshot and last else freshness(last, now),
                        syncing=_syncing(row, now),
                        retry_at=retry,
                        can_sync=bool(
                            (snapshot_allowed or canvas_allowed)
                            and definition
                            and definition.active
                            and not agent_paused
                            and state not in {"PAUSED", "DISCONNECTED", "AUTH_EXPIRED"}
                            and not _syncing(row, now)
                            and retry is None
                        ),
                    )
                ],
                can_pause=state not in {"PAUSED", "DISCONNECTED"},
                can_resume=state == "PAUSED" and bool(definition and definition.active),
                can_reauthorize=is_canvas and state != "DISCONNECTED",
            )
        )
    return result


async def transition_connection(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    operation: Literal["pause", "resume"],
    request_id: UUID,
) -> None:
    # All source mirrors precede legacy authority in the lock order used by runtime.
    rows = list(
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
                (ConnectorConnection.id == connection_id)
                | (ConnectorConnection.legacy_connection_id == connection_id),
            )
            .order_by(ConnectorConnection.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    membership = await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = await database.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    account = await database.scalar(
        select(Connection)
        .where(
            Connection.id == connection_id,
            Connection.workspace_id == workspace_id,
            Connection.user_id == user_id,
            Connection.provider == "google",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if membership is None or user is None or (account is None and not rows):
        raise HTTPException(404, "Connection not found")
    if account is None and (
        len(rows) != 1 or rows[0].provider == "google" or rows[0].id != connection_id
    ):
        raise HTTPException(404, "Connection not found")
    event_type = f"connector.management.{operation}"
    previous = await database.scalar(
        select(AuditEvent.id).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.user_id == user_id,
            AuditEvent.entity_id == connection_id,
            AuditEvent.event_type == event_type,
            AuditEvent.event_metadata["request_id"].as_string() == str(request_id),
        )
    )
    if previous is not None:
        await database.commit()
        return
    if account and account.status in {"revoked", "disconnected"}:
        raise HTTPException(409, "Reconnect the account before changing its state")
    if any(row.status == "DISCONNECTED" for row in rows):
        raise HTTPException(409, "A disconnected connector cannot be resumed")
    if operation == "resume":
        for row in rows:
            definition = await database.get(ConnectorDefinition, row.connector_definition_id)
            if definition is None or not definition.active:
                raise HTTPException(409, "Connector is unavailable")
    now = await database_now(database)
    if account:
        anchor = await ensure_google_connector_connection(database, account)
        if all(row.id != anchor.id for row in rows):
            rows.append(anchor)
    requires_reauthorization = False
    if account and operation == "resume":
        credential = (
            await database.get(ConnectionCredential, account.credential_reference)
            if account.credential_reference
            else None
        )
        # The compatibility anchor reflects the original OAuth authority. A
        # stale source-specific auth error cannot undo a successful reconnect.
        requires_reauthorization = not credential or anchor.last_error_code in {
            "AUTH_EXPIRED",
            "AUTH_REVOKED",
        }
    changed = False
    target = "PAUSED" if operation == "pause" else "CONNECTED"
    for row in rows:
        currently_paused = row.status == "PAUSED" or row.paused_at is not None
        if currently_paused == (operation == "pause"):
            continue
        changed = True
        row.status = target
        row.health_state = "PAUSED" if operation == "pause" else "DEGRADED"
        row.paused_at = now if operation == "pause" else None
        if account and operation == "resume" and not requires_reauthorization:
            if row.last_error_code in {"AUTH_EXPIRED", "AUTH_REVOKED"}:
                row.last_error_code = None
        # Fence every in-flight attempt, including a rapid pause followed by resume.
        row.sync_generation += 1
        row.sync_lease_token = None
        row.sync_lease_expires_at = None
        if row.sync_run_id:
            run = await database.get(ConnectorSyncRun, row.sync_run_id)
            if run and run.status == "running":
                run.status = "interrupted"
                run.error_code = "PERMISSION_DENIED"
        if account is None and row.legacy_connection_id:
            provenance = await database.scalar(
                select(Connection)
                .where(
                    Connection.id == row.legacy_connection_id,
                    Connection.workspace_id == workspace_id,
                    Connection.user_id == user_id,
                    Connection.provider == row.provider,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if provenance:
                provenance.status = "paused" if operation == "pause" else "active"
    if account:
        if operation == "pause":
            changed |= account.status != "paused"
            account.status = "paused"
        elif account.status == "paused":
            changed = True
            account.status = "needs_reauthorization" if requires_reauthorization else "active"
        # Credentials, grants, source cursors and existing learned knowledge are unchanged.
    database.add(
        AuditEvent(
            user_id=user_id,
            workspace_id=workspace_id,
            event_type=event_type,
            actor_type="user",
            entity_type="connection",
            entity_id=connection_id,
            event_metadata={"request_id": str(request_id), "changed": changed},
        )
    )
    await database.commit()
