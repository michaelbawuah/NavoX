"""Owner-scoped connection revocation and conservative source-data deletion.

The commands serialize with the connector runtime's row locks. Deletion refuses
ambiguous legacy provenance instead of guessing which learned facts to erase.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.provenance import erase_source_plans
from navox.connectors.sync_state import database_now
from navox.db.models import (
    AuditEvent,
    BriefingSnapshot,
    Commitment,
    CommitmentRelation,
    CommitmentSource,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorEventReceipt,
    ConnectorImportSnapshot,
    ConnectorResource,
    ConnectorSubscription,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
    GmailRecheck,
    GmailSyncPlan,
    IncomingEvent,
    IntelligenceCursor,
    IntelligenceFeedback,
    IntelligenceSourceReceipt,
    OAuthAuthorizationAttempt,
    ObservationEvidence,
    OperationalObservation,
    Person,
    PersonIdentity,
    PersonIdentitySource,
    ProactiveSignal,
    ProviderEventSubscription,
    User,
    WorkspaceMembership,
)
from navox.subscriptions.provenance import erase_source_subscriptions


async def _locked_ownership(
    database: AsyncSession, workspace_id: UUID, user_id: UUID, connection_id: UUID
) -> tuple[list[ConnectorConnection], Connection | None]:
    # This matches the runtime/management ordering: connector, membership, user,
    # then legacy authority. Re-load values under locks even for a reused session.
    rows = list(
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
                (ConnectorConnection.id == connection_id)
                | (ConnectorConnection.legacy_connection_id == connection_id),
            )
            .order_by(ConnectorConnection.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    member = await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.user_id == user_id
        )
        .with_for_update()
    )
    user = await database.scalar(select(User).where(User.id == user_id).with_for_update())
    account = await database.scalar(
        select(Connection)
        .where(
            Connection.id == connection_id,
            Connection.workspace_id == workspace_id,
            Connection.user_id == user_id,
            Connection.provider == "google",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if member is None or user is None or (account is None and not rows):
        raise HTTPException(404, "Connection not found")
    if account is None and (
        len(rows) != 1 or rows[0].id != connection_id or rows[0].provider == "google"
    ):
        raise HTTPException(404, "Connection not found")
    return rows, account


async def _previous(
    database: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    event_type: str,
    request_id: UUID,
) -> bool:
    return (
        await database.scalar(
            select(AuditEvent.id).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.user_id == user_id,
                AuditEvent.entity_id == connection_id,
                AuditEvent.event_type == event_type,
                AuditEvent.event_metadata["request_id"].as_string() == str(request_id),
            )
        )
        is not None
    )


def _audit(
    database: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    event_type: str,
    request_id: UUID,
) -> None:
    database.add(
        AuditEvent(
            user_id=user_id,
            workspace_id=workspace_id,
            event_type=event_type,
            actor_type="user",
            entity_type="connection",
            entity_id=connection_id,
            event_metadata={"request_id": str(request_id)},
        )
    )


async def disconnect(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    request_id: UUID,
) -> None:
    rows, account = await _locked_ownership(database, workspace_id, user_id, connection_id)
    event_type = "connector.management.disconnect"
    if await _previous(database, workspace_id, user_id, connection_id, event_type, request_id):
        await database.commit()
        return
    now = await database_now(database)
    # A provider subscription can outlive the local disconnect. Keep its
    # credential attached to the same owner until an authenticated cleanup
    # attempt retires the remote channel; the cleanup lease is scoped to that
    # purpose and cannot resume normal sync.
    pending_native = {
        row.id
        for row in rows
        if await database.scalar(
            select(ConnectorSubscription.id)
            .where(
                ConnectorSubscription.connector_connection_id == row.id,
                ConnectorSubscription.status != "cancelled",
            )
            .limit(1)
        )
    }
    provenance_ids = {account.id} if account else {row.legacy_connection_id for row in rows}
    provenance_ids.discard(None)
    pending_legacy = set(
        await database.scalars(
            select(ProviderEventSubscription.connection_id).where(
                ProviderEventSubscription.connection_id.in_(provenance_ids),
                ProviderEventSubscription.status != "cancelled",
            )
        )
    )
    credential_ids = {row.credential_reference for row in rows if row.credential_reference}
    if account and account.credential_reference:
        credential_ids.add(account.credential_reference)
    for row in rows:
        row.status = "DISCONNECTED"
        row.health_state = "DISCONNECTED"
        row.paused_at = now
        row.sync_generation += 1
        row.sync_lease_token = None
        row.sync_lease_expires_at = None
        if row.id not in pending_native:
            row.credential_reference = None
        if row.sync_run_id:
            run = await database.get(ConnectorSyncRun, row.sync_run_id)
            if run and run.status in {"running", "queued"}:
                run.status, run.error_code = "interrupted", "PERMISSION_DENIED"
        await database.execute(
            update(ConnectorSubscription)
            .where(
                ConnectorSubscription.connector_connection_id == row.id,
                ConnectorSubscription.status.notin_(("cancelled", "cancel_pending")),
            )
            .values(status="cancel_pending")
        )
    if account:
        account.status = "disconnected"
        if account.id not in pending_legacy and not pending_native:
            account.credential_reference = None
    else:
        provenance_id = rows[0].legacy_connection_id
        if provenance_id:
            provenance = await database.scalar(
                select(Connection)
                .where(
                    Connection.id == provenance_id,
                    Connection.workspace_id == workspace_id,
                    Connection.user_id == user_id,
                    Connection.provider == rows[0].provider,
                )
                .with_for_update()
            )
            if provenance is None:
                raise HTTPException(409, "Connection provenance is unavailable")
            # A shared anchor cannot be deactivated on behalf of one source.
            another = await database.scalar(
                select(ConnectorConnection.id)
                .where(
                    ConnectorConnection.legacy_connection_id == provenance_id,
                    ConnectorConnection.id != connection_id,
                )
                .limit(1)
            )
            if another:
                raise HTTPException(409, "Shared connection provenance needs manual review")
            provenance.status = "disconnected"
            if provenance.id not in pending_legacy and not pending_native:
                provenance.credential_reference = None
    if provenance_ids:
        await database.execute(
            update(ProviderEventSubscription)
            .where(
                ProviderEventSubscription.connection_id.in_(provenance_ids),
                ProviderEventSubscription.status != "cancelled",
            )
            .values(status="cancel_pending")
        )
        await database.execute(
            update(OAuthAuthorizationAttempt)
            .where(
                OAuthAuthorizationAttempt.connection_id.in_(provenance_ids),
                OAuthAuthorizationAttempt.used_at.is_(None),
            )
            .values(used_at=now)
        )
    await database.flush()
    for credential_id in credential_ids:
        still_legacy = await database.scalar(
            select(Connection.id).where(Connection.credential_reference == credential_id).limit(1)
        )
        still_native = await database.scalar(
            select(ConnectorConnection.id)
            .where(ConnectorConnection.credential_reference == credential_id)
            .limit(1)
        )
        if still_legacy is None and still_native is None:
            credential = await database.get(ConnectionCredential, credential_id)
            if credential:
                await database.delete(credential)
    _audit(database, workspace_id, user_id, connection_id, event_type, request_id)
    await database.commit()


async def delete_learned_data(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    request_id: UUID,
) -> None:
    rows, account = await _locked_ownership(database, workspace_id, user_id, connection_id)
    event_type = "connector.management.delete_data"
    if await _previous(database, workspace_id, user_id, connection_id, event_type, request_id):
        await database.commit()
        return
    if (account and account.status != "disconnected") or any(
        row.status != "DISCONNECTED" for row in rows
    ):
        raise HTTPException(409, "Disconnect this connection before deleting learned data")
    provenance_ids = {account.id} if account else {row.legacy_connection_id for row in rows}
    provenance_ids.discard(None)
    connector_ids = [row.id for row in rows]
    pending_connector = (
        await database.scalar(
            select(ConnectorSubscription.id)
            .where(
                ConnectorSubscription.connector_connection_id.in_(connector_ids),
                ConnectorSubscription.status != "cancelled",
            )
            .limit(1)
        )
        if connector_ids
        else None
    )
    pending_provider = (
        await database.scalar(
            select(ProviderEventSubscription.id)
            .where(
                ProviderEventSubscription.connection_id.in_(provenance_ids),
                ProviderEventSubscription.status != "cancelled",
            )
            .limit(1)
        )
        if provenance_ids
        else None
    )
    if pending_connector is not None or pending_provider is not None:
        raise HTTPException(409, "Provider subscription cleanup is still pending")
    credential_ids = {row.credential_reference for row in rows if row.credential_reference}
    if account and account.credential_reference:
        credential_ids.add(account.credential_reference)
    provenance_accounts = list(
        await database.scalars(
            select(Connection).where(
                Connection.id.in_(provenance_ids),
                Connection.workspace_id == workspace_id,
                Connection.user_id == user_id,
            )
        )
    )
    credential_ids.update(
        source.credential_reference for source in provenance_accounts if source.credential_reference
    )
    # A snapshot that was never processed is owned directly by the connector
    # and can be erased. Any partial run/resource without a legacy anchor may
    # already have produced SPEC-002 evidence, even when last_synced_at is null.
    if not provenance_ids:
        connector_ids = [row.id for row in rows]
        has_ingestion = any(row.last_synced_at for row in rows)
        for ingested_model in (ConnectorResource, ConnectorSyncRun):
            has_ingestion |= bool(
                await database.scalar(
                    select(ingested_model.id)
                    .where(ingested_model.connector_connection_id.in_(connector_ids))
                    .limit(1)
                )
            )
        for legacy_model in (IntelligenceSourceReceipt, ObservationEvidence, CommitmentSource):
            has_ingestion |= bool(
                await database.scalar(
                    select(legacy_model.id)
                    .where(legacy_model.connection_id.in_(connector_ids))
                    .limit(1)
                )
            )
        if has_ingestion:
            raise HTTPException(409, "Source provenance needs manual review")
    if provenance_ids:
        other = await database.scalar(
            select(ConnectorConnection.id)
            .where(
                ConnectorConnection.legacy_connection_id.in_(provenance_ids),
                ConnectorConnection.id.notin_([row.id for row in rows]),
            )
            .limit(1)
        )
        if other:
            raise HTTPException(409, "Shared connection provenance needs manual review")
        # Source edges have independent foreign keys, not a composite tenant key.
        # Refuse malformed edges before removing any source or learned record.
        foreign_commitment = await database.scalar(
            select(CommitmentSource.id)
            .outerjoin(Commitment, Commitment.id == CommitmentSource.commitment_id)
            .where(
                CommitmentSource.connection_id.in_(provenance_ids),
                (Commitment.id.is_(None))
                | (Commitment.workspace_id != workspace_id)
                | (Commitment.user_id != user_id),
            )
            .limit(1)
        )
        foreign_observation = await database.scalar(
            select(ObservationEvidence.id)
            .outerjoin(
                OperationalObservation,
                OperationalObservation.id == ObservationEvidence.observation_id,
            )
            .where(
                ObservationEvidence.connection_id.in_(provenance_ids),
                (OperationalObservation.id.is_(None))
                | (OperationalObservation.workspace_id != workspace_id)
                | (OperationalObservation.user_id != user_id),
            )
            .limit(1)
        )
        if foreign_commitment is not None or foreign_observation is not None:
            raise HTTPException(409, "Source provenance needs manual review")
    source_ids = (
        list(
            await database.scalars(
                select(CommitmentSource.commitment_id).where(
                    CommitmentSource.connection_id.in_(provenance_ids)
                )
            )
        )
        if provenance_ids
        else []
    )
    affected = set(source_ids)
    evidence = (
        list(
            (
                await database.execute(
                    select(
                        ObservationEvidence.observation_id,
                        OperationalObservation.subject_person_id,
                        OperationalObservation.object_person_id,
                    )
                    .join(
                        OperationalObservation,
                        OperationalObservation.id == ObservationEvidence.observation_id,
                    )
                    .where(ObservationEvidence.connection_id.in_(provenance_ids))
                )
            ).all()
        )
        if provenance_ids
        else []
    )
    observation_ids = {observation_id for observation_id, _, _ in evidence}
    # Identity edges record every connection that supplied a newly resolved
    # identity. Pre-migration identities remain ambiguous, even if a later
    # connection supplied the same email, and require a reviewed deletion.
    people_ids = {
        person_id
        for _, subject, object_ in evidence
        for person_id in (subject, object_)
        if person_id
    }
    identity_ids = (
        set(
            await database.scalars(
                select(PersonIdentitySource.identity_id).where(
                    PersonIdentitySource.connection_id.in_(provenance_ids)
                )
            )
        )
        if provenance_ids
        else set()
    )
    identities = (
        list(
            await database.scalars(
                select(PersonIdentity).where(
                    PersonIdentity.workspace_id == workspace_id,
                    (PersonIdentity.id.in_(identity_ids))
                    | (PersonIdentity.person_id.in_(people_ids)),
                )
            )
        )
        if identity_ids or people_ids
        else []
    )
    if any(not identity.source_attributed for identity in identities):
        raise HTTPException(409, "Person identity provenance needs manual review")
    if people_ids - {identity.person_id for identity in identities}:
        raise HTTPException(409, "Person identity provenance needs manual review")
    if provenance_ids and await database.scalar(
        select(PersonIdentity.id)
        .where(
            PersonIdentity.workspace_id == workspace_id,
            PersonIdentity.source_attributed.is_(False),
            PersonIdentity.provider.in_(
                {row.provider for row in rows} | ({account.provider} if account else set())
            ),
        )
        .limit(1)
    ):
        raise HTTPException(409, "Person identity provenance needs manual review")
    await erase_source_plans(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        connection_ids={identifier for identifier in provenance_ids if identifier is not None},
    )
    await erase_source_subscriptions(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        connection_ids={identifier for identifier in provenance_ids if identifier is not None},
    )
    if provenance_ids:
        await database.execute(
            delete(CommitmentSource).where(CommitmentSource.connection_id.in_(provenance_ids))
        )
        await database.execute(
            delete(ObservationEvidence).where(ObservationEvidence.connection_id.in_(provenance_ids))
        )
        await database.flush()
    # A surviving source contributes its own observation. Rebuild the card from
    # that evidence, discarding metadata/state that may have come from this source.
    for commitment_id in affected:
        commitment = await database.get(Commitment, commitment_id)
        if commitment is None:
            continue
        remaining = list(
            await database.scalars(
                select(CommitmentSource)
                .where(CommitmentSource.commitment_id == commitment_id)
                .order_by(CommitmentSource.extracted_at.desc())
            )
        )
        if commitment.created_by == "user":
            commitment.intelligence_metadata = {}
            continue
        if not remaining:
            await database.execute(
                delete(IntelligenceFeedback).where(
                    IntelligenceFeedback.workspace_id == workspace_id,
                    IntelligenceFeedback.target_id == commitment_id,
                )
            )
            await database.execute(
                delete(ProactiveSignal).where(
                    ProactiveSignal.workspace_id == workspace_id,
                    ProactiveSignal.commitment_id == commitment_id,
                )
            )
            await database.execute(
                delete(GmailRecheck).where(GmailRecheck.commitment_id == commitment_id)
            )
            await database.execute(
                delete(CommitmentRelation).where(
                    (CommitmentRelation.from_commitment_id == commitment_id)
                    | (CommitmentRelation.to_commitment_id == commitment_id)
                )
            )
            await database.delete(commitment)
            continue
        candidate = None
        for source in remaining:
            raw_id = source.source_metadata.get("observation_id")
            try:
                observation_id = UUID(raw_id) if isinstance(raw_id, str) else None
            except ValueError:
                observation_id = None
            if observation_id:
                candidate = await database.get(OperationalObservation, observation_id)
                if (
                    candidate
                    and candidate.workspace_id == workspace_id
                    and candidate.user_id == user_id
                    and candidate.status == "ACTIVE"
                ):
                    break
                candidate = None
        if candidate is None:
            raise HTTPException(409, "Surviving commitment provenance needs manual review")
        commitment.title = " ".join(
            part for part in (candidate.action_text, candidate.object_text) if part
        )[:256]
        commitment.due_at = candidate.effective_at
        commitment.confidence = float(candidate.confidence)
        commitment.commitment_type = {"request": "task", "alert": "task"}.get(
            candidate.observation_type, candidate.observation_type
        )
        commitment.status = "confirmed" if candidate.confidence >= 0.9 else "candidate"
        commitment.completed_at = None
        commitment.waiting_since = None
        commitment.valid_until = None
        commitment.last_verified_at = max(source.extracted_at for source in remaining)
        commitment.intelligence_metadata = {"resolution": "SOURCE_RECOMPUTED"}
        commitment.attention_score = 0
        commitment.attention_factors = {}
        commitment.attention_band = "SUPPRESS"
        await database.execute(
            delete(ProactiveSignal).where(
                ProactiveSignal.workspace_id == workspace_id,
                ProactiveSignal.commitment_id == commitment_id,
            )
        )
        await database.execute(
            delete(CommitmentRelation).where(
                (CommitmentRelation.from_commitment_id == commitment_id)
                | (CommitmentRelation.to_commitment_id == commitment_id)
            )
        )
    for observation_id in observation_ids:
        if not await database.scalar(
            select(ObservationEvidence.id)
            .where(ObservationEvidence.observation_id == observation_id)
            .limit(1)
        ):
            await database.execute(
                delete(IntelligenceFeedback).where(
                    IntelligenceFeedback.workspace_id == workspace_id,
                    IntelligenceFeedback.target_id == observation_id,
                )
            )
            await database.execute(
                delete(OperationalObservation).where(
                    OperationalObservation.id == observation_id,
                    OperationalObservation.workspace_id == workspace_id,
                )
            )
    if provenance_ids:
        await database.execute(
            delete(PersonIdentitySource).where(
                PersonIdentitySource.connection_id.in_(provenance_ids)
            )
        )
        await database.flush()
    for identity in identities:
        if identity.id not in identity_ids:
            continue
        if not await database.scalar(
            select(PersonIdentitySource.identity_id)
            .where(PersonIdentitySource.identity_id == identity.id)
            .limit(1)
        ):
            await database.delete(identity)
    await database.flush()
    for person_id in people_ids | {identity.person_id for identity in identities}:
        person = await database.scalar(
            select(Person).where(Person.id == person_id, Person.workspace_id == workspace_id)
        )
        if person is None:
            continue
        surviving = await database.scalar(
            select(PersonIdentity)
            .where(PersonIdentity.person_id == person_id)
            .order_by(PersonIdentity.identity_type, PersonIdentity.identity_value)
            .limit(1)
        )
        if surviving:
            # A display name may have come from the erased source. Use only an
            # identity still supported by another authorized connection.
            person.canonical_name = surviving.identity_value
        elif await database.scalar(
            select(OperationalObservation.id)
            .where(
                (OperationalObservation.subject_person_id == person_id)
                | (OperationalObservation.object_person_id == person_id)
            )
            .limit(1)
        ):
            raise HTTPException(409, "Surviving person provenance needs manual review")
        else:
            await database.delete(person)
    await database.flush()
    if provenance_ids:
        await database.execute(
            delete(GmailRecheck).where(GmailRecheck.connection_id.in_(provenance_ids))
        )
        for model in (
            IntelligenceSourceReceipt,
            IntelligenceCursor,
            GmailSyncPlan,
            IncomingEvent,
            ProviderEventSubscription,
            OAuthAuthorizationAttempt,
        ):
            await database.execute(delete(model).where(model.connection_id.in_(provenance_ids)))
    connector_ids = [row.id for row in rows]
    if connector_ids:
        await database.execute(
            delete(ConnectorSyncReceipt).where(
                ConnectorSyncReceipt.sync_run_id.in_(
                    select(ConnectorSyncRun.id).where(
                        ConnectorSyncRun.connector_connection_id.in_(connector_ids)
                    )
                )
            )
        )
        for connector_model in (
            ConnectorResource,
            ConnectorEventReceipt,
            ConnectorSubscription,
            ConnectorSyncRun,
        ):
            await database.execute(
                delete(connector_model).where(
                    connector_model.connector_connection_id.in_(connector_ids)
                )
            )
        await database.execute(
            delete(ConnectorImportSnapshot).where(
                ConnectorImportSnapshot.connection_id.in_(connector_ids)
            )
        )
        for row in rows:
            row.sync_cursor = None
            row.sync_run_id = None
            row.last_synced_at = None
            row.config = {}
    if account:
        account.credential_reference = None
        account.external_email = None
        account.external_account_id = f"deleted:{account.id}"
        account.granted_scopes = []
        account.last_error = None
    for row in rows:
        row.credential_reference = None
        row.display_name = None
        row.external_account_id = f"deleted:{row.id}"
    for provenance_anchor in provenance_accounts:
        provenance_anchor.credential_reference = None
    await database.flush()
    for credential_id in credential_ids:
        still_legacy = await database.scalar(
            select(Connection.id).where(Connection.credential_reference == credential_id).limit(1)
        )
        still_native = await database.scalar(
            select(ConnectorConnection.id)
            .where(ConnectorConnection.credential_reference == credential_id)
            .limit(1)
        )
        if still_legacy is None and still_native is None:
            credential = await database.get(ConnectionCredential, credential_id)
            if credential:
                await database.delete(credential)
    # Audit entries emitted by an erased source may contain source hashes or
    # identifiers. Keep only content-free lifecycle replay receipts.
    audit_entities = {
        *provenance_ids,
        *connector_ids,
        *affected,
        *observation_ids,
        *people_ids,
        *identity_ids,
    }
    if audit_entities:
        await database.execute(
            delete(AuditEvent).where(
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.user_id == user_id,
                AuditEvent.entity_id.in_(audit_entities),
                AuditEvent.event_type.not_like("connector.management.%"),
            )
        )
    # Cached content projections are invalidated; next read must rebuild them.
    await database.execute(
        delete(BriefingSnapshot).where(
            BriefingSnapshot.workspace_id == workspace_id, BriefingSnapshot.user_id == user_id
        )
    )
    _audit(database, workspace_id, user_id, connection_id, event_type, request_id)
    await database.commit()
