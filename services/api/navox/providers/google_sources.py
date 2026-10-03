"""Read-only Google adapters. Source bodies exist only during processing.

Gmail history IDs and Calendar sync tokens are opaque provider cursors; only
complete, validated batches produce a replacement cursor. Exhausting a bounded
scan raises instead of silently dropping unprocessed changes.
"""

import asyncio
import base64
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from navox.intelligence.contracts import SourceDocument, SourceIdentity
from navox.providers.google_gmail import GmailReplyMetadata

GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
SOURCE_SCOPES = {"gmail": GMAIL_READ_SCOPE, "calendar": CALENDAR_READ_SCOPE}
GMAIL_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_ROOT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MAX_CONTENT_CHARS = 32_000
MAX_PAGES = 20
GOOGLE_SOURCE_DIAGNOSTIC_CODES = frozenset(
    {
        "google_api_disabled",
        "google_scope_missing",
        "google_authentication_failed",
        "google_permission_denied",
        "google_rate_limited",
        "google_daily_limit_exceeded",
        "google_quota_exceeded",
        "google_provider_unavailable",
        "google_transport_error",
        "google_invalid_response",
        "google_source_error",
    }
)


class GoogleSourceError(RuntimeError):
    """A source batch could not be read completely and safely."""

    def __init__(
        self,
        message: str = "Google source read failed",
        *,
        code: str = "google_source_error",
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
        provider_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code in GOOGLE_SOURCE_DIAGNOSTIC_CODES else "google_source_error"
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        self.retry_after_seconds = (
            retry_after_seconds
            if type(retry_after_seconds) is int and 1 <= retry_after_seconds <= 86_400
            else None
        )
        self.provider_reason = (
            provider_reason
            if self.code == "google_rate_limited"
            and self.http_status == 403
            and provider_reason in ("userRateLimitExceeded", "rateLimitExceeded")
            else None
        )

    def diagnostic(self) -> dict[str, str | int]:
        """Only fixed categories and bounded values may enter logs or workflow history."""
        result: dict[str, str | int] = {"code": self.code}
        if self.http_status is not None:
            result["http_status"] = self.http_status
        if self.retry_after_seconds is not None:
            result["retry_after_seconds"] = self.retry_after_seconds
        if self.provider_reason is not None:
            result["provider_reason"] = self.provider_reason
        return result


class GoogleSourceAuthorizationError(GoogleSourceError):
    """The provider no longer permits the requested read."""


class ExpiredSourceCursor(GoogleSourceError):
    """The provider requires bounded full reconciliation."""


def _google_error_code(response: httpx.Response) -> str:
    """Classify documented Google errors without retaining their private details.

    Provider prose, request URLs, project IDs and metadata are never diagnostics.
    Only recognized reason tokens from legacy errors and google.rpc.ErrorInfo are
    used for classification; unknown or malformed bodies keep a generic category.
    """
    status = response.status_code
    if status == 401:
        return "google_authentication_failed"
    if status >= 500:
        return "google_provider_unavailable"
    if status not in {403, 429}:
        return "google_source_error"
    fallback = "google_rate_limited" if status == 429 else "google_permission_denied"
    try:
        body = response.json()
    except ValueError:
        return fallback
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return fallback
    reasons: set[str] = set()
    legacy = error.get("errors")
    if isinstance(legacy, list):
        reasons.update(
            entry["reason"]
            for entry in legacy
            if isinstance(entry, dict) and isinstance(entry.get("reason"), str)
        )
    details = error.get("details")
    if isinstance(details, list):
        reasons.update(
            entry["reason"]
            for entry in details
            if isinstance(entry, dict)
            and entry.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo"
            and isinstance(entry.get("reason"), str)
        )
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return "google_api_disabled"
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return "google_scope_missing"
    if "dailyLimitExceeded" in reasons:
        return "google_daily_limit_exceeded"
    if reasons & {"quotaExceeded", "QUOTA_EXCEEDED", "RESOURCE_QUOTA_EXCEEDED"}:
        return "google_quota_exceeded"
    if reasons & {
        "rateLimitExceeded",
        "userRateLimitExceeded",
        "RATE_LIMIT_EXCEEDED",
    }:
        return "google_rate_limited"
    return fallback


def _google_rate_limit_reason(response: httpx.Response, code: str) -> str | None:
    """Retain one allowlisted legacy reason, without interpreting its quota scope.

    These exact tokens aid diagnosis without retaining provider prose or IDs.
    Generic 429, structured ErrorInfo, unknown and mixed reasons are omitted.
    """
    if response.status_code != 403 or code != "google_rate_limited":
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    legacy = error.get("errors") if isinstance(error, dict) else None
    if not isinstance(legacy, list):
        return None
    reasons: set[str] = {
        entry["reason"]
        for entry in legacy
        if isinstance(entry, dict) and isinstance(entry.get("reason"), str)
    }
    if len(reasons) == 1:
        reason = next(iter(reasons))
        if reason in {"userRateLimitExceeded", "rateLimitExceeded"}:
            return reason
    return None


def _retry_after(response: httpx.Response, now: datetime) -> int | None:
    """Keep only a bounded delay, never the provider's raw header value."""
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


@dataclass(frozen=True)
class SourceBatch:
    documents: list[SourceDocument]
    cursor: str
    reset: bool = False


@dataclass(frozen=True)
class GmailPage:
    """One bounded page of IDs; no message headers or bodies are retained."""

    message_ids: dict[str, bool]
    next_page_token: str | None
    cursor: str | None


def _text(value: Any, limit: int = 512) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _time(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
        except ValueError:
            pass
    return fallback


def _identity(email: Any, name: Any = None) -> SourceIdentity | None:
    address = _text(email, 320)
    if not address or "@" not in address:
        return None
    return SourceIdentity(
        provider="google",
        identity_type="email",
        identity_value=address.casefold(),
        display_name=_text(name, 256),
    )


class _HTMLText(HTMLParser):
    """Extract inert, bounded visible text without loading remote resources."""

    _ignored = {"head", "script", "style", "template", "noscript", "svg", "iframe", "object"}
    _blocks = {
        "address",
        "article",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.length = 0
        self.ignored_depth = 0

    def _append(self, text: str) -> None:
        text = text[: MAX_CONTENT_CHARS - self.length]
        self.parts.append(text)
        self.length += len(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._ignored:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in self._blocks:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored:
            self.ignored_depth = max(0, self.ignored_depth - 1)
        elif not self.ignored_depth and tag in self._blocks:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self._append(data)


def _decode_body(encoded: str) -> str:
    # Bound decoding before allocating the decoded body. The allowance covers
    # HTML markup and multibyte UTF-8; final canonical content remains 32k chars.
    encoded = encoded[: 4 * ((MAX_CONTENT_CHARS * 4 + 2) // 3)]
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
            "utf-8", errors="replace"
        )
    except ValueError:
        raise GoogleSourceError(
            "Invalid Gmail text encoding", code="google_invalid_response"
        ) from None


def _plain_text(part: dict[str, Any], *, depth: int = 0, allow_html: bool = True) -> str:
    headers = part.get("headers", [])
    attached = isinstance(headers, list) and any(
        isinstance(header, dict)
        and str(header.get("name", "")).casefold() == "content-disposition"
        and str(header.get("value", "")).casefold().split(";", 1)[0].strip() == "attachment"
        for header in headers
    )
    if depth > 12 or part.get("filename") or attached:
        return ""
    body = part.get("body", {})
    mime_type = str(part.get("mimeType", "")).casefold()
    if mime_type in {"text/plain", "text/html"} and isinstance(body, dict):
        encoded = body.get("data")
        if isinstance(encoded, str):
            if mime_type == "text/plain":
                return _decode_body(encoded)[:MAX_CONTENT_CHARS]
            if allow_html:
                parser = _HTMLText()
                parser.feed(_decode_body(encoded))
                parser.close()
                return "\n".join(
                    line
                    for part in "".join(parser.parts).splitlines()
                    if (line := " ".join(part.split()))
                )[:MAX_CONTENT_CHARS]
            return ""
    parts = part.get("parts", [])
    if not isinstance(parts, list):
        return ""
    if mime_type == "multipart/alternative":
        # Represent alternative bodies only once; prefer original plaintext even
        # when a rich HTML alternative appears earlier in the provider payload.
        for html_allowed in (False, True) if allow_html else (False,):
            for child in parts:
                if isinstance(child, dict):
                    text = _plain_text(child, depth=depth + 1, allow_html=html_allowed)
                    if text.strip():
                        return text
        return ""
    return "\n".join(
        _plain_text(child, depth=depth + 1, allow_html=allow_html)
        for child in parts
        if isinstance(child, dict)
    )[:MAX_CONTENT_CHARS]


def gmail_document(
    data: dict[str, Any], *, workspace_id: UUID, connection_id: UUID, now: datetime
) -> SourceDocument:
    external_id = _text(data.get("id"))
    if external_id is None:
        raise GoogleSourceError("Gmail message ID is missing", code="google_invalid_response")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise GoogleSourceError("Gmail payload is invalid", code="google_invalid_response")
    headers = {
        str(header.get("name", "")).casefold(): str(header.get("value", ""))
        for header in payload.get("headers", [])
        if isinstance(header, dict)
    }
    authors = getaddresses([headers.get("from", "")])
    recipient_pairs = getaddresses([headers.get("to", ""), headers.get("cc", "")])
    author = _identity(authors[0][1], authors[0][0]) if authors else None
    recipients = [
        identity
        for name, email in recipient_pairs
        if (identity := _identity(email, name)) is not None
    ][:256]
    timestamp = data.get("internalDate")
    try:
        occurred = datetime.fromtimestamp(int(timestamp) / 1000, UTC) if timestamp else now
    except (ValueError, TypeError, OverflowError):
        raise GoogleSourceError(
            "Gmail timestamp is invalid", code="google_invalid_response"
        ) from None
    labels = data.get("labelIds", [])
    labels = labels if isinstance(labels, list) else []
    content = _plain_text(payload)
    return SourceDocument(
        id=uuid5(NAMESPACE_URL, f"{connection_id}/gmail/{external_id}"),
        workspace_id=workspace_id,
        provider="google",
        source_type="gmail_message",
        external_id=external_id,
        external_parent_id=_text(data.get("threadId")),
        author=author,
        recipients=recipients,
        subject=_text(headers.get("subject"), 2000),
        content=content or None,
        occurred_at=occurred,
        retrieved_at=now,
        metadata={
            "label_ids": [label for label in labels if isinstance(label, str)],
            "status": "deleted" if data.get("deleted") else "active",
            "content_truncated": len(content) >= MAX_CONTENT_CHARS,
            # Preserve the existing resolver's bulk-mail signal, never its URL/token.
            **({"list_unsubscribe": True} if headers.get("list-unsubscribe", "").strip() else {}),
        },
    )


def calendar_document(
    data: dict[str, Any], *, workspace_id: UUID, connection_id: UUID, now: datetime
) -> SourceDocument:
    external_id = _text(data.get("id"))
    if external_id is None:
        raise GoogleSourceError("Calendar event ID is missing", code="google_invalid_response")
    organizer = data.get("organizer", {})
    organizer = organizer if isinstance(organizer, dict) else {}
    attendees = data.get("attendees", [])
    attendees = attendees if isinstance(attendees, list) else []
    recipients = [
        identity
        for attendee in attendees
        if isinstance(attendee, dict)
        if (identity := _identity(attendee.get("email"), attendee.get("displayName"))) is not None
    ][:256]
    start = data.get("start", {})
    end = data.get("end", {})
    start = start if isinstance(start, dict) else {}
    end = end if isinstance(end, dict) else {}
    return SourceDocument(
        id=uuid5(NAMESPACE_URL, f"{connection_id}/calendar/{external_id}"),
        workspace_id=workspace_id,
        provider="google",
        source_type="calendar_event",
        external_id=external_id,
        external_parent_id=_text(data.get("recurringEventId")),
        author=_identity(organizer.get("email"), organizer.get("displayName")),
        recipients=recipients,
        subject=_text(data.get("summary"), 2000),
        content=_text(data.get("description"), MAX_CONTENT_CHARS),
        occurred_at=_time(data.get("updated"), _time(data.get("created"), now)),
        retrieved_at=now,
        metadata={
            "start_at": _text(start.get("dateTime"), 64),
            "end_at": _text(end.get("dateTime"), 64),
            "scheduling_updated_at": _text(data.get("updated"), 64),
            "location": _text(data.get("location"), 256),
            "meeting_url": _text(data.get("hangoutLink"), 2048),
            "start_date": _text(start.get("date"), 10),
            "end_date": _text(end.get("date"), 10),
            "timezone": _text(start.get("timeZone"), 64),
            "status": _text(data.get("status"), 32) or "confirmed",
            "calendar_id": "primary",
            "ical_uid": _text(data.get("iCalUID"), 512),
            "html_link": _text(data.get("htmlLink"), 2048),
            "recurring_event_id": _text(data.get("recurringEventId"), 512),
            "recurring": bool(data.get("recurrence") or data.get("recurringEventId")),
        },
    )


class GoogleSourceGateway:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.transport = transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._jitter = jitter
        self._gmail_start: float | None = None
        self._gmail_pacing_lock = asyncio.Lock()

    async def _pace_gmail(self, url: str) -> None:
        # This limit belongs to one gateway, not a distributed account quota.
        if httpx.URL(url).host != "gmail.googleapis.com":
            return
        async with self._gmail_pacing_lock:
            if self._gmail_start is not None:
                delay = 0.25 - (self._monotonic() - self._gmail_start)
                if delay > 0:
                    await self._sleep(delay)
            self._gmail_start = self._monotonic()

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, str] | None = None,
        *,
        expired_status: int | tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        waited = 0.0
        for attempt in range(3):
            await self._pace_gmail(url)
            try:
                response = await client.get(url, params=params)
            except httpx.HTTPError:
                raise GoogleSourceError(
                    "Google source transport failed", code="google_transport_error"
                ) from None
            expired_codes = (
                expired_status if isinstance(expired_status, tuple) else (expired_status,)
            )
            if response.status_code in expired_codes:
                raise ExpiredSourceCursor("Google cursor expired")
            if response.is_success:
                break
            code = _google_error_code(response)
            retry_after = _retry_after(response, self._now())
            temporary = code in {"google_rate_limited", "google_provider_unavailable"}
            if temporary and attempt < 2:
                delay = max(2**attempt + self._jitter(), retry_after or 0)
                # Long hints belong to the durable scheduler; do not hold the
                # source transaction open while sleeping through a quota window.
                if waited + delay <= 60:
                    await self._sleep(delay)
                    waited += delay
                    continue
            if temporary and retry_after is None:
                retry_after = 60
            if code in {"google_daily_limit_exceeded", "google_quota_exceeded"}:
                retry_after = retry_after or 300
            error_type = (
                GoogleSourceAuthorizationError
                if response.status_code in {401, 403}
                else GoogleSourceError
            )
            raise error_type(
                "Google source request failed",
                code=code,
                http_status=response.status_code,
                retry_after_seconds=retry_after,
                provider_reason=_google_rate_limit_reason(response, code),
            )
        try:
            data = response.json()
        except ValueError:
            raise GoogleSourceError(
                "Google source response is invalid",
                code="google_invalid_response",
                http_status=response.status_code,
            ) from None
        if not isinstance(data, dict):
            raise GoogleSourceError(
                "Google source response is invalid",
                code="google_invalid_response",
                http_status=response.status_code,
            )
        return data

    async def _gmail_get(
        self,
        access_token: str,
        path: str,
        params: dict[str, str],
        *,
        expired_status: int | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=20.0,
            transport=self.transport,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        ) as client:
            return await self._get(
                client, f"{GMAIL_ROOT}/{path}", params, expired_status=expired_status
            )

    async def gmail_profile(self, access_token: str) -> str:
        """Read the history checkpoint before enumerating a full sync."""
        profile = await self._gmail_get(access_token, "profile", {"fields": "historyId"})
        cursor = profile.get("historyId")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
            raise GoogleSourceError(
                "Gmail did not return a history cursor", code="google_invalid_response"
            )
        return cursor

    async def gmail_page(
        self,
        access_token: str,
        *,
        cursor: str | None,
        page_token: str | None,
        query: str = "newer_than:30d",
    ) -> GmailPage:
        """Enumerate one page for a caller that durably checkpoints progress."""
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 512
            or (
                cursor is not None
                and (not isinstance(cursor, str) or not cursor or len(cursor) > 512)
            )
            or (
                page_token is not None
                and (not isinstance(page_token, str) or not page_token or len(page_token) > 4096)
            )
        ):
            raise GoogleSourceError("Gmail page parameters are invalid")
        params = {"maxResults": "100"}
        if cursor is None:
            path = "messages"
            params.update({"q": query, "fields": "messages/id,nextPageToken"})
        else:
            path = "history"
            params.update(
                {
                    "startHistoryId": cursor,
                    "fields": "history(messagesAdded/message/id,messagesDeleted/message/id),"
                    "nextPageToken,historyId",
                }
            )
        if page_token is not None:
            params["pageToken"] = page_token
        page = await self._gmail_get(
            access_token, path, params, expired_status=404 if cursor is not None else None
        )
        message_ids: dict[str, bool] = {}

        def add_message(message: Any, deleted: bool) -> None:
            identifier = message.get("id") if isinstance(message, dict) else None
            if not isinstance(identifier, str) or not identifier or len(identifier) > 512:
                raise GoogleSourceError(
                    "Gmail message ID is invalid", code="google_invalid_response"
                )
            message_ids[identifier] = deleted

        entries = page.get("messages" if cursor is None else "history", [])
        if not isinstance(entries, list):
            raise GoogleSourceError("Gmail page is invalid", code="google_invalid_response")
        for entry in entries:
            if cursor is None:
                add_message(entry, False)
                continue
            if not isinstance(entry, dict):
                raise GoogleSourceError("Gmail history is invalid", code="google_invalid_response")
            for kind, deleted in (("messagesAdded", False), ("messagesDeleted", True)):
                changes = entry.get(kind, [])
                if not isinstance(changes, list):
                    raise GoogleSourceError(
                        "Gmail history is invalid", code="google_invalid_response"
                    )
                for change in changes:
                    add_message(
                        change.get("message") if isinstance(change, dict) else None, deleted
                    )
        token = page.get("nextPageToken")
        next_cursor = page.get("historyId", cursor) if cursor is not None else None
        if (
            token is not None and (not isinstance(token, str) or not token or len(token) > 4096)
        ) or (
            cursor is not None
            and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 512)
        ):
            raise GoogleSourceError("Gmail page cursor is invalid", code="google_invalid_response")
        return GmailPage(message_ids, token, next_cursor)

    async def gmail_reply_metadata(
        self, access_token: str, *, external_id: str
    ) -> GmailReplyMetadata:
        """Resolve headers of exactly one source message, without reading its body."""
        if not external_id or len(external_id) > 512:
            raise GoogleSourceError("Gmail message ID is invalid")
        data = await self._gmail_get(
            access_token,
            f"messages/{quote(external_id, safe='')}",
            {"format": "metadata", "fields": "id,threadId,payload/headers"},
        )
        try:
            if data.get("id") != external_id:
                raise ValueError
            headers: dict[str, str] = {}
            for header in data["payload"]["headers"]:
                name, value = header["name"].lower(), header["value"]
                if name not in {"message-id", "references", "in-reply-to", "subject", "from"}:
                    continue
                if name in headers or not isinstance(value, str):
                    raise ValueError
                headers[name] = value
            message_id = headers["message-id"]
            references = headers.get("references", headers.get("in-reply-to", ""))
            authors = getaddresses([headers.get("from", "")])
            source_author = (
                authors[0][1].strip() if len(authors) == 1 and authors[0][1].strip() else None
            )
            return GmailReplyMetadata(
                source_message_id=external_id,
                thread_id=data["threadId"],
                source_subject=headers["subject"],
                in_reply_to=message_id,
                references=f"{references} {message_id}" if references else message_id,
                source_author=source_author,
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise GoogleSourceError(
                "Gmail reply metadata is invalid", code="google_invalid_response"
            ) from None

    async def gmail_metadata(
        self, access_token: str, *, external_id: str, now: datetime
    ) -> tuple[datetime, bool]:
        """Read only the timestamp needed to order a durable sync manifest."""
        if not isinstance(external_id, str) or not external_id or len(external_id) > 512:
            raise GoogleSourceError("Gmail message ID is invalid")
        try:
            data = await self._gmail_get(
                access_token,
                f"messages/{quote(external_id, safe='')}",
                # MINIMAL is documented to return only IDs and labels. Select
                # FULL with a strict field mask so chronology is available but
                # headers, snippets, and bodies never enter this response.
                {"format": "full", "fields": "id,internalDate"},
                expired_status=404,
            )
        except ExpiredSourceCursor:
            return now, True
        if data.get("id") != external_id:
            raise GoogleSourceError("Gmail message ID is invalid", code="google_invalid_response")
        timestamp = data.get("internalDate")
        try:
            if isinstance(timestamp, bool) or not isinstance(timestamp, str | int):
                raise ValueError
            occurred = datetime.fromtimestamp(int(timestamp) / 1000, UTC)
        except (ValueError, TypeError, OverflowError, OSError):
            raise GoogleSourceError(
                "Gmail timestamp is invalid", code="google_invalid_response"
            ) from None
        return occurred, False

    async def gmail_message(
        self,
        access_token: str,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        external_id: str,
        now: datetime,
        deleted: bool = False,
    ) -> SourceDocument:
        """Fetch one body for immediate processing, or normalize its deletion."""
        if not isinstance(external_id, str) or not external_id or len(external_id) > 512:
            raise GoogleSourceError("Gmail message ID is invalid")
        data: dict[str, Any] = {"id": external_id, "deleted": True}
        if not deleted:
            try:
                data = await self._gmail_get(
                    access_token,
                    f"messages/{quote(external_id, safe='')}",
                    {"format": "full", "fields": "id,threadId,labelIds,internalDate,payload"},
                    expired_status=404,
                )
            except ExpiredSourceCursor:
                pass
        if data.get("id") != external_id:
            raise GoogleSourceError("Gmail message ID is invalid", code="google_invalid_response")
        return gmail_document(data, workspace_id=workspace_id, connection_id=connection_id, now=now)

    async def fetch(
        self,
        *,
        source: str,
        access_token: str,
        workspace_id: UUID,
        connection_id: UUID,
        cursor: str | None,
        now: datetime | None = None,
    ) -> SourceBatch:
        now = now or datetime.now(UTC)
        if source not in SOURCE_SCOPES:
            raise GoogleSourceError("Unsupported source")
        async with httpx.AsyncClient(
            timeout=20.0,
            transport=self.transport,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        ) as client:
            read = self._gmail if source == "gmail" else self._calendar
            try:
                return await read(client, workspace_id, connection_id, cursor, now)
            except ExpiredSourceCursor:
                batch = await read(client, workspace_id, connection_id, None, now)
                return SourceBatch(batch.documents, batch.cursor, reset=True)

    async def reconcile_existing(
        self,
        *,
        source: str,
        access_token: str,
        workspace_id: UUID,
        connection_id: UUID,
        external_ids: list[str],
        now: datetime | None = None,
    ) -> list[SourceDocument]:
        """Revalidate known facts when expired cursors lose deletion history."""
        if len(external_ids) > MAX_PAGES * 100:
            raise GoogleSourceError("Historical reconciliation exceeded its resource budget")
        documents = []
        async with httpx.AsyncClient(
            timeout=20.0,
            transport=self.transport,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        ) as client:
            for identifier in external_ids:
                base = f"{GMAIL_ROOT}/messages" if source == "gmail" else CALENDAR_ROOT
                try:
                    data = await self._get(
                        client,
                        f"{base}/{quote(identifier, safe='')}",
                        {"format": "full"} if source == "gmail" else None,
                        expired_status=(404, 410) if source == "calendar" else 404,
                    )
                except ExpiredSourceCursor:
                    data = {"id": identifier, "deleted": True, "status": "cancelled"}
                normalize = gmail_document if source == "gmail" else calendar_document
                documents.append(
                    normalize(
                        data,
                        workspace_id=workspace_id,
                        connection_id=connection_id,
                        now=now or datetime.now(UTC),
                    )
                )
        return documents

    async def _gmail(
        self,
        client: httpx.AsyncClient,
        workspace_id: UUID,
        connection_id: UUID,
        cursor: str | None,
        now: datetime,
    ) -> SourceBatch:
        message_ids: dict[str, bool] = {}
        if cursor is None:
            profile = await self._get(client, f"{GMAIL_ROOT}/profile", {"fields": "historyId"})
            next_cursor = _text(profile.get("historyId"))
            params = {
                "maxResults": "100",
                "q": "newer_than:30d",
                "fields": "messages/id,nextPageToken",
            }
            url = f"{GMAIL_ROOT}/messages"
        else:
            next_cursor = cursor
            params = {
                "maxResults": "100",
                "startHistoryId": cursor,
                "fields": "history(messagesAdded/message/id,messagesDeleted/message/id),"
                "nextPageToken,historyId",
            }
            url = f"{GMAIL_ROOT}/history"
        for _ in range(MAX_PAGES):
            page = await self._get(client, url, params, expired_status=404 if cursor else None)
            if cursor:
                for history in page.get("history", []):
                    for kind, deleted in (("messagesAdded", False), ("messagesDeleted", True)):
                        for entry in history.get(kind, []):
                            identifier = _text(entry.get("message", {}).get("id"))
                            if identifier:
                                message_ids[identifier] = deleted
                next_cursor = _text(page.get("historyId")) or next_cursor
            else:
                for message in page.get("messages", []):
                    identifier = _text(message.get("id"))
                    if identifier:
                        message_ids[identifier] = False
            token = _text(page.get("nextPageToken"), 4096)
            if not token:
                break
            params["pageToken"] = token
        else:
            raise GoogleSourceError("Gmail reconciliation exceeded its bounded page budget")
        if not next_cursor:
            raise GoogleSourceError(
                "Gmail did not return a history cursor", code="google_invalid_response"
            )
        documents = []
        for identifier, deleted in message_ids.items():
            if deleted:
                data: dict[str, Any] = {"id": identifier, "deleted": True}
            else:
                try:
                    data = await self._get(
                        client,
                        f"{GMAIL_ROOT}/messages/{identifier}",
                        {"format": "full", "fields": "id,threadId,labelIds,internalDate,payload"},
                        expired_status=404,
                    )
                except ExpiredSourceCursor:
                    data = {"id": identifier, "deleted": True}
            documents.append(
                gmail_document(
                    data, workspace_id=workspace_id, connection_id=connection_id, now=now
                )
            )
        return SourceBatch(documents, next_cursor)

    async def _calendar(
        self,
        client: httpx.AsyncClient,
        workspace_id: UUID,
        connection_id: UUID,
        cursor: str | None,
        now: datetime,
    ) -> SourceBatch:
        params = {
            "maxResults": "100",
            "singleEvents": "true",
            "showDeleted": "true",
            "fields": "items(id,recurringEventId,summary,description,organizer,attendees,"
            "start,end,status,created,updated),nextPageToken,nextSyncToken",
        }
        if cursor:
            params["syncToken"] = cursor
        else:
            params["timeMin"] = (now - timedelta(days=30)).isoformat()
            params["timeMax"] = (now + timedelta(days=90)).isoformat()
        documents: dict[str, SourceDocument] = {}
        for _ in range(MAX_PAGES):
            page = await self._get(client, CALENDAR_ROOT, params, expired_status=410)
            for item in page.get("items", []):
                document = calendar_document(
                    item, workspace_id=workspace_id, connection_id=connection_id, now=now
                )
                documents[document.external_id] = document
            token = _text(page.get("nextPageToken"), 4096)
            if not token:
                next_cursor = _text(page.get("nextSyncToken"), 4096)
                if not next_cursor:
                    raise GoogleSourceError(
                        "Calendar did not return a sync token", code="google_invalid_response"
                    )
                return SourceBatch(list(documents.values()), next_cursor)
            params["pageToken"] = token
        raise GoogleSourceError("Calendar reconciliation exceeded its bounded page budget")
