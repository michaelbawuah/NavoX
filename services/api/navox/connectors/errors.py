from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import ceil

from navox.connectors.contracts import ConnectorErrorCode, ConnectorRuntimeError

__all__ = ["ConnectorErrorCode", "ConnectorRuntimeError", "retry_after_seconds"]


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> int | None:
    """Normalize Retry-After without persisting an arbitrary provider header."""
    if not isinstance(value, str) or len(value) > 128:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        delay = int(value)
    else:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                return None
            delay = ceil((deadline.astimezone(UTC) - (now or datetime.now(UTC))).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
    return min(delay, 86_400) if delay > 0 else None
