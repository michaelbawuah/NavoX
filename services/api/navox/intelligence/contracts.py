from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

SOURCE_DOCUMENT_SCHEMA_VERSION: Literal["source-document.v1"] = "source-document.v1"


class SourceIdentity(BaseModel):
    """Provider-neutral identity attached to an authorized source document."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider: str | None = Field(default=None, min_length=1, max_length=32)
    identity_type: str = Field(min_length=1, max_length=64)
    identity_value: str = Field(min_length=1, max_length=512)
    display_name: str | None = Field(default=None, max_length=256)


class SourceDocument(BaseModel):
    """Versioned canonical input contract for every SPEC-002 connector."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["source-document.v1"] = SOURCE_DOCUMENT_SCHEMA_VERSION
    id: UUID
    workspace_id: UUID
    provider: str = Field(min_length=1, max_length=32)
    source_type: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=512)
    external_parent_id: str | None = Field(default=None, max_length=512)
    author: SourceIdentity | None = None
    recipients: list[SourceIdentity] = Field(default_factory=list, max_length=256)
    subject: str | None = Field(default=None, max_length=2_000)
    content: str | None = None
    occurred_at: datetime
    retrieved_at: datetime
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("occurred_at", "retrieved_at")
    @classmethod
    def require_timezone_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("SourceDocument timestamps must include a timezone")
        return value.astimezone(UTC)
