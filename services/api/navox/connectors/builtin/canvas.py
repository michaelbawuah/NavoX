from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import UUID

import httpx
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

CANVAS_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": "canvas-lms",
        "version": "1.0.0",
        "displayName": "Canvas LMS",
        "category": "education",
        "connectorClass": "TOKEN_API",
        "auth": [
            {
                "kind": "api_token",
                "label": "Canvas access token",
                "scopes": [],
            }
        ],
        "resourceTypes": [
            "academic.course",
            "academic.assignment",
            "academic.announcement",
            "calendar.event",
        ],
        "capabilities": {
            "read": [
                {
                    "name": "academic.courses.read",
                    "description": "Read active Canvas courses",
                    "sensitive": False,
                },
                {
                    "name": "academic.assignments.read",
                    "description": "Read assignments and due dates",
                    "sensitive": True,
                },
                {
                    "name": "academic.submissions.read",
                    "description": "Read the current user's submission state",
                    "sensitive": True,
                },
                {
                    "name": "academic.announcements.read",
                    "description": "Read course announcements",
                    "sensitive": True,
                },
                {
                    "name": "calendar.events.read",
                    "description": "Read Canvas calendar context",
                    "sensitive": True,
                },
            ],
            "write": [],
            "events": [],
            "incrementalSync": False,
        },
        "requiredSecrets": ["CANVAS_API_TOKEN"],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _plain_text(value: object, limit: int = 32_000) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _HTMLText()
    try:
        parser.feed(value)
        text = " ".join(parser.parts) if parser.parts else unescape(value)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
    normalized = " ".join(text.split())
    return normalized[:limit] if normalized else None


def validate_canvas_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Canvas base_url is required")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Canvas base_url must be a clean HTTPS origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        raise ValueError("Canvas base_url cannot target a private or reserved address")
    path = parsed.path.rstrip("/")
    if path:
        raise ValueError("Canvas base_url must not include an API path")
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{parsed.hostname}{port}"


class CanvasConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = validate_canvas_base_url(config.get("base_url"))
        self.secrets = secrets
        self.transport = transport

    def get_manifest(self) -> ConnectorManifest:
        return CANVAS_MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        return AuthorizationResult(authorized=False)

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        del connection
        try:
            async with self._client() as client:
                await self._request(
                    client,
                    f"{self.base_url}/api/v1/courses",
                    params={"per_page": "1", "enrollment_state": "active"},
                )
            return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))
        except ConnectorRuntimeError as error:
            state = {
                "AUTH_EXPIRED": "AUTH_EXPIRED",
                "RATE_LIMITED": "RATE_LIMITED",
                "PROVIDER_UNAVAILABLE": "DEGRADED",
            }.get(error.code, "DEGRADED")
            return ConnectorHealth(
                state=state,
                checked_at=datetime.now(UTC),
                reason_code=error.code,
            )

    async def sync(self, request: SyncRequest) -> SyncPage:
        required = {
            "academic.courses.read",
            "academic.assignments.read",
            "academic.announcements.read",
            "calendar.events.read",
        }
        if not required & set(request.capabilities):
            raise ConnectorRuntimeError(
                "UNSUPPORTED_CAPABILITY",
                "No Canvas read capability is authorized",
            )

        async with self._client() as client:
            courses = await self._paginated(
                client,
                f"{self.base_url}/api/v1/courses",
                params={
                    "per_page": "100",
                    "enrollment_state": "active",
                    "state[]": "available",
                },
            )
            resources: list[CanonicalResource] = []
            for course in courses:
                course_id = _identifier(course.get("id"))
                if course_id is None:
                    continue
                resources.append(self._course_resource(request, course, course_id))

                if "academic.assignments.read" in request.capabilities:
                    assignments = await self._paginated(
                        client,
                        f"{self.base_url}/api/v1/courses/{course_id}/assignments",
                        params={
                            "per_page": "100",
                            "include[]": "submission",
                            "order_by": "due_at",
                        },
                    )
                    resources.extend(
                        self._assignment_resource(request, item, course_id)
                        for item in assignments
                        if _identifier(item.get("id")) is not None
                    )

            course_codes = [
                f"course_{course_id}"
                for course in courses
                if (course_id := _identifier(course.get("id"))) is not None
            ]
            if course_codes and "academic.announcements.read" in request.capabilities:
                for chunk in _chunks(course_codes, 10):
                    announcements = await self._paginated(
                        client,
                        f"{self.base_url}/api/v1/announcements",
                        params=[("per_page", "100"), *[("context_codes[]", code) for code in chunk]],
                    )
                    resources.extend(
                        self._announcement_resource(request, item)
                        for item in announcements
                        if _identifier(item.get("id")) is not None
                    )

            if course_codes and "calendar.events.read" in request.capabilities:
                now = datetime.now(UTC)
                for chunk in _chunks(course_codes, 10):
                    calendar = await self._paginated(
                        client,
                        f"{self.base_url}/api/v1/calendar_events",
                        params=[
                            ("per_page", "100"),
                            ("type", "event"),
                            ("start_date", (now - timedelta(days=30)).date().isoformat()),
                            ("end_date", (now + timedelta(days=180)).date().isoformat()),
                            *[("context_codes[]", code) for code in chunk],
                        ],
                    )
                    resources.extend(
                        self._calendar_resource(request, item)
                        for item in calendar
                        if _identifier(item.get("id")) is not None
                    )

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
        return SyncPage(
            resources=selected,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        page = await self.sync(
            SyncRequest(
                connection_id=request.connection_id,
                workspace_id=request.workspace_id,
                limit=1_000,
                capabilities=frozenset(
                    {
                        "academic.courses.read",
                        "academic.assignments.read",
                        "academic.submissions.read",
                        "academic.announcements.read",
                        "calendar.events.read",
                    }
                ),
            )
        )
        for resource in page.resources:
            if (
                resource.resource_type == request.resource_type
                and resource.external_id == request.external_id
            ):
                return resource
        raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Canvas resource was not found")

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY",
            "Canvas is read-only in SPEC-003",
        )

    def _client(self) -> httpx.AsyncClient:
        token = self._token()
        return httpx.AsyncClient(
            transport=self.transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30.0,
            follow_redirects=False,
        )

    def _token(self) -> str:
        if self.secrets is None or "CANVAS_API_TOKEN" not in self.secrets.names:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Canvas token is unavailable")
        return self.secrets.get("CANVAS_API_TOKEN")

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str] | list[tuple[str, str]] | None = None,
    ) -> httpx.Response:
        self._validate_request_url(url)
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError:
            raise ConnectorRuntimeError(
                "PROVIDER_UNAVAILABLE",
                "Canvas request failed",
            ) from None
        if response.status_code == 401:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Canvas authorization expired")
        if response.status_code == 403:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Canvas permission denied")
        if response.status_code == 404:
            raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Canvas resource not found")
        if response.status_code == 429:
            raise ConnectorRuntimeError("RATE_LIMITED", "Canvas rate limit reached")
        if response.status_code >= 500:
            raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "Canvas is unavailable")
        if response.status_code >= 400:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                "Canvas returned an unexpected response",
            )
        return response

    async def _paginated(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str] | list[tuple[str, str]] | None = None,
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        next_url: str | None = url
        next_params = params
        for _ in range(100):
            if next_url is None:
                break
            response = await self._request(client, next_url, params=next_params)
            next_params = None
            try:
                payload = response.json()
            except ValueError:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE",
                    "Canvas returned invalid JSON",
                ) from None
            if not isinstance(payload, list):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE",
                    "Canvas list endpoint returned a non-list payload",
                )
            items.extend(item for item in payload if isinstance(item, dict))
            if len(items) > 10_000:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE",
                    "Canvas response exceeded the bounded item limit",
                )
            link = response.links.get("next")
            next_url = str(link["url"]) if link and link.get("url") else None
            if next_url is not None:
                self._validate_request_url(next_url)
        else:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                "Canvas pagination exceeded the bounded page limit",
            )
        return items

    def _validate_request_url(self, url: str) -> None:
        base = urlsplit(self.base_url)
        candidate = urlsplit(url)
        if (
            candidate.scheme != "https"
            or candidate.hostname != base.hostname
            or candidate.port != base.port
        ):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE",
                "Canvas pagination attempted to leave the configured origin",
            )

    def _course_resource(
        self,
        request: SyncRequest,
        course: dict[str, object],
        course_id: str,
    ) -> CanonicalResource:
        name = _text(course.get("name")) or _text(course.get("course_code")) or f"Course {course_id}"
        occurred = _time(course.get("start_at")) or datetime.now(UTC)
        return _resource(
            request,
            provider="canvas",
            resource_type="academic.course",
            external_id=f"course:{course_id}",
            subject=name,
            content=None,
            occurred_at=occurred,
            source_url=_safe_url(course.get("calendar", {}), "ics"),
            metadata={
                "course_id": course_id,
                "course_code": _text(course.get("course_code")),
                "workflow_state": _text(course.get("workflow_state")),
            },
        )

    def _assignment_resource(
        self,
        request: SyncRequest,
        assignment: dict[str, object],
        course_id: str,
    ) -> CanonicalResource:
        assignment_id = _identifier(assignment.get("id"))
        if assignment_id is None:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE")
        due_at = _text(assignment.get("due_at"))
        updated = _time(assignment.get("updated_at")) or _time(assignment.get("created_at"))
        description = _plain_text(assignment.get("description"))
        submission = assignment.get("submission")
        submission = submission if isinstance(submission, dict) else {}
        state = _text(submission.get("workflow_state"))
        pieces = [description]
        if due_at:
            pieces.append(f"Due: {due_at}")
        if state:
            pieces.append(f"Submission status: {state}")
        content = "\n".join(piece for piece in pieces if piece)
        return _resource(
            request,
            provider="canvas",
            resource_type="academic.assignment",
            external_id=f"course:{course_id}:assignment:{assignment_id}",
            external_parent_id=f"course:{course_id}",
            subject=_text(assignment.get("name")) or f"Assignment {assignment_id}",
            content=content or None,
            occurred_at=updated or _time(assignment.get("due_at")) or datetime.now(UTC),
            source_url=_safe_direct_url(assignment.get("html_url")),
            metadata={
                "course_id": course_id,
                "assignment_id": assignment_id,
                "due_at": due_at,
                "published": bool(assignment.get("published", True)),
                "submission_state": state,
                "submitted_at": _text(submission.get("submitted_at")),
                "late": bool(submission.get("late", False)),
                "missing": bool(submission.get("missing", False)),
            },
        )

    def _announcement_resource(
        self,
        request: SyncRequest,
        announcement: dict[str, object],
    ) -> CanonicalResource:
        announcement_id = _identifier(announcement.get("id"))
        if announcement_id is None:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE")
        return _resource(
            request,
            provider="canvas",
            resource_type="academic.announcement",
            external_id=f"announcement:{announcement_id}",
            external_parent_id=_text(announcement.get("context_code")),
            subject=_text(announcement.get("title")) or f"Announcement {announcement_id}",
            content=_plain_text(announcement.get("message")),
            occurred_at=(
                _time(announcement.get("posted_at"))
                or _time(announcement.get("created_at"))
                or datetime.now(UTC)
            ),
            source_url=_safe_direct_url(announcement.get("html_url")),
            metadata={
                "context_code": _text(announcement.get("context_code")),
                "read_state": _text(announcement.get("read_state")),
            },
        )

    def _calendar_resource(
        self,
        request: SyncRequest,
        event: dict[str, object],
    ) -> CanonicalResource:
        event_id = _identifier(event.get("id"))
        if event_id is None:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE")
        return _resource(
            request,
            provider="canvas",
            resource_type="calendar.event",
            external_id=f"event:{event_id}",
            external_parent_id=_text(event.get("context_code")),
            subject=_text(event.get("title")) or f"Calendar event {event_id}",
            content=_plain_text(event.get("description")),
            occurred_at=_time(event.get("start_at")) or datetime.now(UTC),
            source_url=_safe_direct_url(event.get("html_url")),
            metadata={
                "context_code": _text(event.get("context_code")),
                "start_at": _text(event.get("start_at")),
                "end_at": _text(event.get("end_at")),
                "all_day": bool(event.get("all_day", False)),
                "location_name": _text(event.get("location_name")),
            },
        )


def _resource(
    request: SyncRequest,
    *,
    provider: str,
    resource_type: str,
    external_id: str,
    subject: str,
    content: str | None,
    occurred_at: datetime,
    metadata: dict[str, JsonValue],
    external_parent_id: str | None = None,
    source_url: str | None = None,
) -> CanonicalResource:
    return CanonicalResource(
        resource_id=stable_resource_id(request.connection_id, resource_type, external_id),
        workspace_id=request.workspace_id,
        connector_connection_id=request.connection_id,
        provider=provider,
        resource_type=resource_type,
        external_id=external_id,
        external_parent_id=external_parent_id,
        canonical={
            "source_type": resource_type,
            "subject": subject,
            "content": content,
            "occurred_at": occurred_at.isoformat(),
            "status": "active",
            "metadata": metadata,
        },
        provider_metadata={},
        source_url=source_url,
        updated_at=occurred_at,
        retrieved_at=datetime.now(UTC),
    )


def _identifier(value: object) -> str | None:
    if isinstance(value, int | str) and str(value).strip():
        return str(value).strip()
    return None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized[:2_000] if normalized else None


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _safe_direct_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    return value[:2_000]


def _safe_url(value: object, key: str) -> str | None:
    if not isinstance(value, dict):
        return None
    return _safe_direct_url(value.get(key))
