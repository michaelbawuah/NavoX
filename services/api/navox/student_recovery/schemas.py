from datetime import date, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from navox.coursework.schemas import MaterialQuote
from navox.student.schemas import Input
from navox.student_planning.schemas import PlanResult, aware


class RecoveryTrigger(Input):
    kind: Literal["reported_absence", "overdue_task", "skipped_blocks"]
    course_ids: list[UUID] = Field(default_factory=list, max_length=10)
    from_day: date | None = None
    through_day: date | None = None
    task_id: UUID | None = None
    task_revision: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def specific(self) -> Self:
        if len(set(self.course_ids)) != len(self.course_ids):
            raise ValueError("Select each course once")
        if self.kind == "reported_absence":
            if not self.course_ids or self.from_day is None or self.through_day is None:
                raise ValueError("Choose the courses and absence dates you are reporting")
            if self.task_id or self.task_revision:
                raise ValueError("An absence trigger uses courses and dates")
        elif self.kind == "overdue_task":
            if self.task_id is None or self.task_revision is None:
                raise ValueError("Choose the overdue task and its current revision")
            if self.from_day or self.through_day or self.course_ids:
                raise ValueError("An overdue trigger uses the selected task")
        elif self.from_day or self.through_day or self.task_id or self.task_revision:
            raise ValueError("Skipped-block triggers use recorded outcomes")
        return self


class WorkChoice(Input):
    task_id: UUID | None = None
    task_revision: int | None = Field(default=None, strict=True, ge=1)
    material_id: UUID | None = None
    material_revision: int | None = Field(default=None, strict=True, ge=1)
    course_id: UUID | None = None
    remaining_minutes: int = Field(strict=True, ge=1, le=10080)
    priority: int = Field(strict=True, ge=1, le=5)
    personal_catchup_target: datetime

    _target = field_validator("personal_catchup_target")(aware)

    @model_validator(mode="after")
    def source(self) -> Self:
        task = self.task_id is not None
        if task == (self.material_id is not None):
            raise ValueError("Choose one saved task or course material")
        if task and (self.task_revision is None or self.material_revision is not None):
            raise ValueError("Choose the current task revision")
        if not task and (
            self.course_id is None
            or self.material_revision is None
            or self.task_revision is not None
        ):
            raise ValueError("Choose the material's course and current revision")
        return self


class PolicyChoice(Input):
    course_id: UUID
    kind: Literal["late_work", "extension"]
    source: MaterialQuote


class RecoveryInput(Input):
    request_id: UUID
    trigger: RecoveryTrigger
    days: int = Field(default=4, strict=True, ge=2, le=14)
    work: list[WorkChoice] = Field(min_length=1, max_length=20)
    policies: list[PolicyChoice] = Field(default_factory=list, max_length=10)


class RecoveryWork(Input):
    id: UUID
    task_id: UUID | None
    material_id: UUID | None
    course_id: UUID | None
    title: str
    remaining_minutes: int
    priority: int
    official_due_at: datetime | None
    deadline_source: Literal["school_assignment", "student_recorded", "not_recorded"]
    official_deadline_status: Literal["overdue", "upcoming", "not_recorded"]
    personal_catchup_target: datetime
    source_revision: int
    deadline_notice: str


class RecoveryView(BaseModel):
    preview_id: UUID
    preview_hash: str
    expires_at: datetime
    server_now: datetime
    timezone: str
    trigger: RecoveryTrigger
    evidence: list[dict[str, object]]
    available_materials: list[dict[str, object]]
    policies: list[PolicyChoice]
    work: list[RecoveryWork]
    result: PlanResult
    unknowns: list[str]
    proposal_only: Literal[True] = True
    requires_student_review: Literal[True] = True
    official_deadlines_changed: Literal[False] = False
    result_deadline_basis: Literal["personal_catchup_target"] = "personal_catchup_target"


class RecipientChoice(Input):
    course_id: UUID
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    confirmed: Literal[True]
    source: MaterialQuote | None = None

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Choose and confirm the recipient")
        return value


class ExtensionInput(Input):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=False)

    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("Text contains unsupported control characters")
        return value

    request_id: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    work_id: UUID
    recipient: RecipientChoice
    student_reason: str = Field(min_length=1, max_length=1000)
    requested_extension_until: datetime
    policy: PolicyChoice | None = None

    _target = field_validator("requested_extension_until")(aware)

    @field_validator("student_reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Tell the recipient your reason in your own words")
        return value


class ExtensionView(BaseModel):
    preview_id: UUID
    preview_hash: str
    expires_at: datetime
    timezone: str
    recipient: str
    recipient_name: str
    recipient_source: MaterialQuote | None
    subject: str
    body: str
    student_reported_reason: str
    official_due_at: datetime
    deadline_source: Literal["school_assignment", "student_recorded"]
    requested_extension_until: datetime
    policy: PolicyChoice | None
    notices: list[str]
    editable_preview_only: Literal[True] = True
    sent: Literal[False] = False
    external_draft_created: Literal[False] = False


class EraseInput(Input):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
