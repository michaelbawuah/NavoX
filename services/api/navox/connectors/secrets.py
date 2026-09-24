from __future__ import annotations

import json
import re
from collections.abc import Mapping
from types import MappingProxyType
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.models import ConnectionCredential, ConnectorConnection


class SecretBrokerError(ValueError):
    pass


class ScopedSecretLease:
    """Short-lived in-process secret view. Values are intentionally not serializable."""

    __slots__ = ("connection_id", "purpose", "_values")

    def __init__(self, connection_id: UUID, purpose: str, values: Mapping[str, str]) -> None:
        self.connection_id = connection_id
        self.purpose = purpose
        self._values = MappingProxyType(dict(values))

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._values)

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise SecretBrokerError("Secret is not available in this scoped lease") from None

    def __repr__(self) -> str:
        return (
            f"ScopedSecretLease(connection_id={self.connection_id!r}, "
            f"purpose={self.purpose!r}, names={sorted(self.names)!r}, values=<redacted>)"
        )


class SecretBroker:
    def __init__(self, settings: Settings) -> None:
        configured = (
            settings.connector_secret_encryption_key or settings.google_token_encryption_key
        )
        if configured is None or not configured.get_secret_value():
            raise SecretBrokerError("Connector secret encryption is not configured")
        try:
            self._cipher = Fernet(configured.get_secret_value().encode("utf-8"))
        except ValueError as error:
            raise SecretBrokerError("Connector secret encryption key is invalid") from error

    async def store(
        self,
        database: AsyncSession,
        values: Mapping[str, str],
    ) -> UUID:
        if not values:
            raise SecretBrokerError("At least one connector secret is required")
        normalized: dict[str, str] = {}
        for name, value in values.items():
            if re.fullmatch(r"^[A-Z][A-Z0-9_]{1,63}$", name) is None:
                raise SecretBrokerError("Invalid connector secret name")
            if not value or len(value) > 16_384:
                raise SecretBrokerError("Invalid connector secret value")
            normalized[name] = value
        sealed = self._cipher.encrypt(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("utf-8")
        credential = ConnectionCredential(
            encrypted_refresh_token=sealed, key_version="connector-v1"
        )
        database.add(credential)
        await database.flush()
        return credential.id

    async def lease(
        self,
        database: AsyncSession,
        *,
        connection_id: UUID,
        purpose: str,
        names: set[str] | frozenset[str],
    ) -> ScopedSecretLease:
        if re.fullmatch(r"^[a-z][a-z0-9_.-]{1,80}$", purpose) is None:
            raise SecretBrokerError("Invalid secret lease purpose")
        connection = await database.get(ConnectorConnection, connection_id)
        if connection is None or connection.credential_reference is None:
            raise SecretBrokerError("Connector credentials are unavailable")
        credential = await database.get(ConnectionCredential, connection.credential_reference)
        if credential is None:
            raise SecretBrokerError("Connector credentials are unavailable")
        try:
            raw = self._cipher.decrypt(
                credential.encrypted_refresh_token.encode("utf-8")
            ).decode("utf-8")
            parsed = json.loads(raw)
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SecretBrokerError("Connector credentials cannot be decrypted") from error
        if not isinstance(parsed, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
        ):
            raise SecretBrokerError("Connector credential bundle is invalid")
        requested = set(names)
        if not requested.issubset(parsed):
            raise SecretBrokerError("Requested secret is not authorized for this connection")
        return ScopedSecretLease(
            connection_id,
            purpose,
            {name: parsed[name] for name in sorted(requested)},
        )

    async def delete_for_connection(
        self,
        database: AsyncSession,
        connection_id: UUID,
    ) -> None:
        connection = await database.get(ConnectorConnection, connection_id)
        if connection is None or connection.credential_reference is None:
            return
        credential = await database.get(ConnectionCredential, connection.credential_reference)
        connection.credential_reference = None
        if credential is not None:
            await database.delete(credential)
