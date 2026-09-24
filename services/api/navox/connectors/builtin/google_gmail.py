"""Gmail's bounded ID/chronology plan as a database-free connector adapter.

Every phase returns an IDs-only checkpoint. Full messages are transient resources;
application wiring selects locator-only persistence and existing email relevance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from navox.connectors.builtin.google_calendar import GoogleCalendarFailure
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
from navox.intelligence.contracts import SourceDocument
from navox.providers.google_sources import (
    GMAIL_READ_SCOPE,
    MAX_PAGES,
    ExpiredSourceCursor,
    GoogleSourceError,
    GoogleSourceGateway,
)

GMAIL_CAPABILITY = "communication.messages.read"
MAX_ENTRIES = MAX_PAGES * 200
GMAIL_MANIFEST = ConnectorManifest.model_validate(
    {
        "id": "google-gmail",
        "version": "1.0.0",
        "displayName": "Gmail",
        "category": "productivity",
        "connectorClass": "OAUTH_API",
        "auth": [
            {
                "kind": "oauth2",
                "label": "Existing Google authorization",
                "scopes": [GMAIL_READ_SCOPE],
            }
        ],
        "resourceTypes": ["communication.message"],
        "capabilities": {
            "read": [
                {
                    "name": GMAIL_CAPABILITY,
                    "description": "Read authorized Gmail messages",
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


class GmailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    legacy_connection_id: UUID
    managed_by_source_workflow: Literal[True] = True


class GmailEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=512)
    deleted: bool
    occurred_at: datetime | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Gmail chronology timestamps require a timezone")
            return value.astimezone(UTC)
        return None


class GmailCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["google-gmail-plan.v1"] = "google-gmail-plan.v1"
    plan_id: UUID
    anchor: datetime
    phase: Literal["list", "metadata", "process", "ready"] = "list"
    initial_cursor: str | None = Field(default=None, max_length=4096)
    cursor: str | None = Field(default=None, max_length=4096)
    page_token: str | None = Field(default=None, max_length=4096)
    entries: list[GmailEntry] = Field(default_factory=list, max_length=MAX_ENTRIES)
    position: int = Field(default=0, ge=0, le=MAX_ENTRIES)
    pages: int = Field(default=0, ge=0, le=MAX_PAGES)
    reset: bool = False
    next_read_at: datetime | None = None
    restarts: int = Field(default=0, ge=0, le=3)
    page_token_error: bool = False

    @model_validator(mode="after")
    def validate_progress(self) -> GmailCheckpoint:
        if self.anchor.tzinfo is None or (
            self.next_read_at is not None and self.next_read_at.tzinfo is None
        ):
            raise ValueError("Gmail plan timestamps require a timezone")
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Duplicate Gmail planned identifier")
        if self.position > len(self.entries):
            raise ValueError("Gmail plan position exceeds its entries")
        if self.phase in {"process", "ready"} and any(
            entry.occurred_at is None or entry.occurred_at.tzinfo is None for entry in self.entries
        ):
            raise ValueError("Gmail chronology is incomplete")
        if self.phase == "ready" and (self.position != len(self.entries) or not self.cursor):
            raise ValueError("Gmail completion is incomplete")
        return self

    def encode(self) -> str:
        return self.model_dump_json()


def decode_gmail_cursor(value: str | None) -> GmailCheckpoint:
    if value is None or len(value) > 2_100_000:
        raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Invalid Gmail checkpoint")
    try:
        return GmailCheckpoint.model_validate_json(value)
    except ValidationError:
        raise ConnectorRuntimeError(
            "INVALID_PROVIDER_RESPONSE", "Invalid Gmail checkpoint"
        ) from None


def gmail_resource(document: SourceDocument, connection_id: UUID) -> CanonicalResource:
    if document.provider != "google" or document.source_type != "gmail_message":
        raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Not a Gmail source")
    return CanonicalResource(
        resource_id=stable_resource_id(
            connection_id, "communication.message", document.external_id
        ),
        workspace_id=document.workspace_id,
        connector_connection_id=connection_id,
        provider="google",
        resource_type="communication.message",
        external_id=document.external_id,
        external_parent_id=document.external_parent_id,
        canonical={
            "source_document": document.model_dump(mode="json", exclude={"retrieved_at"}),
            "status": document.metadata.get("status", "active"),
        },
        source_url=f"https://mail.google.com/mail/u/0/#all/{quote(document.external_id, safe='')}",
        updated_at=document.occurred_at,
        retrieved_at=document.retrieved_at,
    )


class GoogleGmailConnector:
    def __init__(
        self,
        config: Mapping[str, JsonValue],
        secrets: SecretAccessor | None,
        *,
        gateway: GoogleSourceGateway | None = None,
        known_ids: tuple[str, ...] = (),
    ) -> None:
        self.config = GmailConfig.model_validate(config)
        self.secrets = secrets
        self.gateway = gateway or GoogleSourceGateway()
        if len(known_ids) > 2001 or any(not i or len(i) > 512 for i in known_ids):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Gmail recovery budget exceeded"
            )
        self.known_ids = known_ids

    def get_manifest(self) -> ConnectorManifest:
        return GMAIL_MANIFEST

    async def authorize(self, context: AuthorizationRequest) -> AuthorizationResult:
        return AuthorizationResult(authorized=False)

    def _token(self) -> str:
        if self.secrets is None:
            raise ConnectorRuntimeError("AUTH_EXPIRED", "Google access lease is unavailable")
        return self.secrets.get("GOOGLE_ACCESS_TOKEN")

    async def health(self, connection: ConnectorConnectionContext) -> ConnectorHealth:
        # The original OAuth broker validates authority. Provider health is learned
        # from actual reads; no extra profile request may consume or change an anchor.
        if connection.authorized_capabilities != frozenset({GMAIL_CAPABILITY}):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail read permission is required")
        self._token()
        return ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    async def sync(self, request: SyncRequest) -> SyncPage:
        if request.capabilities != frozenset({GMAIL_CAPABILITY}):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail read permission is required")
        state = decode_gmail_cursor(request.cursor)
        state = state.model_copy(update={"page_token_error": False})
        try:
            if state.phase == "list":
                return await self._list(state)
            if state.phase == "metadata":
                return await self._metadata(state)
            if state.phase == "process":
                return await self._message(state, request)
            return SyncPage(next_cursor=state.encode())
        except GoogleSourceError as error:
            raise GoogleCalendarFailure(error) from None

    def _ordered(self, entries: list[GmailEntry]) -> list[GmailEntry]:
        return sorted(
            entries,
            key=lambda e: (
                e.occurred_at or datetime.min.replace(tzinfo=UTC),
                str(uuid5(NAMESPACE_URL, f"{self.config.legacy_connection_id}/gmail/{e.id}")),
            ),
        )

    async def _pace(self, state: GmailCheckpoint) -> None:
        from navox.intelligence import gmail_sync

        if state.next_read_at is not None:
            delay = (state.next_read_at - datetime.now(UTC)).total_seconds()
            if delay > 0:
                await asyncio.sleep(min(delay, gmail_sync.READ_SPACING_SECONDS))

    def _page(
        self,
        state: GmailCheckpoint,
        *,
        resources: list[CanonicalResource] | None = None,
        **changes: object,
    ) -> SyncPage:
        from navox.intelligence import gmail_sync

        payload = state.model_dump()
        payload.update(changes)
        payload["next_read_at"] = datetime.now(UTC) + timedelta(
            seconds=gmail_sync.READ_SPACING_SECONDS
        )
        updated = GmailCheckpoint.model_validate(payload)
        return SyncPage(
            resources=resources or [],
            next_cursor=updated.encode(),
            has_more=updated.phase != "ready",
        )

    async def _list(self, state: GmailCheckpoint) -> SyncPage:
        bootstrap = state.initial_cursor is None or state.reset
        await self._pace(state)
        if bootstrap and state.cursor is None:
            return self._page(state, cursor=await self.gateway.gmail_profile(self._token()))
        if state.pages >= MAX_PAGES:
            raise GoogleSourceError("Gmail reconciliation exceeded its bounded page budget")
        try:
            page = await self.gateway.gmail_page(
                self._token(),
                cursor=None if bootstrap else state.initial_cursor,
                page_token=state.page_token,
                query=f"after:{int((state.anchor - timedelta(days=30)).timestamp())}",
            )
        except ExpiredSourceCursor:
            if bootstrap:
                raise
            return self._page(
                state, reset=True, cursor=None, entries=[], pages=0, position=0, page_token=None
            )
        except GoogleSourceError as error:
            if error.http_status == 400 and state.page_token is not None and state.restarts < 3:
                # Commit a bounded enumeration reset, then surface the original
                # failure; retry remains in the durable workflow, not a busy loop.
                return self._page(
                    state,
                    entries=[],
                    pages=0,
                    position=0,
                    page_token=None,
                    restarts=state.restarts + 1,
                    page_token_error=True,
                )
            raise
        indexed = {entry.id: entry for entry in state.entries}
        for identifier, deleted in page.message_ids.items():
            indexed[identifier] = GmailEntry(id=identifier, deleted=deleted)
        if len(indexed) > MAX_PAGES * 100:
            raise GoogleSourceError("Gmail reconciliation exceeded its resource budget")
        cursor = page.cursor if not bootstrap and page.cursor else state.cursor
        phase = "list"
        if page.next_page_token is None:
            if not cursor:
                raise GoogleSourceError("Gmail did not return a history cursor")
            if state.reset:
                if len(self.known_ids) > 2000:
                    raise GoogleSourceError(
                        "Historical reconciliation exceeded its resource budget"
                    )
                for identifier in self.known_ids:
                    indexed.setdefault(identifier, GmailEntry(id=identifier, deleted=False))
            phase = "metadata"
        return self._page(
            state,
            entries=list(indexed.values()),
            pages=state.pages + 1,
            page_token=page.next_page_token,
            cursor=cursor,
            phase=phase,
            position=0,
        )

    async def _metadata(self, state: GmailCheckpoint) -> SyncPage:
        if state.position == len(state.entries):
            return self._page(
                state, phase="process", position=0, entries=self._ordered(state.entries)
            )
        entry = state.entries[state.position]
        occurred, deleted = state.anchor, entry.deleted
        if not deleted:
            await self._pace(state)
            occurred, deleted = await self.gateway.gmail_metadata(
                self._token(), external_id=entry.id, now=state.anchor
            )
        entries = list(state.entries)
        entries[state.position] = GmailEntry(id=entry.id, deleted=deleted, occurred_at=occurred)
        return self._page(state, entries=entries, position=state.position + 1)

    async def _message(self, state: GmailCheckpoint, request: SyncRequest) -> SyncPage:
        if state.position == len(state.entries):
            return self._page(state, phase="ready")
        entry = state.entries[state.position]
        if not entry.deleted:
            await self._pace(state)
        document = await self.gateway.gmail_message(
            self._token(),
            workspace_id=request.workspace_id,
            connection_id=self.config.legacy_connection_id,
            external_id=entry.id,
            now=entry.occurred_at if entry.deleted and entry.occurred_at else state.anchor,
            deleted=entry.deleted,
        )
        if not entry.deleted and document.metadata.get("status") == "deleted":
            pending = list(state.entries[state.position :])
            pending[0] = GmailEntry(id=entry.id, deleted=True, occurred_at=datetime.now(UTC))
            return self._page(
                state, entries=state.entries[: state.position] + self._ordered(pending)
            )
        if document.occurred_at != entry.occurred_at:
            raise GoogleSourceError(
                "Gmail message chronology changed", code="google_invalid_response"
            )
        return self._page(
            state,
            position=state.position + 1,
            resources=[gmail_resource(document, request.connection_id)],
        )

    async def fetch_resource(self, request: FetchResourceRequest) -> CanonicalResource:
        raise ConnectorRuntimeError("UNSUPPORTED_CAPABILITY", "Use the authorized Gmail sync plan")

    async def execute(self, request: ConnectorActionRequest) -> ConnectorActionResult:
        raise ConnectorRuntimeError(
            "UNSUPPORTED_CAPABILITY", "Gmail writes require SPEC-001 approval"
        )
