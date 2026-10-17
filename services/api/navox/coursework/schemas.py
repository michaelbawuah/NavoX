from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field, field_validator, model_validator

from navox.student.schemas import Input

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_PAGES = 100
MAX_MATERIALS_COURSE = 40
MAX_MATERIALS_OWNER = 120
MAX_BYTES_COURSE = 20 * 1024 * 1024
MAX_BYTES_OWNER = 50 * 1024 * 1024
MaterialKind = Literal["note", "material", "syllabus"]


class MaterialQuote(Input):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=False)

    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("A source quote contains unsupported control characters")
        return value

    material_id: UUID
    revision: int = Field(strict=True, ge=1)
    page: int = Field(strict=True, ge=1, le=MAX_PAGES)
    quote: str = Field(min_length=1, max_length=1000)


class Staff(Input):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    office_hours: str | None = Field(default=None, max_length=500)
    source: MaterialQuote | None = None


class GradeWeight(Input):
    name: str = Field(min_length=1, max_length=120)
    weight_percent: Decimal = Field(ge=0, le=100, decimal_places=2, allow_inf_nan=False)
    source: MaterialQuote | None = None


class OverviewData(Input):
    staff: list[Staff] = Field(default_factory=list, max_length=20)
    grade_breakdown: list[GradeWeight] = Field(default_factory=list, max_length=20)
    ai_policy: MaterialQuote | None = None

    @model_validator(mode="after")
    def weights(self) -> Self:
        if sum(item.weight_percent for item in self.grade_breakdown) > 100:
            raise ValueError("Saved grading weights cannot exceed 100 percent")
        return self


class OverviewUpdate(OverviewData):
    revision: int = Field(strict=True, ge=0)
    request_id: UUID


class MaterialUpload(Input):
    title: str = Field(min_length=1, max_length=160)
    kind: MaterialKind
    request_id: UUID
    revision: int = Field(strict=True, ge=0)


class MaterialView(Input):
    id: UUID
    title: str
    kind: MaterialKind
    revision: int
    media_type: str
    byte_size: int
    text_size: int
    page_count: int
    created_at: datetime
    updated_at: datetime


class MaterialDetail(MaterialView):
    pages: list[str]


class CommandReceipt(Input):
    request_id: UUID
    entity_id: UUID
    revision: int
    reused: bool = False
    available: bool = True


class EmailLinkInput(Input):
    source_id: UUID
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID


class DeleteInput(Input):
    revision: int = Field(strict=True, ge=1)
    request_id: UUID


class EmailLinkView(Input):
    id: UUID
    source_id: UUID
    source_hash: str
    revision: int
    # No body, recipients, or copied private subject is retained in this domain.
    source_type: Literal["communication.message"] = "communication.message"
    source_open_url: str | None = None
    title: str | None = None
    sender_label: str | None = None
    display_label: str | None = None
