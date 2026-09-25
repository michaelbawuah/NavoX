"""Bounded file normalization. No network, model calls, or persistence."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Literal, NoReturn
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

ImportFormat = Literal["ics", "csv", "json"]
MAX_IMPORT_BYTES = 2_000_000
MAX_IMPORT_RECORDS = 1_000
PARSER_VERSION = "file-import.v1"
IMPORT_CAPABILITIES = {
    "ics": "imports.calendar.read",
    "csv": "imports.tabular.read",
    "json": "imports.json.read",
}


class ImportValidationError(ValueError):
    """Fixed diagnostics only; do not echo source text or parser exceptions."""


class ImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    external_id: str = Field(min_length=1, max_length=512)
    resource_type: str
    title: str = Field(min_length=1, max_length=2_000)
    content: str = Field(max_length=24_000)
    source_url: str | None = None
    source_type: str = "import_record"
    status: Literal["active", "cancelled", "deleted"] = "active"


def reject(message: str) -> NoReturn:
    raise ImportValidationError(message)


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ImportValidationError("Select a valid IANA time zone.") from None


def _local(value: datetime, zone: ZoneInfo) -> datetime:
    possibilities = {
        value.replace(tzinfo=zone, fold=f).astimezone(UTC)
        for f in (0, 1)
        if value.replace(tzinfo=zone, fold=f).astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        == value
    }
    if len(possibilities) != 1:
        reject("A local time is ambiguous or nonexistent; export it with a UTC offset.")
    return possibilities.pop()


def _date(value: str, zone: ZoneInfo) -> str:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = _local(parsed, zone)
        return parsed.astimezone(UTC).isoformat()
    except ImportValidationError:
        raise
    except ValueError:
        raise ImportValidationError("Dates must be ISO 8601 dates or timestamps.") from None


def _text(value: object, limit: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        reject("A text field is invalid or exceeds the supported length.")
    result = value.strip()
    if required and not result:
        reject("Every record needs a nonempty title, subject, or name.")
    return result


def _url(value: object) -> str | None:
    value = _text(value, 2_000)
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(c) < 32 for c in value)
        ):
            reject("Source links must be HTTP(S) URLs without credentials or control characters.")
        _ = parsed.port
    except ValueError:
        raise ImportValidationError("The source link is invalid.") from None
    return value


def _first(row: dict[str, object], *keys: str) -> object:
    return next((row[k] for k in keys if row.get(k) is not None and row[k] != ""), None)


def _record(row: dict[str, object], kind: Literal["csv", "json"], zone: ZoneInfo) -> ImportRecord:
    title = _text(_first(row, "title", "subject", "name"), 2_000, required=True)
    body = _text(_first(row, "content", "description", "body", "notes"), 20_000)
    parts = [body] if body else []
    for key, label in (("due_at", "Due"), ("start_at", "Starts"), ("end_at", "Ends")):
        value = _text(row.get(key), 128)
        if value:
            parts.append(f"{label}: {_date(value, zone)}")
    state = _text(row.get("status"), 64).casefold()
    if state and state not in {"active", "cancelled", "deleted", "completed", "pending"}:
        reject("Status must be active, pending, completed, cancelled, or deleted.")
    if state:
        parts.append(f"Status: {state}")
    source_url = _url(_first(row, "source_url", "url"))
    identifier = _first(row, "id", "uid", "external_id")
    if isinstance(identifier, int) and not isinstance(identifier, bool):
        identifier = str(identifier)
    identifier = _text(identifier, 480)
    if not identifier:
        identifier = hashlib.sha256(
            json.dumps([title, parts, state, source_url], ensure_ascii=False).encode()
        ).hexdigest()
    return ImportRecord(
        external_id=f"{kind}:{identifier}",
        resource_type="import.csv_row" if kind == "csv" else "import.json_item",
        title=title,
        content="\n".join(parts),
        source_url=source_url,
        status="cancelled"
        if state == "cancelled"
        else "deleted"
        if state == "deleted"
        else "active",
    )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            reject("JSON contains duplicate object keys.")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    del value
    reject("JSON cannot contain NaN or Infinity.")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        reject("JSON numbers must be finite.")
    return number


def _json(content: str, zone: ZoneInfo) -> list[ImportRecord]:
    try:
        parsed = json.loads(
            content, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float
        )
    except ImportValidationError:
        raise
    except (ValueError, RecursionError, UnicodeError):
        raise ImportValidationError("The JSON document is invalid.") from None
    if isinstance(parsed, dict) and "items" in parsed:
        parsed = parsed["items"]
    elif isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or len(parsed) > MAX_IMPORT_RECORDS:
        reject("JSON must contain at most 1,000 record objects.")
    result: list[ImportRecord] = []
    for row in parsed:
        if not isinstance(row, dict) or len(row) > 64:
            reject("Each JSON record must be an object with at most 64 fields.")
        result.append(_record(row, "json", zone))
    return result


def _csv(content: str, zone: ZoneInfo) -> list[ImportRecord]:
    try:
        reader = csv.reader(io.StringIO(content, newline=""), strict=True)
        headers = [v.strip() for v in next(reader, [])]
        if (
            not headers
            or len(headers) > 64
            or len(set(headers)) != len(headers)
            or not all(headers)
        ):
            reject("CSV needs 1–64 unique, nonempty column names.")
        if not {"title", "subject", "name"} & set(headers):
            reject("CSV needs a title, subject, or name column.")
        result: list[ImportRecord] = []
        for cells in reader:
            if not cells:
                continue
            if len(cells) != len(headers):
                reject("A CSV row has a different number of fields from its header.")
            if len(result) >= MAX_IMPORT_RECORDS:
                reject("CSV can contain at most 1,000 records.")
            result.append(_record(dict(zip(headers, cells, strict=True)), "csv", zone))
        return result
    except csv.Error:
        raise ImportValidationError("The CSV document is invalid.") from None


def _unescape(value: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m[1] in {"n", "N"} else m[1], value)


def _ical_time(params: dict[str, str], value: str, zone: ZoneInfo) -> tuple[str, bool]:
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            if params.get("TZID"):
                reject("All-day dates cannot include TZID.")
            return datetime.strptime(value, "%Y%m%d").date().isoformat(), True
        if value.endswith("Z"):
            if params.get("TZID"):
                reject("A UTC event time cannot also include TZID.")
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat(), False
        selected = _zone(params["TZID"]) if "TZID" in params else zone
        return _local(datetime.strptime(value, "%Y%m%dT%H%M%S"), selected).isoformat(), False
    except ImportValidationError:
        raise
    except ValueError:
        raise ImportValidationError("An ICS date or timestamp is invalid.") from None


def _event(fields: dict[str, tuple[dict[str, str], str]], zone: ZoneInfo) -> ImportRecord:
    if not {"UID", "DTSTART", "SUMMARY"}.issubset(fields):
        reject("Each ICS event needs UID, DTSTART, and SUMMARY.")
    if {"RRULE", "RDATE", "EXDATE", "RECURRENCE-ID", "DURATION"} & fields.keys():
        reject(
            "Recurring events and DURATION are not supported yet; "
            "export individual events with DTEND."
        )
    uid = _text(_unescape(fields["UID"][1]), 480, required=True)
    title = _text(_unescape(fields["SUMMARY"][1]), 2_000, required=True)
    start, all_day = _ical_time(*fields["DTSTART"], zone)
    if "DTEND" in fields:
        end, end_all_day = _ical_time(*fields["DTEND"], zone)
        if end_all_day != all_day or end <= start:
            reject("DTEND must have the same date type and be after DTSTART.")
    else:
        end = (date.fromisoformat(start) + timedelta(days=1)).isoformat() if all_day else start
    description = _text(_unescape(fields.get("DESCRIPTION", ({}, ""))[1]), 20_000)
    state = fields.get("STATUS", ({}, "CONFIRMED"))[1].upper()
    if state not in {"CONFIRMED", "TENTATIVE", "CANCELLED"}:
        reject("ICS event status is invalid.")
    lines = [description] if description else []
    lines += [f"Event starts: {start}", f"Event ends (exclusive): {end}"]
    if all_day:
        lines.append("All-day event; no time of day was specified.")
    lines.append(f"Event status: {state.lower()}")
    return ImportRecord(
        external_id=f"ics:{uid}",
        resource_type="import.calendar_event",
        title=title,
        content="\n".join(lines),
        source_type="calendar_event",
        source_url=_url(fields.get("URL", ({}, ""))[1]),
        status="cancelled" if state == "CANCELLED" else "active",
    )


def _ics(content: str, zone: ZoneInfo) -> list[ImportRecord]:
    stack: list[str] = []
    fields: dict[str, tuple[dict[str, str], str]] = {}
    result: list[ImportRecord] = []
    seen_calendar = False
    for line in re.sub(r"\r?\n[ \t]", "", content).splitlines():
        if not line.strip():
            continue
        if len(line) > 24_000:
            reject("An ICS property exceeds the supported length.")
        if ":" not in line:
            reject("ICS contains an invalid content line.")
        head, value = line.split(":", 1)
        key, *parameters = head.split(";")
        key = key.upper()
        if key == "BEGIN":
            kind = value.upper()
            allowed = (
                (not stack and not seen_calendar and kind == "VCALENDAR")
                or (stack == ["VCALENDAR"] and kind in {"VEVENT", "VTIMEZONE"})
                or (stack == ["VCALENDAR", "VEVENT"] and kind == "VALARM")
                or (stack == ["VCALENDAR", "VTIMEZONE"] and kind in {"STANDARD", "DAYLIGHT"})
            )
            if not allowed:
                reject("Unsupported or incorrectly nested ICS component.")
            stack.append(kind)
            seen_calendar |= kind == "VCALENDAR"
            if kind == "VEVENT":
                fields = {}
        elif key == "END":
            if not stack or stack[-1] != value.upper():
                reject("ICS component boundaries do not match.")
            if stack[-1] == "VEVENT":
                if len(result) >= MAX_IMPORT_RECORDS:
                    reject("ICS can contain at most 1,000 events.")
                result.append(_event(fields, zone))
            stack.pop()
        elif not stack:
            reject("ICS content must be inside VCALENDAR.")
        elif stack == ["VCALENDAR", "VEVENT"]:
            if key in fields and key in {
                "UID",
                "SUMMARY",
                "DESCRIPTION",
                "DTSTART",
                "DTEND",
                "STATUS",
                "URL",
            }:
                reject("An ICS event contains a duplicate singleton property.")
            params = {}
            for part in parameters:
                name, equal, parameter = part.partition("=")
                if not equal or name.upper() in params:
                    reject("An ICS property parameter is invalid.")
                params[name.upper()] = parameter.strip('"')
            fields[key] = (params, value)
    if stack or not seen_calendar:
        reject("ICS must be a complete VCALENDAR document.")
    return result


def parse_import(content: str, format_name: ImportFormat, timezone_name: str) -> list[ImportRecord]:
    try:
        size = len(content.encode("utf-8"))
    except UnicodeError:
        raise ImportValidationError("Import text must be valid UTF-8.") from None
    if not 1 <= size <= MAX_IMPORT_BYTES or "\x00" in content:
        reject("Import a UTF-8 text file no larger than 2 MB.")
    parser = {"csv": _csv, "json": _json, "ics": _ics}[format_name]
    records = parser(content.removeprefix("\ufeff"), _zone(timezone_name))
    if not records:
        reject("The file contains no supported records.")
    if len({r.external_id for r in records}) != len(records):
        reject("Records must have distinct IDs; duplicate records need explicit unique IDs.")
    return records


def import_digest(content: str, format_name: ImportFormat, timezone_name: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [PARSER_VERSION, format_name, timezone_name, content], ensure_ascii=False
        ).encode()
    ).hexdigest()
