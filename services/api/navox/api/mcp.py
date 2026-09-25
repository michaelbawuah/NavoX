"""Owner-consented setup for deployment-reviewed MCP read connections."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.builtin.mcp import MCPConnector, MCPServerPolicy
from navox.connectors.dispatcher import dispatch_connector_sync
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.mcp_registration import approved_mcp_policies
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    WorkspaceMembership,
)

router = APIRouter(prefix="/connectors/mcp", tags=["configured MCP"])
MAX_SETUP_BYTES = 20_000
MAX_CONNECTIONS_PER_OWNER = 20


class ConnectCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    server_id: str = Field(min_length=3, max_length=40)
    capabilities: list[str] = Field(min_length=1, max_length=32)
    token: SecretStr | None = None
    confirmed: bool
    request_id: UUID

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit MCP read authorization is required")
        return value


def policies(settings: Settings) -> tuple[MCPServerPolicy, ...]:
    """Only the operator-controlled Settings source can define a server."""
    try:
        return approved_mcp_policies(settings)
    except (ValueError, ValidationError):
        raise HTTPException(503, "Operator-reviewed MCP policies are invalid") from None


def selected_policy(settings: Settings, server_id: str) -> MCPServerPolicy:
    selected = next((item for item in policies(settings) if item.server_id == server_id), None)
    if selected is None:
        raise HTTPException(404, "Reviewed MCP server not found")
    return selected


@router.get("/servers")
async def available_servers(
    account: CurrentAccountDependency, settings: SettingsDependency
) -> list[dict[str, object]]:
    del account
    return [
        {
            "id": policy.server_id,
            "name": policy.display_name,
            "read_capabilities": [
                item.name
                for item in MCPConnector(policy.connection_config(), None, policy=policy)
                .get_manifest()
                .capabilities.read
            ],
            "authentication": "api_token" if policy.token_secret_name else "none",
        }
        for policy in policies(settings)
    ]


async def _read_command(request: Request) -> ConnectCommand:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "Use an application/json request")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_SETUP_BYTES:
            raise HTTPException(413, "MCP setup request is too large")
        body.extend(chunk)
    try:
        return ConnectCommand.model_validate_json(body)
    except (ValidationError, ValueError, UnicodeError):
        # Pydantic validation details could echo a bearer token.
        raise HTTPException(422, "Invalid MCP setup request") from None


async def _definition(database: DatabaseSession, policy: MCPServerPolicy) -> ConnectorDefinition:
    manifest = MCPConnector(policy.connection_config(), None, policy=policy).get_manifest()
    row = {
        "connector_key": policy.connector_key,
        "version": manifest.version,
        "display_name": manifest.display_name,
        "connector_class": "MCP",
        "trust_level": "WORKSPACE_PRIVATE",
        "manifest": manifest.model_dump(mode="json", by_alias=True),
        "active": True,
    }
    dialect = database.get_bind().dialect.name
    if dialect == "postgresql":
        await database.execute(
            pg_insert(ConnectorDefinition)
            .values(**row)
            .on_conflict_do_nothing(
                index_elements=[ConnectorDefinition.connector_key, ConnectorDefinition.version]
            )
        )
    elif dialect == "sqlite":
        await database.execute(
            sqlite_insert(ConnectorDefinition)
            .values(**row)
            .on_conflict_do_nothing(
                index_elements=[ConnectorDefinition.connector_key, ConnectorDefinition.version]
            )
        )
    else:
        raise HTTPException(503, "MCP registration requires a supported database")
    definition = await database.scalar(
        select(ConnectorDefinition)
        .where(
            ConnectorDefinition.connector_key == policy.connector_key,
            ConnectorDefinition.version == manifest.version,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        definition is None
        or not definition.active
        or definition.manifest != row["manifest"]
        or definition.connector_class != "MCP"
    ):
        raise HTTPException(409, "Reviewed MCP connector definition is unavailable")
    return definition


async def queue(
    database: DatabaseSession,
    connection: ConnectorConnection,
    request_id: UUID,
    settings: Settings,
) -> str:
    payload = ConnectorSyncWork(
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
        await dispatch_connector_sync(payload, settings=settings, initial=True)
        return "queued"
    except Exception:
        return "pending"


@router.post("/connect", status_code=201)
async def connect(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    if not policies(settings):
        raise HTTPException(409, "MCP setup is not enabled for this deployment")
    command = await _read_command(request)
    policy = selected_policy(settings, command.server_id)
    declared = {
        item.name
        for item in MCPConnector(policy.connection_config(), None, policy=policy)
        .get_manifest()
        .capabilities.read
    }
    capabilities = set(command.capabilities)
    if len(capabilities) != len(command.capabilities) or not capabilities <= declared:
        raise HTTPException(422, "Only distinct reviewed MCP read capabilities may be selected")
    token = command.token.get_secret_value() if command.token is not None else None
    if policy.token_secret_name:
        if (
            not token
            or len(token) > 16_384
            or any(ord(character) < 33 or ord(character) > 126 for character in token)
        ):
            raise HTTPException(422, "A valid MCP server token is required")
        try:
            broker = SecretBroker(settings)
        except SecretBrokerError:
            raise HTTPException(503, "MCP credential encryption is unavailable") from None
    elif token is not None:
        raise HTTPException(422, "This MCP server does not use a token")
    else:
        broker = None
    if account.user.agent_paused:
        raise HTTPException(409, "Resume the agent before connecting MCP")
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
        raise HTTPException(403, "MCP setup is not authorized")
    connection_id = uuid5(
        NAMESPACE_URL,
        f"navox:mcp:{account.workspace.id}:{user.id}:{policy.connector_key}",
    )
    previous = await database.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == account.workspace.id,
            AuditEvent.user_id == user.id,
            AuditEvent.event_type == "connector.mcp.connected",
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
            or existing.config != policy.connection_config()
            or existing.status in {"PAUSED", "DISCONNECTED"}
            or set(existing.authorized_capabilities) != capabilities
            or previous is None
        ):
            raise HTTPException(409, "MCP connection exists; review its permissions")
        return {"connection_id": str(existing.id), "dispatch_status": "existing", "reused": True}
    count = await database.scalar(
        select(func.count())
        .select_from(ConnectorConnection)
        .where(
            ConnectorConnection.workspace_id == account.workspace.id,
            ConnectorConnection.user_id == user.id,
            ConnectorConnection.connector_definition_id.in_(
                select(ConnectorDefinition.id).where(ConnectorDefinition.connector_class == "MCP")
            ),
        )
    )
    if count is not None and count >= MAX_CONNECTIONS_PER_OWNER:
        raise HTTPException(409, "MCP connection limit reached")
    definition = await _definition(database, policy)
    connection = ConnectorConnection(
        id=connection_id,
        connector_definition_id=definition.id,
        user_id=user.id,
        workspace_id=account.workspace.id,
        provider=policy.provider,
        external_account_id=f"configured:{user.id}:{policy.connector_key}",
        display_name=policy.display_name,
        authorized_capabilities=sorted(capabilities),
        provider_capabilities=sorted(declared),
        config=policy.connection_config(),
    )
    database.add(connection)
    await database.flush()
    if token is not None and broker is not None and policy.token_secret_name is not None:
        try:
            await broker.store(
                database,
                {policy.token_secret_name: token},
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
            )
        except SecretBrokerError:
            await database.rollback()
            raise HTTPException(503, "MCP credential could not be stored") from None
    database.add(
        AuditEvent(
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
            actor_type="user",
            event_type="connector.mcp.connected",
            entity_type="connector_connection",
            entity_id=connection.id,
            event_metadata={
                "request_id": str(command.request_id),
                "server_id": policy.server_id,
                "capabilities": sorted(capabilities),
            },
        )
    )
    status = await queue(database, connection, command.request_id, settings)
    return {"connection_id": str(connection.id), "dispatch_status": status, "reused": False}
