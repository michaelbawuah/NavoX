"""Read-only authority checks; never lock a connector/feed after the User lock."""

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.google import GOOGLE_CONNECTOR_KEY
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST
from navox.db.lms_feeds import LmsFeed, LmsFeedItem, LmsFeedSource
from navox.db.models import Connection, ConnectorConnection, ConnectorDefinition, ConnectorResource
from navox.db.student_email import EmailTaskSource
from navox.db.student_planning import StudentTask
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.student_email.service import source_authority_hash


async def lms_current(
    database: AsyncSession, task: StudentTask, user: UUID, workspace: UUID
) -> bool:
    item = (
        await database.execute(
            select(LmsFeedItem.revision, LmsFeedItem.cancelled, LmsFeedItem.conflicted).where(
                LmsFeedItem.id == task.source_item_id,
                LmsFeedItem.user_id == user,
                LmsFeedItem.workspace_id == workspace,
            )
        )
    ).one_or_none()
    if item is None or item.revision != task.source_revision or item.cancelled or item.conflicted:
        return False
    links = list(
        await database.execute(
            select(
                LmsFeed.user_id.label("feed_user"),
                LmsFeed.workspace_id.label("feed_workspace"),
                ConnectorConnection.user_id.label("connector_user"),
                ConnectorConnection.workspace_id.label("connector_workspace"),
                ConnectorConnection.status,
                ConnectorConnection.authorized_capabilities,
                ConnectorConnection.provider_capabilities,
                ConnectorDefinition.active,
            )
            .select_from(LmsFeedSource)
            .join(LmsFeed, LmsFeed.id == LmsFeedSource.feed_id)
            .join(ConnectorConnection, ConnectorConnection.id == LmsFeed.connection_id)
            .join(
                ConnectorDefinition,
                ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
            )
            .where(LmsFeedSource.item_id == task.source_item_id)
            .limit(2001)
        )
    )
    return (
        bool(links)
        and len(links) <= 2000
        and all(
            row.feed_user == row.connector_user == user
            and row.feed_workspace == row.connector_workspace == workspace
            and row.active
            and row.status in {"CONNECTED", "DEGRADED"}
            and "academic.calendar.read" in row.authorized_capabilities
            and "academic.calendar.read" in row.provider_capabilities
            for row in links
        )
    )


async def email_current(
    database: AsyncSession, task: StudentTask, user: UUID, workspace: UUID
) -> bool:
    provenance = await database.scalar(
        select(EmailTaskSource)
        .where(
            EmailTaskSource.task_id == task.id,
            EmailTaskSource.user_id == user,
            EmailTaskSource.workspace_id == workspace,
        )
        .execution_options(populate_existing=True)
    )
    if provenance is None or provenance.source_id is None or provenance.connector_id is None:
        return False
    # Read metadata and the deletion marker only, never an email body or canonical payload.
    resource = (
        await database.execute(
            select(
                ConnectorResource.connector_connection_id,
                ConnectorResource.content_hash,
                ConnectorResource.provider,
                ConnectorResource.external_id,
                ConnectorResource.canonical["status"].as_string().label("source_status"),
            ).where(
                ConnectorResource.id == provenance.source_id,
                ConnectorResource.workspace_id == workspace,
                ConnectorResource.resource_type == "communication.message",
                ConnectorResource.deleted.is_(False),
            )
        )
    ).one_or_none()
    if (
        resource is None
        or resource.source_status == "deleted"
        or resource.connector_connection_id != provenance.connector_id
        or resource.content_hash != provenance.source_hash
        or resource.provider != "google"
        or not re.fullmatch(r"[a-fA-F0-9]{8,128}", resource.external_id)
    ):
        return False
    try:
        # require_active=False avoids gating manual cached facts on AI pause.
        # Current grants/status/definition are checked explicitly below, with no locks.
        connector = await owned_connector(
            database,
            connection_id=provenance.connector_id,
            user_id=user,
            workspace_id=workspace,
            require_active=False,
            lock_connection=False,
            lock_authority=False,
        )
    except ConnectorAccessDenied:
        return False
    definition = await database.get(
        ConnectorDefinition, connector.connector_definition_id, populate_existing=True
    )
    legacy = (
        await database.get(Connection, connector.legacy_connection_id, populate_existing=True)
        if connector.legacy_connection_id
        else None
    )
    if (
        definition is None
        or not definition.active
        or definition.connector_class != "OAUTH_API"
        or definition.trust_level != "NAVOX_FIRST_PARTY"
        or definition.connector_key not in {GMAIL_MANIFEST.id, GOOGLE_CONNECTOR_KEY}
        or connector.status not in {"CONNECTED", "DEGRADED"}
        or connector.health_state in {"PAUSED", "DISCONNECTED"}
        or "communication.messages.read" not in connector.authorized_capabilities
        or "communication.messages.read" not in connector.provider_capabilities
        or legacy is None
        or (legacy.user_id, legacy.workspace_id) != (user, workspace)
        or legacy.provider != "google"
        or legacy.status != "active"
        or GMAIL_READ_SCOPE not in legacy.granted_scopes
    ):
        return False
    return (
        await source_authority_hash(database, connector, in_flight=False)
        == provenance.authority_hash
    )
