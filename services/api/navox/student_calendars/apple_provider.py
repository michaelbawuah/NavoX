"""Single-attempt personal CalDAV writes; native consent belongs to the caller.

RFC 4791 sections 5.3.2/5.3.4 and RFC 9110 section 13 require create-only
PUT and strong-version conditions. This source implements those conditions;
synthetic protocol tests do not qualify an iCloud account or enable live writes.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar, Event

from navox.providers.apple_availability import (
    CALDAV,
    DAV,
    DISCOVERY_URL,
    _properties,
    _Unavailable,
    _url,
    _xml,
)
from navox.student_calendars.provider import (
    CalendarTarget,
    CalendarWriteChanged,
    CalendarWriteRejected,
    CalendarWriteUncertain,
    Check,
    GoogleCalendarWriteError,
)

MAX_RESPONSE_BYTES = 256 * 1024
MAX_CALENDARS = 100
MAX_REDIRECTS = 2
REQUEST_SECONDS = 8
TOTAL_SECONDS = 30
NOTE = "Added by NavoX"
PRODID = "-//NavoX//Reviewed personal events//EN"
_PRINCIPAL_QUERY = (
    '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
)
_HOME_QUERY = (
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><c:calendar-home-set/></d:prop></d:propfind>"
)
_TARGETS_QUERY = (
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:resourcetype/><d:displayname/><d:owner/>"
    "<d:current-user-privilege-set/><c:supported-calendar-component-set/>"
    "</d:prop></d:propfind>"
)


def _text(value: object, limit: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not value and not empty)
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    ):
        raise GoogleCalendarWriteError("The Apple calendar response could not be verified")
    return value


def _trusted(base: str, value: str) -> str:
    try:
        result = _url(base, value)
        path = unquote(urlsplit(result).path, errors="strict")
        if (
            "\\" in path
            or any(part in {".", ".."} for part in path.split("/"))
            or any(ord(char) <= 32 or ord(char) == 127 for char in path)
            or re.search(r"%(?:2f|5c|2e)", urlsplit(result).path, re.IGNORECASE)
        ):
            raise _Unavailable
        return result
    except (_Unavailable, UnicodeError, ValueError):
        raise GoogleCalendarWriteError("The Apple calendar address is unavailable") from None


def _action(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise CalendarWriteRejected("The local calendar action is invalid")
    return value


def _etag(value: object) -> str:
    value = _text(value, 1024)
    if re.fullmatch(r'"[\x21\x23-\x7e]+"', value) is None:
        raise GoogleCalendarWriteError("A strong Apple event version is required")
    return value


def _within(home: str, value: str) -> bool:
    first, second = urlsplit(home), urlsplit(value)
    return (
        first.hostname == second.hostname
        and first.port == second.port
        and second.path.startswith(first.path.rstrip("/") + "/")
    )


def _href(content: bytes, actual: str, property_name: str) -> str:
    try:
        rows = _xml(content).findall(DAV + "response")
        if len(rows) != 1:
            raise _Unavailable
        identifiers = rows[0].findall(DAV + "href")
        if (
            len(identifiers) != 1
            or not identifiers[0].text
            or _trusted(actual, identifiers[0].text) != actual
        ):
            raise _Unavailable
        prop = _properties(rows[0]).get(property_name)
        hrefs = prop.findall(DAV + "href") if prop is not None else []
        if len(hrefs) != 1 or not hrefs[0].text:
            raise _Unavailable
        return _trusted(actual, hrefs[0].text)
    except (_Unavailable, AttributeError, TypeError):
        raise GoogleCalendarWriteError("Apple calendar discovery could not be verified") from None


def _targets(
    content: bytes, home: str, principal: str, timezone: str
) -> tuple[CalendarTarget, ...]:
    try:
        rows = _xml(content).findall(DAV + "response")
        if not rows or len(rows) > MAX_CALENDARS + 1:
            raise _Unavailable
        seen, result = set(), []
        calendar_count = 0
        for row in rows:
            hrefs = row.findall(DAV + "href")
            if len(hrefs) != 1 or not hrefs[0].text:
                raise _Unavailable
            href = _trusted(home, hrefs[0].text)
            if href in seen:
                raise _Unavailable
            seen.add(href)
            props = _properties(row)
            kind = props.get(DAV + "resourcetype")
            if kind is None:
                raise _Unavailable
            if kind.find(CALDAV + "calendar") is None:
                if href.rstrip("/") != home.rstrip("/"):
                    raise _Unavailable
                continue
            calendar_count += 1
            if calendar_count > MAX_CALENDARS:
                raise _Unavailable
            if (
                kind.find(DAV + "collection") is None
                or not _within(home, href)
                or not href.endswith("/")
                or len(href) > 1024
            ):
                raise _Unavailable
            owner = props.get(DAV + "owner")
            owners = owner.findall(DAV + "href") if owner is not None else []
            if len(owners) != 1 or not owners[0].text:
                raise _Unavailable
            if _trusted(home, owners[0].text) != principal:
                continue
            components = props.get(CALDAV + "supported-calendar-component-set")
            names = (
                [item.attrib.get("name") for item in components.findall(CALDAV + "comp")]
                if components is not None
                else []
            )
            if (
                not names
                or len(set(names)) != len(names)
                or any(
                    not isinstance(name, str) or re.fullmatch(r"[A-Z][A-Z0-9-]*", name) is None
                    for name in names
                )
            ):
                raise _Unavailable
            if "VEVENT" not in names:
                continue
            current = props.get(DAV + "current-user-privilege-set")
            if current is None:
                raise _Unavailable
            privileges = set()
            for item in current:
                if item.tag != DAV + "privilege" or len(item) != 1:
                    raise _Unavailable
                privileges.add(item[0].tag)
            if DAV + "all" not in privileges:
                if DAV + "write" in privileges:
                    privileges.update(DAV + name for name in ("bind", "unbind", "write-content"))
                if (
                    not {DAV + name for name in ("read", "bind", "unbind", "write-content")}
                    <= privileges
                ):
                    continue
            label = props.get(DAV + "displayname")
            if label is None or len(label):
                raise _Unavailable
            result.append(CalendarTarget(href, _text(label.text, 1024), timezone))
            if len(result) > MAX_CALENDARS:
                raise _Unavailable
        return tuple(result)
    except (_Unavailable, AttributeError, TypeError, ValueError):
        raise GoogleCalendarWriteError("Writable Apple calendars could not be verified") from None


def _instant(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError
    if value.microsecond:
        raise ValueError
    return value.astimezone(UTC)


def _input(event: dict[str, Any]) -> dict[str, Any]:
    try:
        if not set(event) <= {
            "id",
            "summary",
            "location",
            "description",
            "start",
            "end",
            "visibility",
            "transparency",
            "extendedProperties",
            "reminders",
        }:
            raise ValueError
        action = _action(event["id"])
        marker = event["extendedProperties"]["private"]
        digest = marker["navoxDigest"]
        if (
            marker != {"navoxAction": action, "navoxDigest": digest}
            or event["extendedProperties"] != {"private": marker}
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or event["visibility"] != "private"
            or event["transparency"] != "opaque"
            or event["description"] != NOTE
            or event["reminders"] != {"useDefault": False}
            or set(event["start"]) != {"dateTime", "timeZone"}
            or set(event["end"]) != {"dateTime", "timeZone"}
        ):
            raise ValueError
        zone = _text(event["start"]["timeZone"], 64)
        ZoneInfo(zone)
        if zone != event["end"]["timeZone"]:
            raise ValueError
        start = _instant(datetime.fromisoformat(_text(event["start"]["dateTime"], 80)))
        end = _instant(datetime.fromisoformat(_text(event["end"]["dateTime"], 80)))
        if not 60 <= (end - start).total_seconds() <= 8 * 3600:
            raise ValueError
        return {
            "id": action,
            "summary": _text(event["summary"], 200),
            "location": _text(event.get("location", ""), 500, empty=True),
            "description": NOTE,
            "start": {"dateTime": start.isoformat(), "timeZone": zone},
            "end": {"dateTime": end.isoformat(), "timeZone": zone},
            "visibility": "private",
            "transparency": "opaque",
            "extendedProperties": {"private": {"navoxAction": action, "navoxDigest": digest}},
            "reminders": {"useDefault": False},
        }
    except (ValueError, KeyError, TypeError, AttributeError, GoogleCalendarWriteError):
        raise CalendarWriteRejected(
            "Only an exact reviewed personal Apple event is supported"
        ) from None


def _ical(event: dict[str, Any]) -> bytes:
    calendar, item = Calendar(), Event()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    item.add("uid", "navox-" + event["id"] + "@navox.app")
    item.add("dtstamp", datetime.now(UTC).replace(microsecond=0))
    item.add("dtstart", datetime.fromisoformat(event["start"]["dateTime"]))
    item.add("dtend", datetime.fromisoformat(event["end"]["dateTime"]))
    for key, value in (
        ("summary", event["summary"]),
        ("description", NOTE),
        ("class", "PRIVATE"),
        ("transp", "OPAQUE"),
        ("status", "CONFIRMED"),
        ("x-navox-action", event["id"]),
        ("x-navox-digest", event["extendedProperties"]["private"]["navoxDigest"]),
        ("x-navox-timezone", event["start"]["timeZone"]),
    ):
        item.add(key, value)
    if event["location"]:
        item.add("location", event["location"])
    calendar.add_component(item)
    return bytes(calendar.to_ical())


def _event(content: bytes, event_id: str, etag: str) -> dict[str, Any]:
    try:
        content.decode("utf-8", errors="strict")
        calendar = Calendar.from_ical(content)
        if (
            calendar.name != "VCALENDAR"
            or calendar.errors
            or len(calendar.subcomponents) != 1
            or calendar.subcomponents[0].name != "VEVENT"
            or not set(calendar) <= {"VERSION", "PRODID", "CALSCALE"}
            or "PRODID" not in calendar
            or str(calendar.get("VERSION")) != "2.0"
            or any(isinstance(value, list) or value.params for value in calendar.values())
            or calendar.get("CALSCALE", "GREGORIAN") != "GREGORIAN"
        ):
            raise ValueError
        item = calendar.subcomponents[0]
        if (
            item.errors
            or item.subcomponents
            or not set(item)
            <= {
                "UID",
                "DTSTAMP",
                "DTSTART",
                "DTEND",
                "SUMMARY",
                "LOCATION",
                "DESCRIPTION",
                "CLASS",
                "TRANSP",
                "STATUS",
                "X-NAVOX-ACTION",
                "X-NAVOX-DIGEST",
                "X-NAVOX-TIMEZONE",
                "CREATED",
                "LAST-MODIFIED",
                "SEQUENCE",
            }
            or any(isinstance(value, list) for value in item.values())
        ):
            raise ValueError
        for key, value in item.items():
            if key in {"DTSTART", "DTEND", "DTSTAMP", "CREATED", "LAST-MODIFIED"}:
                if dict(value.params) not in ({}, {"VALUE": "DATE-TIME"}):
                    raise ValueError
                if not value.to_ical().endswith(b"Z"):
                    raise ValueError
                _instant(item.decoded(key))
            elif value.params:
                raise ValueError
        _text(str(calendar["PRODID"]), 1024)
        if "SEQUENCE" in item and not 0 <= int(str(item["SEQUENCE"])) <= 2147483647:
            raise ValueError
        action = _action(str(item["X-NAVOX-ACTION"]))
        if (
            action != event_id
            or str(item["UID"]) != "navox-" + action + "@navox.app"
            or str(item["CLASS"]) != "PRIVATE"
            or str(item["TRANSP"]) != "OPAQUE"
            or str(item["STATUS"]) != "CONFIRMED"
            or "DTSTAMP" not in item
        ):
            raise ValueError
        normalized = _input(
            {
                "id": action,
                "summary": str(item["SUMMARY"]),
                "location": str(item.get("LOCATION", "")),
                "description": str(item["DESCRIPTION"]),
                "start": {
                    "dateTime": _instant(item.decoded("DTSTART")).isoformat(),
                    "timeZone": str(item["X-NAVOX-TIMEZONE"]),
                },
                "end": {
                    "dateTime": _instant(item.decoded("DTEND")).isoformat(),
                    "timeZone": str(item["X-NAVOX-TIMEZONE"]),
                },
                "visibility": "private",
                "transparency": "opaque",
                "reminders": {"useDefault": False},
                "extendedProperties": {
                    "private": {
                        "navoxAction": action,
                        "navoxDigest": str(item["X-NAVOX-DIGEST"]),
                    }
                },
            }
        )
        return {**normalized, "status": "confirmed", "etag": _etag(etag)}
    except (
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
        RecursionError,
        UnicodeError,
        GoogleCalendarWriteError,
    ):
        raise GoogleCalendarWriteError("The exact Apple event could not be verified") from None


@dataclass
class _Session:
    client: httpx.AsyncClient
    deadline: float


@dataclass
class _Response:
    content: bytes
    url: str
    headers: httpx.Headers
    status: int


class AppleCalendarWriteProvider:
    conditional_undo_supported = True

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        enabled: bool = False,
        timezone: str = "UTC",
    ):
        ZoneInfo(timezone)
        self.transport, self.enabled, self.timezone = transport, enabled is True, timezone

    @asynccontextmanager
    async def _session(self, apple_id: str, app_password: str) -> AsyncIterator[_Session]:
        if (
            not isinstance(apple_id, str)
            or not 3 <= len(apple_id) <= 320
            or ":" in apple_id
            or any(ord(char) <= 32 or ord(char) == 127 for char in apple_id)
            or not isinstance(app_password, str)
            or re.fullmatch(r"[a-z]{4}(?:-[a-z]{4}){3}", app_password) is None
        ):
            raise CalendarWriteRejected("Apple calendar authorization is unavailable")
        async with httpx.AsyncClient(
            timeout=REQUEST_SECONDS,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
            auth=httpx.BasicAuth(apple_id, app_password),
        ) as client:
            yield _Session(client, time.monotonic() + TOTAL_SECONDS)

    async def _request(
        self,
        session: _Session,
        method: str,
        url: str,
        check: Check,
        *,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> _Response:
        current = _trusted(DISCOVERY_URL, url)
        writing = method in {"PUT", "DELETE"}
        error = CalendarWriteUncertain if writing else GoogleCalendarWriteError
        for redirect in range(MAX_REDIRECTS + 1):
            await check()
            remaining = session.deadline - time.monotonic()
            if remaining <= 0:
                raise error("The Apple calendar response could not be verified")
            status, body, response_headers, failed = None, bytearray(), httpx.Headers(), False
            try:
                async with (
                    asyncio.timeout(min(REQUEST_SECONDS, remaining)),
                    session.client.stream(
                        method, current, content=content, headers=headers
                    ) as response,
                ):
                    status, response_headers = response.status_code, response.headers
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            failed = True
                            break
                        body.extend(chunk)
            except (httpx.HTTPError, TimeoutError):
                failed = True
            await check()
            if failed or status is None:
                raise error("The Apple calendar response could not be verified")
            location = response_headers.get("location")
            if method == "PROPFIND" and status in {301, 302, 307, 308} and location:
                if redirect >= MAX_REDIRECTS:
                    raise GoogleCalendarWriteError("Apple calendar discovery is unavailable")
                current = _trusted(current, location)
                continue
            if method == "GET" and status == 404:
                return _Response(bytes(body), current, response_headers, status)
            if method == "DELETE" and status == 412:
                raise CalendarWriteChanged("The Apple event version changed", status_code=status)
            if (
                writing
                and status in {400, 401, 403, 405, 413, 415, 422}
                or (method == "PUT" and status == 404)
            ):
                raise CalendarWriteRejected("Apple rejected the event request", status_code=status)
            expected = {"PROPFIND": 207, "GET": 200, "PUT": 201, "DELETE": 204}[method]
            if status != expected or method in {"PUT", "DELETE"} and body:
                raise error("The Apple calendar response could not be verified", status_code=status)
            return _Response(bytes(body), current, response_headers, status)
        raise GoogleCalendarWriteError("Apple calendar discovery is unavailable")

    async def _discover(self, session: _Session, check: Check) -> tuple[CalendarTarget, ...]:
        headers = {"Content-Type": "application/xml; charset=utf-8", "Depth": "0"}
        response = await self._request(
            session,
            "PROPFIND",
            DISCOVERY_URL,
            check,
            content=_PRINCIPAL_QUERY.encode(),
            headers=headers,
        )
        principal = _href(response.content, response.url, DAV + "current-user-principal")
        response = await self._request(
            session, "PROPFIND", principal, check, content=_HOME_QUERY.encode(), headers=headers
        )
        home = _href(response.content, response.url, CALDAV + "calendar-home-set")
        response = await self._request(
            session,
            "PROPFIND",
            home,
            check,
            content=_TARGETS_QUERY.encode(),
            headers={**headers, "Depth": "1"},
        )
        return _targets(response.content, response.url, principal, self.timezone)

    async def _target(self, session: _Session, calendar_id: str, check: Check) -> CalendarTarget:
        if _trusted(DISCOVERY_URL, calendar_id) != calendar_id:
            raise CalendarWriteRejected("Select the exact listed Apple calendar")
        for item in await self._discover(session, check):
            if item.id == calendar_id:
                return item
        raise CalendarWriteRejected("Choose a verified personal Apple calendar")

    async def owned_calendars(
        self, *, apple_id: str, app_password: str, check: Check
    ) -> tuple[CalendarTarget, ...]:
        async with self._session(apple_id, app_password) as session:
            return await self._discover(session, check)

    async def target(
        self, *, apple_id: str, app_password: str, calendar_id: str, check: Check
    ) -> CalendarTarget:
        async with self._session(apple_id, app_password) as session:
            return await self._target(session, calendar_id, check)

    @staticmethod
    def _resource(calendar_id: str, event_id: str) -> str:
        return _trusted(calendar_id, calendar_id + _action(event_id) + ".ics")

    async def _read(
        self, session: _Session, calendar_id: str, event_id: str, check: Check
    ) -> tuple[dict[str, Any], bytes] | None:
        response = await self._request(session, "GET", self._resource(calendar_id, event_id), check)
        if response.status == 404:
            return None
        if response.headers.get("content-type", "").split(";", 1)[0].lower() != "text/calendar":
            raise GoogleCalendarWriteError("The Apple event response is unavailable")
        return _event(
            response.content, event_id, _etag(response.headers.get("etag"))
        ), response.content

    async def event(
        self, *, apple_id: str, app_password: str, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None:
        event_id = _action(event_id)
        async with self._session(apple_id, app_password) as session:
            target = await self._target(session, calendar_id, check)
            result = await self._read(session, target.id, event_id, check)
            return result[0] if result is not None else None

    async def insert(
        self,
        *,
        apple_id: str,
        app_password: str,
        calendar_id: str,
        event: dict[str, Any],
        check: Check,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise CalendarWriteRejected("Apple calendar writes are not enabled")
        reviewed = _input(event)
        content = _ical(reviewed)
        async with self._session(apple_id, app_password) as session:
            target = await self._target(session, calendar_id, check)
            resource = self._resource(target.id, reviewed["id"])
            response = await self._request(
                session,
                "PUT",
                resource,
                check,
                content=content,
                headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            )
            try:
                if (
                    response.headers.get("location") is not None
                    and _trusted(resource, response.headers["location"]) != resource
                ):
                    raise ValueError
            except (ValueError, GoogleCalendarWriteError):
                raise CalendarWriteUncertain("Apple creation could not be confirmed") from None
            # Native checks can raise StudentError (a ValueError). Keep every
            # await outside generic parsing catches so revoked authority wins.
            try:
                observed = await self._read(session, target.id, reviewed["id"], check)
            except GoogleCalendarWriteError:
                raise CalendarWriteUncertain("Apple creation could not be confirmed") from None
            try:
                if observed is None:
                    raise ValueError
                normalized, stored = observed
                put_etag = response.headers.get("etag")
                if put_etag is not None and put_etag.startswith("W/"):
                    _etag(put_etag[2:])
                if (
                    put_etag is not None
                    and not put_etag.startswith("W/")
                    and (_etag(put_etag) != normalized["etag"] or stored != content)
                ):
                    raise ValueError
                if {
                    key: value for key, value in normalized.items() if key not in {"status", "etag"}
                } != reviewed:
                    raise ValueError
                return normalized
            except (ValueError, GoogleCalendarWriteError):
                raise CalendarWriteUncertain("Apple creation could not be confirmed") from None

    async def delete(
        self,
        *,
        apple_id: str,
        app_password: str,
        calendar_id: str,
        event_id: str,
        etag: str,
        check: Check,
    ) -> None:
        if not self.enabled:
            raise CalendarWriteRejected("Apple calendar writes are not enabled")
        event_id = _action(event_id)
        try:
            etag = _etag(etag)
        except GoogleCalendarWriteError:
            raise CalendarWriteRejected(
                "A specific strong Apple event version is required"
            ) from None
        async with self._session(apple_id, app_password) as session:
            target = await self._target(session, calendar_id, check)
            observed = await self._read(session, target.id, event_id, check)
            if observed is None or observed[0]["etag"] != etag:
                raise CalendarWriteChanged("The exact Apple event version is unavailable")
            await self._request(
                session,
                "DELETE",
                self._resource(target.id, event_id),
                check,
                headers={"If-Match": etag},
            )
