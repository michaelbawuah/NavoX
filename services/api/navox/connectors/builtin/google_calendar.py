"""Read-only primary Calendar adapter with opaque, resumable provider pagination.

Provider requests contain no database access. Source bodies remain transient;
application wiring selects locator-only persistence in the common runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from navox.connectors.contracts import (
    AuthorizationRequest,
    AuthorizationResult,
    CanonicalResource,
    ConnectorActionRequest,
    ConnectorActionResult,
    ConnectorConnectionContext,
    ConnectorErrorCode,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    FetchResourceRequest,
    SecretAccessor,
    SyncPage,
    SyncRequest,
    stable_resource_id,
)
from navox.intelligence.contracts import SourceDocument
from navox.providers.google_sources import (
    CALENDAR_READ_SCOPE,
    CALENDAR_ROOT,
    ExpiredSourceCursor,
    GoogleSourceError,
    GoogleSourceGateway,
    calendar_document,
)

CALENDAR_CAPABILITY = "calendar.events.read"
CALENDAR_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": "google-calendar",
        "version": "1.0.0",
        "displayName": "Google Calendar",
        "category": "productivity",
        "connectorClass": "OAUTH_API",
        "auth": [
            {
                "kind": "oauth2",
                "label": "Existing Google authorization",
                "scopes": [CALENDAR_READ_SCOPE],
            }
        ],
        "resourceTypes": ["calendar.event"],
        "capabilities": {
            "read": [
                {
                    "name": CALENDAR_CAPABILITY,
                    "description": "Read authorized primary calendar events",
                    "sensitive": True,
                }
            ],
            "write": [],
            "events": [],
            "incrementalSync": True,
        },
        "requiredSecrets": ["GOOGLE_ACCESS_TOKEN"],
        "rateLimitStrategy": "provider_headers",
        "minimumNavoxConnectorApiVersion": "1",
    }
)


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    legacy_connection_id: UUID
    managed_by_source_workflow: Literal[True] = True


class CalendarCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["google-calendar-cursor.v1"] = "google-calendar-cursor.v1"
    phase: Literal["ready", "list", "reconcile"] = "ready"
    token: str | None = Field(default=None, max_length=4096)
    page_token: str | None = Field(default=None, max_length=4096)
    anchor: datetime | None = None
    reset: bool = False
    seen: list[str] = Field(default_factory=list, max_length=2000)
    pending: list[str] = Field(default_factory=list, max_length=2000)
    pages: int = Field(default=0, ge=0, le=20)

    @field_validator("anchor")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Calendar cursor time must include timezone")
        return value

    def encode(self) -> str:
        return self.model_dump_json()


def decode_cursor(value: str | None) -> CalendarCursor:
    if value is None:
        return CalendarCursor()
    if len(value) > 2_100_000:
        raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Calendar cursor is too large")
    try:
        return CalendarCursor.model_validate_json(value)
    except ValidationError:
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE", "Invalid Calendar checkpoint"
        ) from None


class GoogleCalendarFailure(ConnectorRuntimeError):
    """Preserve only the existing allowlisted Google diagnostic vocabulary."""

    def __init__(self, error: GoogleSourceError) -> None:
        codes: dict[str, ConnectorErrorCode] = {
            "google_rate_limited": "RATE_LIMITED",
            "google_provider_unavailable": "PROVIDER_UNAVAILABLE",
            "google_transport_error": "PROVIDER_UNAVAILABLE",
            "google_authentication_failed": "AUTH_EXPIRED",
            "google_scope_missing": "PERMISSION_DENIED",
            "google_permission_denied": "PERMISSION_DENIED",
            "google_api_disabled": "PERMISSION_DENIED",
            "google_daily_limit_exceeded": "PERMANENT_FAILURE",
            "google_quota_exceeded": "PERMANENT_FAILURE",
            "google_invalid_response": "INVALID_PROVIDER_RESPONSE",
        }
        code = codes.get(error.code, "TEMPORARY_FAILURE")
        super().__init__(
            code, "Google Calendar read failed", retry_after_seconds=error.retry_after_seconds
        )
        self.google_diagnostic = error.diagnostic()
        self.google_http_status = error.http_status


def calendar_source_url(value: object) -> str | None:
    from urllib.parse import urlsplit

    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    if parsed.netloc == "calendar.google.com" or (
        parsed.netloc == "www.google.com" and parsed.path.startswith("/calendar/")
    ):
        return value
    return None


def calendar_resource(document: SourceDocument, connection_id: UUID) -> CanonicalResource:
    if document.provider != "google" or document.source_type != "calendar_event":
        raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Not a Calendar source")
    # Retrieved time must not alter a revision digest on replay. Keep exact text,
    # source IDs, identities and metadata; no re-trimming/reinterpretation here.
    return CanonicalResource(
        resource_id=stable_resource_id(connection_id, "calendar.event", document.external_id),
        workspace_id=document.workspace_id,
        connector_connection_id=connection_id,
        provider="google",
        resource_type="calendar.event",
        external_id=document.external_id,
        external_parent_id=document.external_parent_id,
        canonical={
            "source_document": document.model_dump(mode="json", exclude={"retrieved_at"}),
            "status": document.metadata.get("status", "active"),
        },
        source_url=calendar_source_url(document.metadata.get("html_link")),
        updated_at=document.occurred_at,
        retrieved_at=document.retrieved_at,
    )


class GoogleCalendarConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        gateway: GoogleSourceGateway | None = None,
        known_ids: tuple[str, ...] = (),
    ) -> None:
        self.config = CalendarConfig.model_validate(config)
        self.secrets = secrets
        self.gateway = gateway or GoogleSourceGateway()
        if len(known_ids) > 2000 or any(not value or len(value) > 512 for value in known_ids):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Calendar recovery budget exceeded"
            )
        self.known_ids = known_ids

    def get_manifest(self) -> ConnectorManifest:
        return CALENDAR_MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        del context
        # The existing incremental Google OAuth flow remains the sole grant path.
        return AuthorizationResult(authorized=False)

    def _client(self) -> httpx.AsyncClient:
        if self.secrets is None:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Google access lease is unavailable")
        return httpx.AsyncClient(
            timeout=20.0,
            transport=self.gateway.transport,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {self.secrets.get('GOOGLE_ACCESS_TOKEN')}",
                "Accept": "application/json",
            },
        )

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        if CALENDAR_CAPABILITY not in connection.authorized_capabilities:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Calendar read permission is required")
        try:
            async with self._client() as client:
                await self.gateway._get(
                    client, CALENDAR_ROOT, {"maxResults": "1", "fields": "kind"}
                )
        except GoogleSourceError as error:
            raise GoogleCalendarFailure(error) from None
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        if request.capabilities != frozenset({CALENDAR_CAPABILITY}):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Calendar read permission is required")
        state = decode_cursor(request.cursor)
        if state.phase == "ready":
            state = CalendarCursor(
                phase="list", token=state.token, anchor=request.started_at or datetime.now(UTC)
            )
        try:
            if state.phase == "reconcile":
                return await self._reconcile(request, state)
            return await self._list(request, state)
        except GoogleSourceError as error:
            raise GoogleCalendarFailure(error) from None

    async def _list(self, request: SyncRequest, state: CalendarCursor) -> SyncPage:
        anchor = state.anchor
        if anchor is None or state.pages >= 20:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Invalid Calendar page checkpoint"
            )
        params = {
            "maxResults": "100",
            "singleEvents": "true",
            "showDeleted": "true",
            "fields": (
                "items(id,recurringEventId,summary,description,organizer,attendees,"
                "start,end,status,created,updated),nextPageToken,nextSyncToken"
            ),
        }
        if state.token:
            params["syncToken"] = state.token
        else:
            params["timeMin"] = (anchor - timedelta(days=30)).isoformat()
            params["timeMax"] = (anchor + timedelta(days=90)).isoformat()
        if state.page_token:
            params["pageToken"] = state.page_token
        async with self._client() as client:
            try:
                payload = await self.gateway._get(client, CALENDAR_ROOT, params, expired_status=410)
            except ExpiredSourceCursor:
                if state.reset or state.token is None:
                    raise
                # Persist an explicit restart page: no recursion, no token advancement.
                reset = CalendarCursor(phase="list", anchor=anchor, reset=True)
                return SyncPage(resources=[], next_cursor=reset.encode(), has_more=True)
        items = payload.get("items", [])
        if (
            not isinstance(items, list)
            or len(items) > 100
            or any(not isinstance(item, dict) for item in items)
        ):
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid Calendar page items")
        documents = [
            calendar_document(
                item,
                workspace_id=request.workspace_id,
                connection_id=self.config.legacy_connection_id,
                now=anchor,
            )
            for item in items
        ]
        documents.sort(key=lambda document: (document.occurred_at, str(document.id)))
        seen = sorted(set(state.seen) | {document.external_id for document in documents})
        if len(seen) > 2000:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Calendar recovery budget exceeded"
            )
        resources = [calendar_resource(document, request.connection_id) for document in documents]
        page_token, sync_token = payload.get("nextPageToken"), payload.get("nextSyncToken")
        if page_token is not None:
            if (
                not isinstance(page_token, str)
                or not page_token
                or len(page_token) > 4096
                or sync_token is not None
                or page_token == state.page_token
            ):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Invalid Calendar pagination token"
                )
            next_state = state.model_copy(
                update={"page_token": page_token, "seen": seen, "pages": state.pages + 1}
            )
            return SyncPage(resources=resources, next_cursor=next_state.encode(), has_more=True)
        if not isinstance(sync_token, str) or not sync_token or len(sync_token) > 4096:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Calendar omitted its final sync token"
            )
        pending = sorted(set(self.known_ids) - set(seen)) if state.reset else []
        next_state = (
            CalendarCursor(
                phase="reconcile", token=sync_token, anchor=anchor, reset=True, pending=pending
            )
            if pending
            else CalendarCursor(token=sync_token)
        )
        return SyncPage(
            resources=resources, next_cursor=next_state.encode(), has_more=bool(pending)
        )

    async def _reconcile(self, request: SyncRequest, state: CalendarCursor) -> SyncPage:
        if not state.token or not state.pending or state.anchor is None:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Invalid Calendar reconciliation checkpoint"
            )
        if self.secrets is None:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Google access lease is unavailable")
        documents = await self.gateway.reconcile_existing(
            source="calendar",
            access_token=self.secrets.get("GOOGLE_ACCESS_TOKEN"),
            workspace_id=request.workspace_id,
            connection_id=self.config.legacy_connection_id,
            external_ids=state.pending[:100],
            now=state.anchor,
        )
        expected = set(state.pending[:100])
        if {document.external_id for document in documents} != expected or len(documents) != len(
            expected
        ):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Calendar reconciliation is incomplete"
            )
        pending = state.pending[100:]
        next_state = (
            state.model_copy(update={"pending": pending})
            if pending
            else CalendarCursor(token=state.token)
        )
        return SyncPage(
            resources=[
                calendar_resource(document, request.connection_id) for document in documents
            ],
            next_cursor=next_state.encode(),
            has_more=bool(pending),
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        # This release routes reads through a capability-checked sync operation.
        del request
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY", "Use authorized Calendar synchronization"
        )

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        del request
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Calendar adapter is read-only")
