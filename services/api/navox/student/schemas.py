"""Reviewed student input and deterministic timetable views."""

from datetime import date, datetime, time
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_COURSES = 50
MAX_MEETINGS = 200
MAX_FIXED_BLOCKS = 50
MAX_BREAKS = 50
MAX_IMPORT_BYTES = 2_000_000
MAX_IMPORT_OCCURRENCES = 5_000
MeetingKind = Literal["lecture", "lab", "discussion", "recitation", "seminar", "other"]
Lms = Literal["none", "canvas", "blackboard", "moodle", "brightspace", "google_classroom", "other"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(char) < 32 for char in value):
            raise ValueError("Text cannot contain control characters")
        return value


def valid_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Choose a valid IANA time zone") from None
    return value


def valid_term(start: date, end: date) -> None:
    if end < start or (end - start).days > 366:
        raise ValueError("Term end must follow its start and be within 366 days")


def wall_time(value: str) -> str:
    if len(value) != 5 or value[2] != ":":
        raise ValueError("Use a 24-hour HH:MM time")
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        raise ValueError("Use a valid 24-hour HH:MM time") from None
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError("Use a 24-hour HH:MM time")
    return value


class ProfileInput(Input):
    school_name: str | None = Field(default=None, max_length=120)
    lms: Lms = "none"
    display_name: str | None = Field(default=None, max_length=120)
    timezone: str = Field(min_length=1, max_length=64)
    term_start: date | None = None
    term_end: date | None = None
    revision: int = Field(ge=0)

    _timezone = field_validator("timezone")(valid_zone)

    @model_validator(mode="after")
    def term(self) -> Self:
        if (self.term_start is None) != (self.term_end is None):
            raise ValueError("Choose both term dates")
        if self.term_start is not None and self.term_end is not None:
            valid_term(self.term_start, self.term_end)
        return self


class ProfileView(ProfileInput):
    pass


class CourseInput(Input):
    code: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=200)
    term_start: date
    term_end: date
    professor: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def term(self) -> Self:
        valid_term(self.term_start, self.term_end)
        return self


class CourseUpdate(CourseInput):
    revision: int = Field(ge=1)
    archived: bool = False


class ScheduleInput(Input):
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: str
    end_time: str
    timezone: str = Field(min_length=1, max_length=64)

    _zone = field_validator("timezone")(valid_zone)
    _times = field_validator("start_time", "end_time")(wall_time)

    @field_validator("weekdays", mode="before")
    @classmethod
    def days(cls, value: object) -> object:
        if not isinstance(value, list) or any(
            type(day) is not int or not 0 <= day <= 6 for day in value
        ):
            raise ValueError("Choose weekdays from 0 (Monday) to 6 (Sunday)")
        if len(set(value)) != len(value):
            raise ValueError("Choose each weekday once")
        return sorted(value)

    @model_validator(mode="after")
    def times(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("End time must follow start time on the same day")
        return self


class MeetingInput(ScheduleInput):
    kind: MeetingKind = "lecture"
    building: str | None = Field(default=None, max_length=160)
    room: str | None = Field(default=None, max_length=80)
    instructor: str | None = Field(default=None, max_length=120)
    walk_minutes: int | None = Field(default=None, ge=0, le=120)


class MeetingUpdate(MeetingInput):
    revision: int = Field(ge=1)


class MeetingView(MeetingInput):
    id: UUID
    course_id: UUID
    dates: list[date] | None = None
    revision: int


class CourseView(CourseInput):
    id: UUID
    archived: bool
    revision: int
    meetings: list[MeetingView]


class ExceptionInput(Input):
    revision: int = Field(ge=1)
    cancelled: bool = False
    start_time: str | None = None
    end_time: str | None = None
    building: str | None = Field(default=None, max_length=160)
    room: str | None = Field(default=None, max_length=80)

    @field_validator("start_time", "end_time")
    @classmethod
    def time(cls, value: str | None) -> str | None:
        return wall_time(value) if value is not None else None

    @model_validator(mode="after")
    def times(self) -> Self:
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("Choose both replacement times")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.end_time <= self.start_time
        ):
            raise ValueError("End time must follow start time")
        return self


class ExceptionView(Input):
    id: UUID
    meeting_id: UUID
    date: date
    cancelled: bool
    start_time: str | None
    end_time: str | None
    building: str | None
    room: str | None


class FixedBlockInput(ScheduleInput):
    title: str = Field(min_length=1, max_length=120)
    term_start: date
    term_end: date

    @model_validator(mode="after")
    def term(self) -> Self:
        valid_term(self.term_start, self.term_end)
        return self


class FixedBlockUpdate(FixedBlockInput):
    revision: int = Field(ge=1)


class FixedBlockView(FixedBlockInput):
    id: UUID
    revision: int


class BreakInput(Input):
    name: str = Field(min_length=1, max_length=120)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def dates(self) -> Self:
        valid_term(self.start_date, self.end_date)
        return self


class BreakView(BreakInput):
    id: UUID
    revision: int


class Occurrence(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    meeting_id: UUID
    course_id: UUID
    course_code: str
    course_name: str
    kind: MeetingKind
    start_at: datetime
    end_at: datetime
    timezone: str
    building: str | None
    room: str | None
    instructor: str | None
    walk_minutes: int | None
    cancelled: bool
    exception: bool


class FixedOccurrence(BaseModel):
    id: str
    fixed_block_id: UUID
    title: str
    start_at: datetime
    end_at: datetime


class Conflict(BaseModel):
    left_id: str
    right_id: str
    kind: Literal["class_class", "class_fixed"]
    start_at: datetime
    end_at: datetime


class TimetableView(BaseModel):
    server_now: datetime
    timezone: str
    date: date
    days: int
    courses: list[CourseView]
    fixed_blocks: list[FixedBlockView]
    breaks: list[BreakView]
    exceptions: list[ExceptionView]
    occurrences: list[Occurrence]
    fixed_occurrences: list[FixedOccurrence]
    conflicts: list[Conflict]
    conflicts_truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class LookupView(BaseModel):
    server_now: datetime
    timezone: str
    mode: Literal["now", "next", "at", "day"]
    date: date
    at: datetime
    occurrences: list[Occurrence]
    next_upcoming: Occurrence | None
    reason: Literal["matches", "no_classes", "after_last_class", "break"]
    conflicts: list[Conflict]


class ImportCandidate(Input):
    id: str = Field(min_length=1, max_length=128)
    course_code: str = Field(default="", max_length=40)
    course_name: str = Field(min_length=1, max_length=200)
    kind: MeetingKind = "other"
    dates: list[date] = Field(min_length=1, max_length=367)
    start_time: str
    end_time: str
    timezone: str = Field(min_length=1, max_length=64)
    building: str | None = Field(default=None, max_length=160)
    room: str | None = Field(default=None, max_length=80)
    instructor: str | None = Field(default=None, max_length=120)

    _zone = field_validator("timezone")(valid_zone)
    _times = field_validator("start_time", "end_time")(wall_time)

    @model_validator(mode="after")
    def schedule(self) -> Self:
        if self.end_time <= self.start_time:
            raise ValueError("Imported end time must follow start on the same day")
        if len(set(self.dates)) != len(self.dates):
            raise ValueError("Imported dates must be unique")
        return self


class ImportPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=MAX_IMPORT_BYTES)
    term_start: date
    term_end: date

    @model_validator(mode="after")
    def term(self) -> Self:
        valid_term(self.term_start, self.term_end)
        return self


class ImportPreviewView(BaseModel):
    preview_id: UUID
    preview_hash: str
    expires_at: datetime
    timezone: str
    candidates: list[ImportCandidate]
    warnings: list[str]


class ImportConfirmInput(Input):
    preview_id: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    confirmed: Literal[True]
    candidates: list[ImportCandidate] = Field(min_length=1, max_length=MAX_MEETINGS)

    @field_validator("confirmed", mode="before")
    @classmethod
    def consent(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value
