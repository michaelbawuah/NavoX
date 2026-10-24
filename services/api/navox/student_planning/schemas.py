from datetime import UTC, date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from navox.student.schemas import Input, wall_time

MAX_TASKS = 200
MAX_BLOCKS = 600
MAX_DAYS = 14
TaskStatus = Literal["active", "completed", "archived"]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Choose a deadline with an explicit timezone")
    return value.astimezone(UTC)


class TaskFields(Input):
    title: str = Field(min_length=1, max_length=200)
    course_id: UUID | None = None
    due_at: datetime | None = None
    estimated_minutes: int = Field(default=60, ge=1, le=10080)
    difficulty: int = Field(default=3, ge=1, le=5)
    priority: int = Field(default=3, ge=1, le=5)
    course_weight: int = Field(default=1, ge=1, le=10)
    status: TaskStatus = "active"

    @field_validator("due_at")
    @classmethod
    def deadline(cls, value: datetime | None) -> datetime | None:
        return aware(value) if value else None


class TaskCreate(TaskFields):
    request_id: UUID
    remaining_minutes: int | None = Field(default=None, ge=0, le=10080)


class TaskUpdate(TaskFields):
    request_id: UUID
    revision: int = Field(ge=1)
    remaining_minutes: int = Field(ge=0, le=10080)
    reviewed_source_revision: int | None = Field(default=None, ge=1)
    confirmed_source: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def reviewed_source(self) -> Self:
        if self.confirmed_source != (self.reviewed_source_revision is not None):
            raise ValueError("Confirm the exact source revision you reviewed")
        return self


class TaskImport(TaskCreate):
    source_item_id: UUID
    source_revision: int = Field(ge=1)
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def consent(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit review is required")
        return value


class TaskView(TaskFields):
    id: UUID
    remaining_minutes: int
    actual_minutes: int
    revision: int
    source_item_id: UUID | None
    source_revision: int | None
    source_current_revision: int | None = None
    source_current_title: str | None = None
    source_current_due_at: datetime | None = None
    source_needs_review: bool = False
    source_reviewable: bool = False
    source_review_notice: str | None = None


class DailyWindow(Input):
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    start_time: str
    end_time: str

    _clocks = field_validator("start_time", "end_time")(wall_time)

    @field_validator("weekdays", mode="before")
    @classmethod
    def days(cls, value: object) -> list[int]:
        if not isinstance(value, list) or any(
            type(day) is not int or not 0 <= day <= 6 for day in value
        ):
            raise ValueError("Choose weekdays from 0 to 6")
        return sorted(set(value))

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.start_time == self.end_time:
            raise ValueError("Choose different window start and end times")
        return self


class AlarmPreferences(Input):
    enabled: bool = Field(default=False, strict=True)
    days: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    smart: bool = Field(default=False, strict=True)
    prep_minutes: int = Field(default=30, ge=0, le=180, strict=True)
    sound: Literal["gentle", "chime"] = "gentle"
    snooze_minutes: Literal[5, 10, 15] = 10
    skip_date: date | None = None

    @field_validator("days", mode="before")
    @classmethod
    def unique_days(cls, value: object) -> list[int]:
        if not isinstance(value, list) or any(
            type(day) is not int or not 0 <= day <= 6 for day in value
        ):
            raise ValueError("Choose alarm weekdays from 0 to 6")
        return sorted(set(value))


class Preferences(Input):
    wake_times: list[str] | None = Field(default=None, min_length=7, max_length=7)
    alarm: AlarmPreferences = Field(default_factory=AlarmPreferences)

    @field_validator("wake_times")
    @classmethod
    def daily_wakes(cls, value: list[str] | None) -> list[str] | None:
        return [wall_time(clock) for clock in value] if value is not None else None

    wake_time: str = "07:00"
    sleep_time: str = "23:00"
    focus_windows: list[DailyWindow] = Field(default_factory=list, max_length=14)
    never_schedule: list[DailyWindow] = Field(default_factory=list, max_length=28)
    max_study_minutes: int = Field(default=90, ge=15, le=90)
    break_minutes: int = Field(default=15, ge=5, le=60)

    _clocks = field_validator("wake_time", "sleep_time")(wall_time)

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.wake_time == self.sleep_time or (
            self.wake_times and self.sleep_time in self.wake_times
        ):
            raise ValueError("Choose different wake and sleep times")
        return self


class PreferencesUpdate(Preferences):
    request_id: UUID
    revision: int = Field(ge=0)


class PreferencesConfirm(PreferencesUpdate):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreferencesView(Preferences):
    revision: int
    timezone: str


class PreviewInput(Input):
    day: date | None = None
    days: int = Field(default=7, ge=1, le=MAX_DAYS)


class ProposedBlock(Input):
    task_id: UUID
    title: str
    start_at: datetime
    end_at: datetime
    minutes: int
    focus_window: bool


class UnfittedWork(Input):
    task_id: UUID
    title: str
    minutes: int
    due_at: datetime | None
    reason: Literal[
        "deadline_passed", "cannot_meet_deadline", "outside_horizon", "insufficient_time"
    ]


class DayCapacity(Input):
    day: date
    free_minutes: int
    buffer_minutes: int
    planned_minutes: int


class PlanResult(Input):
    start_at: datetime
    end_at: datetime
    blocks: list[ProposedBlock]
    unfitted_work: list[UnfittedWork]
    capacities: list[DayCapacity]
    warnings: list[str]
    planning_only: Literal[True] = True


class PreviewView(Input):
    preview_id: UUID
    preview_hash: str
    expires_at: datetime
    base_revision: int
    result: PlanResult
    requires_acceptance: Literal[True] = True


class AcceptInput(Input):
    preview_id: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def consent(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit acceptance is required")
        return value


class BlockView(ProposedBlock):
    id: UUID
    plan_id: UUID
    revision: int
    status: Literal["planned", "done", "took_longer", "skipped", "superseded", "needs_review"]
    actual_minutes: int
    outcome_at: datetime | None


class PlanView(Input):
    revision: int
    blocks: list[BlockView]
    has_more_history: bool = False
    needs_review: bool = False
    review_notice: str | None = None


class OutcomeInput(Input):
    request_id: UUID
    revision: int = Field(ge=1)
    task_revision: int = Field(ge=1)
    outcome: Literal["done", "took_longer", "skipped"]
    actual_minutes: int = Field(ge=0, le=1440)
    remaining_minutes: int | None = Field(default=None, ge=0, le=10080)
    complete_task: bool = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.outcome == "took_longer" and (
            self.remaining_minutes is None or self.remaining_minutes == 0
        ):
            raise ValueError("Tell NavoX how many minutes remain")
        if self.outcome == "skipped" and (
            self.actual_minutes != 0 or self.complete_task or self.remaining_minutes is not None
        ):
            raise ValueError("A skipped block has no reported study time")
        if self.outcome != "done" and self.complete_task:
            raise ValueError("Only Done can complete the task")
        if self.complete_task and self.remaining_minutes not in (None, 0):
            raise ValueError("A completed task cannot have remaining work")
        return self


class OutcomeConfirmInput(OutcomeInput):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class OutcomeView(Input):
    task: TaskView
    block: BlockView
    plan: PlanView
    result: PlanResult
    undo_request_id: UUID
    preview_hash: str | None = None
    replaced_blocks: list[BlockView] = Field(default_factory=list)


class UndoInput(Input):
    request_id: UUID
    outcome_request_id: UUID
    revision: int = Field(ge=1)
