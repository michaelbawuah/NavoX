"""Bounded iCloud CalDAV busy reads; credentials are supplied from the server vault.

No credential collection, storage, consent, writes or live-provider qualification
is implemented here. A failed/unsupported free-busy REPORT is unknown coverage.
Server-side free-busy expansion handles recurrence, exceptions and time zones;
this reader never guesses busy time from an incomplete local event expansion.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from icalendar import Calendar

from navox.providers.google_availability import ProviderBusy

DISCOVERY_URL = "https://caldav.icloud.com/"
DAV = "{DAV:}"
CALDAV = "{urn:ietf:params:xml:ns:caldav}"
MAX_CALENDARS = 100
MAX_BUSY_INTERVALS = 8000
MAX_RESPONSE_BYTES = 2_000_000
MAX_XML_NODES = 5000
MAX_REDIRECTS = 2
PER_REQUEST_SECONDS = 5
TOTAL_SECONDS = 30
MAX_WINDOW_DAYS = 31

_PRINCIPAL_QUERY = (
    '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
)
_HOME_QUERY = (
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><c:calendar-home-set/></d:prop></d:propfind>"
)
_CALENDARS_QUERY = (
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:resourcetype/><c:supported-calendar-component-set/>"
    "</d:prop></d:propfind>"
)


@dataclass(frozen=True)
class AppleAvailabilitySnapshot:
    state: Literal["complete", "partial", "unavailable"]
    calendar_count: int
    busy: tuple[ProviderBusy, ...]
    observed_at: datetime


class _Unavailable(Exception):
    pass


def _url(base: str, value: str) -> str:
    """Never send an iCloud credential to a user-selected or provider-linked host."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise _Unavailable
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise _Unavailable
    try:
        parts = urlsplit(urljoin(base, value))
        if (
            parts.scheme != "https"
            or not re.fullmatch(r"(?:caldav|p[0-9]{2,3}-caldav)\.icloud\.com", parts.hostname or "")
            or parts.port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise _Unavailable
    except ValueError:
        raise _Unavailable from None
    return urlunsplit(("https", parts.hostname or "", parts.path or "/", "", ""))


def _xml(content: bytes) -> ET.Element:
    try:
        text = content.decode("utf-8", errors="strict")
        # Reject DTDs before parsing, including internal entities. UTF-16 and other
        # encodings are deliberately unsupported rather than bypassing this gate.
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, flags=re.IGNORECASE) or "\x00" in text:
            raise _Unavailable
        root = ET.fromstring(text)
    except (ValueError, UnicodeError, ET.ParseError, RecursionError):
        raise _Unavailable from None
    if root.tag != DAV + "multistatus" or sum(1 for _ in root.iter()) > MAX_XML_NODES:
        raise _Unavailable
    return root


def _properties(response: ET.Element) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    if response.find(DAV + "status") is not None:
        raise _Unavailable
    for propstat in response.findall(DAV + "propstat"):
        status = propstat.findtext(DAV + "status", "")
        if not re.fullmatch(r"HTTP/1\.[01] 200(?: .*)?", status):
            # A failed property may hide a calendar or unsupported event type.
            raise _Unavailable
        prop = propstat.find(DAV + "prop")
        if prop is None:
            raise _Unavailable
        for item in prop:
            if item.tag in result:
                raise _Unavailable
            result[item.tag] = item
    if not result:
        raise _Unavailable
    return result


def _single_href(content: bytes, property_name: str, base: str) -> str:
    responses = _xml(content).findall(DAV + "response")
    if len(responses) != 1:
        raise _Unavailable
    prop = _properties(responses[0]).get(property_name)
    if prop is None:
        raise _Unavailable
    hrefs = prop.findall(DAV + "href")
    if len(hrefs) != 1 or not hrefs[0].text:
        raise _Unavailable
    return _url(base, hrefs[0].text)


def _calendars(content: bytes, base: str) -> list[str]:
    calendars: list[str] = []
    seen: set[str] = set()
    responses = _xml(content).findall(DAV + "response")
    if not responses or len(responses) > MAX_CALENDARS + 1:
        raise _Unavailable
    for response in responses:
        hrefs = response.findall(DAV + "href")
        if len(hrefs) != 1 or not hrefs[0].text:
            raise _Unavailable
        href = _url(base, hrefs[0].text)
        if href in seen:
            raise _Unavailable
        seen.add(href)
        props = _properties(response)
        kind = props.get(DAV + "resourcetype")
        if kind is None:
            raise _Unavailable
        if kind.find(CALDAV + "calendar") is None:
            # Depth=1 may contain the queried home itself; anything else that is
            # not identified as a calendar prevents proof of complete coverage.
            if href.rstrip("/") != base.rstrip("/"):
                raise _Unavailable
            continue
        components = props.get(CALDAV + "supported-calendar-component-set")
        if components is not None:
            names = [item.attrib.get("name") for item in components.findall(CALDAV + "comp")]
            if not names or any(
                not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9-]*", name)
                for name in names
            ):
                raise _Unavailable
            if "VEVENT" not in names:
                continue
        if len(calendars) >= MAX_CALENDARS:
            raise _Unavailable
        calendars.append(href)
    return calendars


def _aware(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _Unavailable
    return value.astimezone(UTC)


def _freebusy(content: bytes, start_at: datetime, end_at: datetime) -> list[ProviderBusy]:
    try:
        calendar = Calendar.from_ical(content)
        if calendar.name != "VCALENDAR" or calendar.errors:
            raise _Unavailable
        if len(calendar.subcomponents) != 1 or calendar.subcomponents[0].name != "VFREEBUSY":
            raise _Unavailable
        component = calendar.subcomponents[0]
        if component.errors:
            raise _Unavailable
        if (
            _aware(component.decoded("DTSTART")) != start_at
            or _aware(component.decoded("DTEND")) != end_at
        ):
            raise _Unavailable
        values = component.get("FREEBUSY", [])
        if not isinstance(values, list):
            values = [values]
        busy: list[ProviderBusy] = []
        for value in values:
            first, last = value.dt
            first = _aware(first)
            if isinstance(last, timedelta):
                last = first + last
            last = _aware(last)
            if not first < last:
                raise _Unavailable
            if str(value.params.get("FBTYPE", "BUSY")).upper() == "FREE":
                continue
            first, last = max(first, start_at), min(last, end_at)
            if first >= last:
                continue
            if len(busy) >= MAX_BUSY_INTERVALS:
                raise _Unavailable
            busy.append(ProviderBusy(first, last))
        return busy
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        raise _Unavailable from None


class AppleAvailabilityProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        check: Callable[[], Awaitable[None]],
        *,
        body: str,
        depth: str,
        expected_status: int,
    ) -> tuple[bytes, str]:
        current = _url(DISCOVERY_URL, url)
        for redirect in range(MAX_REDIRECTS + 1):
            await check()
            failed = False
            status = 0
            location: str | None = None
            content = bytearray()
            try:
                async with (
                    asyncio.timeout(PER_REQUEST_SECONDS),
                    client.stream(
                        method,
                        current,
                        content=body.encode("utf-8"),
                        headers={"Content-Type": "application/xml; charset=utf-8", "Depth": depth},
                    ) as response,
                ):
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise _Unavailable
                        content.extend(chunk)
                    status = response.status_code
                    location = response.headers.get("location")
            except (httpx.HTTPError, TimeoutError, _Unavailable):
                failed = True
            await check()
            if failed:
                raise _Unavailable
            if status in (301, 302, 307, 308) and location and redirect < MAX_REDIRECTS:
                current = _url(current, location)
                continue
            if status != expected_status:
                raise _Unavailable
            return bytes(content), current
        raise _Unavailable

    async def collect(
        self,
        *,
        apple_id: str,
        app_password: str,
        start_at: datetime,
        end_at: datetime,
        check: Callable[[], Awaitable[None]],
    ) -> AppleAvailabilitySnapshot:
        if (
            start_at.tzinfo is None
            or end_at.tzinfo is None
            or not start_at < end_at
            or end_at - start_at > timedelta(days=MAX_WINDOW_DAYS)
            or start_at.microsecond
            or end_at.microsecond
        ):
            raise ValueError("Choose an aware increasing availability window of at most 31 days")
        if (
            not isinstance(apple_id, str)
            or not 1 <= len(apple_id) <= 320
            or ":" in apple_id
            or any(ord(char) < 33 for char in apple_id)
            or not re.fullmatch(r"[a-z]{4}(?:-[a-z]{4}){3}", app_password)
        ):
            raise ValueError("An Apple Account and app-specific password are required")
        start_at, end_at = start_at.astimezone(UTC), end_at.astimezone(UTC)
        observed = datetime.now(UTC)
        calendars: list[str] = []
        busy: list[ProviderBusy] = []
        deadline = asyncio.timeout(TOTAL_SECONDS)
        try:
            async with (
                deadline,
                httpx.AsyncClient(
                    timeout=PER_REQUEST_SECONDS,
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                    auth=httpx.BasicAuth(apple_id, app_password),
                ) as client,
            ):
                content, origin = await self._request(
                    client,
                    "PROPFIND",
                    DISCOVERY_URL,
                    check,
                    body=_PRINCIPAL_QUERY,
                    depth="0",
                    expected_status=207,
                )
                principal = _single_href(content, DAV + "current-user-principal", origin)
                content, origin = await self._request(
                    client,
                    "PROPFIND",
                    principal,
                    check,
                    body=_HOME_QUERY,
                    depth="0",
                    expected_status=207,
                )
                home = _single_href(content, CALDAV + "calendar-home-set", origin)
                content, origin = await self._request(
                    client,
                    "PROPFIND",
                    home,
                    check,
                    body=_CALENDARS_QUERY,
                    depth="1",
                    expected_status=207,
                )
                calendars = _calendars(content, origin)
                body = (
                    '<c:free-busy-query xmlns:c="urn:ietf:params:xml:ns:caldav">'
                    f'<c:time-range start="{start_at:%Y%m%dT%H%M%SZ}" '
                    f'end="{end_at:%Y%m%dT%H%M%SZ}"/></c:free-busy-query>'
                )
                for calendar_url in calendars:
                    content, _ = await self._request(
                        client,
                        "REPORT",
                        calendar_url,
                        check,
                        body=body,
                        depth="1",
                        expected_status=200,
                    )
                    intervals = _freebusy(content, start_at, end_at)
                    if len(busy) + len(intervals) > MAX_BUSY_INTERVALS:
                        raise _Unavailable
                    busy.extend(intervals)
        except _Unavailable:
            return AppleAvailabilitySnapshot(
                "partial" if calendars or busy else "unavailable",
                len(calendars),
                tuple(busy),
                observed,
            )
        except TimeoutError:
            if not deadline.expired():
                raise
            return AppleAvailabilitySnapshot(
                "partial" if calendars or busy else "unavailable",
                len(calendars),
                tuple(busy),
                observed,
            )
        return AppleAvailabilitySnapshot("complete", len(calendars), tuple(busy), observed)
