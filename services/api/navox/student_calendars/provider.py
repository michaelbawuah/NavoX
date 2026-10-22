"""Bounded single-attempt calendar operations; native consent stays with the caller.

Event insertion is never retried here. A transport failure or ambiguous write
response must be reconciled by the caller using its preallocated event ID.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeGuard, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

ROOT = "https://www.googleapis.com/calendar/v3"
LIST_URL = ROOT + "/users/me/calendarList"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CALENDARS = 100
MAX_PAGES = 4
REQUEST_SECONDS = 8
Check = Callable[[], Awaitable[None]]
# Only explicit authorization/validation failures prove a submitted write was
# rejected. Redirects, conflicts and unfamiliar responses require exact-ID reads.
DEFINITE_WRITE_REJECTIONS = frozenset(
    {400, 401, 403, 404, 405, 406, 410, 411, 412, 413, 414, 415, 416, 417, 422, 431}
)


class GoogleCalendarWriteError(RuntimeError):
    def __init__(
        self, message: str = "Calendar could not be checked", *, status_code: int | None = None
    ):
        super().__init__(message)
        self.status_code = status_code


class CalendarWriteRejected(GoogleCalendarWriteError):
    """The provider rejected this operation; its response body is never exposed."""


class CalendarWriteUncertain(GoogleCalendarWriteError):
    """A write may have succeeded. Read its exact ID before any further action."""


class CalendarWriteChanged(GoogleCalendarWriteError):
    """A conditional deletion refused to overwrite a changed event."""


@dataclass(frozen=True)
class CalendarTarget:
    id: str
    label: str
    timezone: str


def _text(value: object, limit: int) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    )


def _identifier(value: object) -> str:
    if not _text(value, 1024) or value in {".", ".."}:
        raise CalendarWriteRejected("The calendar identifier is invalid")
    return value


def _event_id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-v]{5,1024}", value) is None:
        raise CalendarWriteRejected("The event identifier is invalid")
    return value


def _target(value: dict[str, Any]) -> CalendarTarget:
    identifier, label, timezone = value.get("id"), value.get("summary"), value.get("timeZone")
    if value.get("accessRole") != "owner" or value.get("deleted", False) is not False:
        raise CalendarWriteRejected("Choose an available calendar you manage")
    if not _text(identifier, 1024) or not _text(label, 1024) or not _text(timezone, 64):
        raise GoogleCalendarWriteError()
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise GoogleCalendarWriteError() from None
    return CalendarTarget(identifier, label, timezone)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


class GoogleCalendarWriteProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def _request(
        self,
        *,
        access_token: str,
        method: str,
        url: str,
        check: Check,
        missing: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        writing = method in {"POST", "DELETE"}
        error_type = CalendarWriteUncertain if writing else GoogleCalendarWriteError
        if not _text(access_token, 16384) or not access_token.isascii():
            raise CalendarWriteRejected("Calendar authorization is unavailable")
        await check()
        status: int | None = None
        content = bytearray()
        failed = False
        try:
            async with (
                asyncio.timeout(REQUEST_SECONDS),
                httpx.AsyncClient(
                    timeout=REQUEST_SECONDS,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                    headers={"Authorization": "Bearer " + access_token},
                ) as client,
                client.stream(method, url, **kwargs) as response,
            ):
                status = response.status_code
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                        failed = True
                        break
                    content.extend(chunk)
        except (httpx.HTTPError, TimeoutError):
            failed = True
        # Native authority errors must propagate even when the request failed.
        await check()
        if failed or status is None:
            raise error_type("Calendar response could not be verified") from None
        if status in {404, 410} and missing:
            return None
        if status == 412 and method == "DELETE":
            raise CalendarWriteChanged("The calendar event changed", status_code=status)
        if writing:
            expected = 204 if method == "DELETE" else 200
            if status in DEFINITE_WRITE_REJECTIONS:
                raise CalendarWriteRejected("Calendar operation was rejected", status_code=status)
            if status != expected:
                raise CalendarWriteUncertain(
                    "Calendar response could not be verified", status_code=status
                )
        elif status != 200:
            raise GoogleCalendarWriteError(
                "Calendar response could not be verified", status_code=status
            )
        if method == "DELETE":
            if status != 204 or content:
                raise CalendarWriteUncertain("Calendar deletion could not be verified")
            return None
        try:
            value = json.loads(content, object_pairs_hook=_unique)
        except (ValueError, UnicodeError, RecursionError):
            raise error_type("Calendar response could not be verified") from None
        if not isinstance(value, dict):
            raise error_type("Calendar response could not be verified")
        return cast(dict[str, Any], value)

    async def owned_calendars(
        self, *, access_token: str, check: Check
    ) -> tuple[CalendarTarget, ...]:
        result: list[CalendarTarget] = []
        identifiers: set[str] = set()
        tokens: set[str] = set()
        page: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, str | int] = {
                "minAccessRole": "owner",
                "showHidden": "true",
                "showDeleted": "false",
                "maxResults": MAX_CALENDARS,
                "fields": "items(id,summary,timeZone,accessRole,deleted),nextPageToken",
            }
            if page is not None:
                params["pageToken"] = page
            data = await self._request(
                access_token=access_token, method="GET", url=LIST_URL, check=check, params=params
            )
            if data is None or not isinstance(data.get("items"), list):
                raise GoogleCalendarWriteError()
            if len(data["items"]) > MAX_CALENDARS:
                raise GoogleCalendarWriteError()
            for item in data["items"]:
                if not isinstance(item, dict):
                    raise GoogleCalendarWriteError()
                target = _target(item)
                if target.id in identifiers or len(result) >= MAX_CALENDARS:
                    raise GoogleCalendarWriteError(
                        "Narrow the calendar inventory before scheduling"
                    )
                identifiers.add(target.id)
                result.append(target)
            page = data.get("nextPageToken")
            if page is None:
                return tuple(result)
            if not _text(page, 4096) or page in tokens:
                raise GoogleCalendarWriteError()
            tokens.add(page)
        raise GoogleCalendarWriteError("The calendar inventory could not be completed")

    async def target(self, *, access_token: str, calendar_id: str, check: Check) -> CalendarTarget:
        data = await self._request(
            access_token=access_token,
            method="GET",
            url=LIST_URL + "/" + quote(_identifier(calendar_id), safe=""),
            check=check,
            params={"fields": "id,summary,timeZone,accessRole,deleted"},
        )
        if data is None:
            raise GoogleCalendarWriteError()
        target = _target(data)
        if target.id != calendar_id:
            raise CalendarWriteRejected("The selected calendar changed")
        return target

    def _event_url(self, calendar_id: str, event_id: str | None = None) -> str:
        url = ROOT + "/calendars/" + quote(_identifier(calendar_id), safe="") + "/events"
        return url if event_id is None else url + "/" + _event_id(event_id)

    async def event(
        self, *, access_token: str, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None:
        data = await self._request(
            access_token=access_token,
            method="GET",
            url=self._event_url(calendar_id, event_id),
            check=check,
            missing=True,
        )
        if data is None:
            return None
        if data.get("id") != event_id:
            raise GoogleCalendarWriteError()
        if data.get("status") == "cancelled":
            return None
        if not _text(data.get("etag"), 1024):
            raise GoogleCalendarWriteError()
        return data

    async def insert(
        self, *, access_token: str, calendar_id: str, event: dict[str, Any], check: Check
    ) -> dict[str, Any]:
        _event_id(event.get("id"))
        if any(
            key in event for key in ("attendees", "recurrence", "conferenceData", "attachments")
        ):
            raise CalendarWriteRejected("Only a single personal calendar event is supported")
        data = await self._request(
            access_token=access_token,
            method="POST",
            url=self._event_url(calendar_id),
            check=check,
            params={"sendUpdates": "none"},
            json=event,
        )
        if data is None or data.get("id") != event["id"] or not _text(data.get("etag"), 1024):
            raise CalendarWriteUncertain("Calendar creation could not be verified")
        return data

    async def delete(
        self, *, access_token: str, calendar_id: str, event_id: str, etag: str, check: Check
    ) -> None:
        if not _text(etag, 1024) or re.fullmatch(r'"[\x21\x23-\x7e]+"', etag) is None:
            raise CalendarWriteRejected("A specific calendar event version is required")
        await self._request(
            access_token=access_token,
            method="DELETE",
            url=self._event_url(calendar_id, event_id),
            check=check,
            params={"sendUpdates": "none"},
            headers={"If-Match": etag},
            missing=True,
        )
