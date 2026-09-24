from __future__ import annotations

from datetime import UTC
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.contracts import CanonicalResource
from navox.connectors.normalization import (
    canonical_resource_to_source_document,
    connector_receipt_source,
)
from navox.connectors.provenance import ensure_provenance_connection
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    IntelligenceSourceReceipt,
    User,
    WorkspaceMembership,
)
from navox.intelligence.extraction import (
    InvalidOperationalExtraction,
    OperationalExtraction,
    OperationalExtractionResult,
    OperationalExtractor,
    source_document_hash,
)
from navox.intelligence.resolution import resolve_extraction


async def ingest_connector_resource(
    database: AsyncSession,
    *,
    connector_connection: ConnectorConnection,
    resource: CanonicalResource,
    extractor: OperationalExtractor,
    timezone_name: str,
) -> list[UUID]:
    """Feed one accepted canonical resource through existing SPEC-002 logic."""

    if (
        resource.workspace_id != connector_connection.workspace_id
        or resource.connector_connection_id != connector_connection.id
        or resource.provider != connector_connection.provider
    ):
        raise PermissionError("Connector resource does not belong to this connection")

    membership = await database.get(
        WorkspaceMembership,
        (connector_connection.workspace_id, connector_connection.user_id),
    )
    user = await database.get(User, connector_connection.user_id)
    if membership is None or user is None or user.agent_paused:
        raise PermissionError("Connector intelligence processing is paused or unauthorized")

    provenance = await ensure_provenance_connection(database, connector_connection)
    document = canonical_resource_to_source_document(
        resource,
        provenance_connection_id=provenance.id,
    )
    source = connector_receipt_source(resource.resource_type)
    source_hash = source_document_hash(document)
    tombstone = document.metadata.get("status") in {"cancelled", "deleted"}
    extractor_version = "provider-tombstone.v1" if tombstone else extractor.extractor_version

    latest_revision = await database.scalar(
        select(func.max(IntelligenceSourceReceipt.source_occurred_at)).where(
            IntelligenceSourceReceipt.connection_id == provenance.id,
            IntelligenceSourceReceipt.source == source,
            IntelligenceSourceReceipt.external_id == document.external_id,
        )
    )
    if latest_revision is not None:
        if latest_revision.tzinfo is None:
            latest_revision = latest_revision.replace(tzinfo=UTC)
        if document.occurred_at < latest_revision:
            return []

    receipt = await database.scalar(
        select(IntelligenceSourceReceipt).where(
            IntelligenceSourceReceipt.connection_id == provenance.id,
            IntelligenceSourceReceipt.source == source,
            IntelligenceSourceReceipt.external_id == document.external_id,
            IntelligenceSourceReceipt.source_hash == source_hash,
            IntelligenceSourceReceipt.extractor_version.in_(
                (extractor_version,) if tombstone else extractor.receipt_versions
            ),
        )
    )
    if receipt is not None:
        return [UUID(identifier) for identifier in receipt.commitment_ids]

    if tombstone:
        result = OperationalExtractionResult(
            extraction=OperationalExtraction(),
            extractor_version="provider-tombstone.v1",
            model_provider="deterministic",
            model_name="provider-state",
            source_hash=source_hash,
        )
    else:
        try:
            result = await extractor.extract(document)
        except InvalidOperationalExtraction as error:
            database.add(
                IntelligenceSourceReceipt(
                    connection_id=provenance.id,
                    source=source,
                    external_id=document.external_id,
                    source_hash=source_hash,
                    extractor_version=extractor_version,
                    outcome="rejected",
                    source_occurred_at=document.occurred_at,
                    commitment_ids=[],
                )
            )
            database.add(
                AuditEvent(
                    user_id=connector_connection.user_id,
                    workspace_id=connector_connection.workspace_id,
                    event_type="connector.intelligence.rejected",
                    actor_type="system",
                    entity_type="connector_connection",
                    entity_id=connector_connection.id,
                    event_metadata={
                        "resource_type": resource.resource_type,
                        "source_hash": source_hash,
                        "validation_error": error.diagnostic(),
                    },
                )
            )
            return []

    affected = await resolve_extraction(
        database,
        connection=provenance,
        document=document,
        result=result,
        timezone_name=timezone_name,
    )
    database.add(
        IntelligenceSourceReceipt(
            connection_id=provenance.id,
            source=source,
            external_id=document.external_id,
            source_hash=source_hash,
            extractor_version=extractor_version,
            outcome="processed",
            source_occurred_at=document.occurred_at,
            commitment_ids=[str(identifier) for identifier in affected],
        )
    )
    return affected
