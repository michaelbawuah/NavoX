"""Read-only connector execution with fenced, transactional acceptance checkpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorConnectionContext,
    ConnectorManifest,
    ConnectorRuntimeError,
    NavoXConnector,
    SyncPage,
    SyncRequest,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.secrets import ScopedSecretLease, SecretBrokerError
from navox.connectors.sync_state import (
    SyncTicket,
    claim_sync,
    database_now,
    fail_sync,
    guard_sync,
    renew_sync_lease,
    utc,
)
from navox.core.settings import Settings
from navox.db.models import (
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
)

ResourceConsumer = Callable[[CanonicalResource], Awaitable[list[UUID] | None]]
AuthorityCheck = Callable[[AsyncSession, bool], Awaitable[None]]
SyncFinalizer = Callable[[ConnectorSyncRun], Awaitable[None]]
PageCheckpoint = Callable[[ConnectorSyncRun, SyncPage], Awaitable[ConnectorRuntimeError | None]]


class ReadSecretBroker(Protocol):
    """Trusted credential adapters return only a bounded operation handle."""

    async def lease(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        purpose: str,
        names: set[str] | frozenset[str],
    ) -> ScopedSecretLease: ...


MAX_SYNC_PAGES = 50
MAX_SYNC_RESOURCES = 50_000
PROVIDER_OPERATION_SECONDS = 240


@contextmanager
def consumer_transaction(database: AsyncSession) -> Iterator[None]:
    """The trusted consumer may stage writes, never commit before the runtime's fence."""

    def deny_commit(session: Session) -> None:
        if not session.in_nested_transaction():
            raise ConnectorRuntimeError(
                "PERMANENT_FAILURE", "Consumer cannot commit sync transaction"
            )

    event.listen(database.sync_session, "before_commit", deny_commit)
    try:
        yield
    finally:
        event.remove(database.sync_session, "before_commit", deny_commit)


def resource_hash(resource: CanonicalResource) -> str:
    return hashlib.sha256(
        json.dumps(
            resource.model_dump(mode="json", exclude={"retrieved_at"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class ConnectorRuntime:
    """Trusted adapters receive scoped handles, never direct database access."""

    def __init__(
        self,
        registry: ConnectorRegistry,
        capability_gateway: CapabilityGateway | None = None,
        secret_broker: ReadSecretBroker | None = None,
        *,
        authority_check: AuthorityCheck | None = None,
        retain_canonical_content: bool = True,
        page_budget: int = MAX_SYNC_PAGES,
        knowledge_settings: Settings | None = None,
    ) -> None:
        self.registry = registry
        self.capability_gateway = capability_gateway or CapabilityGateway()
        self.secret_broker = secret_broker
        self.authority_check = authority_check
        self.retain_canonical_content = retain_canonical_content
        if not 1 <= page_budget <= MAX_SYNC_RESOURCES:
            raise ValueError("Invalid trusted connector page budget")
        self.page_budget = page_budget
        self.knowledge_settings = knowledge_settings

    async def read_preview(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        policy_allowed: frozenset[str],
        max_pages: int = 12,
        max_resources: int = 400,
    ) -> tuple[CanonicalResource, ...]:
        """Transient, bounded provider read with the normal connector authority fence.

        It never persists a source body or advances a sync cursor. The caller may
        project only fields from resources that survive the final scope recheck.
        """
        if not 1 <= max_pages <= 20 or not 1 <= max_resources <= 400:
            raise ValueError("Invalid preview bound")
        try:
            connection = await owned_connector(
                database,
                connection_id=connection_id,
                workspace_id=workspace_id,
                user_id=user_id,
                require_active=True,
                lock_connection=False,
            )
        except ConnectorAccessDenied:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector access denied") from None
        definition = await database.get(
            ConnectorDefinition, connection.connector_definition_id, populate_existing=True
        )
        if definition is None or not definition.active:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector definition unavailable")
        context = ConnectorConnectionContext.model_validate(
            {
                "id": connection.id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "connector_id": definition.connector_key,
                "provider": connection.provider,
                "external_account_id": connection.external_account_id,
                "status": connection.health_state,
                "authorized_capabilities": connection.authorized_capabilities,
                "config": connection.config,
            }
        )
        try:
            registered = self.registry.get(definition.connector_key)
        except KeyError:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector is unavailable") from None
        manifest = registered.factory(context.config, None).get_manifest()
        if manifest.id != registered.manifest.id or manifest.version != definition.version:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector definition changed")
        capabilities = self.capability_gateway.evaluate(
            manifest=manifest,
            provider_capabilities=set(connection.provider_capabilities),
            user_authorized=set(connection.authorized_capabilities),
            policy_allowed=policy_allowed,
            health_state=context.status,
        ).read
        if not capabilities:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector read permission is absent")
        cursor: str | None = None
        seen_cursors: set[str] = set()
        resources: list[CanonicalResource] = []
        started_at = await database_now(database)
        for _ in range(max_pages):
            async with self._adapter(
                database, context, manifest, capabilities, purpose="sync.read"
            ) as connector:
                async with asyncio.timeout(20):
                    output = await connector.sync(
                        SyncRequest(
                            connection_id=connection_id,
                            workspace_id=workspace_id,
                            cursor=cursor,
                            limit=100,
                            capabilities=capabilities,
                            started_at=started_at,
                        )
                    )
            try:
                page = SyncPage.model_validate(output.model_dump(mode="json"))
            except (ValidationError, AttributeError, ValueError):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Invalid connector page"
                ) from None
            for resource in page.resources:
                self._validate_resource(context, manifest, resource)
            resources.extend(page.resources)
            if len(resources) > max_resources:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Preview resource bound exceeded"
                )
            try:
                current = await owned_connector(
                    database,
                    connection_id=connection_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    require_active=True,
                    lock_connection=False,
                )
            except ConnectorAccessDenied:
                raise ConnectorRuntimeError(
                    "PERMISSION_DENIED", "Connector access denied"
                ) from None
            if current.config != context.config or not capabilities.issubset(
                set(current.authorized_capabilities) & set(current.provider_capabilities)
            ):
                raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector permission changed")
            await self._check_authority(database, lock=False)
            if not page.has_more:
                return tuple(resources)
            if not page.next_cursor or page.next_cursor in seen_cursors:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Preview cursor did not progress"
                )
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        raise ConnectorRuntimeError("INVALID_PROVIDER_RESPONSE", "Preview page bound exceeded")

    async def _check_authority(self, database: AsyncSession, *, lock: bool) -> None:
        if self.authority_check is not None:
            await self.authority_check(database, lock)

    async def sync(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        request_id: UUID,
        policy_allowed: set[str] | frozenset[str],
        consume: ResourceConsumer,
        trigger: str = "manual",
        consumer_version: str = "canonical-consumer.v1",
        finalize: SyncFinalizer | None = None,
        checkpoint: PageCheckpoint | None = None,
    ) -> ConnectorSyncRun:
        if not consumer_version or len(consumer_version) > 128:
            raise ConnectorRuntimeError("PERMANENT_FAILURE", "Invalid consumer version")
        ticket: SyncTicket | None = None
        try:
            connection = await owned_connector(
                database,
                connection_id=connection_id,
                workspace_id=workspace_id,
                user_id=user_id,
                require_active=True,
            )
            definition = await database.get(
                ConnectorDefinition, connection.connector_definition_id, populate_existing=True
            )
            if definition is None or not definition.active:
                raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector definition unavailable")
            context = ConnectorConnectionContext.model_validate(
                {
                    "id": connection.id,
                    "workspace_id": workspace_id,
                    "user_id": user_id,
                    "connector_id": definition.connector_key,
                    "provider": connection.provider,
                    "external_account_id": connection.external_account_id,
                    "status": connection.health_state,
                    "authorized_capabilities": connection.authorized_capabilities,
                    "config": connection.config,
                }
            )
            registered = self.registry.get(definition.connector_key)
            manifest = registered.factory(context.config, None).get_manifest()
            if (
                manifest.id != registered.manifest.id
                or manifest.version != registered.manifest.version
                or definition.version != manifest.version
            ):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Connector version mismatch"
                )
            if manifest.connector_class == "BROWSER_ASSISTED":
                # A persisted config bit cannot attest to a fresh user action.
                # Browser capture needs a separate, owner-authenticated command.
                raise ConnectorRuntimeError(
                    "PERMISSION_DENIED", "Browser capture requires an explicit user action"
                )
            authorized = self.capability_gateway.evaluate(
                manifest=manifest,
                provider_capabilities=set(connection.provider_capabilities),
                user_authorized=set(connection.authorized_capabilities),
                policy_allowed=policy_allowed,
                health_state="CONNECTED",
            )
            if not authorized.read:
                raise ConnectorRuntimeError("PERMISSION_DENIED", "No authorized read capability")
            await self._check_authority(database, lock=True)
            claimed = await claim_sync(
                database,
                connection=connection,
                definition=definition,
                request_id=request_id,
                policy_allowed=frozenset(policy_allowed),
                trigger=trigger,
                consumer_version=consumer_version,
            )
            if isinstance(claimed, ConnectorSyncRun):
                return claimed
            ticket = claimed
            async with renew_sync_lease(database, ticket):
                if checkpoint is None:
                    return await self._run(
                        database, ticket, context, manifest, authorized.read, consume, finalize
                    )
                return await self._run(
                    database,
                    ticket,
                    context,
                    manifest,
                    authorized.read,
                    consume,
                    finalize,
                    checkpoint,
                )
        except ConnectorAccessDenied:
            denied = ConnectorRuntimeError("PERMISSION_DENIED", "Connector access denied")
            if ticket is not None:
                await fail_sync(database, ticket, denied)
            else:
                await database.rollback()
            raise denied from None
        except (Exception, asyncio.CancelledError) as error:
            if ticket is not None:
                await fail_sync(database, ticket, error)
            else:
                await database.rollback()
            if isinstance(error, (ConnectorRuntimeError, asyncio.CancelledError)):
                raise
            raise ConnectorRuntimeError(
                "TEMPORARY_FAILURE", "Connector processing failed"
            ) from None

    async def _run(
        self,
        database: AsyncSession,
        ticket: SyncTicket,
        context: ConnectorConnectionContext,
        manifest: ConnectorManifest,
        capabilities: frozenset[str],
        consume: ResourceConsumer,
        finalize: SyncFinalizer | None,
        checkpoint: PageCheckpoint | None = None,
    ) -> ConnectorSyncRun:
        _, run = await guard_sync(database, ticket)
        complete = run.fetch_complete
        await database.commit()
        if not complete:
            async with self._adapter(
                database, context, manifest, capabilities, purpose="health.read"
            ) as connector:
                async with asyncio.timeout(PROVIDER_OPERATION_SECONDS):
                    health = await connector.health(context)
            connection, _ = await guard_sync(database, ticket, lock_authority=True)
            await self._check_authority(database, lock=True)
            if health.state not in {"CONNECTED", "DEGRADED"}:
                code = health.reason_code or (
                    "RATE_LIMITED"
                    if health.state == "RATE_LIMITED"
                    else "AUTH_EXPIRED"
                    if health.state == "AUTH_EXPIRED"
                    else "TEMPORARY_FAILURE"
                )
                raise ConnectorRuntimeError(
                    code,
                    "Connector health prevents sync",
                    retry_after_seconds=health.retry_after_seconds,
                )
            connection.health_state = health.state
            connection.last_error_code = health.reason_code
            if health.state == "CONNECTED":
                connection.last_healthy_at = health.checked_at
            await database.commit()

        while not complete:
            _, run = await guard_sync(database, ticket)
            cursor = run.checkpoint_cursor
            started_at = utc(run.started_at)
            if run.pages_completed >= self.page_budget:
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Connector page budget exceeded"
                )
            await database.commit()
            async with self._adapter(
                database, context, manifest, capabilities, purpose="sync.read"
            ) as connector:
                async with asyncio.timeout(PROVIDER_OPERATION_SECONDS):
                    output = await connector.sync(
                        SyncRequest(
                            connection_id=ticket.connection_id,
                            workspace_id=ticket.workspace_id,
                            cursor=cursor,
                            capabilities=capabilities,
                            started_at=started_at,
                        )
                    )
            try:
                # Revalidate returned model instances too; model_copy can bypass validators.
                page = SyncPage.model_validate(output.model_dump(mode="json"))
            except (ValidationError, AttributeError, ValueError):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Invalid connector page"
                ) from None
            connection, run = await guard_sync(database, ticket)
            next_hash = hashlib.sha256((page.next_cursor or "").encode()).hexdigest()
            if page.has_more and (
                page.next_cursor is None
                or page.next_cursor == cursor
                or next_hash in run.cursor_hashes
            ):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Connector cursor did not progress"
                )
            for resource in page.resources:
                self._validate_resource(context, manifest, resource)
            await database.commit()
            for resource in page.resources:
                await self._accept(database, ticket, resource, consume)
            connection, run = await guard_sync(database, ticket, lock_authority=True)
            await self._check_authority(database, lock=True)
            deferred_error = None
            if checkpoint is not None:
                with consumer_transaction(database):
                    deferred_error = await checkpoint(run, page)
                connection, run = await guard_sync(database, ticket, lock_authority=True)
                await self._check_authority(database, lock=True)
            run.checkpoint_cursor = page.next_cursor
            run.pages_completed += 1
            run.fetch_complete = not page.has_more
            if page.has_more:
                run.cursor_hashes = [*run.cursor_hashes, next_hash]
            complete = run.fetch_complete
            await database.commit()
            if deferred_error is not None:
                raise deferred_error

        connection, run = await guard_sync(database, ticket, lock_authority=True)
        await self._check_authority(database, lock=True)
        if finalize is not None:
            with consumer_transaction(database):
                await finalize(run)
            # A finalizer stages compatibility state, never acknowledges the run.
            connection, run = await guard_sync(database, ticket, lock_authority=True)
            await self._check_authority(database, lock=True)
        connection.sync_cursor = run.checkpoint_cursor
        connection.last_synced_at = await database_now(database)
        connection.retry_not_before = None
        connection.sync_lease_token = None
        connection.sync_lease_expires_at = None
        run.status = "completed"
        run.cursor_after = run.checkpoint_cursor
        run.error_code = None
        run.completed_at = connection.last_synced_at
        await database.commit()
        return run

    async def _accept(
        self,
        database: AsyncSession,
        ticket: SyncTicket,
        resource: CanonicalResource,
        consume: ResourceConsumer,
    ) -> None:
        connection, run = await guard_sync(database, ticket)
        await self._check_authority(database, lock=False)
        digest = resource_hash(resource)
        receipt = await database.scalar(
            select(ConnectorSyncReceipt.id).where(
                ConnectorSyncReceipt.sync_run_id == ticket.run_id,
                ConnectorSyncReceipt.workspace_id == ticket.workspace_id,
                ConnectorSyncReceipt.resource_id == resource.resource_id,
                ConnectorSyncReceipt.content_hash == digest,
            )
        )
        if receipt is not None:
            await database.commit()
            return
        if run.resource_count >= MAX_SYNC_RESOURCES:
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Connector resource budget exceeded"
            )
        existing = await database.get(
            ConnectorResource, resource.resource_id, populate_existing=True
        )
        stale = self._is_stale(existing, resource)
        previous = await database.scalar(
            select(ConnectorSyncReceipt)
            .where(
                ConnectorSyncReceipt.workspace_id == ticket.workspace_id,
                ConnectorSyncReceipt.resource_id == resource.resource_id,
                ConnectorSyncReceipt.content_hash == digest,
                ConnectorSyncReceipt.consumer_version == run.consumer_version,
                ConnectorSyncReceipt.outcome.in_(["processed", "duplicate"]),
            )
            .limit(1)
        )
        reusable = previous is not None and existing is not None and existing.content_hash == digest
        identifiers = (
            [UUID(i) for i in previous.result_ids] if reusable and previous is not None else None
        )
        await database.commit()  # No connection/authority lock across the model call.
        if not stale and not reusable:
            with consumer_transaction(database):
                async with asyncio.timeout(PROVIDER_OPERATION_SECONDS):
                    identifiers = await consume(resource)
        connection, run = await guard_sync(database, ticket, lock_authority=True)
        await self._check_authority(database, lock=True)
        existing = await database.get(
            ConnectorResource, resource.resource_id, populate_existing=True
        )
        # Only this fenced attempt can write synced resources. Do not resurrect
        # an older revision even when delivery order is reversed.
        stale = self._is_stale(existing, resource)
        duplicate = existing is not None and existing.content_hash == digest
        if not stale:
            await self._persist_resource(database, resource, digest)
            if self.knowledge_settings is not None:
                from navox.knowledge.jobs import KnowledgeResourceWork
                from navox.knowledge.lifecycle import process_resource_work

                await process_resource_work(
                    database,
                    payload=KnowledgeResourceWork(
                        str(ticket.workspace_id),
                        str(ticket.user_id),
                        str(resource.resource_id),
                        digest,
                    ),
                    settings=self.knowledge_settings,
                )
        if identifiers is not None:
            if not isinstance(identifiers, list) or not all(
                isinstance(i, UUID) for i in identifiers
            ):
                raise ConnectorRuntimeError("PERMANENT_FAILURE", "Invalid consumer receipt")
            run.result_ids = sorted(set(run.result_ids) | {str(i) for i in identifiers})
        database.add(
            ConnectorSyncReceipt(
                sync_run_id=ticket.run_id,
                workspace_id=ticket.workspace_id,
                resource_id=resource.resource_id,
                content_hash=digest,
                consumer_version=run.consumer_version,
                result_ids=[str(i) for i in (identifiers or [])],
                outcome="stale" if stale else "duplicate" if duplicate else "processed",
            )
        )
        run.resource_count += 1
        run.stale_count += int(stale)
        run.duplicate_count += int(duplicate and not stale)
        run.processed_count += int(not stale and not duplicate)
        await database.commit()

    @staticmethod
    def _is_stale(existing: ConnectorResource | None, resource: CanonicalResource) -> bool:
        return bool(
            existing is not None
            and existing.source_updated_at is not None
            and resource.updated_at is not None
            and utc(existing.source_updated_at) > utc(resource.updated_at)
        )

    @staticmethod
    def _validate_resource(
        context: ConnectorConnectionContext,
        manifest: ConnectorManifest,
        resource: CanonicalResource,
    ) -> None:
        if resource.workspace_id != context.workspace_id:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Cross-workspace resource rejected")
        if resource.connector_connection_id != context.id:
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Cross-connection resource rejected")
        if (
            resource.provider != context.provider
            or resource.resource_type not in manifest.resource_types
        ):
            raise ConnectorRuntimeError(
                "INVALID_PROVIDER_RESPONSE", "Undeclared provider or resource type"
            )

    async def _persist_resource(
        self, database: AsyncSession, resource: CanonicalResource, digest: str
    ) -> None:
        existing = await database.get(
            ConnectorResource, resource.resource_id, populate_existing=True
        )
        if existing is None:
            existing = ConnectorResource(
                id=resource.resource_id,
                workspace_id=resource.workspace_id,
                connector_connection_id=resource.connector_connection_id,
                provider=resource.provider,
                resource_type=resource.resource_type,
                external_id=resource.external_id,
            )
            database.add(existing)
        if (
            existing.workspace_id != resource.workspace_id
            or existing.connector_connection_id != resource.connector_connection_id
        ):
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Resource ownership mismatch")
        existing.external_parent_id = resource.external_parent_id
        existing.version = resource.version
        if self.retain_canonical_content:
            existing.canonical = dict(resource.canonical)
            existing.provider_metadata = dict(resource.provider_metadata)
        else:
            # Selected by trusted application code, never by provider metadata.
            # The complete transient resource is hashed/consumed but not stored.
            status = resource.canonical.get("status")
            existing.canonical = {
                "status": status if isinstance(status, str) else "active",
                "content_persisted": False,
            }
            existing.provider_metadata = {"resource_hash": digest}
        existing.source_url = resource.source_url
        existing.source_created_at = resource.created_at
        existing.source_updated_at = resource.updated_at
        existing.retrieved_at = resource.retrieved_at
        existing.content_hash = digest
        existing.deleted = resource.canonical.get("status") in {"deleted", "cancelled"}
        await database.flush()

    @asynccontextmanager
    async def _adapter(
        self,
        database: AsyncSession,
        context: ConnectorConnectionContext,
        manifest: ConnectorManifest,
        capabilities: frozenset[str],
        *,
        purpose: str,
    ) -> AsyncIterator[NavoXConnector]:
        lease = None
        try:
            current = await owned_connector(
                database,
                connection_id=context.id,
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                require_active=True,
            )
            if (
                current.config != context.config
                or current.provider != context.provider
                or current.external_account_id != context.external_account_id
                or not capabilities.issubset(
                    set(current.authorized_capabilities) & set(current.provider_capabilities)
                )
            ):
                raise ConnectorAccessDenied("Connector authorization changed")
            await self._check_authority(database, lock=False)
            if manifest.required_secrets:
                if self.secret_broker is None:
                    raise SecretBrokerError("Connector credentials are unavailable")
                lease = await self.secret_broker.lease(
                    database,
                    connection_id=context.id,
                    workspace_id=context.workspace_id,
                    user_id=context.user_id,
                    purpose=purpose,
                    names=frozenset(manifest.required_secrets),
                )
            connector = self.registry.build(manifest.id, context.config, lease)
            # Lease issuance may audit a credential access. Finish that short
            # transaction before network I/O; pause/revoke must not wait for I/O.
            await database.commit()
        except (ConnectorAccessDenied, SecretBrokerError):
            if lease is not None:
                lease.close()
            raise ConnectorRuntimeError("PERMISSION_DENIED", "Connector access denied") from None
        except BaseException:
            if lease is not None:
                lease.close()
            raise
        try:
            yield connector
        finally:
            if lease is not None:
                lease.close()
