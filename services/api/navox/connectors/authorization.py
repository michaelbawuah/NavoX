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
    lock_authority: bool = False,
    lock_connection: bool = True,
) -> ConnectorConnection:
    # Refresh even when the caller retains an ORM object from an earlier operation.
    connection_query = select(ConnectorConnection).where(
        ConnectorConnection.id == connection_id,
        ConnectorConnection.workspace_id == workspace_id,
        ConnectorConnection.user_id == user_id,
    )
    if lock_connection:
        connection_query = connection_query.with_for_update()
    connection = await database.scalar(connection_query.execution_options(populate_existing=True))
    member_query = select(WorkspaceMembership).where(
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
    )
    user_query = select(User).where(User.id == user_id)
    if lock_authority:
        member_query = member_query.with_for_update()
        user_query = user_query.with_for_update()
    member = await database.scalar(member_query.execution_options(populate_existing=True))
    user = await database.scalar(user_query.execution_options(populate_existing=True))
    if connection is None or member is None or user is None:
        raise ConnectorAccessDenied("Connector access denied")
    definition_query = select(ConnectorDefinition).where(
        ConnectorDefinition.id == connection.connector_definition_id
    )
    if lock_authority:
        definition_query = definition_query.with_for_update()
    definition = await database.scalar(definition_query.execution_options(populate_existing=True))
    if require_active and (
        definition is None
        or not definition.active
        or user.agent_paused
        or connection.status not in {"CONNECTED", "DEGRADED"}
        or connection.health_state in {"PAUSED", "DISCONNECTED"}
    ):
        raise ConnectorAccessDenied("Connector access denied")
    return connection
