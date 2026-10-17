from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from navox.student.schemas import Input
from navox.student_planning.schemas import aware

Category = Literal["needs_reply", "has_deadline", "fyi", "ignore"]


class AnalyzeInput(Input):
    request_id: UUID
    source_id: UUID
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class CorrectionInput(Input):
    request_id: UUID
    revision: int = Field(strict=True, ge=1)
    category: Category
    not_important: bool = Field(strict=True)
    course_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_at: datetime | None = None
    estimated_minutes: int | None = Field(default=None, strict=True, ge=1, le=10080)
    confirmed_course: bool = Field(strict=True)
    confirmed_deadline: bool = Field(strict=True)

    @field_validator("due_at")
    @classmethod
    def deadline(cls, value: datetime | None) -> datetime | None:
        return aware(value) if value else None


class AcceptInput(Input):
    request_id: UUID
    revision: int = Field(strict=True, ge=1)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    task_id: UUID | None = None
    task_revision: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def exact_existing_task(self) -> Self:
        if (self.task_id is None) != (self.task_revision is None):
            raise ValueError("Review the exact existing task revision")
        return self

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit acceptance is required")
        return value


class EraseInput(Input):
    revision: int = Field(strict=True, ge=0)
