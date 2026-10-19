"""No credentials or event titles enter the pure availability contract.

An authenticated adapter must construct the complete required-source inventory
and load every fact for the current owner. Matching IDs here checks consistency;
it does not establish database authorization or provider completeness.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, ConfigDict, Field, field_validator, model_validator

from navox.student.schemas import Input, valid_zone
from navox.student_planning.schemas import Preferences


def identifier(value: str) -> str:
    if (
        not value
        or len(value) > 128
        or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    ):
        raise ValueError("Availability IDs must be bounded opaque identifiers")
    return value


SourceId = Annotated[str, AfterValidator(identifier)]
Reason = Literal["coverage_unknown", "routine_unconfirmed", "travel_unknown", "movable_study"]
WarningCode = Literal[
    "coverage_missing",
    "coverage_partial",
    "coverage_stale",
    "coverage_unavailable",
    "coverage_revoked",
    "coverage_future",
    "coverage_outside_window",
    "projection_uncertainty",
    "routine_unconfirmed",
    "routine_clock_change",
    "never_schedule_clock_change",
    "travel_unknown",
    "movable_study",
]


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Availability instants require an explicit timezone")
    # Leave arithmetic room for adjacent local dates, all-day spans, and buffers.
    if not 2 <= value.year <= 9998:
        raise ValueError("Availability instants must be within years 2 through 9998")
    result = value.astimezone(UTC)
    if not 2 <= result.year <= 9998:
        raise ValueError("Availability instants must be within years 2 through 9998")
    return result


class FrozenInput(Input):
    model_config = ConfigDict(frozen=True)


class TimeInterval(FrozenInput):
    """Half-open UTC interval [start_at, end_at); all-day inputs are resolved upstream."""

    start_at: datetime
    end_at: datetime

    _aware = field_validator("start_at", "end_at")(instant)

    @model_validator(mode="after")
    def increasing(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("Availability intervals must be increasing")
        if self.end_at - self.start_at > timedelta(days=366):
            raise ValueError("An availability fact cannot exceed 366 days")
        return self


class OwnedFact(FrozenInput):
    user_id: UUID
    workspace_id: UUID


class SourceCoverage(TimeInterval, OwnedFact):
    source_id: SourceId = Field(min_length=1, max_length=128)
    state: Literal["complete", "partial", "stale", "unavailable", "revoked"]
    observed_at: datetime
    fresh_until: datetime
    revision: str = Field(min_length=1, max_length=128)

    _freshness = field_validator("observed_at", "fresh_until")(instant)

    @model_validator(mode="after")
    def freshness(self) -> Self:
        if self.fresh_until <= self.observed_at:
            raise ValueError("A coverage freshness deadline must follow its observation")
        return self


class BusyInterval(TimeInterval, OwnedFact):
    id: SourceId
    source_id: SourceId = Field(min_length=1, max_length=128)
    kind: Literal["class", "meeting", "shift", "fixed", "travel", "all_day"]
    # Classes/meetings require known travel (including explicit zero for remote
    # meetings); other fixed facts may opt into the same requirement.
    travel_required: bool = Field(default=False, strict=True)
    travel_before_minutes: int | None = Field(default=None, ge=0, le=240, strict=True)
    travel_after_minutes: int | None = Field(default=None, ge=0, le=240, strict=True)

    @model_validator(mode="after")
    def all_day_travel(self) -> Self:
        if self.kind == "all_day" and (
            self.travel_required
            or self.travel_before_minutes not in (None, 0)
            or self.travel_after_minutes not in (None, 0)
        ):
            raise ValueError("Resolved all-day blockers cannot carry travel padding")
        return self


class StudyBlock(TimeInterval, OwnedFact):
    id: SourceId
    movable: bool = Field(default=False, strict=True)
    calendar_backed: bool = Field(default=False, strict=True)
    needs_review: bool = Field(default=False, strict=True)


class ProjectionUncertainty(TimeInterval, OwnedFact):
    source_id: SourceId = Field(min_length=1, max_length=128)
    reason: Literal["projection_warning", "unresolved_time", "unavailable_source"]


class AvailabilityRequest(TimeInterval, OwnedFact):
    timezone: str = Field(min_length=1, max_length=64)
    now: datetime
    preferences: Preferences
    routine_confirmed: bool = Field(strict=True)
    required_source_ids: tuple[SourceId, ...] = Field(min_length=1, max_length=200)
    coverage: tuple[SourceCoverage, ...] = Field(default=(), max_length=200)
    busy: tuple[BusyInterval, ...] = Field(default=(), max_length=10_000)
    study_blocks: tuple[StudyBlock, ...] = Field(default=(), max_length=600)
    uncertainties: tuple[ProjectionUncertainty, ...] = Field(default=(), max_length=1_000)
    duration_minutes: int | None = Field(default=None, ge=1, le=480, strict=True)
    include_short_slots: bool = Field(default=False, strict=True)

    _now = field_validator("now")(instant)
    _zone = field_validator("timezone")(valid_zone)

    @model_validator(mode="after")
    def bounded_owned_snapshot(self) -> Self:
        zone = ZoneInfo(self.timezone)
        first = self.start_at.astimezone(zone).date()
        last = (self.end_at - timedelta(microseconds=1)).astimezone(zone).date()
        if self.end_at - self.start_at > timedelta(days=15) or (last - first).days >= 14:
            raise ValueError("Choose at most 14 local days of availability")
        required = set(self.required_source_ids)
        if len(required) != len(self.required_source_ids) or any(
            not source or len(source) > 128 or any(ord(char) < 32 for char in source)
            for source in required
        ):
            raise ValueError("Required availability source IDs must be bounded and unique")
        if len({item.source_id for item in self.coverage}) != len(self.coverage):
            raise ValueError("Supply one coverage record per required source")
        for collection in (self.coverage, self.busy, self.uncertainties, self.study_blocks):
            for item in collection:
                if item.user_id != self.user_id or item.workspace_id != self.workspace_id:
                    raise ValueError("Availability facts must belong to the supplied owner scope")
        if any(
            item.source_id not in required
            for collection in (self.coverage, self.busy, self.uncertainties)
            for item in collection
        ):
            raise ValueError("Every supplied availability source must be explicitly required")
        if len({item.id for item in self.busy}) != len(self.busy) or len(
            {item.id for item in self.study_blocks}
        ) != len(self.study_blocks):
            raise ValueError("Availability fact IDs must be unique within their collection")
        return self


class Opening(TimeInterval):
    minutes: int = Field(ge=1)
    reasons: tuple[Reason, ...] = ()
    unchecked_source_ids: tuple[SourceId, ...] = ()
    unknown_travel_record_ids: tuple[str, ...] = ()
    displaced_study_block_ids: tuple[str, ...] = ()


class AvailabilityWarning(FrozenInput):
    code: WarningCode
    source_id: SourceId | None = None
    record_id: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class AvailabilityResult(FrozenInput):
    timezone: str
    checked_at: datetime
    scope: Literal["provided_owned_sources_only"] = "provided_owned_sources_only"
    status: Literal["complete", "partial", "needs_routine"]
    confirmed_openings: tuple[Opening, ...]
    provisional_openings: tuple[Opening, ...]
    busy: tuple[TimeInterval, ...]
    warnings: tuple[AvailabilityWarning, ...]
    changes_applied: Literal[False] = False
