from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.secrets import SecretBroker, SecretBrokerError, _delete_unreferenced
from navox.core.settings import get_settings
from navox.db.models import Connection, ConnectionCredential, ConnectorConnection


async def ensure_provenance_connection(
    database: AsyncSession,
    connector_connection: ConnectorConnection,
) -> Connection:
    """Provide SPEC-002 with its existing provider-neutral provenance anchor.

    Google mirrors reuse the original connection. New universal connectors get
    a compatibility row that carries identifiers/capabilities only; raw secrets
    remain referenced through the protected credential table.
    """

    if connector_connection.legacy_connection_id is not None:
        existing = await database.scalar(
            select(Connection)
            .where(Connection.id == connector_connection.legacy_connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is None:
            raise ValueError("Connector provenance connection is missing")
        _validate_ownership(existing, connector_connection)
        if existing.provider != "google" and not connector_connection.config.get("legacy_bridge"):
            if existing.external_account_id != f"connector:{connector_connection.id}":
                raise ValueError("Connector provenance is not an owned compatibility row")
            shared = await database.scalar(
                select(ConnectorConnection.id)
                .where(
                    ConnectorConnection.legacy_connection_id == existing.id,
                    ConnectorConnection.id != connector_connection.id,
                )
                .limit(1)
            )
            if shared is not None:
                raise ValueError("Connector provenance is shared")
            previous_reference = existing.credential_reference
            _refresh(existing, connector_connection)
        await database.flush()
        if existing.provider != "google" and not connector_connection.config.get("legacy_bridge"):
            await _release_stale_credential(
                database, connector_connection, previous_reference, existing.credential_reference
            )
        return existing

    status = _legacy_status(connector_connection)
    provenance = Connection(
        user_id=connector_connection.user_id,
        workspace_id=connector_connection.workspace_id,
        provider=connector_connection.provider,
        external_account_id=f"connector:{connector_connection.id}",
        external_email=None,
        status=status,
        granted_scopes=sorted(connector_connection.authorized_capabilities),
        credential_reference=connector_connection.credential_reference,
    )
    database.add(provenance)
    await database.flush()
    connector_connection.legacy_connection_id = provenance.id
    await database.flush()
    return provenance


async def _release_stale_credential(
    database: AsyncSession,
    connection: ConnectorConnection,
    previous_reference: UUID | None,
    current_reference: UUID | None,
) -> None:
    if previous_reference is None or previous_reference == current_reference:
        return
    credential = await database.get(ConnectionCredential, previous_reference)
    if credential is None or not credential.key_version.startswith("connector-v2:"):
        return
    try:
        # The old row can only be reclaimed if the bound envelope proves that
        # this connector owns it. Missing or rotated keys leave it untouched.
        SecretBroker(get_settings())._decrypt(credential, connection)
    except SecretBrokerError:
        return
    await _delete_unreferenced(database, credential)


def _validate_ownership(
    provenance: Connection,
    connector_connection: ConnectorConnection,
) -> None:
    if (
        provenance.workspace_id != connector_connection.workspace_id
        or provenance.user_id != connector_connection.user_id
        or provenance.provider != connector_connection.provider
    ):
        raise ValueError("Connector provenance crossed an ownership boundary")


def _refresh(
    provenance: Connection,
    connector_connection: ConnectorConnection,
) -> None:
    provenance.status = _legacy_status(connector_connection)
    provenance.granted_scopes = sorted(connector_connection.authorized_capabilities)
    provenance.credential_reference = connector_connection.credential_reference
    provenance.last_error = connector_connection.last_error_code


def _legacy_status(connector_connection: ConnectorConnection) -> str:
    if connector_connection.status == "CONNECTED":
        return "active"
    if connector_connection.status == "PAUSED":
        return "paused"
    if connector_connection.status == "DISCONNECTED":
        return "disconnected"
    if connector_connection.health_state == "AUTH_EXPIRED":
        return "needs_reauthorization"
    return "active"
