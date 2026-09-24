"""Tenant-bound encrypted credentials and expiring, operation-scoped secret handles.

This is trusted infrastructure, not a Python-code sandbox. Closing a lease drops
its references; it cannot erase a string already copied by a provider client.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from time import monotonic
from types import TracebackType
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.core.settings import Settings
from navox.db.models import AuditEvent, Connection, ConnectionCredential, ConnectorConnection

LEASE_PURPOSES = frozenset({"sync.read", "health.read", "events.manage", "events.cleanup"})
LEASE_TTL_SECONDS = 300.0
MAX_SECRET_BYTES = 65_536
_KEY_PRIMARY = "connector-v2:primary"
_KEY_LOCAL = "connector-v2:google-local"


class SecretBrokerError(ValueError):
    """Fixed, content-free credential failure."""


class ScopedSecretLease:
    """A bounded handle, explicitly closed after one provider operation."""

    __slots__ = ("connection_id", "purpose", "_values", "_expires_at", "_clock", "_closed")

    def __init__(
        self,
        connection_id: UUID,
        purpose: str,
        values: Mapping[str, str],
        *,
        ttl_seconds: float = LEASE_TTL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if purpose not in LEASE_PURPOSES:
            raise SecretBrokerError("Unsupported secret lease purpose")
        if not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= LEASE_TTL_SECONDS:
            raise SecretBrokerError("Invalid secret lease lifetime")
        self.connection_id = connection_id
        self.purpose = purpose
        self._values = dict(values)
        self._clock = clock
        self._expires_at = clock() + ttl_seconds
        self._closed = False

    def _require_live(self) -> None:
        if self._closed or self._clock() >= self._expires_at:
            self.close()
            raise SecretBrokerError("Secret lease is closed or expired")

    @property
    def names(self) -> frozenset[str]:
        self._require_live()
        return frozenset(self._values)

    def get(self, name: str) -> str:
        self._require_live()
        try:
            return self._values[name]
        except KeyError:
            raise SecretBrokerError("Secret is not available in this scoped lease") from None

    def close(self) -> None:
        self._closed = True
        self._values.clear()

    def __enter__(self) -> ScopedSecretLease:
        self._require_live()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __getstate__(self) -> object:
        raise TypeError("Secret leases cannot be serialized or copied")

    def __repr__(self) -> str:
        return (
            f"ScopedSecretLease(connection_id={self.connection_id!r}, "
            f"purpose={self.purpose!r}, values=<redacted>)"
        )


class SecretBroker:
    def __init__(self, settings: Settings) -> None:
        self._ciphers: dict[str, Fernet] = {}
        for version, configured in (
            (_KEY_PRIMARY, settings.connector_secret_encryption_key),
            (_KEY_LOCAL, settings.google_token_encryption_key),
        ):
            if configured is None or not configured.get_secret_value():
                continue
            try:
                self._ciphers[version] = Fernet(configured.get_secret_value().encode("utf-8"))
            except (ValueError, UnicodeError):
                raise SecretBrokerError("Connector encryption key is invalid") from None
        if not self._ciphers:
            raise SecretBrokerError("Connector secret encryption is not configured")
        self._write_version = _KEY_PRIMARY if _KEY_PRIMARY in self._ciphers else _KEY_LOCAL

    async def _owned(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        active: bool,
    ) -> ConnectorConnection:
        try:
            return await owned_connector(
                database,
                connection_id=connection_id,
                workspace_id=workspace_id,
                user_id=user_id,
                require_active=active,
            )
        except ConnectorAccessDenied:
            raise SecretBrokerError("Connector credential access denied") from None

    async def store(
        self,
        database: AsyncSession,
        values: Mapping[str, str],
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
    ) -> UUID:
        """Bind a new encrypted bundle to an owned connection in the caller's transaction.

        Explicit credential replacement may be done while paused; it never resumes
        processing. Legacy Google credentials are not migrated or mutated here.
        """
        normalized = _validate_values(values)
        connection = await self._owned(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            active=False,
        )
        if connection.provider == "google" or connection.config.get("legacy_bridge"):
            raise SecretBrokerError("Legacy connector credentials require their original flow")
        previous = await _credential(database, connection)
        provenance = await _owned_provenance(database, connection)
        provenance_previous = (
            await database.get(ConnectionCredential, provenance.credential_reference)
            if provenance is not None and provenance.credential_reference is not None
            else None
        )
        if previous is not None and previous.key_version.startswith("connector-v2:"):
            self._decrypt(previous, connection)
        elif previous is not None and previous.key_version != "connector-v1":
            raise SecretBrokerError("Legacy connector credentials require their original flow")
        # A stale mirror can survive a credential rotation made before this fix.
        # Prove it belongs to this native connection before replacing its link.
        if provenance_previous is not None and provenance_previous is not previous:
            if not provenance_previous.key_version.startswith("connector-v2:"):
                raise SecretBrokerError("Connector provenance credentials require review")
            self._decrypt(provenance_previous, connection)
        credential_id = uuid4()
        envelope = {
            "version": 2,
            "connection_id": str(connection_id),
            "workspace_id": str(workspace_id),
            "user_id": str(user_id),
            "credential_id": str(credential_id),
            "values": normalized,
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_SECRET_BYTES + 4_096:
            raise SecretBrokerError("Invalid connector secret bundle")
        sealed = self._ciphers[self._write_version].encrypt(encoded).decode("utf-8")
        database.add(
            ConnectionCredential(
                id=credential_id,
                encrypted_refresh_token=sealed,
                key_version=self._write_version,
            )
        )
        await database.flush()
        connection.credential_reference = credential_id
        if provenance is not None:
            provenance.credential_reference = credential_id
        await database.flush()
        if previous is not None and previous.key_version.startswith("connector-v2:"):
            await _delete_unreferenced(database, previous)
        if provenance_previous is not None and provenance_previous is not previous:
            await _delete_unreferenced(database, provenance_previous)
        _audit(database, connection, "stored")
        return credential_id

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
        if purpose not in LEASE_PURPOSES:
            raise SecretBrokerError("Unsupported secret lease purpose")
        if not names or len(names) > 32 or any(not _valid_name(name) for name in names):
            raise SecretBrokerError("Invalid secret lease scope")
        connection = await self._owned(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            active=purpose != "events.cleanup",
        )
        credential = await _credential(database, connection)
        if credential is None:
            raise SecretBrokerError("Connector credentials are unavailable")
        values = self._decrypt(credential, connection)
        if not set(names).issubset(values):
            raise SecretBrokerError("Requested secret is not authorized for this connection")
        lease = ScopedSecretLease(connection_id, purpose, {name: values[name] for name in names})
        _audit(database, connection, "leased", purpose=purpose)
        return lease

    def _decrypt(
        self, credential: ConnectionCredential, connection: ConnectorConnection
    ) -> dict[str, str]:
        cipher = self._ciphers.get(credential.key_version)
        if cipher is None:
            # Old unbound bundles cannot establish tenant ownership by decryption.
            raise SecretBrokerError("Connector credential format or key requires reauthorization")
        if len(credential.encrypted_refresh_token) > 150_000:
            raise SecretBrokerError("Connector credential bundle is invalid")
        try:
            raw = cipher.decrypt(credential.encrypted_refresh_token.encode("utf-8"))
            parsed = json.loads(raw)
        except (InvalidToken, UnicodeError, ValueError):
            raise SecretBrokerError("Connector credentials cannot be decrypted") from None
        expected = {
            "version": 2,
            "connection_id": str(connection.id),
            "workspace_id": str(connection.workspace_id),
            "user_id": str(connection.user_id),
            "credential_id": str(credential.id),
        }
        if not isinstance(parsed, dict) or any(parsed.get(k) != v for k, v in expected.items()):
            raise SecretBrokerError("Connector credential binding is invalid")
        values = parsed.get("values")
        if not isinstance(values, dict):
            raise SecretBrokerError("Connector credential bundle is invalid")
        return _validate_values(values)

    async def delete_for_connection(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
    ) -> None:
        """Detach owned credentials; never destroy a borrowed/shared credential row."""
        connection = await self._owned(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            active=False,
        )
        if connection.provider == "google" or connection.config.get("legacy_bridge"):
            raise SecretBrokerError("Legacy connector credentials require their original flow")
        provenance = await _owned_provenance(database, connection)
        credential = await _credential(database, connection)
        if credential is None:
            return
        self._decrypt(credential, connection)
        if provenance is not None and provenance.credential_reference not in {
            None,
            credential.id,
        }:
            raise SecretBrokerError("Connector provenance credentials require review")
        connection.credential_reference = None
        if provenance is not None:
            provenance.credential_reference = None
        await database.flush()
        await _delete_unreferenced(database, credential)
        _audit(database, connection, "deleted")


async def _credential(
    database: AsyncSession, connection: ConnectorConnection
) -> ConnectionCredential | None:
    if connection.credential_reference is None:
        return None
    return await database.get(
        ConnectionCredential, connection.credential_reference, populate_existing=True
    )


async def _owned_provenance(
    database: AsyncSession, connection: ConnectorConnection
) -> Connection | None:
    """Lock only this connector's generated compatibility row, never a borrowed legacy account."""
    if connection.legacy_connection_id is None:
        return None
    provenance = await database.scalar(
        select(Connection)
        .where(
            Connection.id == connection.legacy_connection_id,
            Connection.workspace_id == connection.workspace_id,
            Connection.user_id == connection.user_id,
            Connection.provider == connection.provider,
            Connection.external_account_id == f"connector:{connection.id}",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if provenance is None:
        raise SecretBrokerError("Connector provenance requires review")
    shared = await database.scalar(
        select(ConnectorConnection.id)
        .where(
            ConnectorConnection.legacy_connection_id == provenance.id,
            ConnectorConnection.id != connection.id,
        )
        .limit(1)
    )
    if shared is not None:
        raise SecretBrokerError("Connector provenance requires review")
    return provenance


async def _delete_unreferenced(database: AsyncSession, credential: ConnectionCredential) -> None:
    universal = await database.scalar(
        select(ConnectorConnection.id)
        .where(ConnectorConnection.credential_reference == credential.id)
        .limit(1)
    )
    legacy = await database.scalar(
        select(Connection.id).where(Connection.credential_reference == credential.id).limit(1)
    )
    if universal is None and legacy is None:
        await database.delete(credential)


def _valid_name(name: object) -> bool:
    return isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name) is not None


def _validate_values(values: Mapping[str, str]) -> dict[str, str]:
    if not values or len(values) > 32:
        raise SecretBrokerError("Invalid connector secret bundle")
    normalized: dict[str, str] = {}
    total = 0
    for name, value in values.items():
        if not _valid_name(name) or not isinstance(value, str) or not value:
            raise SecretBrokerError("Invalid connector secret bundle")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            raise SecretBrokerError("Invalid connector secret bundle") from None
        if size > 16_384:
            raise SecretBrokerError("Invalid connector secret bundle")
        total += size
        normalized[name] = value
    if total > MAX_SECRET_BYTES:
        raise SecretBrokerError("Invalid connector secret bundle")
    return normalized


def _audit(
    database: AsyncSession, connection: ConnectorConnection, outcome: str, *, purpose: str = ""
) -> None:
    database.add(
        AuditEvent(
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            event_type=f"connector.credentials.{outcome}",
            actor_type="system",
            entity_type="connector_connection",
            entity_id=connection.id,
            event_metadata={"purpose": purpose} if purpose else {},
        )
    )
