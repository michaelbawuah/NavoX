from uuid import UUID

from pydantic import ConfigDict, EmailStr, Field, field_validator

from navox.ai.foundation.contracts import Contract


class DraftContent(Contract):
    # Attachments and multiple recipients must not be silently dropped by a connector.
    to: list[EmailStr] = Field(min_length=1, max_length=1)
    cc: list[EmailStr] = Field(default_factory=list, max_length=0)
    bcc: list[EmailStr] = Field(default_factory=list, max_length=0)
    subject: str = Field(min_length=1, max_length=256, pattern=r"^[^\r\n]+$")
    body: str = Field(min_length=1, max_length=50000)
    attachment_refs: list[str] = Field(default_factory=list, max_length=0)

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator("subject", "body")
    @classmethod
    def normalize_reviewed_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Draft text cannot be blank")
        return value


class GenerateDraft(Contract):
    commitment_id: UUID
    recipient: EmailStr
    instructions: str = Field(min_length=1, max_length=4000)
    source_id: UUID


class ReviseDraft(DraftContent):
    expected_version: int = Field(ge=1)


class RegenerateDraft(Contract):
    expected_version: int = Field(ge=1)
    instructions: str = Field(min_length=1, max_length=4000)


class PrepareDraft(Contract):
    expected_version: int = Field(ge=1)
    connection_id: UUID
    request_id: UUID
    reply_to_source: bool = False
