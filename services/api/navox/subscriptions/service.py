"""Owner-scoped registry commands with append-only manual evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    Connection,
    ConnectorConnection,
    Merchant,
    ObligationPriceHistory,
    RecurringObligation,
    RecurringObligationEvidence,
    User,
    WorkspaceMembership,
)
from navox.subscriptions.billing import cycle_fingerprint, equivalents, monthly_equivalent, utc
from navox.subscriptions.events import queue_event as queue_event
from navox.subscriptions.schemas import (
    CurrencyTotal,
    EvidenceRead,
    PriceHistoryRead,
    SubscriptionCreate,
    SubscriptionKeep,
    SubscriptionPatch,
    SubscriptionRead,
    SubscriptionReview,
    SubscriptionSummary,
)


async def lock_owner(database: AsyncSession, *, workspace_id: UUID, user_id: UUID) -> None:
    membership = await database.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.user_id == user_id
        )
        .with_for_update()
    )
    user = await database.scalar(select(User).where(User.id == user_id).with_for_update())
    if membership is None or user is None:
        raise HTTPException(404, "Workspace not found")


async def validate_source_connection(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, connection_id: UUID
) -> UUID:
    """Normalize native connector IDs to owned, live provenance anchors under locks."""
    connectors = list(
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
    await lock_owner(database, workspace_id=workspace_id, user_id=user_id)
    native = next((row for row in connectors if row.id == connection_id), None)
    anchor_id = native.legacy_connection_id if native is not None else connection_id
    if anchor_id is None:
        raise HTTPException(409, "Connection has no provenance anchor")
    anchor = await database.scalar(
        select(Connection)
        .where(
            Connection.id == anchor_id,
            Connection.workspace_id == workspace_id,
            Connection.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if anchor is None:
        raise HTTPException(404, "Connection not found")
    if anchor.status != "active" or any(row.status != "CONNECTED" for row in connectors):
        raise HTTPException(409, "Connection is not active")
    return anchor.id


def to_subscription_read(row: RecurringObligation) -> SubscriptionRead:
    response = SubscriptionRead.model_validate(row)
    reasons = row.obligation_metadata.get("attention_reasons")
    if isinstance(reasons, list):
        response.attention_reasons = [item for item in reasons if isinstance(item, str)]
    verification = row.obligation_metadata.get("verification_status")
    if isinstance(verification, str):
        response.verification_status = verification
    response.monthly_equivalent, response.yearly_equivalent = equivalents(
        row.billing_amount, row.billing_interval, row.interval_count
    )
    return response


async def get_subscription(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
    lock: bool = False,
) -> RecurringObligation:
    if lock:
        await lock_owner(database, workspace_id=workspace_id, user_id=user_id)
    query = select(RecurringObligation).where(
        RecurringObligation.workspace_id == workspace_id,
        RecurringObligation.user_id == user_id,
        RecurringObligation.id == obligation_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await database.scalar(query)
    if row is None:
        raise HTTPException(404, "Subscription not found")
    return row


async def list_subscriptions(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, status: str | None = None
) -> list[RecurringObligation]:
    query = select(RecurringObligation).where(
        RecurringObligation.workspace_id == workspace_id, RecurringObligation.user_id == user_id
    )
    if status is not None:
        query = query.where(RecurringObligation.status == status)
    return list(
        await database.scalars(
            query.order_by(RecurringObligation.created_at.desc(), RecurringObligation.id)
        )
    )


async def list_upcoming(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    days: int = 30,
    now: datetime | None = None,
) -> list[RecurringObligation]:
    current = now or datetime.now(UTC)
    return list(
        await database.scalars(
            select(RecurringObligation)
            .where(
                RecurringObligation.workspace_id == workspace_id,
                RecurringObligation.user_id == user_id,
                RecurringObligation.status.in_(
                    ["ACTIVE", "TRIAL", "CANCEL_PENDING", "CANCELLATION_REQUESTED"]
                ),
                RecurringObligation.review_state != "NOT_MINE",
                RecurringObligation.next_renewal_at >= current,
                RecurringObligation.next_renewal_at <= current + timedelta(days=days),
            )
            .order_by(RecurringObligation.next_renewal_at, RecurringObligation.id)
        )
    )


async def list_trials(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> list[RecurringObligation]:
    return list(
        await database.scalars(
            select(RecurringObligation)
            .where(
                RecurringObligation.workspace_id == workspace_id,
                RecurringObligation.user_id == user_id,
                RecurringObligation.status == "TRIAL",
                RecurringObligation.review_state != "NOT_MINE",
            )
            .order_by(RecurringObligation.trial_ends_at, RecurringObligation.id)
        )
    )


async def subscription_summary(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> SubscriptionSummary:
    rows = await list_subscriptions(database, workspace_id=workspace_id, user_id=user_id)
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    unknown = 0
    known = 0
    for row in rows:
        if (
            row.status not in {"ACTIVE", "TRIAL", "CANCEL_PENDING", "CANCELLATION_REQUESTED"}
            or row.review_state == "NOT_MINE"
        ):
            continue
        known += 1
        monthly = monthly_equivalent(row.billing_amount, row.billing_interval, row.interval_count)
        if monthly is None or row.billing_currency is None:
            unknown += 1
            continue
        totals[row.billing_currency] = totals.get(row.billing_currency, Decimal(0)) + monthly
        counts[row.billing_currency] = counts.get(row.billing_currency, 0) + 1
    return SubscriptionSummary(
        currency_totals=[
            CurrencyTotal(
                currency=currency,
                monthly_equivalent=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                yearly_equivalent=(total * 12).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                obligation_count=counts[currency],
            )
            for currency, total in sorted(totals.items())
        ],
        known_count=known,
        unknown_cost_count=unknown,
    )


async def list_evidence(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, obligation_id: UUID
) -> list[EvidenceRead]:
    await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id
    )
    rows = await database.scalars(
        select(RecurringObligationEvidence)
        .where(
            RecurringObligationEvidence.obligation_id == obligation_id,
            RecurringObligationEvidence.workspace_id == workspace_id,
            RecurringObligationEvidence.user_id == user_id,
        )
        .order_by(RecurringObligationEvidence.observed_at, RecurringObligationEvidence.id)
    )
    return [EvidenceRead.model_validate(row) for row in rows]


async def list_history(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, obligation_id: UUID
) -> list[PriceHistoryRead]:
    await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id
    )
    rows = await database.scalars(
        select(ObligationPriceHistory)
        .where(
            ObligationPriceHistory.obligation_id == obligation_id,
            ObligationPriceHistory.workspace_id == workspace_id,
            ObligationPriceHistory.user_id == user_id,
        )
        .order_by(ObligationPriceHistory.effective_from, ObligationPriceHistory.id)
    )
    return [PriceHistoryRead.model_validate(row) for row in rows]


def _hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _snapshot(row: RecurringObligation) -> dict[str, object]:
    return {
        name: utc(value).isoformat()
        if isinstance(value, datetime)
        else str(value)
        if isinstance(value, (Decimal, UUID))
        else value
        for name in SubscriptionCreate.model_fields
        if name not in {"merchant_name", "request_id"}
        for value in [getattr(row, name)]
    }


async def _manual_evidence(
    database: AsyncSession,
    row: RecurringObligation,
    *,
    evidence_type: str,
    dedupe_key: str,
    metadata: dict[str, object],
    now: datetime,
) -> RecurringObligationEvidence:
    merchant = await database.scalar(
        select(Merchant).where(
            Merchant.id == row.merchant_id,
            Merchant.workspace_id == row.workspace_id,
            Merchant.user_id == row.user_id,
        )
    )
    if merchant is None:
        raise HTTPException(409, "Subscription merchant ownership is invalid")
    evidence = RecurringObligationEvidence(
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        obligation_id=row.id,
        evidence_type=evidence_type,
        source_type="manual",
        dedupe_key=dedupe_key,
        merchant_text=row.name,
        plan_text=row.plan_name,
        amount=row.billing_amount,
        currency=row.billing_currency,
        billing_interval=row.billing_interval,
        interval_count=row.interval_count,
        effective_at=now,
        renewal_at=row.next_renewal_at,
        confidence=Decimal(1),
        evidence_metadata={
            **metadata,
            "snapshot": _snapshot(row),
            "merchant_canonical_name": merchant.canonical_name,
            "merchant_website_domain": merchant.website_domain,
        },
        observed_at=now,
    )
    database.add(evidence)
    await database.flush()
    return evidence


async def _price_history(
    database: AsyncSession,
    row: RecurringObligation,
    evidence: RecurringObligationEvidence,
    now: datetime,
) -> None:
    previous = list(
        await database.scalars(
            select(ObligationPriceHistory).where(
                ObligationPriceHistory.obligation_id == row.id,
                ObligationPriceHistory.workspace_id == row.workspace_id,
                ObligationPriceHistory.user_id == row.user_id,
                ObligationPriceHistory.effective_until.is_(None),
            )
        )
    )
    for price in previous:
        price.effective_until = now
    if row.billing_amount is not None and row.billing_currency is not None:
        database.add(
            ObligationPriceHistory(
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                obligation_id=row.id,
                amount=row.billing_amount,
                currency=row.billing_currency,
                billing_interval=row.billing_interval,
                interval_count=row.interval_count,
                effective_from=now,
                evidence_id=evidence.id,
            )
        )


async def create_subscription(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, payload: SubscriptionCreate
) -> RecurringObligation:
    values = payload.model_dump(exclude={"request_id", "merchant_name"})
    request_hash = _hash(payload.model_dump(mode="json", exclude={"request_id"}))
    request_id = payload.request_id or uuid4()
    dedupe_key = _hash({"manual_create": str(request_id)})
    if payload.cancellation_connection_id is not None:
        values["cancellation_connection_id"] = await validate_source_connection(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            connection_id=payload.cancellation_connection_id,
        )
    else:
        await lock_owner(database, workspace_id=workspace_id, user_id=user_id)
    previous = await database.scalar(
        select(RecurringObligationEvidence).where(
            RecurringObligationEvidence.workspace_id == workspace_id,
            RecurringObligationEvidence.user_id == user_id,
            RecurringObligationEvidence.dedupe_key == dedupe_key,
        )
    )
    if previous is not None:
        if previous.evidence_metadata.get("request_hash") != request_hash:
            raise HTTPException(409, "Request ID was already used for a different subscription")
        return await get_subscription(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            obligation_id=previous.obligation_id,
        )
    merchant_name = payload.merchant_name or payload.name
    normalized_name = " ".join(merchant_name.casefold().split())
    merchant = await database.scalar(
        select(Merchant).where(
            Merchant.workspace_id == workspace_id,
            Merchant.user_id == user_id,
            Merchant.normalized_name == normalized_name,
        )
    )
    if merchant is None:
        merchant = Merchant(
            workspace_id=workspace_id,
            user_id=user_id,
            canonical_name=merchant_name,
            normalized_name=normalized_name,
        )
        database.add(merchant)
        await database.flush()
    now = datetime.now(UTC)
    row = RecurringObligation(
        workspace_id=workspace_id,
        user_id=user_id,
        merchant_id=merchant.id,
        **values,
        confidence=Decimal(1),
        review_state="CONFIRMED",
        revision=1,
        last_verified_at=now,
        obligation_metadata={"user_overrides": sorted(values), "manually_created": True},
    )
    database.add(row)
    await database.flush()
    evidence = await _manual_evidence(
        database,
        row,
        evidence_type="MANUAL",
        dedupe_key=dedupe_key,
        metadata={
            "request_hash": request_hash,
            "request_id": str(request_id),
            "user_overrides": sorted(values),
        },
        now=now,
    )
    await _price_history(database, row, evidence, now)
    await queue_event(
        database, row, "obligation.discovered", f"manual:{request_id}", {"manual": True}
    )
    await _refresh_attention(database, row)
    await database.commit()
    await database.refresh(row)
    return row


async def update_subscription(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
    payload: SubscriptionPatch,
) -> RecurringObligation:
    changes = payload.model_dump(exclude_unset=True)
    if payload.cancellation_connection_id is not None:
        changes["cancellation_connection_id"] = await validate_source_connection(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            connection_id=payload.cancellation_connection_id,
        )
    row = await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id, lock=True
    )
    if not changes:
        return row
    merged = {**_snapshot(row), **changes}
    # Validation of the merged state prevents clearing currency under an existing price.
    try:
        SubscriptionCreate.model_validate(merged)
    except ValueError as exc:
        raise HTTPException(
            422, "The updated subscription has incomplete billing or cancellation details"
        ) from exc
    before = cycle_fingerprint(row)
    price_fields = {"billing_amount", "billing_currency", "billing_interval", "interval_count"}
    price_changed = any(
        getattr(row, key) != value for key, value in changes.items() if key in price_fields
    )
    for key, value in changes.items():
        setattr(row, key, value)
    row.revision += 1
    prior_overrides = row.obligation_metadata.get("user_overrides", [])
    overrides = (
        {item for item in prior_overrides if isinstance(item, str)}
        if isinstance(prior_overrides, list)
        else set()
    )
    row.obligation_metadata = {
        **row.obligation_metadata,
        "user_overrides": sorted(overrides | changes.keys()),
    }
    if cycle_fingerprint(row) != before:
        row.kept_cycle_fingerprint = None
        if row.review_state == "KEPT":
            row.review_state = "CONFIRMED"
    now = datetime.now(UTC)
    evidence = await _manual_evidence(
        database,
        row,
        evidence_type="USER_OVERRIDE",
        dedupe_key=_hash({"obligation": str(row.id), "revision": row.revision}),
        metadata={
            "changed_fields": sorted(changes),
            "user_overrides": sorted(overrides | changes.keys()),
        },
        now=now,
    )
    if price_changed:
        await _price_history(database, row, evidence, now)
    await queue_event(
        database,
        row,
        "obligation.price_changed" if price_changed else "obligation.updated",
        f"manual:{row.id}:{row.revision}",
        {"changed_fields": sorted(changes)},
    )
    await _refresh_attention(database, row)
    await database.commit()
    await database.refresh(row)
    return row


async def keep_subscription(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
    payload: SubscriptionKeep,
) -> RecurringObligation:
    row = await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id, lock=True
    )
    if row.status in {"CANCELLED", "EXPIRED"} or row.review_state == "NOT_MINE":
        raise HTTPException(409, "This subscription cannot be kept in its current state")
    row.kept_cycle_fingerprint = cycle_fingerprint(row)
    row.review_state = "KEPT"
    row.review_after = payload.review_after
    await _refresh_attention(database, row)
    await database.commit()
    await database.refresh(row)
    return row


async def review_subscription(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
    payload: SubscriptionReview,
) -> RecurringObligation:
    row = await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id, lock=True
    )
    key = _hash({"review": str(payload.request_id or uuid4()), "obligation": str(row.id)})
    request_hash = _hash(payload.model_dump(mode="json", exclude={"request_id"}))
    previous = await database.scalar(
        select(RecurringObligationEvidence).where(
            RecurringObligationEvidence.workspace_id == workspace_id,
            RecurringObligationEvidence.user_id == user_id,
            RecurringObligationEvidence.dedupe_key == key,
        )
    )
    if previous is not None:
        if previous.evidence_metadata.get("review_command_hash") != request_hash:
            raise HTTPException(409, "Request ID already used for another review")
        return row
    now = datetime.now(UTC)
    if payload.decision == "SNOOZE":
        if payload.review_after is None or utc(payload.review_after) <= now:
            raise HTTPException(422, "Review later requires a future review_after date")
        row.review_state = "SNOOZED"
        row.review_after = payload.review_after
    elif payload.decision == "NOT_MINE":
        row.review_state = "NOT_MINE"
        row.review_after = None
        row.revision += 1
    else:
        row.review_state = "CONFIRMED"
        row.review_after = None
        if row.status == "CANDIDATE":
            row.status = (
                "TRIAL"
                if row.trial_ends_at is not None and utc(row.trial_ends_at) > now
                else "ACTIVE"
            )
            row.revision += 1
    await _manual_evidence(
        database,
        row,
        evidence_type="USER_OVERRIDE",
        dedupe_key=key,
        metadata={"review_decision": payload.decision, "review_command_hash": request_hash},
        now=now,
    )
    await _refresh_attention(database, row)
    await database.commit()
    await database.refresh(row)
    return row


async def _refresh_attention(database: AsyncSession, row: RecurringObligation) -> None:
    from navox.subscriptions.events import project_pending_events, reevaluate_obligation

    await reevaluate_obligation(database, obligation=row)
    await project_pending_events(database, workspace_id=row.workspace_id, user_id=row.user_id)


async def apply_cancellation_binding(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    obligation_id: UUID,
    connection_id: UUID,
    external_resource_id: str,
    verified_merchant_domain: str,
    request_id: UUID,
) -> RecurringObligation:
    """Persist a binding only after the caller independently inspects its reviewed profile."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", external_resource_id):
        raise HTTPException(422, "Cancellation provider target is invalid")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", verified_merchant_domain):
        raise HTTPException(422, "Verified merchant domain is invalid")
    anchor = await validate_source_connection(
        database, workspace_id=workspace_id, user_id=user_id, connection_id=connection_id
    )
    row = await get_subscription(
        database, workspace_id=workspace_id, user_id=user_id, obligation_id=obligation_id, lock=True
    )
    digest = _hash(
        {
            "connection_id": str(anchor),
            "external_resource_id": external_resource_id,
            "domain": verified_merchant_domain,
            "obligation_id": str(row.id),
        }
    )
    key = _hash({"cancellation_binding": str(request_id)})
    existing = await database.scalar(
        select(RecurringObligationEvidence).where(
            RecurringObligationEvidence.workspace_id == workspace_id,
            RecurringObligationEvidence.user_id == user_id,
            RecurringObligationEvidence.dedupe_key == key,
        )
    )
    if existing is not None:
        if existing.evidence_metadata.get("request_hash") != digest:
            raise HTTPException(409, "Request ID already used for another binding")
        return row
    merchant = await database.scalar(
        select(Merchant)
        .where(
            Merchant.id == row.merchant_id,
            Merchant.workspace_id == workspace_id,
            Merchant.user_id == user_id,
        )
        .with_for_update()
    )
    if merchant is None:
        raise HTTPException(409, "Subscription merchant ownership is invalid")
    if merchant.website_domain is not None and merchant.website_domain != verified_merchant_domain:
        raise HTTPException(409, "Verified provider conflicts with subscription merchant")
    merchant.website_domain = verified_merchant_domain
    row.cancellation_connection_id = anchor
    row.cancellation_external_resource_id = external_resource_id
    row.revision += 1
    changed = ["cancellation_connection_id", "cancellation_external_resource_id"]
    prior = row.obligation_metadata.get("user_overrides", [])
    previous = (
        {field for field in prior if isinstance(field, str)} if isinstance(prior, list) else set()
    )
    row.obligation_metadata = {
        **row.obligation_metadata,
        "user_overrides": sorted(previous | set(changed)),
    }
    await _manual_evidence(
        database,
        row,
        evidence_type="USER_OVERRIDE",
        dedupe_key=key,
        metadata={
            "changed_fields": changed,
            "request_hash": digest,
            "operator_verified_merchant_domain": verified_merchant_domain,
        },
        now=datetime.now(UTC),
    )
    await database.commit()
    await database.refresh(row)
    return row
