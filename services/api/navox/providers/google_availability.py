"""Bounded Google calendar-list/free-busy reads, with no retained calendar IDs.

The caller supplies a server-vault access token and freshly rechecks the separate
availability purpose before and after each request. Incomplete inventory or one
calendar error makes the whole account's coverage incomplete, never free.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
MAX_PAGES = 4
MAX_CALENDARS = 1000
MAX_BUSY_INTERVALS = 8000
MAX_RESPONSE_BYTES = 2_000_000
PER_REQUEST_SECONDS = 5
TOTAL_SECONDS = 30


@dataclass(frozen=True)
class ProviderBusy:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class GoogleAvailabilitySnapshot:
    state: Literal["complete", "partial", "unavailable"]
    calendar_count: int
    busy: tuple[ProviderBusy, ...]
    observed_at: datetime


class _Unavailable(Exception):
    pass


def _instant(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _opaque(value: object, *, limit: int = 4096) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    )


class GoogleAvailabilityProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        check: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        await check()
        failed = False
        try:
            async with (
                asyncio.timeout(PER_REQUEST_SECONDS),
                client.stream(method, url, **kwargs) as response,
            ):
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise _Unavailable
                    content.extend(chunk)
                status = response.status_code
        except (httpx.HTTPError, TimeoutError, _Unavailable):
            failed = True
        await check()
        if failed:
            raise _Unavailable
        if status != 200:
            raise _Unavailable
        try:
            data = json.loads(content)
        except (ValueError, UnicodeError, RecursionError):
            raise _Unavailable from None
        if not isinstance(data, dict):
            raise _Unavailable
        return data

    async def collect(
        self,
        *,
        access_token: str,
        start_at: datetime,
        end_at: datetime,
        check: Callable[[], Awaitable[None]],
    ) -> GoogleAvailabilitySnapshot:
        if start_at.tzinfo is None or end_at.tzinfo is None or not start_at < end_at:
            raise ValueError("Choose an aware increasing availability window")
        observed = datetime.now(UTC)
        calendars: list[str] = []
        seen: set[str] = set()
        busy: list[ProviderBusy] = []
        complete = True
        deadline = asyncio.timeout(TOTAL_SECONDS)
        try:
            async with (
                deadline,
                httpx.AsyncClient(
                    timeout=PER_REQUEST_SECONDS,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                    headers={"Authorization": "Bearer " + access_token},
                ) as client,
            ):
                page: str | None = None
                tokens: set[str] = set()
                for _ in range(MAX_PAGES):
                    params: dict[str, str | int] = {
                        "showHidden": "true",
                        "showDeleted": "false",
                        "maxResults": 250,
                        "fields": "items(id,deleted),nextPageToken,nextSyncToken",
                    }
                    if page is not None:
                        params["pageToken"] = page
                    data = await self._request(client, "GET", LIST_URL, check, params=params)
                    items = data.get("items", [])
                    if not isinstance(items, list) or len(items) > 250:
                        complete = False
                        break
                    for item in items:
                        if (
                            not isinstance(item, dict)
                            or not _opaque(item.get("id"), limit=1024)
                            or item.get("deleted", False) is not False
                        ):
                            complete = False
                            continue
                        identifier = item["id"]
                        if identifier in seen:
                            complete = False
                            continue
                        seen.add(identifier)
                        if len(calendars) >= MAX_CALENDARS:
                            complete = False
                            break
                        calendars.append(identifier)
                    following = data.get("nextPageToken")
                    if following is None:
                        if not _opaque(data.get("nextSyncToken")):
                            complete = False
                        break
                    if not _opaque(following) or following in tokens:
                        complete = False
                        break
                    tokens.add(following)
                    page = following
                else:
                    complete = False
                for first in range(0, len(calendars), 50):
                    chunk = calendars[first : first + 50]
                    data = await self._request(
                        client,
                        "POST",
                        FREEBUSY_URL,
                        check,
                        json={
                            "timeMin": start_at.isoformat(),
                            "timeMax": end_at.isoformat(),
                            "timeZone": "UTC",
                            "calendarExpansionMax": 50,
                            "groupExpansionMax": 1,
                            "items": [{"id": value} for value in chunk],
                        },
                    )
                    try:
                        if (
                            _instant(data.get("timeMin")) != start_at
                            or _instant(data.get("timeMax")) != end_at
                        ):
                            raise ValueError
                    except (ValueError, OverflowError):
                        complete = False
                        continue
                    entries = data.get("calendars")
                    if not isinstance(entries, dict):
                        complete = False
                        continue
                    if set(entries) != set(chunk) or data.get("groups", {}):
                        complete = False
                    for identifier in chunk:
                        entry = entries.get(identifier)
                        if (
                            not isinstance(entry, dict)
                            or entry.get("errors")
                            or not isinstance(entry.get("busy"), list)
                        ):
                            complete = False
                            continue
                        for interval in entry["busy"]:
                            try:
                                if not isinstance(interval, dict):
                                    raise ValueError
                                start = _instant(interval.get("start"))
                                end = _instant(interval.get("end"))
                                if not start < end:
                                    raise ValueError
                            except (ValueError, OverflowError):
                                complete = False
                                continue
                            start, end = max(start, start_at), min(end, end_at)
                            if start >= end:
                                continue
                            if len(busy) >= MAX_BUSY_INTERVALS:
                                complete = False
                                break
                            busy.append(ProviderBusy(start, end))
        except _Unavailable:
            return GoogleAvailabilitySnapshot(
                "partial" if calendars or busy else "unavailable",
                len(calendars),
                tuple(busy),
                observed,
            )
        except TimeoutError:
            if not deadline.expired():
                # A caller's authority-check timeout must not be mistaken for a
                # successful authorization followed by a provider read failure.
                raise
            return GoogleAvailabilitySnapshot(
                "partial" if calendars or busy else "unavailable",
                len(calendars),
                tuple(busy),
                observed,
            )
        return GoogleAvailabilitySnapshot(
            "complete" if complete else "partial", len(calendars), tuple(busy), observed
        )
