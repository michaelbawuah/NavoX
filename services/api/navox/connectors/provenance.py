from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import Connection, ConnectorConnection


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
        existing = await database.get(Connection, connector_connection.legacy_connection_id)
        if existing is None:
            raise ValueError("Connector provenance connection is missing")
        _validate_ownership(existing, connector_connection)
        _refresh(existing, connector_connection)
        await database.flush()
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
