"""Owner-consented setup for deployment-approved, read-only REST connectors."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.dispatcher import dispatch_connector_sync
from navox.connectors.generic_registration import ApprovedGenericConfig, approved_generic_connectors
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    WorkspaceMembership,
)

router = APIRouter(prefix="/connectors/generic-rest-api", tags=["configured REST"])
MAX_SETUP_BYTES = 20_000
MAX_CONNECTIONS_PER_OWNER = 20


class ConnectCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    configuration_id: str = Field(min_length=2, max_length=27)
    capabilities: list[str] = Field(min_length=1, max_length=32)
    token: SecretStr | None = None
    confirmed: bool
    request_id: UUID

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit read consent is required")
        return value


class CredentialCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: SecretStr
    confirmed: bool
    request_id: UUID

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit credential replacement is required")
        return value


def configurations(settings: Settings) -> tuple[ApprovedGenericConfig, ...]:
    try:
        return approved_generic_connectors(settings)
    except (ValueError, ValidationError):
        raise HTTPException(503, "Operator-approved REST configurations are invalid") from None


def selected_config(settings: Settings, identifier: str) -> ApprovedGenericConfig:
    return (
        next(
            (item for item in configurations(settings) if item.id == identifier),
            None,
        )
        or _not_configured()
    )


def _not_configured() -> ApprovedGenericConfig:
    raise HTTPException(404, "Approved REST configuration not found")


@router.get("/configurations")
async def available_configurations(
    account: CurrentAccountDependency, settings: SettingsDependency
) -> list[dict[str, object]]:
    del account
    return [
        {
            "id": item.id,
            "name": item.config.display_name,
            "read_capabilities": sorted(endpoint.capability for endpoint in item.config.endpoints),
            "authentication": "api_token" if item.config.auth == "bearer" else "none",
        }
        for item in configurations(settings)
    ]


async def _command[T: BaseModel](request: Request, schema: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "Use an application/json request")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_SETUP_BYTES:
            raise HTTPException(413, "REST setup request is too large")
        body.extend(chunk)
    try:
        return schema.model_validate_json(body)
    except (ValidationError, ValueError, UnicodeError):
        # Standard 422 validation diagnostics may echo a submitted bearer token.
        raise HTTPException(422, "Invalid REST setup request") from None


def _token(command: ConnectCommand, approved: ApprovedGenericConfig) -> str | None:
    value = command.token.get_secret_value() if command.token is not None else None
    if approved.config.auth == "none":
        if value is not None:
            raise HTTPException(422, "This configuration does not use a token")
        return None
    if (
        not value
        or len(value) > 16_384
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise HTTPException(422, "A valid API token is required")
    return value


async def _definition(
    database: DatabaseSession, approved: ApprovedGenericConfig
) -> ConnectorDefinition:
    manifest = approved.manifest
    row = {
        "connector_key": approved.connector_key,
        "version": manifest.version,
        "display_name": manifest.display_name,
        "connector_class": "GENERIC_API",
        "trust_level": "WORKSPACE_PRIVATE",
        "manifest": manifest.model_dump(mode="json", by_alias=True),
        "active": True,
    }
    dialect = database.get_bind().dialect.name
    if dialect == "postgresql":
        statement = (
            pg_insert(ConnectorDefinition)
            .values(**row)
            .on_conflict_do_nothing(
                index_elements=[ConnectorDefinition.connector_key, ConnectorDefinition.version]
            )
        )
        await database.execute(statement)
    elif dialect == "sqlite":
        statement_sqlite = (
            sqlite_insert(ConnectorDefinition)
            .values(**row)
            .on_conflict_do_nothing(
                index_elements=[ConnectorDefinition.connector_key, ConnectorDefinition.version]
            )
        )
        await database.execute(statement_sqlite)
    else:
        raise HTTPException(503, "Configured REST registration requires a supported database")
    definition = await database.scalar(
        select(ConnectorDefinition)
        .where(
            ConnectorDefinition.connector_key == approved.connector_key,
            ConnectorDefinition.version == manifest.version,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        definition is None
        or not definition.active
        or definition.manifest != row["manifest"]
        or definition.connector_class != "GENERIC_API"
    ):
        raise HTTPException(409, "Approved REST connector definition is unavailable")
    return definition


async def queue(
    database: DatabaseSession,
    connection: ConnectorConnection,
    request_id: UUID,
    settings: Settings,
) -> str:
    work = ConnectorSyncWork(
        str(connection.id),
        str(connection.user_id),
        str(connection.workspace_id),
        str(request_id),
        "manual",
    )
    await database.commit()
    if settings.ai_provider == "disabled":
        return "pending"
    try:
        await dispatch_connector_sync(work, settings=settings, initial=True)
        return "queued"
    except Exception:
        # A persisted connection can be synced manually after Temporal recovers.
        return "pending"


@router.post("/connect", status_code=201)
async def connect(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    if not configurations(settings):
        raise HTTPException(409, "Use the connector-specific setup flow")
    command = await _command(request, ConnectCommand)
    approved = selected_config(settings, command.configuration_id)
    declared = {endpoint.capability for endpoint in approved.config.endpoints}
    capabilities = set(command.capabilities)
    if len(capabilities) != len(command.capabilities) or not capabilities <= declared:
        raise HTTPException(422, "Only distinct approved read capabilities may be selected")
    token = _token(command, approved)
    if account.user.agent_paused:
        raise HTTPException(409, "Resume the agent before connecting a REST service")
    if token is not None:
        try:
            broker = SecretBroker(settings)
        except SecretBrokerError:
            raise HTTPException(503, "Connector credential encryption is unavailable") from None
    else:
        broker = None
    user = await database.scalar(
        select(User)
        .where(User.id == account.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    member = await database.get(
        WorkspaceMembership, (account.workspace.id, account.user.id), populate_existing=True
    )
    if user is None or member is None or user.agent_paused:
        raise HTTPException(403, "REST setup is not authorized")
    identity = f"navox:rest:{account.workspace.id}:{user.id}:{approved.connector_key}"
    connection_id = uuid5(NAMESPACE_URL, identity)
    previous = await database.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == account.workspace.id,
            AuditEvent.user_id == user.id,
            AuditEvent.event_type == "connector.rest.connected",
            AuditEvent.event_metadata["request_id"].as_string() == str(command.request_id),
        )
    )
    if previous is not None and previous.entity_id != connection_id:
        raise HTTPException(409, "Request identifier was already used")
    existing = await database.get(ConnectorConnection, connection_id, populate_existing=True)
    if existing is not None:
        if (
            existing.workspace_id != account.workspace.id
            or existing.user_id != user.id
            or existing.config != approved.config.model_dump(mode="json")
            or existing.status in {"PAUSED", "DISCONNECTED"}
            or set(existing.authorized_capabilities) != capabilities
            or previous is None
        ):
            raise HTTPException(409, "REST connection already exists; review its permissions")
        return {"connection_id": str(existing.id), "dispatch_status": "existing", "reused": True}
    count = await database.scalar(
        select(func.count())
        .select_from(ConnectorConnection)
        .where(
            ConnectorConnection.workspace_id == account.workspace.id,
            ConnectorConnection.user_id == user.id,
            ConnectorConnection.provider.notin_(["google", "canvas", "import"]),
        )
    )
    if count is not None and count >= MAX_CONNECTIONS_PER_OWNER:
        raise HTTPException(409, "Configured REST connection limit reached")
    definition = await _definition(database, approved)
    connection = ConnectorConnection(
        id=connection_id,
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=account.workspace.id,
        provider=approved.config.provider,
        external_account_id=f"configured:{user.id}:{approved.connector_key}",
        display_name=approved.config.display_name,
        authorized_capabilities=sorted(capabilities),
        provider_capabilities=sorted(declared),
        config=approved.config.model_dump(mode="json"),
    )
    database.add(connection)
    await database.flush()
    if token is not None and broker is not None:
        try:
            await broker.store(
                database,
                {approved.config.token_secret_name: token},
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
            )
        except SecretBrokerError:
            await database.rollback()
            raise HTTPException(503, "REST credential could not be stored") from None
    database.add(
        AuditEvent(
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
            actor_type="user",
            event_type="connector.rest.connected",
            entity_type="connector_connection",
            entity_id=connection.id,
            event_metadata={
                "request_id": str(command.request_id),
                "configuration_id": approved.id,
                "capabilities": sorted(capabilities),
            },
        )
    )
    status = await queue(database, connection, command.request_id, settings)
    return {"connection_id": str(connection.id), "dispatch_status": status, "reused": False}


@router.post("/connections/{connection_id}/credential")
async def replace_credential(
    connection_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    command = await _command(request, CredentialCommand)
    try:
        connection = await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            require_active=False,
            lock_authority=True,
        )
    except ConnectorAccessDenied:
        raise HTTPException(404, "REST connection not found") from None
    definition = await database.get(ConnectorDefinition, connection.connector_definition_id)
    approved = next(
        (
            item
            for item in configurations(settings)
            if item.connector_key == (definition.connector_key if definition else None)
        ),
        None,
    )
    if (
        approved is None
        or not definition
        or not definition.active
        or approved.config.auth != "bearer"
        or connection.provider != approved.config.provider
        or connection.config != approved.config.model_dump(mode="json")
        or connection.status == "DISCONNECTED"
    ):
        raise HTTPException(409, "REST credential replacement is unavailable")
    if set(connection.authorized_capabilities) - {
        endpoint.capability for endpoint in approved.config.endpoints
    }:
        raise HTTPException(409, "REST permissions changed; review a new connection")
    value = command.token.get_secret_value()
    if not value or len(value) > 16_384 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise HTTPException(422, "A valid API token is required")
    previous = await database.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == connection.workspace_id,
            AuditEvent.user_id == connection.user_id,
            AuditEvent.event_type == "connector.rest.credential_replaced",
            AuditEvent.event_metadata["request_id"].as_string() == str(command.request_id),
        )
    )
    if previous is not None:
        if previous.entity_id != connection_id:
            raise HTTPException(409, "Request identifier was already used")
        return {"connection_id": str(connection_id), "dispatch_status": "existing", "reused": True}
    try:
        await SecretBroker(settings).store(
            database,
            {approved.config.token_secret_name: value},
            connection_id=connection_id,
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
        )
    except SecretBrokerError:
        await database.rollback()
        raise HTTPException(503, "REST credential could not be stored") from None
    if connection.status != "PAUSED" and connection.paused_at is None:
        connection.health_state = "CONNECTED"
        connection.last_error_code = None
        connection.retry_not_before = None
    database.add(
        AuditEvent(
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
            actor_type="user",
            event_type="connector.rest.credential_replaced",
            entity_type="connector_connection",
            entity_id=connection_id,
            event_metadata={"request_id": str(command.request_id)},
        )
    )
    if (
        connection.status == "PAUSED"
        or connection.paused_at is not None
        or account.user.agent_paused
    ):
        await database.commit()
        status = "pending"
    else:
        status = await queue(database, connection, command.request_id, settings)
    return {"connection_id": str(connection_id), "dispatch_status": status, "reused": False}
