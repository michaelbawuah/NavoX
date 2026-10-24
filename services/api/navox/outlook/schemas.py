from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Connect(Input):
    confirmed: Literal[True]
    connection_id: UUID | None = None
    send: bool = False


class Content(Input):
    subject: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=12000)

    @field_validator("subject")
    @classmethod
    def subject_header(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid subject")
        return value

    @field_validator("body")
    @classmethod
    def body_text(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("Invalid message")
        return value.replace("\r\n", "\n").replace("\r", "\n")


class Preview(Content):
    request_id: UUID
    connection_id: UUID
    source_message_id: str = Field(min_length=1, max_length=512)


class Revise(Content):
    expected_revision: int = Field(ge=1)


class Send(Input):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    expected_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


class ReplyCheck(Input):
    automatic: bool = False


class Generate(Input):
    connection_id: UUID
    source_message_id: str = Field(min_length=1, max_length=512)
    instructions: str = Field(min_length=1, max_length=2000)
    previous: Content | None = None


class TaskPreview(Input):
    request_id: UUID
    connection_id: UUID
    source_message_id: str = Field(min_length=1, max_length=512)
    confirmed: Literal[True]
