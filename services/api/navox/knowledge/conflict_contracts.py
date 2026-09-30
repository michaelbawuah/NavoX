"""Application-owned, source-attributed event conflict responses."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ConflictValue(BaseModel):
    resource_id: UUID
    source_type: str
    value: datetime
    title: str | None
    canonical_url: str | None
    source_updated_at: datetime | None
    authority: str


class KnowledgeConflict(BaseModel):
    entity_key: str
    predicate: str = "EVENT_START"
    values: tuple[ConflictValue, ConflictValue]
    preferred_resource_id: UUID | None = None
    explanation: str = (
        "The calendar record and a calendar block in the email give different start times "
        "for the same calendar identity."
    )
