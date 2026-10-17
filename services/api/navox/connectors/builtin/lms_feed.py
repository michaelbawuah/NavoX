"""Bounded iCalendar normalization shared by all learning-system feeds.

Calendar bodies are untrusted data. No links, alarms, attachments or descriptions
are executed. Production parsing runs in a CPU/memory/time bounded subprocess.
"""

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import recurring_ical_events  # type: ignore[import-untyped]
from icalendar import Calendar

from navox.connectors.contracts import ConnectorManifest
from navox.lms_feeds.schemas import MAX_ITEMS, FeedItem, ParsedFeed, Platform
from navox.lms_feeds.transport import MAX_FEED_BYTES, FeedError, feed_domain
from navox.student.occurrences import local_moment
from navox.student.schemas import valid_zone, wall_time

LMS_FEED_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": "lms-calendar-feed",
        "version": "1.0.0",
        "displayName": "Learning-system calendar",
        "category": "education",
        "connectorClass": "POLLING",
        "auth": [{"kind": "api_token", "label": "Private calendar feed link", "scopes": []}],
        "resourceTypes": ["academic.calendar_item"],
        "capabilities": {
            "read": [
                {
                    "name": "academic.calendar.read",
                    "description": "Read assignment dates and course calendar events",
                    "sensitive": True,
                }
            ],
            "write": [],
            "events": [],
            "incrementalSync": True,
        },
        "requiredSecrets": ["FEED_URL"],
        "rateLimitStrategy": "fixed_backoff",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


class _PlainText(HTMLParser):
    def __init__(self, private_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.private_url = private_url

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden:
            if tag in {"br", "p", "div", "li"}:
                self.parts.append(" ")
            if tag == "a":
                href = next((value for name, value in attrs if name == "href"), None)
                if href and (link := safe_source_link(href, private_url=self.private_url)):
                    self.parts.append(f" {link} ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _private_values(private_url: str) -> set[str]:
    source = urlsplit(private_url)
    values = {
        value for _, value in parse_qsl(source.query, keep_blank_values=True) if len(value) >= 3
    }
    # Subscription tokens can also be embedded in the final path segment.
    for segment in unquote(source.path).split("/"):
        part = segment.removesuffix(".ics")
        if len(part) >= 6 and part.lower() not in {
            "calendar",
            "calendars",
            "calendarfeed",
            "subscribe",
            "subscriptions",
            "icalendar",
            "export",
        }:
            values.add(part)
    return values


def _subscription_link(value: str, private_url: str) -> bool:
    try:
        target, source = urlsplit(value), urlsplit(private_url)
        target_host = (target.hostname or "").encode("idna").decode("ascii").lower()
        source_host = (source.hostname or "").encode("idna").decode("ascii").lower()
        return target_host == source_host and (
            unquote(target.path).rstrip("/").casefold()
            == unquote(source.path).rstrip("/").casefold()
        )
    except (ValueError, UnicodeError):
        return True


def safe_source_link(value: str, *, private_url: str | None = None) -> str | None:
    """Navigation retains an HTTPS domain/path, never a subscription query."""
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.username or parts.password:
            return None
        if private_url and (
            _subscription_link(value, private_url)
            or any(secret in unquote(parts.path) for secret in _private_values(private_url))
        ):
            return None
        feed_domain(urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", "")))
        if parts.path.lower().endswith(".ics") or "/calendarfeed" in parts.path.lower():
            return None
        clean = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
        return clean if len(clean) <= 2_048 else None
    except (ValueError, FeedError):
        return None


def _text(value: object, limit: int, private_url: str) -> str:
    raw = unescape(str(value or ""))
    raw = re.sub(
        r"https?://[^\s<>\"']+",
        lambda match: "[calendar link]" if _subscription_link(match[0], private_url) else match[0],
        raw,
        flags=re.I,
    )
    for secret in sorted(_private_values(private_url), key=len, reverse=True):
        raw = raw.replace(secret, "[calendar value]")
    parser = _PlainText(private_url)
    parser.feed(raw)
    text = " ".join("".join(parser.parts).split())
    text = re.sub(
        r"https?://[^\s<>]+",
        lambda m: safe_source_link(m[0], private_url=private_url) or "[link]",
        text,
        flags=re.I,
    )
    return "".join(c for c in text if ord(c) >= 32 and ord(c) != 127)[:limit]


def _property(event: Any, key: str) -> Any:
    result = event.get(key)
    if isinstance(result, list):
        raise FeedError("invalid_calendar")
    return result


def _fix_dates(event: Any, timezone: str) -> None:
    for key in ("DTSTART", "DTEND", "RECURRENCE-ID", "RDATE", "EXDATE"):
        values = event.get(key, [])
        if not isinstance(values, list):
            values = [values]
        for prop in values:
            tzid = prop.params.get("TZID")
            zone = ZoneInfo(valid_zone(str(tzid))) if tzid else ZoneInfo(timezone)
            for value in getattr(prop, "dts", [prop]):
                dt = getattr(value, "dt", None)
                if isinstance(dt, datetime) and (tzid or dt.tzinfo is None):
                    # Ignore embedded VTIMEZONE offsets; trusted IANA rules win.
                    value.dt = dt.replace(tzinfo=zone)


def _moment(value: object, timezone: str, due_time: str = "00:00") -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            zone = ZoneInfo(timezone)
            value = value.replace(tzinfo=zone)
        local = value.replace(tzinfo=None)
        local_zone = value.tzinfo
        candidate = value.astimezone(UTC)
        if candidate.astimezone(local_zone).replace(tzinfo=None) != local:
            raise FeedError("nonexistent_calendar_time")
        return candidate
    if isinstance(value, date):
        instant = local_moment(value, due_time, timezone)
        if instant is None:
            raise FeedError("nonexistent_calendar_time")
        return instant
    raise FeedError("invalid_calendar_time")


def _trusted_calendar(content: bytes) -> bytes:
    """Embedded transition rules are unnecessary when meeting TZIDs use IANA."""
    lines = re.sub(rb"\r?\n[ \t]", b"", content).splitlines()
    result: list[bytes] = []
    inside = False
    for line in lines:
        marker = line.strip().upper()
        if marker == b"BEGIN:VTIMEZONE":
            if inside:
                raise FeedError("invalid_calendar")
            inside = True
        elif marker == b"END:VTIMEZONE":
            if not inside:
                raise FeedError("invalid_calendar")
            inside = False
        elif not inside:
            result.append(line)
    if inside:
        raise FeedError("invalid_calendar")
    return b"\r\n".join(result)


def parse_feed(
    content: bytes,
    *,
    timezone: str,
    all_day_due_time: str,
    now: datetime,
    private_url: str,
    platform: Platform = "other",
) -> ParsedFeed:
    """Pure normalization; caller supplies a bounded source and trusted clock."""
    valid_zone(timezone)
    wall_time(all_day_due_time)
    if not 1 <= len(content) <= MAX_FEED_BYTES or b"\x00" in content:
        raise FeedError("invalid_calendar")
    window_start = (now.astimezone(UTC) - timedelta(days=30)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window_end = window_start + timedelta(days=397)
    try:
        calendar = Calendar.from_ical(_trusted_calendar(content))
        events = calendar.walk("VEVENT")
        if len(events) > 1_000:
            raise FeedError("too_many_calendar_events")
        recurring_uids: set[str] = set()
        seen: set[tuple[str, str]] = set()
        for event in events:
            if getattr(event, "errors", None):
                raise FeedError("invalid_calendar")
            uid = str(_property(event, "UID") or "").strip()
            if not uid or len(uid) > 512 or any(ord(c) < 32 for c in uid):
                raise FeedError("invalid_calendar_uid")
            recurrence = _property(event, "RECURRENCE-ID")
            identity = (uid, str(getattr(recurrence, "dt", "")))
            if identity in seen:
                raise FeedError("duplicate_calendar_uid")
            seen.add(identity)
            if _property(event, "DTSTART") is None:
                raise FeedError("invalid_calendar_time")
            for key in (
                "SUMMARY",
                "DESCRIPTION",
                "LOCATION",
                "URL",
                "DTEND",
                "DURATION",
                "LAST-MODIFIED",
                "RRULE",
            ):
                _property(event, key)
            rule = event.get("RRULE")
            if rule:
                recurring_uids.add(uid)
                frequency = str(rule.get("FREQ", [""])[0])
                if frequency not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
                    raise FeedError("unsupported_recurrence_frequency")
                if {"BYSECOND", "BYMINUTE", "BYHOUR"} & set(rule):
                    raise FeedError("unsupported_recurrence_frequency")
                if len(rule.to_ical()) > 1_024:
                    raise FeedError("invalid_recurrence")
            if event.get("RDATE") or recurrence is not None:
                recurring_uids.add(uid)
            _fix_dates(event, timezone)
        calendar.subcomponents = [e for e in calendar.subcomponents if e.name == "VEVENT"]
        calendar["X-WR-TIMEZONE"] = timezone
        product = str(calendar.get("PRODID", "")).lower()
        domain = feed_domain(private_url)
        for name, signals in (
            ("canvas", ("canvas", "instructure")),
            ("moodle", ("moodle",)),
            ("brightspace", ("brightspace", "desire2learn", "d2l")),
            ("blackboard", ("blackboard",)),
        ):
            if any(signal in f"{domain} {product}" for signal in signals):
                platform = name  # type: ignore[assignment]
                break
        items: list[FeedItem] = []
        keys: set[tuple[str, str]] = set()
        query = recurring_ical_events.of(calendar, components=("VEVENT",), skip_bad_series=False)
        for event in query.after(window_start):
            start_value = getattr(event.get("DTSTART"), "dt", None)
            start = _moment(start_value, timezone)
            if start >= window_end:
                break
            uid = str(event.get("UID", ""))
            recurrence_id = getattr(event.get("RECURRENCE-ID"), "dt", None)
            key = (
                _moment(recurrence_id, timezone).isoformat()
                if uid in recurring_uids and recurrence_id is not None
                else ""
            )
            if uid in recurring_uids and not key:
                key = start.isoformat()
            if (uid, key) in keys:
                raise FeedError("duplicate_calendar_occurrence")
            keys.add((uid, key))
            raw_title = _text(event.get("SUMMARY"), 300, private_url)
            if not raw_title:
                raise FeedError("missing_calendar_title")
            description = _text(event.get("DESCRIPTION"), 2_000, private_url)
            category = event.get("CATEGORIES")
            categories = (
                getattr(category, "cats", [])
                if not isinstance(category, list)
                else [cat for part in category for cat in getattr(part, "cats", [])]
            )
            hint_match = re.search(
                r"\[([^\]]{1,120})\]|\b([A-Z]{2,8}\s?\d{3,5}(?:[- ]\d{1,3})?)\b", raw_title
            )
            hint = (
                _text(categories[0], 120, private_url)
                if categories
                else (hint_match[1] or hint_match[2] if hint_match else None)
            )
            title = raw_title.replace(f"[{hint}]", "", 1).strip() if hint else raw_title
            end_value = getattr(event.get("DTEND"), "dt", start_value)
            end = _moment(end_value, timezone)
            all_day = isinstance(start_value, date) and not isinstance(start_value, datetime)
            kind: Literal["assignment", "event", "exam"] = (
                "exam"
                if re.search(r"\b(exam|prelim|midterm|final)\b", title, re.I)
                else "assignment"
                if all_day
                or end == start
                or re.search(
                    r"\b(due|assignment|homework|problem set|pset)\b",
                    f"{uid} {title}",
                    re.I,
                )
                else "event"
            )
            updated = getattr(event.get("LAST-MODIFIED"), "dt", None)
            if updated is not None and not isinstance(updated, datetime):
                raise FeedError("invalid_calendar_time")
            source = safe_source_link(str(event.get("URL", "")), private_url=private_url)
            items.append(
                FeedItem(
                    # UID is stable identity, not user-facing source text. A
                    # publisher can put the subscription password in a UID.
                    external_id="uid:" + hashlib.sha256(uid.encode()).hexdigest(),
                    occurrence_id=key,
                    title=title or raw_title,
                    kind=kind,
                    starts_at=start if kind != "assignment" else None,
                    due_at=_moment(start_value, timezone, all_day_due_time)
                    if kind in {"assignment", "exam"}
                    else None,
                    ends_at=end,
                    all_day=all_day,
                    course_hint=hint,
                    details=description,
                    source_url=source,
                    location=_text(event.get("LOCATION"), 256, private_url) or None,
                    source_updated_at=_moment(updated, timezone) if updated else None,
                    cancelled=str(event.get("STATUS", "")).upper() == "CANCELLED",
                )
            )
            if len(items) > MAX_ITEMS:
                raise FeedError("too_many_calendar_occurrences")
        hints = sorted({item.course_hint for item in items if item.course_hint})
        if len(hints) > 100:
            raise FeedError("too_many_courses")
        return ParsedFeed(
            platform=platform,
            items=items,
            course_hints=hints,
            all_day_count=sum(item.all_day for item in items),
            window_start=window_start,
            window_end=window_end,
        )
    except FeedError:
        raise
    except Exception:
        # Parser exceptions often contain the private source line.
        raise FeedError("invalid_calendar") from None


def item_key(item: FeedItem) -> str:
    return hashlib.sha256(f"{item.external_id}\x00{item.occurrence_id}".encode()).hexdigest()


def item_hash(item: FeedItem) -> str:
    return hashlib.sha256(item.model_dump_json().encode()).hexdigest()
