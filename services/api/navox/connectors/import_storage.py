"""Encrypted immutable source snapshots. Source content is not a credential."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_parser import IMPORT_CAPABILITIES, ImportRecord
from navox.core.settings import Settings
from navox.db.models import ConnectorImportSnapshot
from navox.db.session import get_session_factory


class SnapshotEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: UUID
    workspace_id: UUID
    user_id: UUID
    digest: str
    imported_at: datetime
    records: list[ImportRecord] = Field(min_length=1, max_length=1_000)


def cipher(settings: Settings) -> Fernet:
    for secret in (settings.connector_secret_encryption_key, settings.google_token_encryption_key):
        if secret is not None and secret.get_secret_value():
            try:
                return Fernet(secret.get_secret_value().encode())
            except ValueError:
                break
    raise ConnectorRuntimeError("PERMANENT_FAILURE", "Import encryption is not configured")


def seal(envelope: SnapshotEnvelope, settings: Settings) -> str:
    return cipher(settings).encrypt(envelope.model_dump_json().encode()).decode()


class ImportSnapshotReader:
    """Trusted storage boundary; adapters receive records, never a database session or key."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def load(
        self, connection_id: UUID, workspace_id: UUID, user_id: UUID, digest: str
    ) -> SnapshotEnvelope:
        async with get_session_factory()() as database:
            try:
                connection = await owned_connector(
                    database,
                    connection_id=connection_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    require_active=True,
                    lock_connection=False,
                )
                row = await database.scalar(
                    select(ConnectorImportSnapshot).where(
                        ConnectorImportSnapshot.connection_id == connection.id,
                        ConnectorImportSnapshot.workspace_id == workspace_id,
                        ConnectorImportSnapshot.user_id == user_id,
                        ConnectorImportSnapshot.digest == digest,
                    )
                )
                if (
                    row is None
                    or connection.provider != "import"
                    or connection.config.get("snapshot_digest") != digest
                ):
                    raise ConnectorAccessDenied("Import access denied")
                capability = IMPORT_CAPABILITIES.get(str(connection.config.get("format")))
                if (
                    not capability
                    or capability not in connection.authorized_capabilities
                    or capability not in connection.provider_capabilities
                ):
                    raise ConnectorAccessDenied("Import read permission is required")
                ciphertext = row.encrypted_payload
                if len(ciphertext) > 8_000_000:
                    raise ValueError("Invalid snapshot size")
                await database.rollback()
                raw = cipher(self.settings).decrypt(ciphertext.encode())
                envelope = SnapshotEnvelope.model_validate(json.loads(raw))
                if (
                    envelope.connection_id,
                    envelope.workspace_id,
                    envelope.user_id,
                    envelope.digest,
                ) != (connection_id, workspace_id, user_id, digest):
                    raise ConnectorAccessDenied("Import access denied")
                if envelope.imported_at.tzinfo is None:
                    raise ValueError("Unzoned snapshot")
                return envelope
            except ConnectorAccessDenied:
                raise ConnectorRuntimeError("PERMISSION_DENIED", "Import access denied") from None
            except (InvalidToken, ValueError, UnicodeError, ValidationError):
                raise ConnectorRuntimeError(
                    "INVALID_PROVIDER_RESPONSE", "Import snapshot is unavailable or invalid"
                ) from None
