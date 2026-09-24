"""Explicit preview and confirmation for immutable ICS/CSV/JSON sources."""

from __future__ import annotations

from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.dispatcher import dispatch_connector_sync
from navox.connectors.import_parser import (
    IMPORT_CAPABILITIES,
    MAX_IMPORT_BYTES,
    ImportFormat,
    ImportValidationError,
    import_digest,
    parse_import,
)
from navox.connectors.import_storage import SnapshotEnvelope, seal
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorImportSnapshot,
    User,
    WorkspaceMembership,
)

router = APIRouter(prefix="/connectors/generic-import", tags=["file imports"])
MAX_REQUEST_BYTES = 6_000_000


class PreviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: ImportFormat
    content: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)


class ConfirmCommand(PreviewCommand):
    name: str = Field(min_length=1, max_length=120)
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=36, max_length=36)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> bool:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return True


async def read_command[T: BaseModel](request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "Use an application/json request")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Import request is too large")
        body.extend(chunk)
    try:
        return model.model_validate_json(body)
    except (ValidationError, ValueError, UnicodeError):
        # Pydantic's usual errors include input values, which may contain source text.
        raise HTTPException(
            422, "Invalid import request or missing explicit confirmation"
        ) from None


@router.post("/preview")
async def preview(
    request: Request, account: CurrentAccountDependency, settings: SettingsDependency
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    command = await read_command(request, PreviewCommand)
    try:
        records = parse_import(command.content, command.format, account.user.timezone)
    except ImportValidationError as error:
        raise HTTPException(422, str(error)) from None
    return {
        "preview_hash": import_digest(command.content, command.format, account.user.timezone),
        "count": len(records),
        "timezone": account.user.timezone,
        "examples": [
            {"id": r.external_id, "title": r.title, "resource_type": r.resource_type}
            for r in records[:3]
        ],
        "notes": (
            "Preview performs no model calls and saves nothing. "
            "Confirm to save an encrypted immutable snapshot and process it. "
            "Only mapped fields are imported. ICS recurrence and custom time-zone "
            "rules are not supported; alarms and attachments are not executed or fetched."
        ),
    }


async def queue_snapshot(
    database: AsyncSession, connection: ConnectorConnection, request_id: UUID, settings: Settings
) -> str:
    payload = ConnectorSyncWork(
        str(connection.id),
        str(connection.user_id),
        str(connection.workspace_id),
        str(request_id),
        "manual",
    )
    await database.commit()
    try:
        await dispatch_connector_sync(payload, settings=settings, initial=True)
        return "queued"
    except Exception:
        # The saved snapshot survives a Temporal outage; reconciliation retries unsynced imports.
        return "pending"


@router.post("/connect", status_code=201)
async def confirm(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    command = await read_command(request, ConfirmCommand)
    try:
        request_id = UUID(command.request_id)
        if not command.name.strip():
            raise ValueError("empty name")
    except ValueError:
        raise HTTPException(422, "Invalid request identifier or snapshot name") from None
    user = await database.scalar(
        select(User)
        .where(User.id == account.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    member = await database.get(
        WorkspaceMembership, (account.workspace.id, account.user.id), populate_existing=True
    )
    if user is None or member is None:
        raise HTTPException(403, "Import is not authorized")
    if user.agent_paused or settings.ai_provider == "disabled":
        raise HTTPException(
            409, "Enable intelligence and resume the agent before confirming an import"
        )
    try:
        digest = import_digest(command.content, command.format, user.timezone)
    except UnicodeError:
        raise HTTPException(422, "Import text must be valid UTF-8") from None
    if digest != command.preview_hash:
        raise HTTPException(409, "The file or time zone changed; preview it again")
    previous = await database.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == account.workspace.id,
            AuditEvent.user_id == user.id,
            AuditEvent.event_type.in_(("connector.import.created", "connector.import.reused")),
            AuditEvent.event_metadata["request_id"].as_string() == str(request_id),
        )
    )
    connection_id = uuid5(NAMESPACE_URL, f"navox:import:{account.workspace.id}:{user.id}:{digest}")
    if previous is not None and previous.entity_id != connection_id:
        raise HTTPException(409, "This request identifier was used for another import")
    existing = await database.get(ConnectorConnection, connection_id)
    if existing is not None:
        if existing.workspace_id != account.workspace.id or existing.user_id != user.id:
            raise HTTPException(403, "Import is not authorized")
        if existing.status in {"PAUSED", "DISCONNECTED"}:
            raise HTTPException(409, "The saved snapshot is paused or disconnected")
        if previous is None:
            database.add(
                AuditEvent(
                    user_id=user.id,
                    workspace_id=account.workspace.id,
                    event_type="connector.import.reused",
                    actor_type="user",
                    entity_type="connector_connection",
                    entity_id=connection_id,
                    event_metadata={"request_id": str(request_id), "format": command.format},
                )
            )
        status = (
            "completed"
            if existing.last_synced_at
            else await queue_snapshot(database, existing, connection_id, settings)
        )
        await database.commit()
        return {"connection_id": str(existing.id), "dispatch_status": status, "reused": True}
    count = await database.scalar(
        select(func.count())
        .select_from(ConnectorImportSnapshot)
        .where(
            ConnectorImportSnapshot.workspace_id == account.workspace.id,
            ConnectorImportSnapshot.user_id == user.id,
        )
    )
    if count is not None and count >= 20:
        raise HTTPException(409, "This workspace owner has reached the 20-snapshot import limit")
    try:
        records = parse_import(command.content, command.format, user.timezone)
    except ImportValidationError as error:
        raise HTTPException(422, str(error)) from None
    now = await database_now(database)
    try:
        encrypted = seal(
            SnapshotEnvelope(
                connection_id=connection_id,
                workspace_id=account.workspace.id,
                user_id=user.id,
                digest=digest,
                imported_at=now,
                records=records,
            ),
            settings,
        )
    except Exception:
        raise HTTPException(503, "Import encryption is unavailable") from None
    definition = await database.scalar(
        select(ConnectorDefinition).where(
            ConnectorDefinition.connector_key == "generic-import",
            ConnectorDefinition.version == "1.0.0",
        )
    )
    if definition is None or not definition.active:
        # A deterministic definition is inserted in the migration, avoiding cross-owner races.
        raise HTTPException(503, "Run the import database migration before connecting files")
    connection = ConnectorConnection(
        id=connection_id,
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=account.workspace.id,
        provider="import",
        external_account_id=f"snapshot:{user.id}:{digest}",
        display_name=command.name.strip(),
        status="CONNECTED",
        health_state="CONNECTED",
        authorized_capabilities=[IMPORT_CAPABILITIES[command.format]],
        provider_capabilities=[IMPORT_CAPABILITIES[command.format]],
        config={"format": command.format, "snapshot_digest": digest, "owner_id": str(user.id)},
    )
    database.add(connection)
    await database.flush()
    database.add(
        ConnectorImportSnapshot(
            connection_id=connection_id,
            workspace_id=account.workspace.id,
            user_id=user.id,
            digest=digest,
            format=command.format,
            record_count=len(records),
            encrypted_payload=encrypted,
            created_at=now,
        )
    )
    database.add(
        AuditEvent(
            user_id=user.id,
            workspace_id=account.workspace.id,
            event_type="connector.import.created",
            actor_type="user",
            entity_type="connector_connection",
            entity_id=connection_id,
            event_metadata={
                "request_id": str(request_id),
                "format": command.format,
                "record_count": len(records),
            },
        )
    )
    status = await queue_snapshot(database, connection, connection_id, settings)
    return {"connection_id": str(connection.id), "dispatch_status": status, "reused": False}
