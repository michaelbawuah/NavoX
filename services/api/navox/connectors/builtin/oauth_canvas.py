"""Read-only, one-provider-page Canvas adapter using approved institutional OAuth."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from navox.connectors import canvas_oauth
from navox.connectors.builtin.canvas import CANVAS_MANIFEST, CanvasConnector, _time
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorConnectionContext,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SecretAccessor,
    SyncPage,
    SyncRequest,
)
from navox.connectors.outbound import validate_target
from navox.core.settings import Settings

_manifest = CANVAS_MANIFEST.model_dump(by_alias=True)
_manifest.update(
    version="1.1.0",
    connectorClass="OAUTH_API",
    auth=[
        {
            "kind": "oauth2",
            "label": "Institution-approved Canvas OAuth",
            "scopes": sorted(set(canvas_oauth.CANVAS_SCOPES.values())),
        }
    ],
    requiredSecrets=["CANVAS_REFRESH_TOKEN"],
)
OAUTH_CANVAS_MANIFEST = ConnectorManifest.model_validate(_manifest)


class CanvasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str
    canvas_user_id: str = Field(pattern=r"^[0-9]{1,32}$")
    deployment_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_id: UUID
    capabilities: list[str]


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["courses", "assignments", "announcements", "calendar"] = "courses"
    courses: list[str] = Field(default_factory=list, max_length=100)
    index: int = Field(default=0, ge=0, le=100)
    next_url: str | None = Field(default=None, max_length=4096)
    # A single scan has a fixed calendar window even across retries.
    window: str


AccessCheck = Callable[[UUID, UUID, CanvasConfig, frozenset[str]], Awaitable[None]]


async def check_access(
    connection_id: UUID, workspace_id: UUID, config: CanvasConfig, capabilities: frozenset[str]
) -> None:
    # Trusted infrastructure: no session is passed to provider code.
    from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
    from navox.db.session import get_session_factory

    async with get_session_factory()() as db:
        try:
            row = await owned_connector(
                db,
                connection_id=connection_id,
                workspace_id=workspace_id,
                user_id=config.owner_id,
                require_active=True,
                lock_connection=False,
            )
            if (
                row.provider != "canvas"
                or row.config != config.model_dump(mode="json")
                or not capabilities.issubset(row.authorized_capabilities)
                or not capabilities.issubset(row.provider_capabilities)
            ):
                raise ConnectorAccessDenied("Canvas access denied")
        except ConnectorAccessDenied:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Canvas access denied") from None
        finally:
            await db.rollback()


class OAuthCanvasConnector(CanvasConnector):
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        settings: Settings,
        access_check: AccessCheck = check_access,
    ) -> None:
        self.config = CanvasConfig.model_validate(config)
        self.deployment = canvas_oauth.deployment(settings)
        if (
            self.config.base_url != self.deployment.origin
            or self.config.deployment_hash != self.deployment.fingerprint
        ):
            raise ConnectorRuntimeError(
                "AUTH_REVOKED", "Canvas configuration requires reconnection"
            )
        canvas_oauth.validate_capabilities(self.config.capabilities)
        self.base_url = self.config.base_url
        self.secrets = secrets
        self.access_check = access_check
        self.transport = None

    def get_manifest(self) -> ConnectorManifest:
        return OAUTH_CANVAS_MANIFEST

    async def _get(
        self, connection_id: UUID, workspace_id: UUID, capabilities: frozenset[str], url: httpx.URL
    ) -> httpx.Response:
        if not capabilities.issubset(self.config.capabilities):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Canvas permission is not approved")
        validate_target(url, self.base_url)
        await self.access_check(connection_id, workspace_id, self.config, capabilities)
        if self.secrets is None:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Canvas authorization is unavailable")
        refresh = self.secrets.get("CANVAS_REFRESH_TOKEN")
        tokens = await canvas_oauth.exchange(self.deployment, refresh=refresh)
        if tokens.user_id != self.config.canvas_user_id:
            raise ConnectorRuntimeError("AUTH_REVOKED", "Canvas account binding changed")
        # Revocation during refresh must stop the following provider read as well.
        await self.access_check(connection_id, workspace_id, self.config, capabilities)
        self.secrets.get("CANVAS_REFRESH_TOKEN")  # Also recheck the expiring operation handle.
        async with canvas_oauth.safe_client(self.base_url) as client:
            response = await self._request(
                client,
                str(url),
                params=None,
                headers={"Authorization": f"Bearer {tokens.access.get_secret_value()}"},
            )
        try:
            normalized = json.dumps(response.json(), ensure_ascii=False)
        except ValueError:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Invalid Canvas JSON"
            ) from None
        if any(
            value in normalized
            for value in (
                refresh,
                tokens.access.get_secret_value(),
                self.deployment.client_secret.get_secret_value(),
            )
        ):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Provider response contains credential material"
            )
        return response

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        params: Mapping[str, str] | list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        from navox.connectors.errors import retry_after_seconds

        response = await client.get(
            url,
            params=(
                httpx.QueryParams(tuple(params) if isinstance(params, list) else params)
                if params is not None
                else None
            ),
            headers=headers,
        )
        codes = {
            401: "AUTH_EXPIRED",
            403: "PERMISSION_DENIED",
            404: "RESOURCE_NOT_FOUND",
            429: "RATE_LIMITED",
        }
        if response.status_code in codes:
            # Explicit branches retain the Literal error-code boundary.
            if response.status_code == 401:
                raise ConnectorRuntimeError("AUTH_EXPIRED", "Canvas authorization expired")
            if response.status_code == 403:
                raise ConnectorRuntimeError("PERMISSION_DENIED", "Canvas read permission denied")
            if response.status_code == 404:
                raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Canvas resource is unavailable")
            raise ConnectorRuntimeError(
                "RATE_LIMITED",
                "Canvas rate limit reached",
                retry_after_seconds=retry_after_seconds(response.headers.get("Retry-After")),
            )
        if response.status_code != 200:
            raise ConnectorRuntimeError("PROVIDER_UNAVAILABLE", "Canvas is unavailable")
        return response

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        await self._get(
            connection.id,
            connection.workspace_id,
            frozenset({"academic.courses.read"}),
            httpx.URL(
                self.base_url + "/api/v1/courses",
                params={
                    "per_page": "1",
                    "enrollment_state": "active",
                    "enrollment_type": "student",
                },
            ),
        )
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        canvas_oauth.validate_capabilities(list(request.capabilities))
        try:
            if request.cursor is not None and len(request.cursor) > 16_384:
                raise ValueError
            cursor = (
                Cursor.model_validate_json(request.cursor)
                if request.cursor
                else Cursor(window=(request.started_at or datetime.now(UTC)).date().isoformat())
            )
            window = datetime.fromisoformat(cursor.window).replace(tzinfo=UTC)
            if any(not c.isdigit() or len(c) > 32 for c in cursor.courses):
                raise ValueError
            if cursor.phase != "courses" and cursor.index >= len(cursor.courses):
                raise ValueError
        except (ValueError, ValidationError):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Invalid Canvas scan cursor"
            ) from None
        course_id = cursor.courses[cursor.index] if cursor.phase != "courses" else ""
        path, params = self._endpoint(cursor, request, course_id, window)
        url = httpx.URL(self.base_url + path, params=params)
        if cursor.next_url:
            candidate = httpx.URL(cursor.next_url)
            validate_target(candidate, self.base_url)
            if candidate.path != path:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Canvas pagination changed endpoint"
                )
            # Only pagination keys can change; permissions/filter includes remain ours.
            page = candidate.params.get("page")
            if not page or len(page) > 128:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Canvas pagination has no bounded page"
                )
            url = url.copy_merge_params({"page": page})
        response = await self._get(
            request.connection_id, request.workspace_id, request.capabilities, url
        )
        try:
            items = response.json()
            if (
                not isinstance(items, list)
                or len(items) > min(request.limit, 100)
                or any(not isinstance(i, dict) for i in items)
            ):
                raise ValueError
        except ValueError:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Invalid Canvas resource page"
            ) from None
        resources: list[CanonicalResource] = []
        for item in items:
            identifier = str(item.get("id", ""))
            if not identifier.isdigit() or len(identifier) > 32:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Canvas resource has no valid identifier"
                )
            if cursor.phase == "courses":
                if identifier not in cursor.courses:
                    cursor.courses.append(identifier)
                if len(cursor.courses) > 100:
                    raise ConnectorRuntimeError(
                        "INVALID_PROVIDER_RESPONSE", "Canvas course limit exceeded"
                    )
                resource = self._course_resource(request, item, identifier)
            elif cursor.phase == "assignments":
                if item.get("published") is False:
                    continue
                if "academic.submissions.read" not in request.capabilities:
                    item = {k: v for k, v in item.items() if k != "submission"}
                resource = self._assignment_resource(request, item, course_id)
            elif cursor.phase == "announcements":
                resource = self._announcement_resource(request, item)
            else:
                resource = self._calendar_resource(request, item)
            resources.append(self._stable_resource(resource, item, cursor.phase, course_id))
        link = response.links.get("next", {}).get("url")
        if link:
            next_url = httpx.URL(link)
            validate_target(next_url, self.base_url)
            if next_url.path != path or len(link) > 4096 or str(next_url) == str(url):
                raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid Canvas next page")
            cursor.next_url = str(next_url)
            more = True
        else:
            cursor.next_url = None
            more = self._advance(cursor, request.capabilities)
        return SyncPage(
            resources=resources,
            next_cursor=cursor.model_dump_json() if more else None,
            has_more=more,
        )

    @staticmethod
    def _endpoint(
        cursor: Cursor, request: SyncRequest, course: str, window: datetime
    ) -> tuple[str, list[tuple[str, str]]]:
        params = [("per_page", str(min(request.limit, 100)))]
        if cursor.phase == "courses":
            return "/api/v1/courses", params + [
                ("enrollment_state", "active"),
                ("enrollment_type", "student"),
                ("state[]", "available"),
            ]
        if cursor.phase == "assignments":
            if "academic.assignments.read" not in request.capabilities:
                raise ConnectorRuntimeError(
                    "PERMISSION_DENIED", "Assignment reads are not approved"
                )
            if "academic.submissions.read" in request.capabilities:
                params.append(("include[]", "submission"))
            return f"/api/v1/courses/{course}/assignments", params + [("order_by", "id")]
        if cursor.phase == "announcements":
            if "academic.announcements.read" not in request.capabilities:
                raise ConnectorRuntimeError(
                    "PERMISSION_DENIED", "Announcement reads are not approved"
                )
            return "/api/v1/announcements", params + [("context_codes[]", f"course_{course}")]
        if "calendar.events.read" not in request.capabilities:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Calendar reads are not approved")
        return "/api/v1/calendar_events", params + [
            ("context_codes[]", f"course_{course}"),
            ("type", "event"),
            ("start_date", (window - timedelta(days=30)).date().isoformat()),
            ("end_date", (window + timedelta(days=180)).date().isoformat()),
        ]

    @staticmethod
    def _advance(cursor: Cursor, capabilities: frozenset[str]) -> bool:
        phases: list[Literal["courses", "assignments", "announcements", "calendar"]] = ["courses"]
        for phase, capability in (
            ("assignments", "academic.assignments.read"),
            ("announcements", "academic.announcements.read"),
            ("calendar", "calendar.events.read"),
        ):
            if capability in capabilities:
                if phase == "assignments":
                    phases.append("assignments")
                elif phase == "announcements":
                    phases.append("announcements")
                else:
                    phases.append("calendar")
        if not cursor.courses:
            return False
        if cursor.phase != "courses" and cursor.index + 1 < len(cursor.courses):
            cursor.index += 1
            return True
        position = phases.index(cursor.phase) + 1
        if position >= len(phases):
            return False
        cursor.phase, cursor.index = phases[position], 0
        return True

    def _stable_resource(
        self, resource: CanonicalResource, item: dict[str, object], phase: str, course: str
    ) -> CanonicalResource:
        revisions = [_time(item.get(k)) for k in ("updated_at", "created_at", "posted_at")]
        submission = item.get("submission")
        if isinstance(submission, dict):
            revisions += [
                _time(submission.get(k)) for k in ("updated_at", "submitted_at", "graded_at")
            ]
        when = max(
            [t for t in revisions if t is not None], default=datetime(1970, 1, 1, tzinfo=UTC)
        )
        canonical = dict(resource.canonical)
        canonical["occurred_at"] = when.isoformat()
        canonical["source_type"] = (
            "calendar_event" if phase == "calendar" else resource.resource_type
        )
        if phase == "calendar":
            content = str(canonical.get("content") or "")
            for field, label in (("start_at", "Event starts"), ("end_at", "Event ends")):
                value = item.get(field)
                if isinstance(value, str):
                    content += f"\n{label}: {value}"
            canonical["content"] = content[:32_000]
        if item.get("workflow_state") in {"deleted", "cancelled"}:
            canonical["status"] = "deleted"
        url = resource.source_url
        if phase == "courses":
            url = f"{self.base_url}/courses/{item['id']}"
        if url:
            try:
                validate_target(httpx.URL(url), self.base_url)
            except (ValueError, ConnectorRuntimeError):
                url = None
        return resource.model_copy(
            update={"canonical": canonical, "updated_at": when, "source_url": url}
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        # Use the same approved list routes rather than requesting undeclared detail scopes.
        cursor = None
        for _ in range(1_000):
            page = await self.sync(
                SyncRequest(
                    connection_id=request.connection_id,
                    workspace_id=request.workspace_id,
                    capabilities=frozenset(self.config.capabilities),
                    cursor=cursor,
                    limit=100,
                )
            )
            for resource in page.resources:
                if (
                    resource.resource_type == request.resource_type
                    and resource.external_id == request.external_id
                ):
                    return resource
            if not page.has_more:
                break
            cursor = page.next_cursor
        raise ConnectorRuntimeError("RESOURCE_NOT_FOUND", "Canvas resource was not found")
