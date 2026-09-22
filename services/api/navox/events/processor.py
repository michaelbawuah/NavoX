from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from navox.db.models import IncomingEvent


@dataclass(frozen=True)
class NormalizedProviderEvent:
    """Content-minimized provider fact accepted by the NavoX event pipeline."""

    connection_id: UUID
    user_id: UUID
    workspace_id: UUID
    provider: str
    source: str
    event_type: str
    external_event_id: str
    external_resource_id: str | None
    payload_hash: str
    metadata: Mapping[str, str]
    occurred_at: datetime | None = None


class IncomingEventProcessor:
    """Marks canonical events ready for later state-update processors.

    Milestone 2 intentionally stores notification facts only. The context, memory,
    and proactive-engine milestones will consume these processed facts without
    re-reading raw provider payloads.
    """

    def process(self, event: IncomingEvent) -> None:
        if event.status == "received":
            event.status = "processed"
            event.processed_at = datetime.now(UTC)
