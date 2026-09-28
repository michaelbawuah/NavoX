from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from navox.providers.google_gmail import GmailReplyMetadata

PostSendState = Literal["waiting", "completed", "unchanged"]


class PrepareGmailSendRequest(BaseModel):
    request_id: UUID
    connection_id: UUID
    to: EmailStr
    subject: str = Field(min_length=1, max_length=256)
    body_text: str = Field(min_length=1, max_length=50_000)
    post_send_state: PostSendState = "waiting"

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("subject must contain non-whitespace characters")
        if "\r" in normalized or "\n" in normalized:
            raise ValueError("subject must be a single line")
        return normalized

    @field_validator("body_text")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("body_text must contain non-whitespace characters")
        return normalized


class EditGmailSendRequest(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=256)
    body_text: str = Field(min_length=1, max_length=50_000)
    post_send_state: PostSendState = "waiting"

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("subject must contain non-whitespace characters")
        if "\r" in normalized or "\n" in normalized:
            raise ValueError("subject must be a single line")
        return normalized

    @field_validator("body_text")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("body_text must contain non-whitespace characters")
        return normalized


class ApprovalDecisionRequest(BaseModel):
    request_id: UUID
    expected_payload_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    draft_version: int | None = Field(default=None, ge=1)


class StoredGmailSendPayload(BaseModel):
    connection_id: UUID
    sender: EmailStr
    to: EmailStr
    subject: str = Field(min_length=1, max_length=256)
    body_text: str = Field(min_length=1, max_length=50_000)
    post_send_state: PostSendState
    draft_id: UUID | None = None
    draft_version: int | None = None
    draft_payload_hash: str | None = None
    reply: GmailReplyMetadata | None = None
