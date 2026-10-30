"""Public memory controls never accept an owner supplied by the client."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from navox.memory.schemas import safe_text


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


class PrivacyResponse(StrictModel):
    history_enabled: bool = False
    retention_days: Literal[30, 90] | None = None
    off_the_record: bool = False
    generation: int = 0
    disclosure_version: Literal[1] | None = None


class PrivacyUpdate(PrivacyResponse):
    generation: int = Field(ge=0)
    disclosure_accepted: bool = False

    @model_validator(mode="after")
    def explicit_choice(self) -> Self:
        if self.history_enabled and "retention_days" not in self.model_fields_set:
            raise ValueError("Choose 30 days, 90 days, or Never before enabling history")
        return self


class FactCreate(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=500)
    source_conversation_id: UUID | None = None
    sensitive_agreement: bool = False

    @field_validator("label", "content")
    @classmethod
    def safe(cls, value: str) -> str:
        return safe_text(value)


class FactUpdate(StrictModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    content: str | None = Field(default=None, min_length=1, max_length=500)
    sensitive_agreement: bool = False

    @field_validator("label", "content")
    @classmethod
    def safe(cls, value: str | None) -> str | None:
        return safe_text(value) if value is not None else None

    @model_validator(mode="after")
    def change(self) -> Self:
        if self.label is None and self.content is None:
            raise ValueError("Provide a label or fact to change")
        return self


class FactResponse(StrictModel):
    id: UUID
    label: str
    content: str
    source_kind: Literal["user_saved", "vox", "chat"]
    source_conversation_id: UUID | None
    sensitive: bool
    created_at: datetime
    updated_at: datetime


class HistoryResponse(StrictModel):
    id: UUID
    title: str
    source_kind: Literal["vox", "chat"]
    created_at: datetime
    updated_at: datetime
    message_count: int


class HistoryPage(StrictModel):
    items: list[HistoryResponse] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=4096)
    search_complete: bool
    scanned: int = Field(ge=0, le=100)


class HistoryPageInput(StrictModel):
    query: str = Field(default="", max_length=200)
    cursor: str | None = Field(default=None, max_length=4096)
    page_size: int = Field(default=25, strict=True, ge=1, le=100)


class HistoryTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class HistoryDetail(HistoryResponse):
    turns: list[HistoryTurn]
    summary: str | None


class EraseAll(StrictModel):
    generation: int = Field(ge=0)
    confirmation: Literal["DELETE EVERYTHING"]
