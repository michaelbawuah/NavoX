"""Calendar migration wiring: existing OAuth/provenance, common read-sync runtime.

The old source workflow/event/watch entry points remain stable. The common
runtime owns resource acceptance, attempt fencing and atomic cursor completion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.builtin.google_calendar import (
    CALENDAR_CAPABILITY,
    CALENDAR_MANIFEST,
    CalendarConfig,
    CalendarCursor,
    GoogleCalendarConnector,
    GoogleCalendarFailure,
    decode_cursor,
)
from navox.connectors.contracts import CanonicalResource, ConnectorRuntimeError, SecretAccessor
from navox.connectors.intelligence import ingest_connector_resource
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.connectors.secrets import LEASE_PURPOSES, ScopedSecretLease, SecretBrokerError
from navox.connectors.sync_state import (
    RETRYABLE_CODES,
    SyncAlreadyActive,
    authorization_hash,
    database_now,
    utc,
)
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    CommitmentSource,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncRun,
    IntelligenceCursor,
    User,
    WorkspaceMembership,
)
from navox.intelligence.extraction import OperationalExtractor
from navox.intelligence.source_cooldown import GoogleSourceBusyError
from navox.providers.google_oauth import GoogleAccessTokenError
from navox.providers.google_sources import (
    CALENDAR_READ_SCOPE,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
)


@dataclass(frozen=True)
class CalendarAuthority:
    legacy_id: UUID
    user_id: UUID
    workspace_id: UUID
    credential_id: UUID | None
    account_id: str
    scopes: frozenset[str]
    required_scope: str = CALENDAR_READ_SCOPE

    async def check(self, database: AsyncSession, lock: bool) -> None:
        query = select(Connection).where(
            Connection.id == self.legacy_id,
            Connection.user_id == self.user_id,
            Connection.workspace_id == self.workspace_id,
        )
        if lock:
            query = query.with_for_update()
        with database.no_autoflush:
            row = await database.scalar(query.execution_options(populate_existing=True))
        if (
            row is None
            or row.provider != "google"
            or row.status != "active"
            or row.credential_reference != self.credential_id
            or row.external_account_id != self.account_id
            or frozenset(row.granted_scopes) != self.scopes
            or self.required_scope not in row.granted_scopes
        ):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Google read authorization changed")


class GoogleCalendarTokenBroker:
    """Lease access tokens via the original vault; never copy refresh credentials.

    Refresh runs against a detached record. Recheck committed ownership before
    copying back refresh metadata, so slow OAuth I/O cannot undo a revocation.
    """

    def __init__(
        self,
        settings: Settings,
        authority: CalendarAuthority,
        connector_id: UUID,
        *,
        cache_access_tokens: bool = False,
    ) -> None:
        self.settings, self.authority, self.connector_id = settings, authority, connector_id
        self.cache_access_tokens = cache_access_tokens
        self._cached_token: str | None = None
        self._cached_until: datetime | None = None

    async def lease(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        purpose: str,
        names: set[str] | frozenset[str],
    ) -> ScopedSecretLease:
        from navox.connectors.authorization import owned_connector
        from navox.intelligence import ingestion

        if (
            connection_id != self.connector_id
            or workspace_id != self.authority.workspace_id
            or user_id != self.authority.user_id
            or purpose not in LEASE_PURPOSES
            or set(names) != {"GOOGLE_ACCESS_TOKEN"}
        ):
            raise SecretBrokerError("Google credential lease denied")
        await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=True,
        )
        await self.authority.check(database, False)
        original = await database.get(Connection, self.authority.legacy_id)
        if original is None:
            raise SecretBrokerError("Google credential lease denied")
        now = datetime.now(UTC)
        if (
            self.cache_access_tokens
            and self._cached_token is not None
            and self._cached_until is not None
            and now < self._cached_until
            and (
                original.access_token_expires_at is None
                or utc(original.access_token_expires_at) > now + timedelta(minutes=1)
            )
        ):
            return ScopedSecretLease(
                connection_id, purpose, {"GOOGLE_ACCESS_TOKEN": self._cached_token}
            )
        detached = Connection(
            id=original.id,
            workspace_id=workspace_id,
            user_id=user_id,
            provider="google",
            status="active",
            external_account_id=original.external_account_id,
            external_email=original.external_email,
            granted_scopes=list(original.granted_scopes),
            credential_reference=original.credential_reference,
        )
        await database.commit()  # Release authorization locks before refresh I/O.
        try:
            token = await ingestion.access_token_for_connection(
                database, connection=detached, settings=self.settings
            )
        except GoogleAccessTokenError:
            raise GoogleCalendarFailure(
                GoogleSourceAuthorizationError(code="google_authentication_failed")
            ) from None
        await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=True,
        )
        await self.authority.check(database, True)
        original = await database.get(Connection, self.authority.legacy_id)
        if original is None:
            raise SecretBrokerError("Google credential lease denied")
        retained = sorted(self.authority.scopes & set(detached.granted_scopes))
        original.granted_scopes = retained
        original.access_token_expires_at = detached.access_token_expires_at
        original.last_checked_at = detached.last_checked_at
        if frozenset(retained) != self.authority.scopes:
            await database.commit()
            raise GoogleCalendarFailure(GoogleSourceAuthorizationError(code="google_scope_missing"))
        database.add(
            AuditEvent(
                user_id=user_id,
                workspace_id=workspace_id,
                event_type="connector.credential.leased",
                actor_type="system",
                entity_type="connector_connection",
                entity_id=connection_id,
                event_metadata={"purpose": purpose, "credential_kind": "google_access_token"},
            )
        )
        if self.cache_access_tokens:
            self._cached_token = token
            self._cached_until = datetime.now(UTC) + timedelta(minutes=45)
        return ScopedSecretLease(connection_id, purpose, {"GOOGLE_ACCESS_TOKEN": token})


async def _definition(database: AsyncSession) -> ConnectorDefinition:
    identifier = uuid5(NAMESPACE_URL, "navox:google-calendar:1.0.0")
    row = await database.get(ConnectorDefinition, identifier)
    if row is not None:
        return row
    try:
        async with database.begin_nested():
            row = ConnectorDefinition(
                id=identifier,
                connector_key=CALENDAR_MANIFEST.id,
                version=CALENDAR_MANIFEST.version,
                display_name=CALENDAR_MANIFEST.display_name,
                connector_class="OAUTH_API",
                trust_level="NAVOX_FIRST_PARTY",
                manifest=CALENDAR_MANIFEST.model_dump(mode="json", by_alias=True),
                active=True,
            )
            database.add(row)
            await database.flush()
    except IntegrityError:
        row = await database.get(ConnectorDefinition, identifier, populate_existing=True)
        if row is None:
            raise
    return row


async def process_calendar_connection(
    database: AsyncSession,
    *,
    connection_id: UUID,
    settings: Settings,
    extractor: OperationalExtractor,
) -> list[UUID]:
    """Use the common runtime for primary Calendar, preserving legacy identifiers."""
    from navox.intelligence import ingestion

    # Register lazily when an authorized existing Calendar sync is requested.
    # Deterministic IDs and savepoints make first use race-safe.
    original = await database.get(Connection, connection_id, populate_existing=True)
    if original is None or original.provider != "google":
        raise GoogleSourceAuthorizationError(code="google_scope_missing")
    user_id, workspace_id = original.user_id, original.workspace_id
    user = await database.get(User, user_id, populate_existing=True)
    member = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    if (
        user is None
        or member is None
        or user.agent_paused
        or original.status != "active"
        or CALENDAR_READ_SCOPE not in original.granted_scopes
    ):
        raise GoogleSourceAuthorizationError(code="google_scope_missing")
    authority = CalendarAuthority(
        connection_id,
        user_id,
        workspace_id,
        original.credential_reference,
        original.external_account_id,
        frozenset(original.granted_scopes),
    )
    timezone_name = user.timezone
    # The legacy caller may have locked its connection; don't carry that lock
    # into connector acquisition (all later locks use connector -> legacy order).
    await database.commit()
    definition = await _definition(database)
    connector_id = uuid5(NAMESPACE_URL, f"navox:google-calendar:{connection_id}")
    connection = await database.scalar(
        select(ConnectorConnection)
        .where(ConnectorConnection.id == connector_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if connection is None:
        try:
            async with database.begin_nested():
                connection = ConnectorConnection(
                    id=connector_id,
                    connector_definition_id=definition.id,
                    legacy_connection_id=connection_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    provider="google",
                    external_account_id=authority.account_id,
                    status="CONNECTED",
                    health_state="CONNECTED",
                    authorized_capabilities=[CALENDAR_CAPABILITY],
                    provider_capabilities=[CALENDAR_CAPABILITY],
                    credential_reference=authority.credential_id,
                    config=CalendarConfig(legacy_connection_id=connection_id).model_dump(
                        mode="json"
                    ),
                )
                database.add(connection)
                await database.flush()
        except IntegrityError:
            connection = await database.get(
                ConnectorConnection, connector_id, populate_existing=True
            )
            if connection is None:
                raise
    if (
        connection.user_id != user_id
        or connection.workspace_id != workspace_id
        or connection.legacy_connection_id != connection_id
        or connection.connector_definition_id != definition.id
    ):
        raise GoogleSourceAuthorizationError("Calendar connector ownership mismatch")
    await authority.check(database, True)
    cursor = await database.scalar(
        select(IntelligenceCursor)
        .where(
            IntelligenceCursor.connection_id == connection_id,
            IntelligenceCursor.source == "calendar",
        )
        .execution_options(populate_existing=True)
    )
    initial_token = cursor.cursor if cursor else None
    now = await database_now(database)
    active = (
        connection.sync_lease_token is not None
        and connection.sync_lease_expires_at is not None
        and utc(connection.sync_lease_expires_at) > now
    )
    if not active:
        connection.credential_reference = authority.credential_id
        connection.external_account_id = authority.account_id
        desired = CalendarCursor(token=initial_token).encode()
        if connection.sync_cursor != desired:
            connection.sync_cursor = desired
    policy = frozenset({CALENDAR_CAPABILITY})
    run = (
        await database.get(ConnectorSyncRun, connection.sync_run_id)
        if connection.sync_run_id
        else None
    )
    fingerprint = authorization_hash(connection, definition, policy)
    reusable = (
        run is not None
        and run.generation > 0
        and run.status in {"running", "failed", "interrupted"}
        and run.error_code in (RETRYABLE_CODES | {None})
        and run.authorization_hash == fingerprint
        and run.cursor_before == connection.sync_cursor
    )
    request_id = run.request_id if reusable and run is not None else uuid4()
    known = set(
        await database.scalars(
            select(CommitmentSource.external_resource_id)
            .where(
                CommitmentSource.connection_id == connection_id,
                CommitmentSource.source_type == "calendar_event",
                CommitmentSource.external_resource_id.is_not(None),
            )
            .distinct()
            .limit(2001)
        )
    )
    known.update(
        await database.scalars(
            select(ConnectorResource.external_id)
            .where(ConnectorResource.connector_connection_id == connector_id)
            .limit(2001)
        )
    )
    known_ids = tuple(sorted(identifier for identifier in known if identifier is not None))
    if len(known_ids) > 2000:
        raise GoogleSourceError("Historical reconciliation exceeded its resource budget")
    await database.commit()
    gateway = ingestion.GoogleSourceGateway()

    def factory(
        config: Mapping[str, JsonValue], secrets: SecretAccessor | None
    ) -> GoogleCalendarConnector:
        return GoogleCalendarConnector(config, secrets, gateway=gateway, known_ids=known_ids)

    registry = ConnectorRegistry()
    registry.register(CALENDAR_MANIFEST, factory)
    runtime = ConnectorRuntime(
        registry,
        secret_broker=GoogleCalendarTokenBroker(settings, authority, connector_id),
        authority_check=authority.check,
        retain_canonical_content=False,
    )

    async def consume(resource: CanonicalResource) -> list[UUID]:
        await authority.check(database, False)
        return await ingest_connector_resource(
            database,
            connector_connection=connection,
            resource=resource,
            extractor=extractor,
            timezone_name=timezone_name,
            receipt_source="calendar",
        )

    async def finalize(completed: ConnectorSyncRun) -> None:
        state = decode_cursor(completed.checkpoint_cursor)
        if state.phase != "ready" or state.token is None:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Calendar snapshot is incomplete"
            )
        current = await database.scalar(
            select(IntelligenceCursor)
            .where(
                IntelligenceCursor.connection_id == connection_id,
                IntelligenceCursor.source == "calendar",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (current.cursor if current else None) != initial_token:
            raise ConnectorRuntimeError(
                "PERMISSION_DENIED", "Legacy Calendar cursor changed during sync"
            )
        if current is None:
            current = IntelligenceCursor(connection_id=connection_id, source="calendar")
            database.add(current)
        current.cursor = state.token
        current.updated_at = await database_now(database)
        database.add(
            AuditEvent(
                workspace_id=workspace_id,
                user_id=user_id,
                event_type="intelligence.source_processed",
                actor_type="system",
                entity_type="connection",
                entity_id=connection_id,
                event_metadata={
                    "source": "calendar",
                    "connector_connection_id": str(connector_id),
                    "documents": completed.resource_count,
                    "commitments": len(completed.result_ids),
                    "cursor_advanced": True,
                },
            )
        )
        await database.flush()

    try:
        completed = await runtime.sync(
            database,
            connection_id=connector_id,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request_id,
            policy_allowed=policy,
            consume=consume,
            consumer_version=extractor.extractor_version,
            finalize=finalize,
        )
        return sorted((UUID(identifier) for identifier in completed.result_ids), key=str)
    except GoogleCalendarFailure as error:
        diagnostic = error.google_diagnostic
        raise GoogleSourceError(
            "Google Calendar read failed",
            code=str(diagnostic["code"]),
            http_status=error.google_http_status,
            retry_after_seconds=error.retry_after_seconds,
        ) from None
    except SyncAlreadyActive:
        raise GoogleSourceBusyError() from None
    except ConnectorRuntimeError as error:
        if error.code in {"PERMISSION_DENIED", "AUTH_EXPIRED", "AUTH_REVOKED"}:
            raise GoogleSourceAuthorizationError(
                "Calendar authorization changed", code="google_scope_missing"
            ) from None
        raise GoogleSourceError(
            "Calendar synchronization deferred",
            code="google_rate_limited"
            if error.code == "RATE_LIMITED"
            else "google_provider_unavailable",
            retry_after_seconds=error.retry_after_seconds,
        ) from None
