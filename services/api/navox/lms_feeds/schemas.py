"""Public feed commands never echo a private subscription URL in errors."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from navox.student.schemas import valid_zone, wall_time

Platform = Literal["canvas", "blackboard", "moodle", "brightspace", "google_classroom", "other"]
MAX_FEEDS = 10
MAX_ITEMS = 5_000


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PreviewCommand(Command):
    url: str = Field(min_length=1, max_length=4_096, repr=False)
    platform: Platform = "other"
    timezone: str = Field(min_length=1, max_length=64)
    all_day_due_time: str

    _zone = field_validator("timezone")(valid_zone)
    _clock = field_validator("all_day_due_time")(wall_time)


class ConfirmCommand(Command):
    preview_token: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    name: str = Field(min_length=1, max_length=120)
    # Every discovered hint must be acknowledged, including an explicit null.
    course_matches: dict[str, UUID | None] = Field(max_length=100)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_yes(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class FeedUpdate(Command):
    revision: int = Field(ge=1)
    enabled: bool = Field(strict=True)


class CourseMatches(Command):
    revision: int = Field(ge=1)
    course_matches: dict[str, UUID | None] = Field(max_length=100)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_yes(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class FeedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    external_id: str = Field(min_length=1, max_length=512)
    occurrence_id: str = Field(max_length=128)
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["assignment", "event", "exam"]
    starts_at: datetime | None
    due_at: datetime | None
    ends_at: datetime | None
    all_day: bool
    course_hint: str | None = Field(max_length=120)
    details: str = Field(max_length=2_000)
    source_url: str | None = Field(max_length=2_048)
    location: str | None = Field(max_length=256)
    source_updated_at: datetime | None
    cancelled: bool = False


class ParsedFeed(BaseModel):
    platform: Platform
    items: list[FeedItem] = Field(max_length=MAX_ITEMS)
    course_hints: list[str] = Field(max_length=100)
    all_day_count: int
    window_start: datetime
    window_end: datetime


class SyncResult(BaseModel):
    state: Literal["synced", "unchanged", "paused", "superseded", "failed", "disabled"]
    created: int = 0
    updated: int = 0
    cancelled: int = 0
