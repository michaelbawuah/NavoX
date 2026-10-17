"""Facts have explicit sources, periods and completeness; no inferred performance."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from navox.student.schemas import FixedOccurrence, Occurrence
from navox.student_goals.schemas import ProgressView

Kind = Literal["morning", "evening", "weekly"]
Status = Literal["complete", "incomplete", "disabled", "unavailable"]
ReviewHref = Literal["/classes", "/planner", "/goals", "/email-actions", "/connections"]


class Window(BaseModel):
    starts_at: datetime
    ends_at: datetime
    observed_through: datetime


class Notice(BaseModel):
    code: str
    message: str
    review_href: ReviewHref | None = None


class Question(BaseModel):
    code: str
    text: str
    review_href: ReviewHref


class ScheduleSection(BaseModel):
    source: Literal["saved_timetable"] = "saved_timetable"
    status: Status
    window: Window
    classes: list[Occurrence] = Field(default_factory=list)
    fixed_blocks: list[FixedOccurrence] = Field(default_factory=list)
    classes_complete: bool = True
    fixed_blocks_complete: bool = True
    class_count: int | None = None
    fixed_block_count: int | None = None
    next_class: Occurrence | None = None
    review_href: Literal["/classes"] = "/classes"


class TaskFact(BaseModel):
    source: Literal["saved_task"] = "saved_task"
    source_kind: Literal["manual", "lms", "email"]
    id: UUID
    revision: int
    title: str
    course_id: UUID | None
    due_at: datetime | None
    deadline_meaning: Literal["saved_task_deadline"] = "saved_task_deadline"
    remaining_minutes: int
    priority: int
    review_href: Literal["/planner"] = "/planner"


class WorkTotals(BaseModel):
    active_tasks: int
    overdue_tasks: int
    due_in_window_tasks: int


class WorkSection(BaseModel):
    source: Literal["saved_task"] = "saved_task"
    status: Status
    window: Window
    items: list[TaskFact] = Field(default_factory=list)
    totals: WorkTotals | None = None
    known_totals: WorkTotals | None = None
    has_more: bool = False


class OutcomeTotals(BaseModel):
    done_blocks: int = 0
    took_longer_blocks: int = 0
    skipped_blocks: int = 0
    recorded_minutes: int = 0


class OutcomeSection(BaseModel):
    source: Literal["recorded_planner_outcome"] = "recorded_planner_outcome"
    status: Status
    window: Window
    totals: OutcomeTotals | None = None
    known_totals: OutcomeTotals | None = None
    done_block_is_assignment_submission: Literal[False] = False


class GoalFact(BaseModel):
    source: Literal["self_report"] = "self_report"
    id: UUID
    revision: int
    title: str
    category: Literal["academic", "habit", "personal"]
    tracking: Literal["qualitative", "count"]
    unit: str | None
    cadence: Literal["daily", "weekly", "total"]
    starts_on: date
    ends_on: date | None
    progress: ProgressView
    review_href: Literal["/goals"] = "/goals"


class GoalSection(BaseModel):
    source: Literal["self_report"] = "self_report"
    status: Status
    items: list[GoalFact] = Field(default_factory=list)
    periods_meaning: Literal["each_goal_has_its_own_progress_period"] = (
        "each_goal_has_its_own_progress_period"
    )


class CoachReview(BaseModel):
    kind: Kind
    server_now: datetime
    timezone: str
    local_date: date
    week_starts_on: Literal["monday"] = "monday"
    window: Window
    schedule: ScheduleSection
    outlook: ScheduleSection
    work: WorkSection
    outcomes: OutcomeSection
    goals: GoalSection
    summary_lines: list[str]
    notices: list[Notice]
    question: Question | None
    basis: Literal["saved_records_and_explicit_reports_only"] = (
        "saved_records_and_explicit_reports_only"
    )
    automatic_rescheduling: Literal[False] = False
    attendance_sleep_grades_observed: Literal[False] = False
