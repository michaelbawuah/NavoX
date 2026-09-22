import base64
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol, cast

import httpx

GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailProviderError(RuntimeError):
    """Raised when Gmail send cannot be safely confirmed."""


@dataclass(frozen=True)
class GmailSendPayload:
    sender: str
    to: str
    subject: str
    body_text: str


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
                    json={"raw": encode_message(payload)},
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
        return GmailSendReceipt(
            message_id=message_id,
            thread_id=thread_id if isinstance(thread_id, str) and thread_id else None,
        )
