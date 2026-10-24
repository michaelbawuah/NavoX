"""Bounded Microsoft Graph calendar busy reads (Outlook / Microsoft 365).

Mirrors `google_availability`: the caller supplies a server-vault access token
and rechecks the separate availability purpose before and after each request.
Every calendar the account can see is read; an incomplete inventory, a paging
problem or one calendar error makes the account's coverage partial, never free.

Only busy instants leave this module. Event titles, bodies, attendees, places
and calendar IDs are not requested (`$select=start,end,showAs,isCancelled`) and
are not retained.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx

from navox.providers.google_availability import ProviderBusy

GRAPH = "https://graph.microsoft.com/v1.0"
CALENDARS_URL = GRAPH + "/me/calendars"
MAX_PAGES = 4
MAX_EVENT_PAGES = 20
MAX_CALENDARS = 200
MAX_BUSY_INTERVALS = 8000
MAX_RESPONSE_BYTES = 2_000_000
PER_REQUEST_SECONDS = 5
TOTAL_SECONDS = 30
# Outlook "free" time does not block; everything else might.
BLOCKING = {"busy", "tentative", "oof", "workingElsewhere", "unknown"}


@dataclass(frozen=True)
class MicrosoftAvailabilitySnapshot:
    state: Literal["complete", "partial", "unavailable"]
    calendar_count: int
    busy: tuple[ProviderBusy, ...]
    observed_at: datetime


class _Unavailable(Exception):
    pass


def _graph_time(value: object) -> datetime:
    """Parse Graph's `{"dateTime": ..., "timeZone": "UTC"}` (requested in UTC)."""
    if not isinstance(value, dict) or value.get("timeZone") not in ("UTC", "Etc/UTC"):
        raise ValueError
    raw = value.get("dateTime")
    if not isinstance(raw, str) or len(raw) > 40:
        raise ValueError
    text = raw.rstrip("Z")
    if "." in text:
        whole, fraction = text.split(".", 1)
        if not fraction.isdigit():
            raise ValueError
        text = whole + "." + fraction[:6]
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        raise ValueError
    return parsed.replace(tzinfo=UTC)


def _opaque(value: object, *, limit: int = 4096) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    )


def _graph_link(value: object) -> bool:
    if not _opaque(value, limit=8192):
        return False
    parts = urlsplit(str(value))
    return parts.scheme == "https" and parts.netloc == "graph.microsoft.com"


class MicrosoftAvailabilityProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        check: Callable[[], Awaitable[None]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        await check()
        failed = False
        try:
            async with (
                asyncio.timeout(PER_REQUEST_SECONDS),
                client.stream("GET", url, **kwargs) as response,
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
        if failed or status != 200:
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
    ) -> MicrosoftAvailabilitySnapshot:
        if start_at.tzinfo is None or end_at.tzinfo is None or not start_at < end_at:
            raise ValueError("Choose an aware increasing availability window")
        start_at, end_at = start_at.astimezone(UTC), end_at.astimezone(UTC)
        observed = datetime.now(UTC)
        calendars: list[str] = []
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
                    headers={
                        "Authorization": "Bearer " + access_token,
                        "Prefer": 'outlook.timezone="UTC"',
                    },
                ) as client,
            ):
                url: str | None = CALENDARS_URL
                params: dict[str, str] | None = {"$select": "id", "$top": "100"}
                seen_links: set[str] = set()
                for _ in range(MAX_PAGES):
                    assert url is not None
                    data = await self._request(client, url, check, params=params)
                    items = data.get("value")
                    if not isinstance(items, list) or len(items) > 1000:
                        complete = False
                        break
                    for item in items:
                        identifier = item.get("id") if isinstance(item, dict) else None
                        if not _opaque(identifier, limit=1024) or identifier in calendars:
                            complete = False
                            continue
                        if len(calendars) >= MAX_CALENDARS:
                            complete = False
                            break
                        calendars.append(str(identifier))
                    following = data.get("@odata.nextLink")
                    if following is None:
                        break
                    if not _graph_link(following) or following in seen_links:
                        complete = False
                        break
                    seen_links.add(str(following))
                    url, params = str(following), None
                else:
                    complete = False

                window = {
                    "startDateTime": start_at.isoformat().replace("+00:00", "Z"),
                    "endDateTime": end_at.isoformat().replace("+00:00", "Z"),
                    "$select": "start,end,showAs,isCancelled",
                    "$top": "250",
                }
                for identifier in calendars:
                    view: str | None = (
                        CALENDARS_URL + "/" + quote(identifier, safe="") + "/calendarView"
                    )
                    query: dict[str, str] | None = window
                    links: set[str] = set()
                    for _ in range(MAX_EVENT_PAGES):
                        assert view is not None
                        try:
                            data = await self._request(client, view, check, params=query)
                        except _Unavailable:
                            complete = False
                            break
                        events = data.get("value")
                        if not isinstance(events, list):
                            complete = False
                            break
                        for event in events:
                            if not isinstance(event, dict):
                                complete = False
                                continue
                            if event.get("isCancelled") is True:
                                continue
                            show = event.get("showAs")
                            if show == "free":
                                continue
                            if show not in BLOCKING:
                                complete = False
                            try:
                                start = _graph_time(event.get("start"))
                                end = _graph_time(event.get("end"))
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
                        following = data.get("@odata.nextLink")
                        if following is None:
                            break
                        if not _graph_link(following) or following in links:
                            complete = False
                            break
                        links.add(str(following))
                        view, query = str(following), None
                    else:
                        complete = False
        except _Unavailable:
            return MicrosoftAvailabilitySnapshot(
                "partial" if calendars or busy else "unavailable",
                len(calendars),
                tuple(busy),
                observed,
            )
        except TimeoutError:
            if not deadline.expired():
                raise
            return MicrosoftAvailabilitySnapshot(
                "partial" if calendars or busy else "unavailable",
                len(calendars),
                tuple(busy),
                observed,
            )
        return MicrosoftAvailabilitySnapshot(
            "complete" if complete else "partial", len(calendars), tuple(busy), observed
        )
