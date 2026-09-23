"""Read-only Google adapters. Source bodies exist only during processing.

Gmail history IDs and Calendar sync tokens are opaque provider cursors; only
complete, validated batches produce a replacement cursor. Exhausting a bounded
scan raises instead of silently dropping unprocessed changes.
"""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from navox.intelligence.contracts import SourceDocument, SourceIdentity

GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
SOURCE_SCOPES = {"gmail": GMAIL_READ_SCOPE, "calendar": CALENDAR_READ_SCOPE}
GMAIL_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_ROOT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MAX_CONTENT_CHARS = 32_000
MAX_PAGES = 20


class GoogleSourceError(RuntimeError):
    """A source batch could not be read completely and safely."""


class GoogleSourceAuthorizationError(GoogleSourceError):
    """The provider no longer permits the requested read."""


class ExpiredSourceCursor(GoogleSourceError):
    """The provider requires bounded full reconciliation."""


@dataclass(frozen=True)
class SourceBatch:
    documents: list[SourceDocument]
    cursor: str
    reset: bool = False


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
    except ValueError as error:
        raise GoogleSourceError("Invalid Gmail text encoding") from error


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
        raise GoogleSourceError("Gmail message ID is missing")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise GoogleSourceError("Gmail payload is invalid")
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
    except (ValueError, TypeError, OverflowError) as error:
        raise GoogleSourceError("Gmail timestamp is invalid") from error
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
        },
    )


def calendar_document(
    data: dict[str, Any], *, workspace_id: UUID, connection_id: UUID, now: datetime
) -> SourceDocument:
    external_id = _text(data.get("id"))
    if external_id is None:
        raise GoogleSourceError("Calendar event ID is missing")
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
            "start_date": _text(start.get("date"), 10),
            "end_date": _text(end.get("date"), 10),
            "timezone": _text(start.get("timeZone"), 64),
            "status": _text(data.get("status"), 32) or "confirmed",
            "calendar_id": "primary",
        },
    )


class GoogleSourceGateway:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, str] | None = None,
        *,
        expired_status: int | tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.get(url, params=params)
            expired_codes = (
                expired_status if isinstance(expired_status, tuple) else (expired_status,)
            )
            if response.status_code in expired_codes:
                raise ExpiredSourceCursor("Google cursor expired")
            if response.status_code in {401, 403}:
                raise GoogleSourceAuthorizationError("Google read permission unavailable")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise GoogleSourceError("Google source read failed") from error
        if not isinstance(data, dict):
            raise GoogleSourceError("Google source response is invalid")
        return data

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
                        now=datetime.now(UTC),
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
            raise GoogleSourceError("Gmail did not return a history cursor")
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
                    raise GoogleSourceError("Calendar did not return a sync token")
                return SourceBatch(list(documents.values()), next_cursor)
            params["pageToken"] = token
        raise GoogleSourceError("Calendar reconciliation exceeded its bounded page budget")
