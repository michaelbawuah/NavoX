"""Bounded Microsoft personal-event transport; writes default to disabled.

Source and mock-wire checks do not qualify live scheduling. Graph supports a
client transactionId and single-value extended properties on event creation:
https://learn.microsoft.com/graph/api/user-post-events
https://learn.microsoft.com/graph/api/singlevaluelegacyextendedproperty-get
https://learn.microsoft.com/graph/api/singlevaluelegacyextendedproperty-post-singlevalueextendedproperties

A marker query can establish exact PRESENT evidence, never absence: another app
can remove a property or move an event. Empty, paged or ambiguous results stay
uncertain. Undo is deliberately unavailable; no best-effort DELETE is sent.
The official event-delete REST page does not promise conditional deletion;
PowerShell exposes IfMatch, but live stale-version enforcement is unqualified.
https://learn.microsoft.com/powershell/module/microsoft.graph.calendar/remove-mguserevent

The caller supplies the display timezone; Graph calendars do not expose their
own timezone. No route, permission, credential or production flag is changed.
"""

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from navox.student_calendars.provider import (
    CalendarTarget,
    CalendarWriteChanged,
    CalendarWriteRejected,
    CalendarWriteUncertain,
    Check,
    GoogleCalendarWriteError,
    _unique,
)

ROOT = "https://graph.microsoft.com/v1.0"
CALENDARS = ROOT + "/me/calendars"
# Fixed app namespace; never allocate one property name per event.
NAMESPACE = "{a9448af3-c063-4c2f-8a21-d03ea965a933}"
ACTION_PROPERTY = "String " + NAMESPACE + " Name NavoXAction"
DATA_PROPERTY = "String " + NAMESPACE + " Name NavoXReview"
NOTE = "Added by NavoX"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CALENDARS = 100
MAX_PAGES = 4
SECONDS = 8
CALENDAR_SELECT = "id,name,canEdit,canShare,isSharedWithMe,owner"
EVENT_SELECT = (
    "id,subject,start,end,originalStartTimeZone,originalEndTimeZone,location,locations,"
    "body,showAs,sensitivity,isReminderOn,isAllDay,isCancelled,isOnlineMeeting,"
    "onlineMeeting,attendees,recurrence,type,hasAttachments,transactionId,"
    "responseRequested,allowNewTimeProposals"
)


def _text(value: object, limit: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not value and not empty)
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise GoogleCalendarWriteError("Microsoft calendar response could not be verified")
    return value


def _id(value: object) -> str:
    result = _text(value, 1024)
    if result in {".", ".."}:
        raise CalendarWriteRejected("The calendar identifier is invalid")
    return result


def _action(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise CalendarWriteRejected("The local calendar action is invalid")
    return value


def _etag(value: object) -> str:
    result = _text(value, 1024)
    if re.fullmatch(r'(?:W/)?"[\x21\x23-\x7e]+"', result) is None:
        raise GoogleCalendarWriteError("Microsoft calendar version could not be verified")
    return result


def _utc(value: object) -> datetime:
    if not isinstance(value, dict) or value.get("timeZone") not in ("UTC", "Etc/UTC"):
        raise GoogleCalendarWriteError("Microsoft calendar time could not be verified")
    raw = _text(value.get("dateTime"), 40)
    # Graph normally returns seven fractional digits and an offset-free UTC time.
    if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?Z?", raw) is None:
        raise GoogleCalendarWriteError("Microsoft calendar time could not be verified")
    fraction = raw.removesuffix("Z").partition(".")[2]
    if len(fraction) == 7 and fraction[-1] != "0":
        # Python/native reviews have microsecond precision; never silently
        # truncate a provider tick into an apparently exact reviewed instant.
        raise CalendarWriteUncertain("The Microsoft event time is more precise than its review")
    try:
        return datetime.fromisoformat(raw.removesuffix("Z")).replace(tzinfo=UTC)
    except ValueError:
        raise GoogleCalendarWriteError("Microsoft calendar time could not be verified") from None


def _property(data: dict[str, Any], property_id: str) -> str:
    rows = data.get("singleValueExtendedProperties")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise CalendarWriteUncertain("The exact calendar marker could not be verified")
    if rows[0].get("id") != property_id:
        raise CalendarWriteUncertain("The exact calendar marker could not be verified")
    return _text(rows[0].get("value"), 1024)


class MicrosoftCalendarWriteProvider:
    """One reviewed create or positive read; no automatic retries or unsafe Undo."""

    conditional_undo_supported = False

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        enabled: bool = False,
        timezone: str = "UTC",
    ):
        ZoneInfo(timezone)
        self.transport, self.enabled, self.timezone = transport, enabled, timezone

    async def _request(
        self, *, access_token: str, method: str, url: str, check: Check, **kwargs: Any
    ) -> dict[str, Any]:
        token = _text(access_token, 32000)
        if not token.isascii():
            raise CalendarWriteRejected("Microsoft calendar authorization is unavailable")
        parts = urlsplit(_text(url, 8192))
        if (
            method not in {"GET", "POST"}
            or parts.scheme != "https"
            or parts.netloc != "graph.microsoft.com"
            or parts.fragment
            or not (
                parts.path == "/v1.0/me"
                or parts.path == "/v1.0/me/calendars"
                or parts.path.startswith("/v1.0/me/calendars/")
            )
        ):
            raise CalendarWriteRejected("The Microsoft calendar endpoint is unavailable")
        status, raw, failed = None, bytearray(), False
        try:
            async with (
                asyncio.timeout(SECONDS),
                httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=SECONDS,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Prefer": 'outlook.timezone="UTC", IdType="ImmutableId"',
                    },
                ) as client,
            ):
                await check()
                async with client.stream(method, url, **kwargs) as response:
                    status = response.status_code
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            failed = True
                            break
                        raw.extend(chunk)
        except (httpx.HTTPError, TimeoutError):
            failed = True
        await check()
        error = CalendarWriteUncertain if method == "POST" else GoogleCalendarWriteError
        if failed or status is None:
            raise error("Microsoft calendar response could not be verified")
        if method == "POST" and status in {400, 401, 403, 404, 405, 413, 415, 422}:
            raise CalendarWriteRejected(
                "Microsoft rejected the calendar request", status_code=status
            )
        if status != (201 if method == "POST" else 200):
            raise error("Microsoft calendar response could not be verified", status_code=status)
        try:
            result = json.loads(raw, object_pairs_hook=_unique)
        except (ValueError, UnicodeError, RecursionError):
            raise error("Microsoft calendar response could not be verified") from None
        if not isinstance(result, dict):
            raise error("Microsoft calendar response could not be verified")
        return cast(dict[str, Any], result)

    async def _owner(self, token: str, check: Check) -> str:
        data = await self._request(
            access_token=token,
            method="GET",
            url=ROOT + "/me",
            check=check,
            params={"$select": "mail,userPrincipalName"},
        )
        return _text(data.get("mail") or data.get("userPrincipalName"), 320).casefold()

    def _target(self, data: object, owner: str) -> CalendarTarget | None:
        if not isinstance(data, dict):
            raise GoogleCalendarWriteError()
        for key in ("canEdit", "canShare", "isSharedWithMe"):
            if type(data.get(key)) is not bool:
                raise GoogleCalendarWriteError()
        if not data["canEdit"] or not data["canShare"] or data["isSharedWithMe"]:
            return None
        calendar_owner = data.get("owner")
        if not isinstance(calendar_owner, dict):
            raise GoogleCalendarWriteError()
        if _text(calendar_owner.get("address"), 320).casefold() != owner:
            return None
        return CalendarTarget(_id(data.get("id")), _text(data.get("name"), 1024), self.timezone)

    async def owned_calendars(
        self, *, access_token: str, check: Check
    ) -> tuple[CalendarTarget, ...]:
        owner = await self._owner(access_token, check)
        url = CALENDARS
        params: dict[str, str] | None = {"$select": CALENDAR_SELECT, "$top": "100"}
        result: list[CalendarTarget] = []
        identifiers: set[str] = set()
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            data = await self._request(
                access_token=access_token, method="GET", url=url, check=check, params=params
            )
            rows = data.get("value")
            if not isinstance(rows, list) or len(rows) > MAX_CALENDARS:
                raise GoogleCalendarWriteError()
            for row in rows:
                if not isinstance(row, dict):
                    raise GoogleCalendarWriteError()
                identifier = _id(row.get("id"))
                if identifier in identifiers or len(identifiers) >= MAX_CALENDARS:
                    raise GoogleCalendarWriteError("Microsoft calendar inventory is incomplete")
                identifiers.add(identifier)
                target = self._target(row, owner)
                if target:
                    result.append(target)
            following = data.get("@odata.nextLink")
            if following is None:
                return tuple(result)
            parts = urlsplit(_text(following, 8192))
            if (parts.scheme, parts.netloc, parts.path) != (
                "https",
                "graph.microsoft.com",
                "/v1.0/me/calendars",
            ) or following in seen:
                raise GoogleCalendarWriteError("Microsoft calendar inventory is incomplete")
            seen.add(following)
            url, params = following, None
        raise GoogleCalendarWriteError("Microsoft calendar inventory is incomplete")

    async def target(self, *, access_token: str, calendar_id: str, check: Check) -> CalendarTarget:
        owner = await self._owner(access_token, check)
        data = await self._request(
            access_token=access_token,
            method="GET",
            url=CALENDARS + "/" + quote(_id(calendar_id), safe=""),
            check=check,
            params={"$select": CALENDAR_SELECT},
        )
        result = self._target(data, owner)
        if result is None or result.id != calendar_id:
            raise CalendarWriteRejected("Choose a writable Microsoft calendar you own")
        return result

    def _events(self, calendar_id: str, remote_id: str | None = None) -> str:
        url = CALENDARS + "/" + quote(_id(calendar_id), safe="") + "/events"
        return url if remote_id is None else url + "/" + quote(_id(remote_id), safe="")

    async def _from_action(
        self, token: str, calendar_id: str, action: str, first: dict[str, Any], check: Check
    ) -> dict[str, Any]:
        if _property(first, ACTION_PROPERTY) != action or first.get("transactionId") != action:
            raise CalendarWriteUncertain("The exact Microsoft calendar action is unconfirmed")
        try:
            remote, version = _id(first.get("id")), _etag(first.get("@odata.etag"))
        except GoogleCalendarWriteError:
            raise CalendarWriteUncertain("The Microsoft event identity is unconfirmed") from None
        data = await self._request(
            access_token=token,
            method="GET",
            url=self._events(calendar_id, remote),
            check=check,
            params={
                "$select": EVENT_SELECT,
                "$expand": "singleValueExtendedProperties($filter=id eq '" + DATA_PROPERTY + "')",
            },
        )
        if data.get("id") != remote or _etag(data.get("@odata.etag")) != version:
            raise CalendarWriteUncertain("The Microsoft event changed during verification")
        try:
            metadata = json.loads(_property(data, DATA_PROPERTY), object_pairs_hook=_unique)
            if not isinstance(metadata, dict):
                raise ValueError
            marker_version = metadata.get("v")
            if type(marker_version) is not int or marker_version not in {1, 2}:
                raise ValueError
            fields = {"v", "action", "digest", "timezone"}
            if marker_version == 2:
                fields.add("wire_timezone")
            if (
                set(metadata) != fields
                or metadata["action"] != action
                or not re.fullmatch(r"[0-9a-f]{64}", metadata["digest"])
            ):
                raise ValueError
            zone = _text(metadata["timezone"], 64)
            ZoneInfo(zone)
            wire_zone = zone if marker_version == 1 else metadata["wire_timezone"]
            if marker_version == 2 and wire_zone != "UTC":
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise CalendarWriteUncertain(
                "The Microsoft event review marker is unconfirmed"
            ) from None
        if (
            data.get("transactionId") != action
            or data.get("originalStartTimeZone") != wire_zone
            or data.get("originalEndTimeZone") != wire_zone
            or data.get("showAs") != "busy"
            or data.get("sensitivity") != "private"
            or data.get("isReminderOn") is not False
            or data.get("isAllDay") is not False
            or data.get("isCancelled") is not False
            or data.get("isOnlineMeeting") is not False
            or data.get("onlineMeeting") is not None
            or data.get("attendees") != []
            or data.get("recurrence") is not None
            or data.get("type") != "singleInstance"
            or data.get("hasAttachments") is not False
            or data.get("responseRequested") is not False
            or data.get("allowNewTimeProposals") is not False
            or data.get("body") != {"contentType": "text", "content": NOTE}
        ):
            raise CalendarWriteChanged("The Microsoft personal event changed")
        location = data.get("location")
        if not isinstance(location, dict):
            raise CalendarWriteUncertain("The Microsoft location is unconfirmed")
        label = _text(location.get("displayName"), 500, empty=True)
        if any(
            value
            for key, value in location.items()
            if key not in {"displayName", "locationType", "uniqueIdType"}
        ):
            raise CalendarWriteChanged("The Microsoft location changed")
        locations = data.get("locations")
        if (
            not isinstance(locations, list)
            or len(locations) > 1
            or (locations and locations[0] != location)
        ):
            raise CalendarWriteChanged("The Microsoft locations changed")
        start, end = _utc(data.get("start")), _utc(data.get("end"))
        if start >= end:
            raise CalendarWriteUncertain("The Microsoft event time is unconfirmed")
        return {
            "id": action,
            "etag": version,
            "status": "confirmed",
            "summary": _text(data.get("subject"), 200),
            "location": label,
            "start": {"dateTime": start.isoformat(), "timeZone": zone},
            "end": {"dateTime": end.isoformat(), "timeZone": zone},
            "visibility": "private",
            "transparency": "opaque",
            "description": "" if marker_version == 1 else NOTE,
            "reminders": {"useDefault": False},
            "extendedProperties": {
                "private": {"navoxAction": action, "navoxDigest": metadata["digest"]}
            },
        }

    async def event(
        self, *, access_token: str, calendar_id: str, event_id: str, check: Check
    ) -> dict[str, Any] | None:
        action = _action(event_id)
        await self.target(access_token=access_token, calendar_id=calendar_id, check=check)
        data = await self._request(
            access_token=access_token,
            method="GET",
            url=self._events(calendar_id),
            check=check,
            params={
                "$select": EVENT_SELECT,
                "$top": "2",
                "$filter": "singleValueExtendedProperties/Any(ep: ep/id eq '"
                + ACTION_PROPERTY
                + "' and ep/value eq '"
                + action
                + "')",
                "$expand": "singleValueExtendedProperties($filter=id eq '" + ACTION_PROPERTY + "')",
            },
        )
        rows = data.get("value")
        if (
            data.get("@odata.nextLink") is not None
            or not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise CalendarWriteUncertain("No unique complete Microsoft event receipt was found")
        return await self._from_action(access_token, calendar_id, action, rows[0], check)

    async def insert(
        self, *, access_token: str, calendar_id: str, event: dict[str, Any], check: Check
    ) -> dict[str, Any]:
        if not self.enabled:
            raise CalendarWriteRejected("Microsoft calendar writes are not qualified")
        allowed = {
            "id",
            "summary",
            "start",
            "end",
            "location",
            "visibility",
            "transparency",
            "extendedProperties",
            "reminders",
            "description",
        }
        if (
            not set(event).issubset(allowed)
            or event.get("visibility") != "private"
            or event.get("transparency") != "opaque"
            or event.get("reminders") != {"useDefault": False}
            or event.get("description") != NOTE
        ):
            raise CalendarWriteRejected("Only a reviewed private personal event is supported")
        try:
            action = _action(event["id"])
            private = event["extendedProperties"]["private"]
            digest = private["navoxDigest"]
            zone = event["start"]["timeZone"]
            if (
                private != {"navoxAction": action, "navoxDigest": digest}
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or zone != event["end"]["timeZone"]
            ):
                raise ValueError
            ZoneInfo(zone)
            start = datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00"))
            if (
                start.tzinfo is None
                or end.tzinfo is None
                or not 60 <= (end - start).total_seconds() <= 8 * 3600
            ):
                raise ValueError
            title = _text(event["summary"], 200)
            location = _text(event.get("location", ""), 500, empty=True)
        except (KeyError, TypeError, ValueError):
            raise CalendarWriteRejected("The reviewed Microsoft event is invalid") from None
        await self.target(access_token=access_token, calendar_id=calendar_id, check=check)
        metadata = json.dumps(
            {
                "v": 2,
                "action": action,
                "digest": digest,
                "timezone": zone,
                "wire_timezone": "UTC",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        body = {
            "subject": title,
            "start": {
                "dateTime": start.astimezone(UTC).replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": end.astimezone(UTC).replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "location": {"displayName": location},
            "body": {"contentType": "text", "content": NOTE},
            "showAs": "busy",
            "sensitivity": "private",
            "isReminderOn": False,
            "isAllDay": False,
            "isOnlineMeeting": False,
            "attendees": [],
            "responseRequested": False,
            "allowNewTimeProposals": False,
            "transactionId": action,
            "singleValueExtendedProperties": [
                {"id": ACTION_PROPERTY, "value": action},
                {"id": DATA_PROPERTY, "value": metadata},
            ],
        }
        # Exactly one POST; never retry a timeout, conflict or malformed response.
        receipt = await self._request(
            access_token=access_token,
            method="POST",
            url=self._events(calendar_id),
            check=check,
            json=body,
        )
        try:
            remote, version = _id(receipt.get("id")), _etag(receipt.get("@odata.etag"))
        except GoogleCalendarWriteError:
            # A malformed success receipt is not proof that the POST failed.
            raise CalendarWriteUncertain("The Microsoft creation receipt is unconfirmed") from None
        first = await self._request(
            access_token=access_token,
            method="GET",
            url=self._events(calendar_id, remote),
            check=check,
            params={
                "$select": EVENT_SELECT,
                "$expand": "singleValueExtendedProperties($filter=id eq '" + ACTION_PROPERTY + "')",
            },
        )
        normalized = await self._from_action(access_token, calendar_id, action, first, check)
        if first.get("id") != remote or normalized["etag"] != version:
            raise CalendarWriteUncertain("The Microsoft creation version is unconfirmed")
        return normalized

    async def delete(
        self, *, access_token: str, calendar_id: str, event_id: str, etag: str, check: Check
    ) -> None:
        # An existence check followed by an unconditional delete would race with
        # attendee/content edits and could send cancellation mail. Never do it.
        raise CalendarWriteRejected("Microsoft exact-version Undo is not qualified")
