"""Authorized Google reads feed the provider-neutral intelligence boundary."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    CommitmentSource,
    Connection,
    IntelligenceCursor,
    IntelligenceSourceReceipt,
    ProviderEventSubscription,
    User,
    WorkspaceMembership,
)
from navox.intelligence.extraction import (
    InvalidOperationalExtraction,
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
    if connection is None or connection.provider != "google" or connection.status != "active":
        raise GoogleSourceAuthorizationError("Google read permission is required")
    if SOURCE_SCOPES[source] not in connection.granted_scopes:
        raise GoogleSourceAuthorizationError(
            "Google read permission is required", code="google_scope_missing"
        )
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
    """Checkpoint each source revision; the caller commits the final cursor.

    Connection locks serialize each checkpoint with ingestion and revocation.
    Transient failures retain the old cursor, but accepted revisions survive and
    are skipped on retry. Invalid proposals are recorded without their content
    and cannot indefinitely block unrelated mail. Use a dedicated session.
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
    initial_cursor = cursor.cursor if cursor else None
    gateway = GoogleSourceGateway()
    batch = await gateway.fetch(
        source=source,
        access_token=token,
        workspace_id=connection.workspace_id,
        connection_id=connection_id,
        cursor=initial_cursor,
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
    # Provider list order is not causal order (Gmail bootstrap is newest first).
    # Resolve original requests before later outcome evidence, including documents
    # recovered outside the bootstrap window during expired-cursor reconciliation.
    documents.sort(key=lambda document: (document.occurred_at, str(document.id)))
    commitment_ids: set[UUID] = set()
    skipped = rejected = 0
    for document in documents:
        connection, user = await authorized_connection(database, connection_id, source)
        if document.workspace_id != connection.workspace_id or document.provider != "google":
            raise GoogleSourceAuthorizationError("Source does not belong to this workspace")
        source_hash = source_document_hash(document)
        tombstone = document.metadata.get("status") in {"cancelled", "deleted"}
        extractor_version = "provider-tombstone.v1" if tombstone else extractor.extractor_version
        latest_revision = await database.scalar(
            select(func.max(IntelligenceSourceReceipt.source_occurred_at)).where(
                IntelligenceSourceReceipt.connection_id == connection_id,
                IntelligenceSourceReceipt.source == source,
                IntelligenceSourceReceipt.external_id == document.external_id,
            )
        )
        if latest_revision is not None:
            if latest_revision.tzinfo is None:
                latest_revision = latest_revision.replace(tzinfo=UTC)
            if document.occurred_at < latest_revision:
                # Empty/rejected/deleted revisions have no observation evidence,
                # but still prevent an older overlapping batch resurrecting facts.
                skipped += 1
                await database.commit()
                continue
        receipt = await database.scalar(
            select(IntelligenceSourceReceipt).where(
                IntelligenceSourceReceipt.connection_id == connection_id,
                IntelligenceSourceReceipt.source == source,
                IntelligenceSourceReceipt.external_id == document.external_id,
                IntelligenceSourceReceipt.source_hash == source_hash,
                IntelligenceSourceReceipt.extractor_version == extractor_version,
            )
        )
        if receipt is not None:
            commitment_ids.update(UUID(identifier) for identifier in receipt.commitment_ids)
            skipped += 1
            await database.commit()
            continue
        if tombstone:
            result = OperationalExtractionResult(
                extraction=OperationalExtraction(),
                extractor_version="provider-tombstone.v1",
                model_provider="deterministic",
                model_name="provider-state",
                source_hash=source_hash,
            )
        else:
            try:
                result = await extractor.extract(document)
            except InvalidOperationalExtraction as error:
                await authorized_connection(database, connection_id, source)
                database.add(
                    IntelligenceSourceReceipt(
                        connection_id=connection_id,
                        source=source,
                        external_id=document.external_id,
                        source_hash=source_hash,
                        extractor_version=extractor_version,
                        outcome="rejected",
                        source_occurred_at=document.occurred_at,
                        commitment_ids=[],
                    )
                )
                database.add(
                    AuditEvent(
                        user_id=connection.user_id,
                        workspace_id=connection.workspace_id,
                        event_type="intelligence.extraction.rejected",
                        actor_type="system",
                        entity_type="connection",
                        entity_id=connection_id,
                        event_metadata={
                            "source": source,
                            "source_hash": source_hash,
                            "extractor_version": extractor_version,
                            "reason": "invalid_model_proposal",
                            "validation_error": error.diagnostic(),
                        },
                    )
                )
                await database.commit()
                rejected += 1
                continue
        # Recheck after a slow provider/model call before applying any proposal.
        connection, user = await authorized_connection(database, connection_id, source)
        resolved_ids = await resolve_extraction(
            database,
            connection=connection,
            document=document,
            result=result,
            timezone_name=user.timezone,
        )
        commitment_ids.update(resolved_ids)
        database.add(
            IntelligenceSourceReceipt(
                connection_id=connection_id,
                source=source,
                external_id=document.external_id,
                source_hash=source_hash,
                extractor_version=extractor_version,
                outcome="processed",
                source_occurred_at=document.occurred_at,
                commitment_ids=[str(identifier) for identifier in resolved_ids],
            )
        )
        # The receipt and the evidence/state it acknowledges commit together.
        await database.commit()

    connection, _ = await authorized_connection(database, connection_id, source)
    cursor = await database.scalar(
        select(IntelligenceCursor)
        .where(
            IntelligenceCursor.connection_id == connection_id,
            IntelligenceCursor.source == source,
        )
        .execution_options(populate_existing=True)
    )
    # Another sync may have advanced the opaque cursor between checkpoints.
    # Never overwrite its progress with the older snapshot's replacement token.
    cursor_advanced = (cursor.cursor if cursor else None) == initial_cursor
    if cursor is None:
        cursor = IntelligenceCursor(connection_id=connection_id, source=source)
        database.add(cursor)
    if cursor_advanced:
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
                "cursor_advanced": cursor_advanced,
                "skipped_revisions": skipped,
                "rejected_revisions": rejected,
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
