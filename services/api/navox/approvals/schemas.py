from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

PostSendState = Literal["waiting", "completed", "unchanged"]


class PrepareGmailSendRequest(BaseModel):
    request_id: UUID
    connection_id: UUID
    to: EmailStr
    subject: str = Field(min_length=1, max_length=256)
    body_text: str = Field(min_length=1, max_length=50_000)
    post_send_state: PostSendState = "waiting"

    @field_validator("subject", "body_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized


class EditGmailSendRequest(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=256)
    body_text: str = Field(min_length=1, max_length=50_000)
    post_send_state: PostSendState = "waiting"

    @field_validator("subject", "body_text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized


class ApprovalDecisionRequest(BaseModel):
    request_id: UUID
