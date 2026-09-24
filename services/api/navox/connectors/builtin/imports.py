from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from hashlib import sha256
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import JsonValue

from navox.connectors.contracts import (
    AuthorizationRequest,
    AuthorizationResult,
    CanonicalResource,
    ConnectorActionRequest,
    ConnectorActionResult,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SecretAccessor,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)

MAX_IMPORT_BYTES = 2_000_000
MAX_IMPORT_ITEMS = 5_000
MAX_IMPORT_FIELDS = 128

IMPORT_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": "generic-import",
        "version": "1.0.0",
        "displayName": "File Import",
        "category": "data",
        "connectorClass": "IMPORT",
        "auth": [{"kind": "none", "label": "No authentication", "scopes": []}],
        "resourceTypes": [
            "import.calendar_event",
            "import.csv_row",
            "import.json_item",
        ],
        "capabilities": {
            "read": [
                {
                    "name": "imports.calendar.read",
                    "description": "Read validated ICS events",
                    "sensitive": True,
                },
                {
                    "name": "imports.tabular.read",
                    "description": "Read validated CSV rows",
                    "sensitive": True,
                },
                {
                    "name": "imports.json.read",
                    "description": "Read validated JSON items",
                    "sensitive": True,
                },
            ],
            "write": [],
            "events": [],
            "incrementalSync": False,
        },
        "requiredSecrets": [],
        "rateLimitStrategy": "none",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


class ImportConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
    ) -> None:
        del secrets
        self.format = str(config.get("format", "")).casefold()
        self.content = config.get("content")
        self.source_name = str(config.get("source_name", "Imported data"))[:256]
        if self.format not in {"ics", "csv", "json"}:
            raise ValueError("Import format must be ics, csv, or json")
        if not isinstance(self.content, str):
            raise ValueError("Import content must be text")
        if len(self.content.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError("Import exceeds the bounded size limit")

    def get_manifest(self) -> ConnectorManifest:
        return IMPORT_MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        return AuthorizationResult(
            authorized=True,
            external_account_id=f"import:{context.workspace_id}",
            granted_capabilities=context.requested_capabilities,
        )

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        del connection
        try:
            self._resources(
                UUID_ZERO,
                UUID_ZERO,
            )
        except (ValueError, ConnectorRuntimeError):
            return ConnectorHealth(
                state="DEGRADED",
                checked_at=datetime.now(UTC),
                reason_code="INVALID_PROVIDER_RESPONSE",
            )
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        resources = self._resources(request.connection_id, request.workspace_id)
        resources.sort(key=lambda item: (item.resource_type, item.external_id))
        after = request.cursor or ""
        pending = [
            item
            for item in resources
            if f"{item.resource_type}\x00{item.external_id}" > after
        ]
        selected = pending[: request.limit]
        has_more = len(pending) > len(selected)
        next_cursor = (
            f"{selected[-1].resource_type}\x00{selected[-1].external_id}"
            if has_more and selected
            else None
        )
        return SyncPage(resources=selected, next_cursor=next_cursor, has_more=has_more)

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        resources = self._resources(request.connection_id, request.workspace_id)
        for resource in resources:
            if (
                resource.resource_type == request.resource_type
                and resource.external_id == request.external_id
            ):
                return resource
        raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Imported resource was not found")

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Imports are read-only")

    def _resources(self, connection_id, workspace_id) -> list[CanonicalResource]:
        if self.format == "ics":
            return _ics_resources(self.content, connection_id, workspace_id)
        if self.format == "csv":
            return _csv_resources(self.content, connection_id, workspace_id)
        return _json_resources(self.content, connection_id, workspace_id)


def _ics_resources(content: str, connection_id, workspace_id) -> list[CanonicalResource]:
    unfolded = re.sub(r"\r?\n[ \t]", "", content)
    events = re.findall(
        r"BEGIN:VEVENT\r?\n(.*?)\r?\nEND:VEVENT",
        unfolded,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if len(events) > MAX_IMPORT_ITEMS:
        raise ValueError("ICS import exceeds the bounded event limit")
    resources: list[CanonicalResource] = []
    for index, block in enumerate(events):
        fields: dict[str, tuple[str, str]] = {}
        for raw_line in block.splitlines():
            if ":" not in raw_line:
                continue
            head, value = raw_line.split(":", 1)
            key, _, params = head.partition(";")
            fields[key.upper()] = (params, value.strip())
        uid = fields.get("UID", ("", ""))[1] or _digest(block)
        summary = fields.get("SUMMARY", ("", f"Calendar event {index + 1}"))[1]
        description = fields.get("DESCRIPTION", ("", ""))[1].replace("\\n", "\n")
        start = _ics_datetime(*fields.get("DTSTART", ("", ""))) or datetime.now(UTC)
        end = _ics_datetime(*fields.get("DTEND", ("", "")))
        due = _ics_datetime(*fields.get("DUE", ("", "")))
        url = _safe_url(fields.get("URL", ("", ""))[1])
        status = fields.get("STATUS", ("", "CONFIRMED"))[1].casefold()
        resources.append(
            _resource(
                connection_id,
                workspace_id,
                "import.calendar_event",
                f"ics:{uid}",
                subject=summary,
                content=description or None,
                occurred_at=start,
                source_url=url,
                source_type="calendar_event",
                metadata={
                    "end_at": end.isoformat() if end else None,
                    "due_at": due.isoformat() if due else None,
                    "status": "cancelled" if status == "cancelled" else "active",
                    "import_format": "ics",
                },
            )
        )
    return resources


def _csv_resources(content: str, connection_id, workspace_id) -> list[CanonicalResource]:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or len(reader.fieldnames) > MAX_IMPORT_FIELDS:
        raise ValueError("CSV must have a bounded header row")
    resources: list[CanonicalResource] = []
    for index, row in enumerate(reader):
        if index >= MAX_IMPORT_ITEMS:
            raise ValueError("CSV import exceeds the bounded row limit")
        normalized = {
            str(key)[:128]: (value[:4_000] if isinstance(value, str) else "")
            for key, value in row.items()
            if key is not None
        }
        external = _first(normalized, "id", "uid", "external_id") or f"row:{index + 1}"
        subject = _first(normalized, "title", "subject", "name") or f"CSV row {index + 1}"
        content_text = _first(normalized, "content", "description", "body", "notes")
        occurred = _record_time(normalized) or datetime.now(UTC)
        resources.append(
            _resource(
                connection_id,
                workspace_id,
                "import.csv_row",
                f"csv:{external}",
                subject=subject,
                content=content_text,
                occurred_at=occurred,
                source_url=_safe_url(_first(normalized, "url", "source_url")),
                source_type="import_record",
                metadata={
                    "fields": normalized,
                    "import_format": "csv",
                },
            )
        )
    return resources


def _json_resources(content: str, connection_id, workspace_id) -> list[CanonicalResource]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError("JSON import is invalid") from error
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items = payload["items"]
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise ValueError("JSON import must contain an object or list")
    if len(items) > MAX_IMPORT_ITEMS:
        raise ValueError("JSON import exceeds the bounded item limit")

    resources: list[CanonicalResource] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or len(item) > MAX_IMPORT_FIELDS:
            raise ValueError("JSON items must be bounded objects")
        safe = _bounded_json_object(item)
        external = _first(safe, "id", "uid", "external_id") or _digest(
            json.dumps(safe, sort_keys=True, separators=(",", ":"))
        )
        subject = _first(safe, "title", "subject", "name") or f"JSON item {index + 1}"
        content_text = _first(safe, "content", "description", "body", "notes")
        occurred = _record_time(safe) or datetime.now(UTC)
        resources.append(
            _resource(
                connection_id,
                workspace_id,
                "import.json_item",
                f"json:{external}",
                subject=subject,
                content=content_text,
                occurred_at=occurred,
                source_url=_safe_url(_first(safe, "url", "source_url")),
                source_type="import_record",
                metadata={"fields": safe, "import_format": "json"},
            )
        )
    return resources


def _resource(
    connection_id,
    workspace_id,
    resource_type: str,
    external_id: str,
    *,
    subject: str,
    content: str | None,
    occurred_at: datetime,
    source_type: str,
    metadata: dict[str, JsonValue],
    source_url: str | None,
) -> CanonicalResource:
    status = metadata.get("status", "active")
    return CanonicalResource(
        resource_id=stable_resource_id(connection_id, resource_type, external_id),
        workspace_id=workspace_id,
        connector_connection_id=connection_id,
        provider="import",
        resource_type=resource_type,
        external_id=external_id,
        canonical={
            "source_type": source_type,
            "subject": subject[:2_000],
            "content": content[:32_000] if content else None,
            "occurred_at": occurred_at.isoformat(),
            "status": status if isinstance(status, str) else "active",
            "metadata": metadata,
        },
        provider_metadata={"source_name": "user_import"},
        source_url=source_url,
        updated_at=occurred_at,
        retrieved_at=datetime.now(UTC),
    )


def _bounded_json_object(value: dict[object, object]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)[:128]
        result[key] = _bounded_json_value(raw_value, depth=0)
    return result


def _bounded_json_value(value: object, *, depth: int) -> JsonValue:
    if depth > 4:
        return "[depth-limited]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:4_000]
    if isinstance(value, list):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bounded_json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:MAX_IMPORT_FIELDS]
        }
    return str(value)[:4_000]


def _first(mapping: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int | float):
            return str(value)
    return None


def _record_time(mapping: Mapping[str, object]) -> datetime | None:
    for key in ("occurred_at", "updated_at", "created_at", "due_at", "date", "timestamp"):
        value = _first(mapping, key)
        if value:
            parsed = _iso_datetime(value)
            if parsed:
                return parsed
    return None


def _iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _ics_datetime(params: str, value: str) -> datetime | None:
    if not value:
        return None
    timezone_name = None
    for parameter in params.split(";"):
        if parameter.upper().startswith("TZID="):
            timezone_name = parameter.split("=", 1)[1]
    try:
        if re.fullmatch(r"\d{8}", value):
            parsed_date = datetime.strptime(value, "%Y%m%d").date()
            return datetime.combine(parsed_date, time.min, tzinfo=UTC)
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
        if timezone_name:
            try:
                return parsed.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(UTC)
            except ZoneInfoNotFoundError:
                return None
        return None
    except ValueError:
        return None


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return None
    return value.strip()[:2_000]


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


UUID_ZERO = __import__("uuid").UUID(int=0)
