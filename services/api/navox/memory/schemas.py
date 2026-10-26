from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from navox.ai.context import ContextDenied, reject_credentials
from navox.memory.sensitivity import SENSITIVE

MemoryKind = Literal["reply_length", "tone", "signoff", "study_hours", "project_fact"]
MAX_MEMORIES = 30
MAX_MEMORY_CONTENT = 500
MAX_DRAFT_INSTRUCTIONS = 4000
MEMORY_LABELS: dict[str, str] = {
    "reply_length": "Reply length",
    "tone": "Writing tone",
    "signoff": "Email sign-off",
    "study_hours": "Study hours",
    "project_fact": "Project fact",
}


def safe_text(value: str) -> str:
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ValueError("Memory cannot include control characters")
    try:
        reject_credentials(value)
    except ContextDenied:
        raise ValueError("Do not save credentials in memory") from None
    return value


def validate_content(kind: MemoryKind, content: str) -> str:
    """Typed preferences remain text that the owner can explicitly choose to use."""
    safe_text(content)
    if kind == "project_fact" and SENSITIVE.search(content):
        raise ValueError("Use Memory facts to review sensitive details before saving.")
    if kind == "reply_length" and content not in {"short", "balanced", "detailed"}:
        raise ValueError("Reply length must be short, balanced, or detailed")
    if kind == "tone" and content not in {"friendly", "professional", "direct"}:
        raise ValueError("Writing tone must be friendly, professional, or direct")
    if kind == "signoff" and len(content) > 120:
        raise ValueError("Email sign-off must be at most 120 characters")
    if kind == "study_hours" and len(content) > 200:
        raise ValueError("Study hours must be at most 200 characters")
    return content


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    kind: MemoryKind
    label: str | None = Field(default=None, min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT)
    enabled: bool = True

    @field_validator("label")
    @classmethod
    def label_is_safe(cls, value: str | None) -> str | None:
        return safe_text(value) if value is not None else None

    @model_validator(mode="after")
    def content_matches_kind(self) -> Self:
        validate_content(self.kind, self.content)
        if self.kind == "project_fact" and self.label and SENSITIVE.search(self.label):
            raise ValueError("Use Memory facts to review sensitive details before saving.")
        return self


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    content: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CONTENT)
    enabled: bool | None = None

    @field_validator("label", "content")
    @classmethod
    def text_is_safe(cls, value: str | None) -> str | None:
        return safe_text(value) if value is not None else None

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null memory change")
        return self


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: MemoryKind
    label: str
    content: str
    enabled: bool
    provenance: Literal["user_saved"]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        # PostgreSQL retains offsets; SQLite fixtures return a naive UTC value.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
