"""Bounded student-owned goals; reports never imply observed attendance or grades."""

from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from navox.student.schemas import Input, valid_zone

MAX_ACTIVE = 3
MAX_GOALS = 30
MAX_CHECKINS = 3000
MAX_COMMANDS = 10000
MAX_FENCES = MAX_COMMANDS + MAX_CHECKINS + MAX_GOALS + 1
BACKFILL_DAYS = 31
Category = Literal["academic", "habit", "personal"]
Tracking = Literal["qualitative", "count"]
Cadence = Literal["daily", "weekly", "total"]
Status = Literal["active", "paused", "completed"]


class GoalInput(Input):
    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value: object) -> object:
        if isinstance(value, str) and any(
            (ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value
        ):
            raise ValueError("Text contains unsupported control characters")
        return value


class GoalDefinition(GoalInput):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    category: Category
    tracking: Tracking = "qualitative"
    cadence: Cadence = "weekly"
    target: int | None = Field(default=None, strict=True, ge=1, le=100000)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    starts_on: date
    ends_on: date | None = None

    @field_validator("title", "unit")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and any(char in value for char in "\n\t"):
            raise ValueError("Use a single line for a goal title or count unit")
        return value

    @model_validator(mode="after")
    def measurement(self) -> Self:
        if self.tracking == "count":
            if self.target is None or self.unit is None:
                raise ValueError("Count goals need a target and the unit you will report")
        elif self.target is not None or self.unit is not None:
            raise ValueError("Qualitative goals use explicit yes/no check-ins")
        if self.ends_on is not None and (
            self.ends_on < self.starts_on or (self.ends_on - self.starts_on).days > 366
        ):
            raise ValueError("Choose a goal period within 366 days")
        return self


class GoalCreate(GoalDefinition):
    request_id: UUID
    generation: int = Field(strict=True, ge=0)


class GoalUpdate(GoalDefinition):
    request_id: UUID
    revision: int = Field(strict=True, ge=1)
    status: Status = "active"


class CheckInInput(GoalInput):
    request_id: UUID
    goal_revision: int = Field(strict=True, ge=1)
    revision: int = Field(default=0, strict=True, ge=0)
    expected_timezone: str = Field(min_length=1, max_length=64)
    on_date: date | None = None
    count: int | None = Field(default=None, strict=True, ge=0, le=100000)
    completed: bool | None = Field(default=None, strict=True)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def explicit_report(self) -> Self:
        valid_zone(self.expected_timezone)
        if (self.count is None) == (self.completed is None):
            raise ValueError("Report either a count for that local day or an explicit yes/no")
        return self


class EraseInput(Input):
    request_id: UUID
    revision: int = Field(strict=True, ge=1)


class ResetInput(Input):
    request_id: UUID
    revision: int = Field(strict=True, ge=0)


class CheckInView(BaseModel):
    id: UUID
    goal_id: UUID
    revision: int
    goal_revision_when_reported: int
    on_date: date
    reported_timezone: str
    count: int | None
    completed: bool | None
    note: str
    recorded_at: datetime
    source: Literal["self_report"] = "self_report"
    count_meaning: Literal["count_for_reported_local_day"] = "count_for_reported_local_day"


class ProgressView(BaseModel):
    source: Literal["self_report"] = "self_report"
    timezone: str
    week_starts_on: Literal["monday"] = "monday"
    period_start: date
    period_end: date
    days_reported: int
    reported_count: int | None
    target: int | None
    remaining_count: int | None
    target_met_from_reports: bool | None
    qualitative_latest: Literal["yes", "no", "unknown"]
    latest_reported_on: date | None


class GoalView(GoalDefinition):
    id: UUID
    revision: int
    status: Status
    progress: ProgressView
    recent_checkins: list[CheckInView]
    tracking_definition_locked: bool
    status_source: Literal["student_selected"] = "student_selected"


class PlannerMetrics(BaseModel):
    source: Literal["recorded_planner_outcomes"] = "recorded_planner_outcomes"
    timezone: str
    period_start: date
    period_end: date
    completed_blocks: int
    skipped_blocks: int
    recorded_minutes: int
    attendance_sleep_and_grades_observed: Literal[False] = False


class GoalsView(BaseModel):
    revision: int
    generation: int
    server_now: datetime
    timezone: str
    active_limit: Literal[3] = 3
    goals: list[GoalView]
    planner_metrics: PlannerMetrics
    notices: list[str]
