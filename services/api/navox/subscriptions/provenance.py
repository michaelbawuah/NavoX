"""Erase source-derived subscription state inside the connector deletion transaction."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    CancellationAttempt,
    CancellationEvidence,
    Commitment,
    CommitmentRelation,
    CommitmentSource,
    Connection,
    GmailRecheck,
    IntelligenceFeedback,
    Merchant,
    MerchantAlias,
    ObligationPriceHistory,
    Plan,
    ProactiveSignal,
    RecurringObligation,
    RecurringObligationEvidence,
    SubscriptionEvent,
)
from navox.subscriptions.events import queue_event, reevaluate_obligation
from navox.subscriptions.resolution import (
    SubscriptionRebuildRequired,
    rebuild_obligation_from_evidence,
)


def _review() -> HTTPException:
    return HTTPException(409, "Subscription source provenance needs manual review")


def _owned(row: object, workspace_id: UUID, user_id: UUID) -> bool:
    return (getattr(row, "workspace_id", None), getattr(row, "user_id", None)) == (
        workspace_id,
        user_id,
    )


def _sanitize_correction(item: RecurringObligationEvidence, connection_ids: set[UUID]) -> None:
    """An old correction's copied snapshot does not attest to every copied field."""
    metadata = item.evidence_metadata
    snapshot = metadata.get("snapshot")
    if not isinstance(snapshot, dict):
        raise _review()
    snapshot = dict(snapshot)
    if str(snapshot.get("cancellation_connection_id")) in {str(value) for value in connection_ids}:
        snapshot["cancellation_connection_id"] = None
        snapshot["cancellation_external_resource_id"] = None
    if item.evidence_type == "MANUAL":
        item.evidence_metadata = {**metadata, "snapshot": snapshot}
        return
    fields = metadata.get("changed_fields", [])
    if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
        raise _review()
    allowed = {field: snapshot[field] for field in fields if field in snapshot}
    item.evidence_metadata = {
        "snapshot": allowed,
        "changed_fields": fields,
        "review_decision": metadata.get("review_decision"),
    }
    item.merchant_text = None
    item.plan_text = str(allowed["plan_name"]) if allowed.get("plan_name") else None
    item.amount = item.amount if "billing_amount" in allowed else None
    item.currency = item.currency if "billing_currency" in allowed else None
    item.billing_interval = item.billing_interval if "billing_interval" in allowed else None
    item.renewal_at = item.renewal_at if "next_renewal_at" in allowed else None


async def _erase_projection(database: AsyncSession, *, obligation: RecurringObligation) -> None:
    events = list(
        await database.scalars(
            select(SubscriptionEvent).where(SubscriptionEvent.obligation_id == obligation.id)
        )
    )
    if any(not _owned(row, obligation.workspace_id, obligation.user_id) for row in events):
        raise _review()
    identifiers = {row.commitment_id for row in events if row.commitment_id is not None}
    cards = list(await database.scalars(select(Commitment).where(Commitment.id.in_(identifiers))))
    if any(not _owned(row, obligation.workspace_id, obligation.user_id) for row in cards):
        raise _review()
    # Source-attributed plans were erased first. Do not guess about another saved plan.
    if await database.scalar(select(Plan.id).where(Plan.commitment_id.in_(identifiers)).limit(1)):
        raise _review()
    await database.execute(
        delete(SubscriptionEvent).where(SubscriptionEvent.obligation_id == obligation.id)
    )
    for model in (CommitmentSource, ProactiveSignal, GmailRecheck):
        await database.execute(delete(model).where(model.commitment_id.in_(identifiers)))
    await database.execute(
        delete(IntelligenceFeedback).where(IntelligenceFeedback.target_id.in_(identifiers))
    )
    await database.execute(
        delete(CommitmentRelation).where(
            CommitmentRelation.from_commitment_id.in_(identifiers)
            | CommitmentRelation.to_commitment_id.in_(identifiers)
        )
    )
    await database.execute(delete(Commitment).where(Commitment.id.in_(identifiers)))


async def erase_source_subscriptions(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, connection_ids: set[UUID]
) -> None:
    """Caller holds owner/connection locks and has erased quiescent source plans.

    Any failure rolls back the entire connector command. In-flight external actions
    cannot lose their idempotency or verification records through source deletion.
    """
    if not connection_ids:
        return
    ids = set(
        await database.scalars(
            select(RecurringObligationEvidence.obligation_id).where(
                RecurringObligationEvidence.connection_id.in_(connection_ids)
            )
        )
    )
    ids.update(
        await database.scalars(
            select(RecurringObligation.id).where(
                RecurringObligation.cancellation_connection_id.in_(connection_ids)
            )
        )
    )
    ids.update(
        await database.scalars(
            select(CancellationAttempt.obligation_id).where(
                or_(
                    CancellationAttempt.id.in_(
                        select(CancellationEvidence.cancellation_attempt_id).where(
                            CancellationEvidence.connection_id.in_(connection_ids)
                        )
                    ),
                    CancellationAttempt.preview["target"]["connection_id"]
                    .as_string()
                    .in_([str(identifier) for identifier in connection_ids]),
                )
            )
        )
    )
    rows = list(
        await database.scalars(
            select(RecurringObligation).where(RecurringObligation.id.in_(ids)).with_for_update()
        )
    )
    if len(rows) != len(ids) or any(not _owned(row, workspace_id, user_id) for row in rows):
        raise _review()
    merchants = {row.merchant_id for row in rows}
    for row in rows:
        evidence = list(
            await database.scalars(
                select(RecurringObligationEvidence).where(
                    RecurringObligationEvidence.obligation_id == row.id
                )
            )
        )
        attempts = list(
            await database.scalars(
                select(CancellationAttempt).where(CancellationAttempt.obligation_id == row.id)
            )
        )
        cancellation_evidence = list(
            await database.scalars(
                select(CancellationEvidence).where(
                    CancellationEvidence.cancellation_attempt_id.in_([item.id for item in attempts])
                )
            )
        )
        if any(
            not _owned(item, workspace_id, user_id)
            for item in [*evidence, *attempts, *cancellation_evidence]
        ):
            raise _review()
        if any(
            attempt.status not in {"FAILED", "ABORTED", "VERIFIED_CANCELLED"}
            for attempt in attempts
        ):
            raise HTTPException(
                409, "Resolve or abort subscription cancellation before deleting its source"
            )
        anchors = {item.connection_id for item in evidence if item.connection_id is not None}
        anchors.update(
            item.connection_id for item in cancellation_evidence if item.connection_id is not None
        )
        if row.cancellation_connection_id:
            anchors.add(row.cancellation_connection_id)
        sources = list(await database.scalars(select(Connection).where(Connection.id.in_(anchors))))
        if len(sources) != len(anchors) or any(
            not _owned(item, workspace_id, user_id) for item in sources
        ):
            raise _review()
        await _erase_projection(database, obligation=row)
        await database.execute(
            delete(CancellationEvidence).where(
                CancellationEvidence.cancellation_attempt_id.in_([item.id for item in attempts])
            )
        )
        await database.execute(
            delete(CancellationAttempt).where(CancellationAttempt.obligation_id == row.id)
        )
        removed = {item.id for item in evidence if item.connection_id in connection_ids}
        await database.execute(
            delete(ObligationPriceHistory).where(ObligationPriceHistory.evidence_id.in_(removed))
        )
        await database.execute(
            delete(RecurringObligationEvidence).where(RecurringObligationEvidence.id.in_(removed))
        )
        survivors = [item for item in evidence if item.id not in removed]
        if not survivors:
            await database.execute(
                delete(ObligationPriceHistory).where(ObligationPriceHistory.obligation_id == row.id)
            )
            await database.delete(row)
            continue
        for item in survivors:
            if item.connection_id is None and item.evidence_type in {"MANUAL", "USER_OVERRIDE"}:
                _sanitize_correction(item, connection_ids)
        try:
            await rebuild_obligation_from_evidence(database, obligation=row, evidence=survivors)
        except SubscriptionRebuildRequired:
            raise _review() from None
        merchants.add(row.merchant_id)
        await queue_event(
            database, row, "obligation.discovered", f"source-erased:{row.revision}", {}
        )
        await reevaluate_obligation(database, obligation=row)
    await database.flush()
    # Merchant hints and aliases can otherwise preserve deleted-source names/domains.
    # Reconstruct from all surviving obligations; an ambiguous shared hint is cleared.
    for merchant_id in merchants:
        merchant = await database.get(Merchant, merchant_id)
        if merchant is None or not _owned(merchant, workspace_id, user_id):
            raise _review()
        surviving = list(
            await database.scalars(
                select(RecurringObligation).where(RecurringObligation.merchant_id == merchant_id)
            )
        )
        if any(not _owned(item, workspace_id, user_id) for item in surviving):
            raise _review()
        aliases = list(
            await database.scalars(
                select(MerchantAlias).where(MerchantAlias.merchant_id == merchant_id)
            )
        )
        if any(not _owned(item, workspace_id, user_id) for item in aliases):
            raise _review()
        await database.execute(
            delete(MerchantAlias).where(MerchantAlias.merchant_id == merchant_id)
        )
        if not surviving:
            await database.delete(merchant)
            continue
        facts = list(
            await database.scalars(
                select(RecurringObligationEvidence).where(
                    RecurringObligationEvidence.obligation_id.in_([item.id for item in surviving])
                )
            )
        )
        names: set[str] = set()
        domains: set[str] = set()
        for fact in facts:
            if not _owned(fact, workspace_id, user_id):
                raise _review()
            snapshot = fact.evidence_metadata.get("extracted_candidate")
            if isinstance(snapshot, dict):
                if isinstance(snapshot.get("merchant_text"), str):
                    names.add(snapshot["merchant_text"])
                if isinstance(snapshot.get("website_domain"), str):
                    domains.add(snapshot["website_domain"])
            if fact.evidence_type == "MANUAL":
                name = fact.evidence_metadata.get("merchant_canonical_name")
                if isinstance(name, str):
                    names.add(name)
        merchant.canonical_name = sorted(names)[0] if names else surviving[0].name
        normalized = " ".join(merchant.canonical_name.casefold().split())
        if await database.scalar(
            select(Merchant.id)
            .where(
                Merchant.workspace_id == workspace_id,
                Merchant.user_id == user_id,
                Merchant.normalized_name == normalized,
                Merchant.id != merchant_id,
            )
            .limit(1)
        ):
            raise _review()
        merchant.normalized_name = normalized
        merchant.website_domain = next(iter(domains)) if len(domains) == 1 else None
        merchant.category = None
        merchant.merchant_metadata = {"source": "surviving_evidence", "domain_verified": False}
    await database.flush()
