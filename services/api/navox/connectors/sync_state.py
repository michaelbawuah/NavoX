"""Database-backed fencing and retry state for trusted, read-only connector syncs.

No provider/model call may hold these locks. A token is required again before
committing each downstream revision, page checkpoint, or final source cursor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.contracts import ConnectorErrorCode, ConnectorRuntimeError
from navox.db.models import ConnectorConnection, ConnectorDefinition, ConnectorSyncRun

SYNC_LEASE_SECONDS = 90
SYNC_RENEW_SECONDS = 15
RETRYABLE_CODES = frozenset({"RATE_LIMITED", "PROVIDER_UNAVAILABLE", "TEMPORARY_FAILURE"})


class SyncLeaseLost(ConnectorRuntimeError):
    def __init__(self) -> None:
        super().__init__("TEMPORARY_FAILURE", "Sync attempt no longer owns its lease")


@dataclass(frozen=True)
class SyncTicket:
    run_id: UUID
    connection_id: UUID
    workspace_id: UUID
    user_id: UUID
    token: UUID
    generation: int
    cursor_before: str | None
    authorization_hash: str
    policy_allowed: frozenset[str]


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def database_now(database: AsyncSession) -> datetime:
    # PostgreSQL now() is transaction-start time, unsafe after a slow model call.
    expression = (
        func.clock_timestamp()
        if database.get_bind().dialect.name == "postgresql"
        else func.current_timestamp()
    )
    value = await database.scalar(select(expression))
    if not isinstance(value, datetime):
        raise ConnectorRuntimeError("TEMPORARY_FAILURE", "Database clock unavailable")
    return utc(value)


def authorization_hash(
    connection: ConnectorConnection,
    definition: ConnectorDefinition,
    policy_allowed: frozenset[str],
) -> str:
    payload = {
        "owner": [str(connection.workspace_id), str(connection.user_id)],
        "provider": connection.provider,
        "account": connection.external_account_id,
        "definition": [str(definition.id), definition.connector_key, definition.version],
        "manifest": definition.manifest,
        "trust": definition.trust_level,
        "config": connection.config,
        "credential": str(connection.credential_reference),
        "authorized": sorted(connection.authorized_capabilities),
        "provider_capabilities": sorted(connection.provider_capabilities),
        "policy": sorted(policy_allowed),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def claim_sync(
    database: AsyncSession,
    *,
    connection: ConnectorConnection,
    definition: ConnectorDefinition,
    request_id: UUID,
    policy_allowed: frozenset[str],
    trigger: str,
    consumer_version: str,
) -> SyncTicket | ConnectorSyncRun:
    """Caller has just locked/reloaded ownership. Never replay a failed run as success."""
    run = await database.scalar(
        select(ConnectorSyncRun)
        .where(
            ConnectorSyncRun.connector_connection_id == connection.id,
            ConnectorSyncRun.workspace_id == connection.workspace_id,
            ConnectorSyncRun.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )
    fingerprint = authorization_hash(connection, definition, policy_allowed)
    if run is not None and run.consumer_version != consumer_version:
        raise ConnectorRuntimeError("PERMANENT_FAILURE", "Sync consumer version changed")
    if run is not None and run.status == "completed":
        await database.commit()
        return run
    now = await database_now(database)
    if connection.retry_not_before is not None and utc(connection.retry_not_before) > now:
        delay = min(
            86_400, max(1, math.ceil((utc(connection.retry_not_before) - now).total_seconds()))
        )
        code: ConnectorErrorCode = (
            "RATE_LIMITED" if connection.last_error_code == "RATE_LIMITED" else "TEMPORARY_FAILURE"
        )
        raise ConnectorRuntimeError(
            code, "Connector is in retry backoff", retry_after_seconds=delay
        )
    if (
        connection.sync_lease_token is not None
        and connection.sync_lease_expires_at is not None
        and utc(connection.sync_lease_expires_at) > now
    ):
        raise ConnectorRuntimeError(
            "TEMPORARY_FAILURE", "Another sync is active", retry_after_seconds=15
        )
    if run is not None and (
        run.status not in {"running", "failed", "interrupted"}
        or run.generation == 0  # An old, uncheckpointed run needs an explicit new request.
        or run.generation != connection.sync_generation
        or run.authorization_hash != fingerprint
        or run.cursor_before != connection.sync_cursor
        or (run.status == "failed" and run.error_code not in RETRYABLE_CODES)
    ):
        raise ConnectorRuntimeError(
            "PERMANENT_FAILURE", "Sync cannot resume; use a new authorized request"
        )
    old_generation, old_run_id = connection.sync_generation, connection.sync_run_id
    generation = old_generation if run is not None else old_generation + 1
    run_id, token = (run.id if run is not None else uuid4()), uuid4()
    # CAS also protects SQLite-based tests: SELECT FOR UPDATE alone is not a
    # SQLite lock. PostgreSQL's row lock serializes the full claim transaction.
    claimed = await database.scalar(
        update(ConnectorConnection)
        .where(
            ConnectorConnection.id == connection.id,
            ConnectorConnection.workspace_id == connection.workspace_id,
            ConnectorConnection.user_id == connection.user_id,
            ConnectorConnection.sync_generation == old_generation,
            or_(
                ConnectorConnection.sync_lease_token.is_(None),
                ConnectorConnection.sync_lease_expires_at <= now,
                ConnectorConnection.sync_lease_expires_at.is_(None),
            ),
        )
        .values(
            sync_generation=generation,
            sync_run_id=run_id,
            sync_lease_token=token,
            sync_lease_expires_at=now + timedelta(seconds=SYNC_LEASE_SECONDS),
        )
        .returning(ConnectorConnection.id)
        .execution_options(synchronize_session=False)
    )
    if claimed is None:
        raise ConnectorRuntimeError(
            "TEMPORARY_FAILURE", "Another sync claimed this connection", retry_after_seconds=15
        )
    if run is None:
        if old_run_id is not None:
            await database.execute(
                update(ConnectorSyncRun)
                .where(
                    ConnectorSyncRun.id == old_run_id,
                    ConnectorSyncRun.connector_connection_id == connection.id,
                    ConnectorSyncRun.status.in_(["running", "failed", "interrupted"]),
                )
                .values(status="superseded", completed_at=now)
            )
        run = ConnectorSyncRun(
            id=run_id,
            connector_connection_id=connection.id,
            workspace_id=connection.workspace_id,
            request_id=request_id,
            trigger=trigger,
            status="running",
            cursor_before=connection.sync_cursor,
            checkpoint_cursor=connection.sync_cursor,
            generation=generation,
            authorization_hash=fingerprint,
            consumer_version=consumer_version,
            attempt_count=1,
        )
        database.add(run)
    else:
        run.status = "running"
        run.error_code = None
        run.completed_at = None
        run.attempt_count += 1
    ticket = SyncTicket(
        run_id,
        connection.id,
        connection.workspace_id,
        connection.user_id,
        token,
        generation,
        connection.sync_cursor,
        fingerprint,
        policy_allowed,
    )
    await database.commit()
    return ticket


async def guard_sync(
    database: AsyncSession, ticket: SyncTicket, *, lock_authority: bool = False
) -> tuple[ConnectorConnection, ConnectorSyncRun]:
    """Recheck committed owner/permission/version state before acknowledging work."""
    try:
        with database.no_autoflush:
            connection = await owned_connector(
                database,
                connection_id=ticket.connection_id,
                workspace_id=ticket.workspace_id,
                user_id=ticket.user_id,
                require_active=True,
                lock_authority=lock_authority,
            )
            definition = await database.get(
                ConnectorDefinition, connection.connector_definition_id, populate_existing=True
            )
            run = await database.get(ConnectorSyncRun, ticket.run_id, populate_existing=True)
            now = await database_now(database)
    except ConnectorAccessDenied:
        raise ConnectorRuntimeError(
            "PERMISSION_DENIED", "Connector authorization changed"
        ) from None
    if (
        connection.sync_lease_token != ticket.token
        or connection.sync_run_id != ticket.run_id
        or connection.sync_generation != ticket.generation
        or connection.sync_lease_expires_at is None
        or utc(connection.sync_lease_expires_at) <= now
        or run is None
        or run.status != "running"
    ):
        raise SyncLeaseLost()
    if (
        definition is None
        or authorization_hash(connection, definition, ticket.policy_allowed)
        != ticket.authorization_hash
        or connection.sync_cursor != ticket.cursor_before
    ):
        raise ConnectorRuntimeError(
            "PERMISSION_DENIED", "Connector authorization or cursor changed"
        )
    connection.sync_lease_expires_at = now + timedelta(seconds=SYNC_LEASE_SECONDS)
    return connection, run


@asynccontextmanager
async def renew_sync_lease(database: AsyncSession, ticket: SyncTicket) -> AsyncIterator[None]:
    """Renew with a separate session; never share an AsyncSession across tasks."""
    bind = database.bind
    if not isinstance(bind, AsyncEngine):
        raise ConnectorRuntimeError("PERMANENT_FAILURE", "Sync requires a dedicated engine session")
    sessions = async_sessionmaker(bind, expire_on_commit=False)

    async def renew() -> None:
        while True:
            await asyncio.sleep(SYNC_RENEW_SECONDS)
            try:
                async with sessions() as heartbeat:
                    connection = await heartbeat.scalar(
                        select(ConnectorConnection)
                        .where(ConnectorConnection.id == ticket.connection_id)
                        .with_for_update()
                    )
                    now = await database_now(heartbeat)
                    if (
                        connection is None
                        or connection.sync_lease_token != ticket.token
                        or connection.sync_generation != ticket.generation
                        or connection.sync_lease_expires_at is None
                        or utc(connection.sync_lease_expires_at) <= now
                        or connection.status not in {"CONNECTED", "DEGRADED"}
                    ):
                        return
                    connection.sync_lease_expires_at = now + timedelta(seconds=SYNC_LEASE_SECONDS)
                    await heartbeat.commit()
            except Exception:
                # No success is inferred from heartbeat failure. Checkpoints must
                # reacquire a live token; takeover fences this worker even if its I/O finishes.
                return

    task = asyncio.create_task(renew())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def fail_sync(database: AsyncSession, ticket: SyncTicket, error: BaseException) -> None:
    """Keep accepted checkpoints; a stale attempt cannot alter its successor."""
    await database.rollback()
    connection = await database.scalar(
        select(ConnectorConnection)
        .where(
            ConnectorConnection.id == ticket.connection_id,
            ConnectorConnection.sync_lease_token == ticket.token,
            ConnectorConnection.sync_generation == ticket.generation,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if connection is None:
        await database.rollback()
        return
    run = await database.get(ConnectorSyncRun, ticket.run_id, populate_existing=True)
    if run is None or run.status != "running":
        await database.rollback()
        return
    now = await database_now(database)
    cancelled = isinstance(error, asyncio.CancelledError)
    code: ConnectorErrorCode = (
        error.code if isinstance(error, ConnectorRuntimeError) else "TEMPORARY_FAILURE"
    )
    run.status = "interrupted" if cancelled else "failed"
    run.error_code = code
    run.completed_at = now
    connection.sync_lease_token = None
    connection.sync_lease_expires_at = None
    if code in RETRYABLE_CODES and not cancelled:
        supplied = error.retry_after_seconds if isinstance(error, ConnectorRuntimeError) else None
        delay = supplied or (60 if code == "RATE_LIMITED" else 10)
        deadline = now + timedelta(seconds=delay)
        if connection.retry_not_before is None or utc(connection.retry_not_before) < deadline:
            connection.retry_not_before = deadline
    if connection.status in {"CONNECTED", "DEGRADED"}:
        connection.last_error_code = code
        connection.health_state = (
            "RATE_LIMITED"
            if code == "RATE_LIMITED"
            else "AUTH_EXPIRED"
            if code in {"AUTH_EXPIRED", "AUTH_REVOKED"}
            else "SYNC_FAILED"
        )
    await database.commit()
