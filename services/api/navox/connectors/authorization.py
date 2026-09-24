"""Owner-bound checks shared by privileged connector credential operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import ConnectorConnection, ConnectorDefinition, User, WorkspaceMembership


class ConnectorAccessDenied(ValueError):
    """Only fixed diagnostics cross this boundary, never provider or secret data."""


async def owned_connector(
    database: AsyncSession,
    *,
    connection_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    require_active: bool,
) -> ConnectorConnection:
    # Refresh even when the caller retains an ORM object from an earlier operation.
    connection = await database.scalar(
        select(ConnectorConnection)
        .where(
            ConnectorConnection.id == connection_id,
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    member = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    user = await database.get(User, user_id, populate_existing=True)
    if connection is None or member is None or user is None:
        raise ConnectorAccessDenied("Connector access denied")
    definition = await database.get(
        ConnectorDefinition, connection.connector_definition_id, populate_existing=True
    )
    if require_active and (
        definition is None
        or not definition.active
        or user.agent_paused
        or connection.status not in {"CONNECTED", "DEGRADED"}
        or connection.health_state in {"PAUSED", "DISCONNECTED"}
    ):
        raise ConnectorAccessDenied("Connector access denied")
    return connection
