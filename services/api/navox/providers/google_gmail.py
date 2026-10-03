import base64
import re
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol, cast

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_EMAIL_ADDRESS = TypeAdapter(EmailStr)


class GmailProviderError(RuntimeError):
    """Raised when Gmail send cannot be safely confirmed."""


class GmailReplyMetadata(BaseModel):
    """Server-resolved source binding, included in the exact approval hash."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_message_id: str = Field(min_length=1, max_length=512, pattern=r"^[\w-]+$")
    thread_id: str = Field(min_length=1, max_length=512, pattern=r"^[\w-]+$")
    source_subject: str = Field(min_length=1, max_length=256, pattern=r"^[^\r\n]+$")
    in_reply_to: str = Field(min_length=1, max_length=900)
    references: str = Field(min_length=1, max_length=8000)
    # Populated from the freshly fetched From header. ``None`` means the source
    # has no single safe reply author; the knowledge-email draft path refuses it.
    source_author: str | None = Field(default=None, max_length=320)

    @field_validator("source_author")
    @classmethod
    def normalize_source_author(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(_EMAIL_ADDRESS.validate_python(value.strip())).casefold()
        except (ValidationError, AttributeError):
            return None

    @model_validator(mode="after")
    def safe_message_ids(self) -> "GmailReplyMetadata":
        # Accept the usual RFC message-id form; unsupported/ambiguous headers
        # fail closed instead of creating a new conversation or extra headers.
        message_id = r"<[-A-Za-z0-9!#$%&'*+/=?^_`{|}~.]+@[-A-Za-z0-9.]+>"
        if not re.fullmatch(message_id, self.in_reply_to) or not re.fullmatch(
            rf"{message_id}( {message_id})*", self.references
        ):
            raise ValueError("Invalid Gmail reply message identifiers")
        if self.references.split(" ")[-1] != self.in_reply_to:
            raise ValueError("Reply references must end with the source Message-ID")
        return self

    def matches_subject(self, subject: str) -> bool:
        def base(value: str) -> str:
            return re.sub(r"^(?:re:\s*)+", "", value.strip(), flags=re.IGNORECASE)

        return base(subject) == base(self.source_subject)


@dataclass(frozen=True)
class GmailSendPayload:
    sender: str
    to: str
    subject: str
    body_text: str
    reply: GmailReplyMetadata | None = None


@dataclass(frozen=True)
class GmailSendReceipt:
    message_id: str
    thread_id: str | None


class GmailGateway(Protocol):
    async def send(
        self,
        *,
        access_token: str,
        payload: GmailSendPayload,
        idempotency_key: str,
    ) -> GmailSendReceipt: ...


def encode_message(payload: GmailSendPayload) -> str:
    message = EmailMessage()
    message["From"] = payload.sender
    message["To"] = payload.to
    message["Subject"] = payload.subject
    if payload.reply is not None:
        if not payload.reply.matches_subject(payload.subject):
            raise ValueError("Reply subject does not match the source thread")
        message["In-Reply-To"] = payload.reply.in_reply_to
        message["References"] = payload.reply.references
    message.set_content(payload.body_text)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


class GoogleGmailGateway:
    """Thin Gmail API gateway. It does not retry ambiguous sends."""

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def send(
        self,
        *,
        access_token: str,
        payload: GmailSendPayload,
        idempotency_key: str,
    ) -> GmailSendReceipt:
        # Gmail messages.send has no provider idempotency key. NavoX therefore
        # performs at-most-once dispatch and never retries an ambiguous request.
        del idempotency_key
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    GMAIL_SEND_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={
                        "raw": encode_message(payload),
                        **({"threadId": payload.reply.thread_id} if payload.reply else {}),
                    },
                )
                response.raise_for_status()
                response_data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise GmailProviderError("Gmail send outcome could not be confirmed") from error

        if not isinstance(response_data, dict):
            raise GmailProviderError("Gmail returned an invalid send response")
        data = cast(dict[str, Any], response_data)
        message_id = data.get("id")
        thread_id = data.get("threadId")
        if not isinstance(message_id, str) or not message_id:
            raise GmailProviderError("Gmail did not confirm a message ID")
        if payload.reply is not None and thread_id != payload.reply.thread_id:
            raise GmailProviderError("Gmail did not confirm the approved reply thread")
        return GmailSendReceipt(
            message_id=message_id,
            thread_id=thread_id if isinstance(thread_id, str) and thread_id else None,
        )
