"""Bounded, read-only Gmail thread metadata for explicit reply watches.

No message body, snippet, subject, or provider error prose leaves this adapter.
A negative result requires a complete valid thread and the exact sent reference.
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Protocol

import httpx
from pydantic import EmailStr, TypeAdapter, ValidationError

GMAIL_THREADS_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_THREAD_MESSAGES = 500
_ID = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_EMAIL = TypeAdapter(EmailStr)
_EXCLUDED_LABELS = frozenset({"SENT", "DRAFT", "TRASH", "SPAM"})
_DIAGNOSTICS = frozenset(
    {
        "google_authentication_failed",
        "google_permission_denied",
        "google_rate_limited",
        "google_provider_unavailable",
        "google_thread_not_found",
        "google_transport_error",
        "google_invalid_response",
        "thread_watch_reference_invalid",
    }
)


class ThreadWatchMetadataError(RuntimeError):
    """Only fixed diagnostics and bounded status data may escape the gateway."""

    def __init__(
        self,
        code: str = "google_invalid_response",
        *,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.code = code if code in _DIAGNOSTICS else "google_invalid_response"
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        self.retry_after_seconds = (
            retry_after_seconds
            if type(retry_after_seconds) is int and 1 <= retry_after_seconds <= 86_400
            else None
        )
        super().__init__(self.code)


@dataclass(frozen=True)
class ThreadMessage:
    id: str
    thread_id: str
    label_ids: frozenset[str]
    internal_date: datetime
    sender: str
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class ThreadMetadata:
    id: str
    messages: tuple[ThreadMessage, ...]


@dataclass(frozen=True)
class ReplyEvidence:
    message_id: str
    received_at: datetime


class ThreadWatchGateway(Protocol):
    async def fetch(self, *, access_token: str, thread_id: str) -> ThreadMetadata: ...


def _address(value: str) -> str:
    if not value or len(value) > 320 or any(ord(char) < 32 for char in value):
        raise ThreadWatchMetadataError()
    try:
        return str(_EMAIL.validate_python(value)).casefold()
    except ValidationError:
        raise ThreadWatchMetadataError() from None


def _header_addresses(value: object, *, single: bool) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ThreadWatchMetadataError()
    parsed = getaddresses([value])
    if not parsed or len(parsed) > 20 or (single and len(parsed) != 1):
        raise ThreadWatchMetadataError()
    addresses = tuple(_address(address) for _, address in parsed)
    if len(set(addresses)) != len(addresses):
        raise ThreadWatchMetadataError()
    return addresses


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ThreadWatchMetadataError()
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ThreadWatchMetadataError()
        result[key] = value
    return result


def _parse_thread(content: bytes, *, thread_id: str, now: datetime) -> ThreadMetadata:
    try:
        data = json.loads(content, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ThreadWatchMetadataError() from None
    if not isinstance(data, dict) or _identifier(data.get("id")) != thread_id:
        raise ThreadWatchMetadataError()
    messages = data.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_THREAD_MESSAGES:
        raise ThreadWatchMetadataError()
    result: list[ThreadMessage] = []
    seen_ids: set[str] = set()
    maximum_date_ms = int((now + timedelta(minutes=5)).timestamp() * 1000)
    for message in messages:
        if not isinstance(message, dict):
            raise ThreadWatchMetadataError()
        message_id = _identifier(message.get("id"))
        if message_id in seen_ids or _identifier(message.get("threadId")) != thread_id:
            raise ThreadWatchMetadataError()
        seen_ids.add(message_id)
        labels = message.get("labelIds", [])
        if not isinstance(labels, list) or len(labels) > 100:
            raise ThreadWatchMetadataError()
        label_ids = frozenset(_identifier(label) for label in labels)
        if len(label_ids) != len(labels):
            raise ThreadWatchMetadataError()
        date_ms = message.get("internalDate")
        if (
            not isinstance(date_ms, str)
            or re.fullmatch(r"[0-9]{1,17}", date_ms) is None
            or not 0 < int(date_ms) <= maximum_date_ms
        ):
            raise ThreadWatchMetadataError()
        internal_date = datetime.fromtimestamp(int(date_ms) / 1000, UTC)
        payload = message.get("payload")
        headers = payload.get("headers") if isinstance(payload, dict) else None
        if not isinstance(headers, list) or not 1 <= len(headers) <= 8:
            raise ThreadWatchMetadataError()
        required_headers: dict[str, object] = {}
        for header in headers:
            if not isinstance(header, dict) or not isinstance(header.get("name"), str):
                raise ThreadWatchMetadataError()
            name = header["name"].casefold()
            if name not in {"from", "to"} or name in required_headers:
                raise ThreadWatchMetadataError()
            required_headers[name] = header.get("value")
        sender = _header_addresses(required_headers.get("from"), single=True)[0]
        recipients = _header_addresses(required_headers.get("to"), single=False)
        result.append(
            ThreadMessage(message_id, thread_id, label_ids, internal_date, sender, recipients)
        )
    return ThreadMetadata(thread_id, tuple(result))


def classify_reply(
    thread: ThreadMetadata,
    *,
    sent_message_id: str,
    sender: str,
    recipient: str,
) -> ReplyEvidence | None:
    """Match only a later incoming message from the watched person to the owner."""
    try:
        owner = _address(sender)
        target = _address(recipient)
        reference_id = _identifier(sent_message_id)
    except ThreadWatchMetadataError:
        raise ThreadWatchMetadataError("thread_watch_reference_invalid") from None
    references = [message for message in thread.messages if message.id == reference_id]
    if len(references) != 1 or owner == target:
        raise ThreadWatchMetadataError("thread_watch_reference_invalid")
    reference = references[0]
    if (
        "SENT" not in reference.label_ids
        or reference.label_ids & {"DRAFT", "TRASH", "SPAM"}
        or reference.sender != owner
        or reference.recipients != (target,)
        or reference.thread_id != thread.id
    ):
        raise ThreadWatchMetadataError("thread_watch_reference_invalid")
    replies = [
        message
        for message in thread.messages
        if message.internal_date > reference.internal_date
        and not message.label_ids & _EXCLUDED_LABELS
        and message.sender == target
        and owner in message.recipients
    ]
    if not replies:
        return None
    reply = min(replies, key=lambda message: (message.internal_date, message.id))
    return ReplyEvidence(reply.id, reply.internal_date)


def _retry_after(response: httpx.Response, *, now: datetime) -> int | None:
    value = response.headers.get("Retry-After", "").strip()
    if not value or len(value) > 128:
        return None
    if value.isascii() and value.isdigit():
        seconds = int(value)
    else:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return None
            seconds = math.ceil((retry_at - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return min(seconds, 86_400) if seconds > 0 else None


def _http_error(response: httpx.Response, content: bytes, *, now: datetime) -> None:
    status = response.status_code
    if status == 401:
        code = "google_authentication_failed"
    elif status == 404:
        code = "google_thread_not_found"
    elif status == 429:
        code = "google_rate_limited"
    elif status >= 500:
        code = "google_provider_unavailable"
    else:
        code = "google_permission_denied" if status == 403 else "google_invalid_response"
    if status == 403:
        try:
            body = json.loads(content)
            error = body.get("error") if isinstance(body, dict) else None
            errors = error.get("errors") if isinstance(error, dict) else None
            reasons: set[str] = set()
            if isinstance(errors, list):
                reasons.update(
                    entry["reason"]
                    for entry in errors
                    if isinstance(entry, dict) and isinstance(entry.get("reason"), str)
                )
            details = error.get("details") if isinstance(error, dict) else None
            if isinstance(details, list):
                reasons.update(
                    entry["reason"]
                    for entry in details
                    if isinstance(entry, dict)
                    and entry.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
                    and isinstance(entry.get("reason"), str)
                )
            if reasons & {
                "rateLimitExceeded",
                "userRateLimitExceeded",
                "RATE_LIMIT_EXCEEDED",
                "quotaExceeded",
                "dailyLimitExceeded",
                "QUOTA_EXCEEDED",
                "RESOURCE_QUOTA_EXCEEDED",
            }:
                code = "google_rate_limited"
        except (ValueError, UnicodeError, RecursionError):
            pass
    raise ThreadWatchMetadataError(
        code, http_status=status, retry_after_seconds=_retry_after(response, now=now)
    )


class GoogleThreadWatchGateway:
    """One metadata GET, without redirects, ambient proxy settings, or retries."""

    async def fetch(self, *, access_token: str, thread_id: str) -> ThreadMetadata:
        thread_id = _identifier(thread_id)
        if (
            not isinstance(access_token, str)
            or not access_token
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in access_token)
        ):
            raise ThreadWatchMetadataError("google_authentication_failed")
        now = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(
                timeout=10.0, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    "GET",
                    f"{GMAIL_THREADS_ENDPOINT}/{thread_id}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    params=[
                        ("format", "metadata"),
                        ("metadataHeaders", "From"),
                        ("metadataHeaders", "To"),
                        (
                            "fields",
                            "id,messages(id,threadId,labelIds,internalDate,payload/headers)",
                        ),
                    ],
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65_536):
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ThreadWatchMetadataError(http_status=response.status_code)
                        content.extend(chunk)
                    if response.status_code != 200:
                        _http_error(response, bytes(content), now=now)
                    return _parse_thread(bytes(content), thread_id=thread_id, now=now)
        except httpx.HTTPError:
            raise ThreadWatchMetadataError("google_transport_error") from None
