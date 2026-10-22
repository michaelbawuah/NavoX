"""Bounded calendar-purpose inputs and content-free status views."""

from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from navox.student.schemas import Input, valid_zone
from navox.student_availability.schemas import AvailabilityPreview, instant

CONSENT_VERSION: Literal["calendar-availability.v1"] = "calendar-availability.v1"
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
CALENDAR_BUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
AVAILABILITY_SCOPES = (CALENDAR_LIST_SCOPE, CALENDAR_BUSY_SCOPE)
MAX_REVISION = 2147483647
SCHEDULING_CONSENT_VERSION: Literal["calendar-scheduling.v1"] = "calendar-scheduling.v1"
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
SCHEDULING_SCOPES = (*AVAILABILITY_SCOPES, CALENDAR_WRITE_SCOPE)


class CalendarCommand(Input):
    generation: int = Field(ge=0, le=MAX_REVISION, strict=True)
    revision: int = Field(ge=0, le=MAX_REVISION, strict=True)
    request_id: UUID


class CalendarStart(CalendarCommand):
    consent_version: Literal["calendar-availability.v1"]
    confirmed: Literal[True]
    connection_id: UUID | None = None

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit calendar-purpose confirmation is required")
        return value


class CalendarProvider(Input):
    id: Literal["google", "outlook", "icloud"]
    availability: Literal["available", "setup_pending", "unavailable"]


class CalendarGrantView(Input):
    id: UUID
    provider: Literal["google"] = "google"
    connection_id: UUID | None
    account_label: str | None
    status: Literal["pending", "connected", "unavailable", "revoked"]
    revision: int


class CalendarView(Input):
    generation: int
    revision: int
    consent_version: Literal["calendar-availability.v1"] = CONSENT_VERSION
    providers: list[CalendarProvider]
    grants: list[CalendarGrantView]
    changes_applied: Literal[False] = False


class CalendarAuthorization(Input):
    authorization_url: str
    requested_scopes: list[str]
    generation: int
    revision: int
    grant_id: UUID


class CalendarSchedulingStart(CalendarCommand):
    consent_version: Literal["calendar-scheduling.v1"]
    confirmed: Literal[True]
    connection_id: UUID

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        return CalendarStart.explicit_confirmation(value)


class CalendarSchedulingView(Input):
    generation: int
    revision: int
    consent_version: Literal["calendar-scheduling.v1"] = SCHEDULING_CONSENT_VERSION
    grants: list[CalendarGrantView]
    changes_applied: Literal[False] = False


class CalendarTargetsRequest(CalendarCommand):
    grant_id: UUID


class CalendarTargetView(Input):
    id: str = Field(min_length=1, max_length=1024)
    label: str = Field(min_length=1, max_length=1024)
    timezone: str = Field(min_length=1, max_length=64)


class CalendarTargetsView(Input):
    generation: int
    revision: int
    grant_id: UUID
    targets: list[CalendarTargetView]
    changes_applied: Literal[False] = False


class CalendarEventPreview(CalendarCommand):
    grant_id: UUID
    calendar_id: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=500)
    start_at: datetime
    end_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    availability: AvailabilityPreview

    _instants = field_validator("start_at", "end_at")(instant)
    _zone = field_validator("timezone")(valid_zone)

    @model_validator(mode="after")
    def bounded_slot(self) -> Self:
        if not timedelta(minutes=1) <= self.end_at - self.start_at <= timedelta(hours=8):
            raise ValueError("Choose an event between one minute and eight hours")
        if (
            self.start_at < self.availability.start_at
            or self.end_at > self.availability.end_at
            or self.timezone != self.availability.expected_timezone
            or self.end_at - self.start_at != timedelta(minutes=self.availability.duration_minutes)
        ):
            raise ValueError("The event must match the reviewed availability window")
        return self


class CalendarEventCommand(CalendarCommand):
    event_revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CalendarEventApproval(CalendarEventCommand):
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value: object) -> object:
        return CalendarStart.explicit_confirmation(value)


CalendarEventStatus = Literal[
    "pending", "executing", "uncertain", "created", "changed", "undoing", "undone", "failed"
]


class CalendarEventView(Input):
    generation: int
    calendar_revision: int
    id: UUID
    revision: int
    digest: str
    status: CalendarEventStatus
    calendar_label: str
    title: str
    location: str
    start_at: datetime
    end_at: datetime
    timezone: str
    expires_at: datetime
    changes_applied: bool | None
    can_undo: bool
    message: str
