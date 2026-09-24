"""Stable Gmail source entry point; ID/chronology progress uses the shared runtime."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.intelligence.extraction import OperationalExtractor

# Kept as the deployment/test pacing seam; the connector imports it lazily.
READ_SPACING_SECONDS = 0.5


async def process_gmail_connection(
    database: AsyncSession,
    *,
    connection_id: UUID,
    settings: Settings,
    extractor: OperationalExtractor,
) -> list[UUID]:
    from navox.connectors.google_gmail_sync import process_gmail_connection as sync

    return await sync(database, connection_id=connection_id, settings=settings, extractor=extractor)
