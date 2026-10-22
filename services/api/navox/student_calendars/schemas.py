"""Bounded calendar-purpose inputs and content-free status views."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from navox.student.schemas import Input

CONSENT_VERSION: Literal["calendar-availability.v1"] = "calendar-availability.v1"
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
CALENDAR_BUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
AVAILABILITY_SCOPES = (CALENDAR_LIST_SCOPE, CALENDAR_BUSY_SCOPE)
MAX_REVISION = 2147483647


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
