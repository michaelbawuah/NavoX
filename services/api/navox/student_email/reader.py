"""One explicitly selected Gmail message, with existing bounded OAuth/read machinery."""

import asyncio
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.google_calendar_sync import CalendarAuthority, GoogleCalendarTokenBroker
from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.models import Connection
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.source_cooldown import source_retry_after
from navox.providers.google_sources import GMAIL_READ_SCOPE, GoogleSourceGateway


class EmailReader(Protocol):
    async def read(
        self,
        database: AsyncSession,
        settings: Settings,
        *,
        user_id: UUID,
        workspace_id: UUID,
        connector_id: UUID,
        legacy_id: UUID,
        external_id: str,
        source_id: UUID,
        source_hash: str,
    ) -> SourceDocument: ...


class GmailEmailReader:
    async def read(
        self,
        database: AsyncSession,
        settings: Settings,
        *,
        user_id: UUID,
        workspace_id: UUID,
        connector_id: UUID,
        legacy_id: UUID,
        external_id: str,
        source_id: UUID,
        source_hash: str,
    ) -> SourceDocument:
        from navox.student_email.service import EmailError, verified_source

        # The service verifies the selected resource before and after this call.
        # This broker refreshes a detached credential and releases locks before OAuth I/O.
        legacy = await database.get(Connection, legacy_id, populate_existing=True)
        if legacy is None or (legacy.user_id, legacy.workspace_id) != (user_id, workspace_id):
            raise EmailError("The selected mailbox is unavailable", 403)
        authority = CalendarAuthority(
            legacy_id,
            user_id,
            workspace_id,
            legacy.credential_reference,
            legacy.external_account_id,
            frozenset(legacy.granted_scopes),
            GMAIL_READ_SCOPE,
        )
        if await source_retry_after(database, legacy_id, "gmail"):
            raise EmailError("Gmail is temporarily rate limited. Try later", 429)
        broker = GoogleCalendarTokenBroker(settings, authority, connector_id)
        async with asyncio.timeout(30):
            with await broker.lease(
                database,
                connection_id=connector_id,
                user_id=user_id,
                workspace_id=workspace_id,
                purpose="sync.read",
                names={"GOOGLE_ACCESS_TOKEN"},
            ) as lease:
                await database.commit()  # No provider request runs under a database lock.
                # Recheck source-level authority after refresh, immediately before message I/O.
                await verified_source(
                    database,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    source_id=source_id,
                    source_hash=source_hash,
                )
                now = await database_now(database)
                await database.rollback()
                return await GoogleSourceGateway().gmail_message(
                    lease.get("GOOGLE_ACCESS_TOKEN"),
                    workspace_id=workspace_id,
                    connection_id=legacy_id,
                    external_id=external_id,
                    now=now,
                )


def get_email_reader() -> EmailReader:
    # Constructing this inert adapter does not create a provider or acquire credentials.
    return GmailEmailReader()
