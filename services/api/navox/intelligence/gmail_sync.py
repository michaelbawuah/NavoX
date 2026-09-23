"""Durable Gmail progress containing only IDs, timestamps and processing receipts.

Enumerating, ordering and processing are separate checkpointed phases. Mail bodies
remain transient and the source cursor advances only after every planned revision
has a durable outcome. Connection locks serialize each checkpoint and revocation.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    CommitmentSource,
    GmailSyncPlan,
    IntelligenceCursor,
)
from navox.intelligence.extraction import OperationalExtractor
from navox.intelligence.source_cooldown import GoogleSourceCooldownError, source_cooldown
from navox.providers.google_sources import MAX_PAGES, ExpiredSourceCursor, GoogleSourceError

READ_SPACING_SECONDS = 0.5


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _ordered(entries: list[dict[str, object]], connection_id: UUID) -> list[dict[str, object]]:
    return sorted(
        entries,
        key=lambda entry: (
            datetime.fromisoformat(str(entry["occurred_at"])),
            str(uuid5(NAMESPACE_URL, f"{connection_id}/gmail/{entry['id']}")),
        ),
    )


async def _pace_plan_read(plan: GmailSyncPlan) -> None:
    """A shared checkpoint bounds starts even when two workers alternate reads."""
    now = datetime.now(UTC)
    if plan.next_read_at is not None:
        remaining = (_utc(plan.next_read_at) - now).total_seconds()
        if remaining > 0:
            await asyncio.sleep(min(remaining, READ_SPACING_SECONDS))
    plan.next_read_at = datetime.now(UTC) + timedelta(seconds=READ_SPACING_SECONDS)


def _read_finished(plan: GmailSyncPlan) -> None:
    plan.next_read_at = datetime.now(UTC) + timedelta(seconds=READ_SPACING_SECONDS)


async def _completed_ids(
    database: AsyncSession, *, connection_id: UUID, plan_id: UUID
) -> list[UUID]:
    metadata = await database.scalar(
        select(AuditEvent.event_metadata).where(
            AuditEvent.entity_id == connection_id,
            AuditEvent.entity_type == "connection",
            AuditEvent.event_type == "intelligence.source_processed",
            AuditEvent.event_metadata["resume_plan_id"].as_string() == str(plan_id),
        )
    )
    if metadata is None:
        raise GoogleSourceError("Gmail progress changed before completion")
    values = metadata.get("commitment_ids", [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise GoogleSourceError("Gmail completion progress is invalid")
    return sorted((UUID(value) for value in values), key=str)


async def process_gmail_connection(
    database: AsyncSession,
    *,
    connection_id: UUID,
    settings: Settings,
    extractor: OperationalExtractor,
) -> list[UUID]:
    # Keep these patch points shared with the existing connector integration tests.
    from navox.intelligence import ingestion

    gateway = ingestion.GoogleSourceGateway()
    observed_plan_id: UUID | None = None
    access_token: str | None = None
    token_obtained_at: datetime | None = None
    token_credential_id: UUID | None = None
    while True:
        connection, user = await ingestion.authorized_connection(database, connection_id, "gmail")
        cooldown = await source_cooldown(database, connection_id, "gmail")
        if cooldown is not None:
            raise GoogleSourceCooldownError(cooldown)
        plan = await database.scalar(
            select(GmailSyncPlan)
            .where(GmailSyncPlan.connection_id == connection_id)
            .execution_options(populate_existing=True)
        )
        if observed_plan_id is not None and (plan is None or plan.id != observed_plan_id):
            return await _completed_ids(
                database, connection_id=connection_id, plan_id=observed_plan_id
            )
        if plan is None:
            cursor = await database.scalar(
                select(IntelligenceCursor).where(
                    IntelligenceCursor.connection_id == connection_id,
                    IntelligenceCursor.source == "gmail",
                )
            )
            initial_cursor = cursor.cursor if cursor else None
            plan = GmailSyncPlan(
                connection_id=connection_id,
                initial_cursor=initial_cursor,
                cursor=initial_cursor,
                phase="list",
                entries=[],
                created_at=datetime.now(UTC),
            )
            database.add(plan)
            await database.flush()
        observed_plan_id = plan.id

        # A slow bootstrap may span a token lifetime. Refresh remains in memory;
        # only the existing protected refresh token is durable.
        now = datetime.now(UTC)
        if (
            access_token is None
            or token_obtained_at is None
            or now - token_obtained_at >= timedelta(minutes=45)
            or token_credential_id != connection.credential_reference
            or (
                connection.access_token_expires_at is not None
                and _utc(connection.access_token_expires_at) <= now + timedelta(minutes=1)
            )
        ):
            access_token = await ingestion.access_token_for_connection(
                database, connection=connection, settings=settings
            )
            token_obtained_at = now
            token_credential_id = connection.credential_reference
            await database.flush()
            connection, user = await ingestion.authorized_connection(
                database, connection_id, "gmail"
            )

        anchor = _utc(plan.created_at)
        if plan.phase == "list":
            bootstrap = plan.initial_cursor is None or plan.reset
            if bootstrap and plan.cursor is None:
                await _pace_plan_read(plan)
                plan.cursor = await gateway.gmail_profile(access_token)
                _read_finished(plan)
                await ingestion.authorized_connection(database, connection_id, "gmail")
                await database.commit()
                continue
            if plan.pages >= MAX_PAGES:
                raise GoogleSourceError("Gmail reconciliation exceeded its bounded page budget")
            await _pace_plan_read(plan)
            try:
                page = await gateway.gmail_page(
                    access_token,
                    cursor=None if bootstrap else plan.initial_cursor,
                    page_token=plan.page_token,
                    query=f"after:{int((anchor - timedelta(days=30)).timestamp())}",
                )
            except ExpiredSourceCursor:
                if bootstrap:
                    raise
                # Retain the original cursor for final compare-and-swap, while
                # establishing a fresh pre-scan anchor for complete reconciliation.
                plan.reset = True
                plan.cursor = None
                plan.entries = []
                plan.pages = 0
                plan.page_token = None
                _read_finished(plan)
                await ingestion.authorized_connection(database, connection_id, "gmail")
                await database.commit()
                continue
            except GoogleSourceError as error:
                if error.http_status == 400 and plan.page_token is not None:
                    # A persisted pagination token can expire while a job waits.
                    # No bodies have been processed in this phase, so discard
                    # only the incomplete ID enumeration. Preserve the original
                    # source cursor and fixed bootstrap window/history anchor.
                    await ingestion.authorized_connection(database, connection_id, "gmail")
                    plan.entries = []
                    plan.page_token = None
                    plan.pages = 0
                    _read_finished(plan)
                    await database.commit()
                # Retry belongs to the bounded workflow policy, never an
                # unbounded in-function restart for a persistent bad request.
                raise
            _read_finished(plan)
            await ingestion.authorized_connection(database, connection_id, "gmail")
            indexed_entries = {str(entry["id"]): dict(entry) for entry in plan.entries}
            for identifier, deleted in page.message_ids.items():
                indexed_entries[identifier] = {
                    "id": identifier,
                    "deleted": deleted,
                    "occurred_at": None,
                }
            if len(indexed_entries) > MAX_PAGES * 100:
                raise GoogleSourceError("Gmail reconciliation exceeded its resource budget")
            plan.entries = list(indexed_entries.values())
            plan.pages += 1
            plan.page_token = page.next_page_token
            if not bootstrap and page.cursor:
                plan.cursor = page.cursor
            if page.next_page_token is None:
                if not plan.cursor:
                    raise GoogleSourceError("Gmail did not return a history cursor")
                if plan.reset:
                    known_ids = set(
                        await database.scalars(
                            select(CommitmentSource.external_resource_id)
                            .where(
                                CommitmentSource.connection_id == connection_id,
                                CommitmentSource.source_type == "gmail_message",
                                CommitmentSource.external_resource_id.is_not(None),
                            )
                            .distinct()
                            .limit(2001)
                        )
                    )
                    if len(known_ids) > 2000:
                        raise GoogleSourceError(
                            "Historical reconciliation exceeded its resource budget"
                        )
                    for identifier in sorted(
                        identifier
                        for identifier in known_ids
                        if identifier is not None and identifier not in indexed_entries
                    ):
                        indexed_entries[identifier] = {
                            "id": identifier,
                            "deleted": False,
                            "occurred_at": None,
                        }
                    plan.entries = list(indexed_entries.values())
                    if len(plan.entries) > MAX_PAGES * 200:
                        raise GoogleSourceError(
                            "Historical reconciliation exceeded its resource budget"
                        )
                plan.phase = "metadata"
                plan.position = 0
            await database.commit()
            continue

        if plan.phase == "metadata":
            if plan.position == len(plan.entries):
                plan.entries = _ordered(plan.entries, connection_id)
                plan.phase = "process"
                plan.position = 0
                await database.commit()
                continue
            entries = [dict(entry) for entry in plan.entries]
            entry = entries[plan.position]
            occurred, deleted = anchor, bool(entry["deleted"])
            if not deleted:
                await _pace_plan_read(plan)
                occurred, deleted = await gateway.gmail_metadata(
                    access_token, external_id=str(entry["id"]), now=anchor
                )
                _read_finished(plan)
                await ingestion.authorized_connection(database, connection_id, "gmail")
            entry.update(occurred_at=occurred.isoformat(), deleted=deleted)
            plan.entries = entries
            plan.position += 1
            await database.commit()
            continue

        if plan.phase != "process":
            raise GoogleSourceError("Gmail progress phase is invalid")
        if plan.position < len(plan.entries):
            entry = plan.entries[plan.position]
            deleted = bool(entry["deleted"])
            if not deleted:
                await _pace_plan_read(plan)
            document = await gateway.gmail_message(
                access_token,
                workspace_id=connection.workspace_id,
                connection_id=connection_id,
                external_id=str(entry["id"]),
                now=datetime.fromisoformat(str(entry["occurred_at"])) if deleted else anchor,
                deleted=deleted,
            )
            if not deleted:
                _read_finished(plan)
            connection, user = await ingestion.authorized_connection(
                database, connection_id, "gmail"
            )
            if not deleted and document.metadata.get("status") == "deleted":
                # A message can disappear after the chronology pass. Defer its
                # tombstone until the remaining older source facts are processed.
                entries = [dict(item) for item in plan.entries]
                entries[plan.position].update(
                    deleted=True, occurred_at=datetime.now(UTC).isoformat()
                )
                plan.entries = entries[: plan.position] + _ordered(
                    entries[plan.position :], connection_id
                )
                await database.commit()
                continue
            if document.occurred_at != datetime.fromisoformat(str(entry["occurred_at"])):
                raise GoogleSourceError(
                    "Gmail message chronology changed", code="google_invalid_response"
                )
            ids, outcome = await ingestion._process_document(
                database,
                connection=connection,
                user=user,
                document=document,
                source="gmail",
                extractor=extractor,
            )
            plan.commitment_ids = sorted(set(plan.commitment_ids) | {str(value) for value in ids})
            plan.skipped += int(outcome == "skipped")
            plan.rejected += int(outcome == "rejected")
            plan.position += 1
            # State, receipt and read progress are one transaction. Nothing can
            # acknowledge a document whose deterministic processing rolled back.
            await database.commit()
            continue

        cursor = await database.scalar(
            select(IntelligenceCursor)
            .where(
                IntelligenceCursor.connection_id == connection_id,
                IntelligenceCursor.source == "gmail",
            )
            .execution_options(populate_existing=True)
        )
        advanced = (cursor.cursor if cursor else None) == plan.initial_cursor
        if cursor is None:
            cursor = IntelligenceCursor(connection_id=connection_id, source="gmail")
            database.add(cursor)
        if advanced:
            cursor.cursor = plan.cursor
            cursor.updated_at = datetime.now(UTC)
        completed_ids = [UUID(value) for value in plan.commitment_ids]
        database.add(
            AuditEvent(
                user_id=connection.user_id,
                workspace_id=connection.workspace_id,
                event_type="intelligence.source_processed",
                actor_type="system",
                entity_type="connection",
                entity_id=connection_id,
                event_metadata={
                    "source": "gmail",
                    "documents": len(plan.entries),
                    "commitments": len(completed_ids),
                    "cursor_reset": plan.reset,
                    "cursor_advanced": advanced,
                    "skipped_revisions": plan.skipped,
                    "rejected_revisions": plan.rejected,
                    "resume_plan_id": str(plan.id),
                    "commitment_ids": plan.commitment_ids,
                },
            )
        )
        await database.delete(plan)
        # The enclosing source activity commits final cursor, completion audit,
        # plan removal and successful workflow bookkeeping together.
        await database.flush()
        return sorted(completed_ids, key=str)
