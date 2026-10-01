"""Foundation-only VIEW eligibility for SPEC-007 M1 knowledge resources.

This predicate reads current persisted authority on every call: membership,
resource deletion, the exact capability the resource was bound to, the live
canonical source, the connector lifecycle state and the permission rows. It is a
necessary foundation check, not a cache, session, token or permanent grant:
later retrieval consumers must revalidate at their own context-assembly
boundary and must never treat stored content as authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast, get_args
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import ConnectorHealthState, ConnectorManifest
from navox.db.knowledge import KnowledgeResource, KnowledgeResourcePermission
from navox.db.models import (
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
    WorkspaceMembership,
)
from navox.knowledge.contracts import (
    STRUCTURED_RESOURCE_TYPES,
    Permission,
    ResourceType,
    aware_utc,
    permission_interval_active,
    principal_matches,
    validate_source_read_capability,
)

ACTIVE_CONNECTION_STATUSES = frozenset({"CONNECTED", "DEGRADED"})
KNOWN_HEALTH_STATES = frozenset(get_args(ConnectorHealthState))

# Authority and identity only. Stored content and metadata are deliberately not
# loaded while a decision is pending, so inaccessible matching text never enters
# the session or the identity map.
RESOURCE_AUTHORITY_COLUMNS = (
    KnowledgeResource.id,
    KnowledgeResource.workspace_id,
    KnowledgeResource.owner_user_id,
    KnowledgeResource.source_type,
    KnowledgeResource.source_connection_id,
    KnowledgeResource.external_resource_id,
    KnowledgeResource.source_resource_id,
    KnowledgeResource.source_read_capability,
    KnowledgeResource.deleted_at,
)
CONNECTION_AUTHORITY_COLUMNS = (
    ConnectorConnection.id,
    ConnectorConnection.workspace_id,
    ConnectorConnection.user_id,
    ConnectorConnection.connector_definition_id,
    ConnectorConnection.legacy_connection_id,
    ConnectorConnection.provider,
    ConnectorConnection.status,
    ConnectorConnection.health_state,
    ConnectorConnection.authorized_capabilities,
    ConnectorConnection.provider_capabilities,
)


def effective_read_capabilities(
    definition: ConnectorDefinition | None,
    connection: ConnectorConnection,
) -> frozenset[str]:
    """Intersect declared, provider and user-authorized read capabilities."""
    if definition is None or not definition.active:
        return frozenset()
    if connection.status not in ACTIVE_CONNECTION_STATUSES:
        return frozenset()
    health_state = connection.health_state
    if health_state not in KNOWN_HEALTH_STATES:
        return frozenset()
    try:
        manifest = ConnectorManifest.model_validate(definition.manifest)
    except ValidationError:
        return frozenset()
    if manifest.id != definition.connector_key or manifest.version != definition.version:
        # The connector runtime registers a manifest that matches its definition.
        # A mismatch means stored authority no longer describes this connection.
        return frozenset()
    authorized = set(connection.authorized_capabilities or [])
    return (
        CapabilityGateway()
        .evaluate(
            manifest=manifest,
            provider_capabilities=set(connection.provider_capabilities or []),
            user_authorized=authorized,
            policy_allowed=authorized,
            health_state=cast(ConnectorHealthState, health_state),
        )
        .read
    )


def _is_eligible_type(source_type: str) -> bool:
    try:
        kind = ResourceType(source_type)
    except ValueError:
        return False
    return kind not in STRUCTURED_RESOURCE_TYPES


async def _current_authority(
    database: AsyncSession, resource: KnowledgeResource
) -> tuple[ConnectorConnection, ConnectorDefinition | None] | None:
    connection = await database.scalar(
        select(ConnectorConnection)
        .options(load_only(*CONNECTION_AUTHORITY_COLUMNS))
        .where(
            ConnectorConnection.id == resource.source_connection_id,
            ConnectorConnection.workspace_id == resource.workspace_id,
            ConnectorConnection.user_id == resource.owner_user_id,
        )
        .execution_options(populate_existing=True)
    )
    if connection is None:
        return None
    definition = await database.scalar(
        select(ConnectorDefinition)
        .where(ConnectorDefinition.id == connection.connector_definition_id)
        .execution_options(populate_existing=True)
    )
    return connection, definition


async def legacy_anchor_active(
    database: AsyncSession, connection: ConnectorConnection, workspace_id: UUID
) -> bool:
    if connection.legacy_connection_id is None:
        return True
    legacy = await database.scalar(
        select(Connection)
        .where(Connection.id == connection.legacy_connection_id)
        .execution_options(populate_existing=True)
    )
    if legacy is None:
        return False
    return (
        legacy.workspace_id == workspace_id
        and legacy.user_id == connection.user_id
        and legacy.provider == connection.provider
        and legacy.status == "active"
    )


async def _canonical_source_is_live(database: AsyncSession, resource: KnowledgeResource) -> bool:
    if resource.source_resource_id is None:
        return False
    deleted = await database.scalar(
        select(ConnectorResource.deleted)
        .where(
            ConnectorResource.id == resource.source_resource_id,
            ConnectorResource.workspace_id == resource.workspace_id,
            ConnectorResource.connector_connection_id == resource.source_connection_id,
            ConnectorResource.external_id == resource.external_resource_id,
        )
        .execution_options(populate_existing=True)
    )
    return deleted is False


async def _member_and_user_current(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, owner_user_id: UUID
) -> bool:
    # A shared grant never outlives the source owner's own authority: both the
    # caller and the resource owner must still be current, unpaused members.
    principals = (user_id,) if user_id == owner_user_id else (user_id, owner_user_id)
    for principal in principals:
        member = await database.scalar(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == principal,
            )
            .execution_options(populate_existing=True)
        )
        if member is None:
            return False
        user = await database.scalar(
            select(User).where(User.id == principal).execution_options(populate_existing=True)
        )
        if user is None or user.agent_paused:
            return False
    return True


async def _has_effective_view_grant(
    database: AsyncSession, resource: KnowledgeResource, *, user_id: UUID, now: datetime
) -> bool:
    grants = await database.scalars(
        select(KnowledgeResourcePermission)
        .where(
            KnowledgeResourcePermission.resource_id == resource.id,
            KnowledgeResourcePermission.workspace_id == resource.workspace_id,
            KnowledgeResourcePermission.permission == Permission.VIEW.value,
        )
        .execution_options(populate_existing=True)
    )
    for grant in grants:
        if not principal_matches(
            grant.principal_type,
            grant.principal_id,
            workspace_id=resource.workspace_id,
            user_id=user_id,
        ):
            continue
        if permission_interval_active(
            valid_from=grant.valid_from,
            valid_until=grant.valid_until,
            revoked_at=grant.revoked_at,
            now=now,
        ):
            return True
    return False


async def can_view_resource(
    database: AsyncSession,
    *,
    resource_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> bool:
    """Return whether ``user_id`` may currently VIEW this resource.

    Every denial path is explicit and conservative: unknown scope, no explicit
    effective VIEW grant, missing membership, deleted resource or source,
    withdrawn capability, disabled definition, inactive or disconnected source,
    missing canonical provenance and structured domains without an adapter.
    """
    moment = aware_utc(now)
    resource = await database.scalar(
        select(KnowledgeResource)
        .options(load_only(*RESOURCE_AUTHORITY_COLUMNS))
        .where(
            KnowledgeResource.id == resource_id,
            KnowledgeResource.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if resource is None or resource.deleted_at is not None:
        return False
    if not _is_eligible_type(resource.source_type):
        return False
    try:
        capability = validate_source_read_capability(resource.source_read_capability)
    except ValueError:
        return False
    if not await _member_and_user_current(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        owner_user_id=resource.owner_user_id,
    ):
        return False
    authority = await _current_authority(database, resource)
    if authority is None:
        return False
    connection, definition = authority
    if capability not in effective_read_capabilities(definition, connection):
        return False
    if not await legacy_anchor_active(database, connection, workspace_id):
        return False
    if not await _canonical_source_is_live(database, resource):
        return False
    return await _has_effective_view_grant(database, resource, user_id=user_id, now=moment)
