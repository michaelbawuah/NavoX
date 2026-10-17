"""Only own text and explicitly approved metadata enter the export worker."""

import unicodedata
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_TEXT_CHARS = 20000
MAX_PAGES = 20
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 128 * 1024


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    course_revision: int = Field(strict=True, ge=1)
    generation: int = Field(strict=True, ge=0)
    title: str = Field(min_length=1, max_length=160)
    author: str | None = Field(default=None, min_length=1, max_length=120)
    course_label: str | None = Field(default=None, min_length=1, max_length=244)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    confirmed_own_work: Literal[True]
    confirmed_metadata: Literal[True]

    @field_validator("confirmed_own_work", "confirmed_metadata", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicitly confirm your own work and the displayed metadata")
        return value

    @field_validator("title", "author", "course_label")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or any(unicodedata.category(char).startswith("C") for char in value)
        ):
            raise ValueError("Metadata needs a nonempty single line of plain text")
        return value

    @model_validator(mode="after")
    def plain_text(self) -> Self:
        if not self.text.strip():
            raise ValueError("Add your own text before formatting it")
        if any(
            unicodedata.category(char).startswith("C") and char not in "\r\n" for char in self.text
        ):
            raise ValueError("Use plain text with line breaks and spaces")
        return self


class ExportContext(BaseModel):
    course_revision: int
    generation: int
    course_label: str
    max_text_chars: Literal[20000] = 20000
    max_pages_including_title: Literal[20] = 20
    max_pdf_bytes: Literal[2097152] = 2097152
    saved: Literal[False] = False
