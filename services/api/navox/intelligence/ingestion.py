"""Authorized Google reads feed the provider-neutral intelligence boundary."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    CommitmentSource,
    Connection,
    IntelligenceCursor,
    ProviderEventSubscription,
    User,
    WorkspaceMembership,
)
from navox.intelligence.extraction import (
    OperationalExtraction,
    OperationalExtractionResult,
    OperationalExtractor,
    source_document_hash,
)
from navox.intelligence.resolution import resolve_extraction
from navox.providers.google_oauth import access_token_for_connection
from navox.providers.google_sources import (
    CALENDAR_ROOT,
    GMAIL_ROOT,
    SOURCE_SCOPES,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
    GoogleSourceGateway,
)


async def authorized_connection(
    database: AsyncSession,
    connection_id: UUID,
    source: str,
) -> tuple[Connection, User]:
    if source not in SOURCE_SCOPES:
        raise GoogleSourceAuthorizationError("Unsupported intelligence source")
    connection = await database.scalar(
        select(Connection)
        .where(Connection.id == connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        connection is None
        or connection.provider != "google"
        or connection.status != "active"
        or SOURCE_SCOPES[source] not in connection.granted_scopes
    ):
        raise GoogleSourceAuthorizationError("Google read permission is required")
    user = await database.get(User, connection.user_id, populate_existing=True)
    member = await database.get(WorkspaceMembership, (connection.workspace_id, connection.user_id))
    if user is None or user.agent_paused or member is None:
        raise GoogleSourceAuthorizationError("Intelligence processing is paused or unauthorized")
    return connection, user


async def process_connection(
    database: AsyncSession,
    *,
    connection_id: UUID,
    source: str,
    settings: Settings,
    extractor: OperationalExtractor,
) -> list[UUID]:
    """Process one complete batch atomically; the caller commits or rolls back.

    A locked connection serializes ingestion and permission revocation. Any read,
    extraction, or resolution failure leaves the old cursor intact on rollback.
    """
    connection, user = await authorized_connection(database, connection_id, source)
    token = await access_token_for_connection(database, connection=connection, settings=settings)
    # Refresh can narrow scopes. Flush before reloading the authoritative row.
    await database.flush()
    connection, user = await authorized_connection(database, connection_id, source)
    cursor = await database.scalar(
        select(IntelligenceCursor).where(
            IntelligenceCursor.connection_id == connection_id,
            IntelligenceCursor.source == source,
        )
    )
    gateway = GoogleSourceGateway()
    batch = await gateway.fetch(
        source=source,
        access_token=token,
        workspace_id=connection.workspace_id,
        connection_id=connection_id,
        cursor=cursor.cursor if cursor else None,
    )
    documents = list(batch.documents)
    if batch.reset:
        # A bounded bootstrap cannot prove that older known resources still exist.
        # Re-read those IDs explicitly to recover missed deletions or corrections.
        known_ids = set(
            await database.scalars(
                select(CommitmentSource.external_resource_id)
                .where(
                    CommitmentSource.connection_id == connection_id,
                    CommitmentSource.source_type
                    == ("gmail_message" if source == "gmail" else "calendar_event"),
                )
                .distinct()
                .limit(2001)
            )
        )
        if len(known_ids) > 2000:
            raise GoogleSourceError("Historical reconciliation exceeded its resource budget")
        known_ids.difference_update(document.external_id for document in documents)
        documents.extend(
            await gateway.reconcile_existing(
                source=source,
                access_token=token,
                workspace_id=connection.workspace_id,
                connection_id=connection_id,
                external_ids=sorted(
                    identifier for identifier in known_ids if identifier is not None
                ),
            )
        )
    commitment_ids: set[UUID] = set()
    for document in documents:
        connection, user = await authorized_connection(database, connection_id, source)
        if document.metadata.get("status") in {"cancelled", "deleted"}:
            result = OperationalExtractionResult(
                extraction=OperationalExtraction(),
                extractor_version="provider-tombstone.v1",
                model_provider="deterministic",
                model_name="provider-state",
                source_hash=source_document_hash(document),
            )
        else:
            result = await extractor.extract(document)
        # Recheck after a slow provider/model call before applying any proposal.
        connection, user = await authorized_connection(database, connection_id, source)
        commitment_ids.update(
            await resolve_extraction(
                database,
                connection=connection,
                document=document,
                result=result,
                timezone_name=user.timezone,
            )
        )
    if cursor is None:
        cursor = IntelligenceCursor(connection_id=connection_id, source=source)
        database.add(cursor)
    cursor.cursor = batch.cursor
    cursor.updated_at = datetime.now(UTC)
    database.add(
        AuditEvent(
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            event_type="intelligence.source_processed",
            actor_type="system",
            entity_type="connection",
            entity_id=connection.id,
            event_metadata={
                "source": source,
                "documents": len(documents),
                "commitments": len(commitment_ids),
                "cursor_reset": batch.reset,
            },
        )
    )
    await database.flush()
    return sorted(commitment_ids, key=str)


async def renew_source_watch(
    database: AsyncSession,
    *,
    connection_id: UUID,
    source: str,
    settings: Settings,
) -> bool:
    """Renew watches with an explicit lease. Caller must use a dedicated session.

    Calendar channels are committed before registration because Google may deliver
    the initial sync notification before the watch HTTP response arrives.
    """
    target = (
        settings.google_gmail_watch_topic
        if source == "gmail"
        else settings.google_calendar_push_url
    )
    if not target:
        return False
    if source == "calendar" and not target.startswith("https://"):
        raise GoogleSourceError("Calendar push delivery requires an HTTPS URL")
    connection, _ = await authorized_connection(database, connection_id, source)
    active = await database.scalar(
        select(ProviderEventSubscription).where(
            ProviderEventSubscription.connection_id == connection_id,
            ProviderEventSubscription.source == source,
            ProviderEventSubscription.status == "active",
            ProviderEventSubscription.expires_at > datetime.now(UTC) + timedelta(days=1),
        )
    )
    if active is not None:
        return True
    token = await access_token_for_connection(database, connection=connection, settings=settings)
    await database.flush()
    connection, _ = await authorized_connection(database, connection_id, source)
    channel_token = token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=6)
    subscription = ProviderEventSubscription(
        connection_id=connection.id,
        workspace_id=connection.workspace_id,
        user_id=connection.user_id,
        provider="google",
        source=source,
        channel_id=str(uuid4()),
        channel_token_hash=sha256(channel_token.encode()).hexdigest(),
        status="active",
        # A crashed registration must be retried, not mistaken for a six-day lease.
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    database.add(subscription)
    await database.commit()
    payload = (
        {"topicName": target}
        if source == "gmail"
        else {
            "id": subscription.channel_id,
            "type": "web_hook",
            "address": target,
            "token": channel_token,
            "expiration": str(int(expires.timestamp() * 1000)),
        }
    )
    endpoint = f"{GMAIL_ROOT}/watch" if source == "gmail" else f"{CALENDAR_ROOT}/watch"
    try:
        # Pause or revocation after the pre-registration commit still fails closed.
        await authorized_connection(database, connection_id, source)
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                endpoint, headers={"Authorization": f"Bearer {token}"}, json=payload
            )
            response.raise_for_status()
            data = response.json()
        expiration = datetime.fromtimestamp(int(data["expiration"]) / 1000, UTC)
        if expiration <= datetime.now(UTC):
            raise ValueError("Expired provider lease")
        subscription.expires_at = expiration
        if source == "calendar":
            resource_id = data.get("resourceId")
            if not isinstance(resource_id, str) or not resource_id:
                raise ValueError("Missing watch resource")
            subscription.resource_id = resource_id
        await database.commit()
    except (httpx.HTTPError, ValueError, KeyError, TypeError, GoogleSourceError) as error:
        subscription.status = "failed"
        await database.commit()
        raise GoogleSourceError("Google watch registration failed") from error
    return True
