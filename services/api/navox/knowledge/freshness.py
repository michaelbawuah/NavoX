"""Connected-source freshness from trusted read capabilities and fetch time.

Projection time never makes an old source fresh. Native News/subscription policies
stay with their owning domains. Unknown connector capabilities have no implied SLA.
"""

from datetime import datetime, timedelta

from navox.knowledge.contracts import stored_utc

_MAX_AGE = {
    "communication.messages.read": timedelta(minutes=15),
    "calendar.events.read": timedelta(minutes=5),
    "academic.assignments.read": timedelta(minutes=30),
    "academic.announcements.read": timedelta(minutes=30),
    "academic.courses.read": timedelta(hours=6),
    "documents.read": timedelta(hours=1),
    "files.read": timedelta(hours=1),
}


def fresh_until(capability: str, retrieved_at: datetime) -> datetime | None:
    age = _MAX_AGE.get(capability)
    return stored_utc(retrieved_at) + age if age is not None else None
