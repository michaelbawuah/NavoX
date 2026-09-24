"""Durable per-source provider backoff, derived from safe failure audit metadata."""

from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import AuditEvent, Connection
from navox.intelligence.sync_errors import sanitize_diagnostic
from navox.providers.google_sources import GoogleSourceError

HARD_QUOTA_CODES = frozenset({"google_daily_limit_exceeded", "google_quota_exceeded"})
SOURCE_QUOTA_CODES = HARD_QUOTA_CODES | {"google_rate_limited"}
SOURCE_BACKOFF_CODES = SOURCE_QUOTA_CODES | {"google_provider_unavailable"}
MAX_RETRY_SECONDS = 86_400


class GoogleSourceBusyError(GoogleSourceError):
    """Retry this caller without recording an upstream failure or shared cooldown."""

    def __init__(self) -> None:
        super().__init__(
            "Source synchronization already active",
            code="google_provider_unavailable",
            retry_after_seconds=15,
        )


class GoogleSourceCooldownError(GoogleSourceError):
    """An existing cooldown was observed; this is not a new provider failure."""

    def __init__(self, diagnostic: dict[str, str | int]) -> None:
        status = diagnostic.get("http_status")
        delay = diagnostic.get("retry_after_seconds")
        provider_reason = diagnostic.get("provider_reason")
        super().__init__(
            "Source cooldown is still active",
            code=str(diagnostic["code"]),
            http_status=status if isinstance(status, int) else None,
            retry_after_seconds=delay if isinstance(delay, int) else None,
            provider_reason=provider_reason if isinstance(provider_reason, str) else None,
        )


async def source_cooldown(
    database: AsyncSession,
    connection_id: UUID,
    source: str,
    *,
    now: datetime | None = None,
) -> dict[str, str | int] | None:
    """Return the newest active cooldown, scoped to the connection's owner/workspace.

    Checking a cooldown never writes or extends it. Audit entries from another
    source, account or tenant cannot suppress this connection's work.
    """
    if source not in {"gmail", "calendar"}:
        return None
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    rows = await database.execute(
        select(AuditEvent.event_metadata, AuditEvent.occurred_at)
        .join(
            Connection,
            (AuditEvent.entity_id == Connection.id)
            & (AuditEvent.user_id == Connection.user_id)
            & (AuditEvent.workspace_id == Connection.workspace_id),
        )
        .where(
            Connection.id == connection_id,
            Connection.provider == "google",
            AuditEvent.entity_type == "connection",
            AuditEvent.event_type == "intelligence.source.failed",
            AuditEvent.event_metadata["source"].as_string() == source,
            AuditEvent.event_metadata["error_diagnostic"]["code"]
            .as_string()
            .in_(SOURCE_BACKOFF_CODES),
            AuditEvent.occurred_at >= current - timedelta(days=1),
            AuditEvent.occurred_at <= current,
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    )
    for metadata, occurred_at in rows:
        deadline = metadata.get("retry_not_before")
        if not isinstance(deadline, str) or len(deadline) > 40:
            continue
        try:
            retry_at = datetime.fromisoformat(deadline)
        except ValueError:
            continue
        if retry_at.tzinfo is None:
            continue
        occurred = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=UTC)
        # The one-second tolerance covers databases that truncate timestamp precision.
        if retry_at <= current or retry_at > occurred + timedelta(days=1, seconds=1):
            continue
        diagnostic = sanitize_diagnostic(metadata.get("error_diagnostic"))
        if diagnostic["code"] not in SOURCE_BACKOFF_CODES:
            continue
        diagnostic["retry_after_seconds"] = min(
            MAX_RETRY_SECONDS, ceil((retry_at - current).total_seconds())
        )
        return diagnostic
    return None


async def source_retry_after(
    database: AsyncSession,
    connection_id: UUID,
    source: str,
    *,
    now: datetime | None = None,
) -> int | None:
    diagnostic = await source_cooldown(database, connection_id, source, now=now)
    if diagnostic is None:
        return None
    remaining = diagnostic["retry_after_seconds"]
    return remaining if isinstance(remaining, int) else None
