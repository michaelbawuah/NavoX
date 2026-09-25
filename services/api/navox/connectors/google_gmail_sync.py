"""Migrate the existing Gmail ID/chronology plan through the shared runtime.

The legacy plan remains a progress projection for the existing UI. Runtime
receipts and fences, not that projection, authorize acceptance and completion.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.google_calendar import GoogleCalendarFailure
from navox.connectors.builtin.google_gmail import (
    GMAIL_CAPABILITY,
    GMAIL_MANIFEST,
    MAX_ENTRIES,
    GmailCheckpoint,
    GmailConfig,
    GoogleGmailConnector,
    decode_gmail_cursor,
)
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorRuntimeError,
    SecretAccessor,
    SyncPage,
)
from navox.connectors.google_calendar_sync import CalendarAuthority, GoogleCalendarTokenBroker
from navox.connectors.normalization import canonical_resource_to_source_document
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
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
    ConnectorSyncRun,
    GmailSyncPlan,
    IntelligenceCursor,
    User,
    WorkspaceMembership,
)
from navox.intelligence.extraction import OperationalExtractor
from navox.intelligence.source_cooldown import (
    GoogleSourceBusyError,
    GoogleSourceCooldownError,
    source_cooldown,
)
from navox.providers.google_sources import (
    GMAIL_READ_SCOPE,
    MAX_PAGES,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
)


class GmailConsumerFailure(ConnectorRuntimeError):
    def __init__(self, original: Exception) -> None:
        super().__init__("TEMPORARY_FAILURE", "Gmail intelligence temporarily unavailable")
        self.original = original


def plan_checkpoint(plan: GmailSyncPlan) -> GmailCheckpoint:
    return GmailCheckpoint.model_validate(
        {
            "plan_id": plan.id,
            "anchor": utc(plan.created_at),
            "phase": plan.phase,
            "initial_cursor": plan.initial_cursor,
            "cursor": plan.cursor,
            "page_token": plan.page_token,
            "pages": plan.pages,
            "reset": plan.reset,
            "entries": plan.entries,
            "position": plan.position,
            "next_read_at": utc(plan.next_read_at) if plan.next_read_at else None,
        }
    )


async def _definition(database: AsyncSession) -> ConnectorDefinition:
    identifier = uuid5(NAMESPACE_URL, "navox:google-gmail:1.0.0")
    row = await database.get(ConnectorDefinition, identifier)
    if row is not None:
        return row
    try:
        async with database.begin_nested():
            row = ConnectorDefinition(
                id=identifier,
                connector_key=GMAIL_MANIFEST.id,
                version=GMAIL_MANIFEST.version,
                display_name=GMAIL_MANIFEST.display_name,
                connector_class="OAUTH_API",
                trust_level="NAVOX_FIRST_PARTY",
                manifest=GMAIL_MANIFEST.model_dump(mode="json", by_alias=True),
                active=True,
            )
            database.add(row)
            await database.flush()
    except IntegrityError:
        row = await database.get(ConnectorDefinition, identifier, populate_existing=True)
        if row is None:
            raise
    return row


async def process_gmail_connection(
    database: AsyncSession,
    *,
    connection_id: UUID,
    settings: Settings,
    extractor: OperationalExtractor,
) -> list[UUID]:
    from navox.intelligence import ingestion

    original = await database.get(Connection, connection_id, populate_existing=True)
    if original is None or original.provider != "google":
        raise GoogleSourceAuthorizationError(code="google_scope_missing")
    user_id, workspace_id = original.user_id, original.workspace_id
    owner = await database.get(User, user_id, populate_existing=True)
    member = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    if (
        owner is None
        or member is None
        or owner.agent_paused
        or original.status != "active"
        or GMAIL_READ_SCOPE not in original.granted_scopes
    ):
        raise GoogleSourceAuthorizationError(code="google_scope_missing")
    cooldown = await source_cooldown(database, connection_id, "gmail")
    if cooldown is not None:
        raise GoogleSourceCooldownError(cooldown)
    authority = CalendarAuthority(
        connection_id,
        user_id,
        workspace_id,
        original.credential_reference,
        original.external_account_id,
        frozenset(original.granted_scopes),
        GMAIL_READ_SCOPE,
    )
    await database.commit()
    definition = await _definition(database)
    connector_id = uuid5(NAMESPACE_URL, f"navox:google-gmail:{connection_id}")
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
                    authorized_capabilities=[GMAIL_CAPABILITY],
                    provider_capabilities=[GMAIL_CAPABILITY],
                    credential_reference=authority.credential_id,
                    config=GmailConfig(legacy_connection_id=connection_id).model_dump(mode="json"),
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
        raise GoogleSourceAuthorizationError("Gmail connector ownership mismatch")
    await authority.check(database, True)
    now = await database_now(database)
    if (
        connection.sync_lease_token is not None
        and connection.sync_lease_expires_at is not None
        and utc(connection.sync_lease_expires_at) > now
    ):
        await database.rollback()
        raise GoogleSourceBusyError()
    cursor = await database.scalar(
        select(IntelligenceCursor)
        .where(
            IntelligenceCursor.connection_id == connection_id, IntelligenceCursor.source == "gmail"
        )
        .execution_options(populate_existing=True)
    )
    initial_token = cursor.cursor if cursor else None
    plan = await database.scalar(
        select(GmailSyncPlan)
        .where(GmailSyncPlan.connection_id == connection_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if plan is None:
        plan = GmailSyncPlan(
            connection_id=connection_id,
            initial_cursor=initial_token,
            cursor=initial_token,
            phase="list",
            entries=[],
            created_at=now,
        )
        database.add(plan)
        await database.flush()
    if plan.initial_cursor != initial_token:
        raise GoogleSourceAuthorizationError("Legacy Gmail cursor changed during sync")
    baseline = plan_checkpoint(plan)
    plan_id = plan.id
    policy = frozenset({GMAIL_CAPABILITY})
    connection.credential_reference = authority.credential_id
    connection.external_account_id = authority.account_id
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
        and run.consumer_version == extractor.extractor_version
        and run.cursor_before == connection.sync_cursor
        and decode_gmail_cursor(run.checkpoint_cursor).plan_id == plan_id
    )
    if reusable and run is not None:
        request_id = run.request_id
    else:
        connection.sync_cursor = baseline.encode()
        request_id = uuid4()
    known_values = await database.scalars(
        select(CommitmentSource.external_resource_id)
        .where(
            CommitmentSource.connection_id == connection_id,
            CommitmentSource.source_type == "gmail_message",
            CommitmentSource.external_resource_id.is_not(None),
        )
        .distinct()
        .limit(2001)
    )
    known = tuple(sorted({value for value in known_values if value is not None}))
    await database.commit()
    gateway = ingestion.GoogleSourceGateway()

    async def check(database: AsyncSession, lock: bool) -> None:
        await authority.check(database, lock)
        query = select(GmailSyncPlan).where(GmailSyncPlan.connection_id == connection_id)
        if lock:
            query = query.with_for_update()
        current = await database.scalar(query.execution_options(populate_existing=True))
        if current is None or current.id != plan_id:
            # Finalization stages deletion in this transaction, then the runtime
            # rechecks authority once more. A committed external deletion still fails.
            if finalizing and (plan in database.deleted or current is None):
                return
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail progress ownership changed")
        current_cursor = await database.scalar(
            select(IntelligenceCursor.cursor).where(
                IntelligenceCursor.connection_id == connection_id,
                IntelligenceCursor.source == "gmail",
            )
        )
        if current_cursor != initial_token:
            # A finalizer may stage the matching new token; no other change is accepted.
            if not finalizing:
                raise ConnectorRuntimeError(
                    "PERMISSION_DENIED", "Legacy Gmail cursor changed during sync"
                )

    async def reauthorize(
        db: AsyncSession, identifier: UUID, source: str
    ) -> tuple[Connection, User]:
        await owned_connector(
            db,
            connection_id=connector_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=True,
            lock_authority=True,
        )
        await check(db, True)
        return await ingestion.authorized_connection(db, identifier, source)

    def factory(
        config: Mapping[str, JsonValue], secrets: SecretAccessor | None
    ) -> GoogleGmailConnector:
        return GoogleGmailConnector(config, secrets, gateway=gateway, known_ids=known)

    registry = ConnectorRegistry()
    registry.register(GMAIL_MANIFEST, factory)
    runtime = ConnectorRuntime(
        registry,
        secret_broker=GoogleCalendarTokenBroker(
            settings, authority, connector_id, cache_access_tokens=True
        ),
        authority_check=check,
        retain_canonical_content=False,
        page_budget=3 * MAX_ENTRIES + MAX_PAGES + 16,
    )
    finalizing = False

    async def consume(resource: CanonicalResource) -> list[UUID]:
        await check(database, False)
        document = canonical_resource_to_source_document(
            resource, provenance_connection_id=connection_id
        )
        original = await database.get(Connection, connection_id, populate_existing=True)
        user = await database.get(User, user_id, populate_existing=True)
        if original is None or user is None:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail source ownership changed")
        try:
            ids, outcome = await ingestion._process_document(
                database,
                connection=original,
                user=user,
                document=document,
                source="gmail",
                extractor=extractor,
                mirror_source=False,
                reauthorize=reauthorize,
            )
        except (ConnectorAccessDenied, ConnectorRuntimeError, GoogleSourceAuthorizationError):
            raise
        except Exception as error:
            raise GmailConsumerFailure(error) from None
        current = await database.get(GmailSyncPlan, plan_id, populate_existing=True)
        if current is None:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail progress ownership changed")
        current.commitment_ids = sorted(set(current.commitment_ids) | {str(i) for i in ids})
        current.skipped += int(outcome == "skipped")
        current.rejected += int(outcome == "rejected")
        await database.flush()
        return sorted(ids, key=str)

    async def checkpoint(run: ConnectorSyncRun, page: SyncPage) -> ConnectorRuntimeError | None:
        state = decode_gmail_cursor(page.next_cursor)
        if state.plan_id != plan_id:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Gmail plan identity changed")
        current = await database.get(GmailSyncPlan, plan_id, populate_existing=True)
        if current is None:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail plan disappeared")
        current.phase = "process" if state.phase == "ready" else state.phase
        current.cursor, current.page_token = state.cursor, state.page_token
        current.pages, current.reset = state.pages, state.reset
        current.entries = [e.model_dump(mode="json") for e in state.entries]
        current.position, current.next_read_at = state.position, state.next_read_at
        current.commitment_ids = sorted(set(current.commitment_ids) | set(run.result_ids))
        await database.flush()
        if state.page_token_error:
            return GoogleCalendarFailure(
                GoogleSourceError("Gmail pagination token expired", http_status=400)
            )
        return None

    async def finalize(completed: ConnectorSyncRun) -> None:
        nonlocal finalizing
        state = decode_gmail_cursor(completed.checkpoint_cursor)
        if state.phase != "ready" or state.plan_id != plan_id:
            raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Gmail snapshot is incomplete")
        current = await database.scalar(
            select(IntelligenceCursor)
            .where(
                IntelligenceCursor.connection_id == connection_id,
                IntelligenceCursor.source == "gmail",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (current.cursor if current else None) != initial_token:
            raise ConnectorRuntimeError(
                "PERMISSION_DENIED", "Legacy Gmail cursor changed during sync"
            )
        current_plan = await database.get(GmailSyncPlan, plan_id, populate_existing=True)
        if current_plan is None:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Gmail progress ownership changed")
        if current is None:
            current = IntelligenceCursor(connection_id=connection_id, source="gmail")
            database.add(current)
        current.cursor, current.updated_at = state.cursor, await database_now(database)
        completed.result_ids = sorted(set(completed.result_ids) | set(current_plan.commitment_ids))
        database.add(
            AuditEvent(
                user_id=user_id,
                workspace_id=workspace_id,
                event_type="intelligence.source_processed",
                actor_type="system",
                entity_type="connection",
                entity_id=connection_id,
                event_metadata={
                    "source": "gmail",
                    "documents": len(state.entries),
                    "commitments": len(completed.result_ids),
                    "cursor_reset": state.reset,
                    "cursor_advanced": True,
                    "skipped_revisions": current_plan.skipped,
                    "rejected_revisions": current_plan.rejected,
                    "resume_plan_id": str(plan_id),
                    "commitment_ids": completed.result_ids,
                    "connector_connection_id": str(connector_id),
                },
            )
        )
        finalizing = True
        await database.delete(current_plan)
        # Don't flush deletion before the runtime's final fence (check sees the
        # scheduled deletion); the outer commit atomically removes the projection.

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
            checkpoint=checkpoint,
        )
        return sorted((UUID(i) for i in completed.result_ids), key=str)
    except GmailConsumerFailure as error:
        raise error.original from None
    except GoogleCalendarFailure as error:
        raise GoogleSourceError(
            "Gmail read failed",
            code=str(error.google_diagnostic["code"]),
            http_status=error.google_http_status,
            retry_after_seconds=error.retry_after_seconds,
        ) from None
    except SyncAlreadyActive:
        raise GoogleSourceBusyError() from None
    except ConnectorRuntimeError as error:
        if error.code in {"PERMISSION_DENIED", "AUTH_EXPIRED", "AUTH_REVOKED"}:
            raise GoogleSourceAuthorizationError(
                "Gmail authorization changed", code="google_scope_missing"
            ) from None
        raise GoogleSourceError(
            "Gmail synchronization deferred",
            code="google_rate_limited"
            if error.code == "RATE_LIMITED"
            else "google_provider_unavailable",
            retry_after_seconds=error.retry_after_seconds,
        ) from None
