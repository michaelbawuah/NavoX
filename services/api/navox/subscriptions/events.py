"""Transactional subscription outbox projected into SPEC-002's existing attention graph.

No provider call, cancellation authority or reminder delivery lives in this module.
The domain supplies facts; the shared intelligence attention system ranks them.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    Commitment,
    CommitmentSource,
    Connection,
    ObligationPriceHistory,
    RecurringObligation,
    RecurringObligationEvidence,
    SubscriptionEvent,
    User,
    WorkspaceMembership,
)
from navox.intelligence.attention import evaluate_workspace_attention
from navox.subscriptions.billing import cycle_fingerprint

ATTENTION_EVENTS = {
    "obligation.discovered": "Review subscription",
    "obligation.renewal_approaching": "Review upcoming renewal",
    "obligation.price_changed": "Review subscription price change",
    "trial.ending": "Review trial before it ends",
    "trial.ended": "Check trial outcome",
    "cancellation.verification_pending": "Check cancellation outcome",
    "cancellation.failed": "Review unsuccessful cancellation",
    "cancellation.contradicted": "Review conflicting cancellation evidence",
}
CANCELLATION_PROBLEMS = frozenset(
    {"cancellation.verification_pending", "cancellation.failed", "cancellation.contradicted"}
)
TERMINAL = frozenset({"CANCELLED", "EXPIRED"})


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _identifier(*parts: object) -> UUID:
    return uuid5(NAMESPACE_URL, "navox:spec004:" + ":".join(map(str, parts)))


async def queue_event(
    database: AsyncSession,
    obligation: RecurringObligation,
    event_type: str,
    dedupe_key: str,
    payload: dict[str, object],
) -> SubscriptionEvent:
    """Record a fact atomically with its domain change; caller commits and locks owner."""
    key = sha256(f"{obligation.id}:{event_type}:{dedupe_key}".encode()).hexdigest()
    event_id = _identifier("event", obligation.workspace_id, obligation.user_id, key)
    existing = await database.get(SubscriptionEvent, event_id)
    if existing is not None:
        return existing
    row = SubscriptionEvent(
        id=event_id,
        workspace_id=obligation.workspace_id,
        user_id=obligation.user_id,
        obligation_id=obligation.id,
        event_type=event_type,
        dedupe_key=key,
        payload={
            **payload,
            "material_fingerprint": cycle_fingerprint(obligation),
            "obligation_revision": obligation.revision,
        },
        delivery_state="PENDING",
        created_at=datetime.now(UTC),
    )
    database.add(row)
    await database.flush()
    return row


async def reevaluate_obligation(
    database: AsyncSession,
    *,
    obligation: RecurringObligation,
    now: datetime | None = None,
) -> None:
    """Time creates review facts, never proof of payment or automatic trial conversion."""
    current = aware(now or datetime.now(UTC))
    if obligation.status in TERMINAL or obligation.review_state == "NOT_MINE":
        return
    price = await database.scalar(
        select(ObligationPriceHistory)
        .where(
            ObligationPriceHistory.obligation_id == obligation.id,
            ObligationPriceHistory.workspace_id == obligation.workspace_id,
            ObligationPriceHistory.user_id == obligation.user_id,
            ObligationPriceHistory.effective_from <= current,
        )
        .order_by(ObligationPriceHistory.effective_from.desc(), ObligationPriceHistory.id)
        .limit(1)
    )
    raw_overrides = obligation.obligation_metadata.get("user_overrides", [])
    overrides = raw_overrides if isinstance(raw_overrides, list) else []
    if price is not None and not any(
        field in overrides
        for field in ("billing_amount", "billing_currency", "billing_interval", "interval_count")
    ):
        old = (
            obligation.billing_amount,
            obligation.billing_currency,
            obligation.billing_interval,
            obligation.interval_count,
        )
        new = (price.amount, price.currency, price.billing_interval, price.interval_count)
        if old != new:
            (
                obligation.billing_amount,
                obligation.billing_currency,
                obligation.billing_interval,
                obligation.interval_count,
            ) = new
            obligation.revision += 1
            obligation.kept_cycle_fingerprint = None
            if obligation.review_state == "KEPT":
                obligation.review_state = "CONFIRMED"
            await queue_event(
                database, obligation, "obligation.price_changed", f"effective:{price.id}", {}
            )
    fingerprint = cycle_fingerprint(obligation)
    if obligation.next_renewal_at is not None and obligation.status != "CANDIDATE":
        if aware(obligation.next_renewal_at) <= current + timedelta(days=7):
            await queue_event(
                database,
                obligation,
                "obligation.renewal_approaching",
                fingerprint,
                {"due_at": aware(obligation.next_renewal_at).isoformat()},
            )
    if obligation.status == "TRIAL" and obligation.trial_ends_at is not None:
        end = aware(obligation.trial_ends_at)
        if end <= current + timedelta(days=7):
            kind = "trial.ended" if end <= current else "trial.ending"
            await queue_event(database, obligation, kind, fingerprint, {"due_at": end.isoformat()})


async def _attach_sources(
    database: AsyncSession, *, obligation: RecurringObligation, commitment: Commitment
) -> None:
    evidence = list(
        await database.scalars(
            select(RecurringObligationEvidence).where(
                RecurringObligationEvidence.obligation_id == obligation.id,
                RecurringObligationEvidence.workspace_id == obligation.workspace_id,
                RecurringObligationEvidence.user_id == obligation.user_id,
            )
        )
    )
    for item in evidence:
        source_id = _identifier("source", commitment.id, item.id)
        if await database.get(CommitmentSource, source_id) is not None:
            continue
        provider = "navox"
        if item.connection_id is not None:
            connection = await database.get(Connection, item.connection_id)
            if (
                connection is None
                or connection.workspace_id != obligation.workspace_id
                or connection.user_id != obligation.user_id
            ):
                raise PermissionError("Subscription source is outside its owner scope")
            provider = connection.provider
        elif item.source_type not in {"manual", "user_override", "MANUAL", "USER_OVERRIDE"}:
            # Never invent an independent provenance anchor for unattributed external data.
            continue
        database.add(
            CommitmentSource(
                id=source_id,
                commitment_id=commitment.id,
                connection_id=item.connection_id,
                provider=provider,
                source_type="manual" if item.connection_id is None else "recurring_obligation",
                external_resource_id=item.external_resource_id or str(item.id),
                source_metadata={
                    "subscription_id": str(obligation.id),
                    "evidence_id": str(item.id),
                },
            )
        )


def _due_at(event: SubscriptionEvent, obligation: RecurringObligation) -> datetime | None:
    raw = event.payload.get("due_at")
    if isinstance(raw, str):
        try:
            return aware(datetime.fromisoformat(raw))
        except ValueError:
            pass
    if event.event_type.startswith("trial."):
        return obligation.trial_ends_at
    if event.event_type == "obligation.renewal_approaching":
        return obligation.next_renewal_at
    return event.created_at


async def project_pending_events(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime | None = None,
) -> int:
    """Idempotent outbox drain and decision refresh. Caller owns the transaction."""
    current = aware(now or datetime.now(UTC))
    user = await database.scalar(select(User).where(User.id == user_id).with_for_update())
    membership = await database.get(WorkspaceMembership, (workspace_id, user_id))
    if user is None or membership is None:
        raise PermissionError("Subscription attention owner is unavailable")
    if user.agent_paused:
        return 0
    rows = list(
        await database.scalars(
            select(SubscriptionEvent)
            .where(
                SubscriptionEvent.workspace_id == workspace_id, SubscriptionEvent.user_id == user_id
            )
            .order_by(SubscriptionEvent.created_at, SubscriptionEvent.id)
            .with_for_update()
        )
    )
    count = 0
    for event in rows:
        obligation = await database.scalar(
            select(RecurringObligation).where(
                RecurringObligation.id == event.obligation_id,
                RecurringObligation.workspace_id == workspace_id,
                RecurringObligation.user_id == user_id,
            )
        )
        if obligation is None:
            raise PermissionError("Subscription event has a foreign obligation")
        label = ATTENTION_EVENTS.get(event.event_type)
        should_project = label is not None and (
            event.commitment_id is not None
            or event.event_type != "obligation.discovered"
            or obligation.status == "CANDIDATE"
        )
        if should_project:
            card_id = _identifier("commitment", event.id)
            card = await database.get(Commitment, card_id)
            fingerprint = cycle_fingerprint(obligation)
            cancellation_problem = event.event_type in CANCELLATION_PROBLEMS
            suppressed = (
                obligation.review_state == "NOT_MINE"
                or (
                    event.event_type == "obligation.discovered" and obligation.status != "CANDIDATE"
                )
                or (
                    event.event_type == "trial.ending"
                    and obligation.trial_ends_at is not None
                    and aware(obligation.trial_ends_at) <= current
                )
                or (
                    not cancellation_problem
                    and (
                        obligation.status in TERMINAL
                        or event.payload.get("material_fingerprint") != fingerprint
                        or obligation.kept_cycle_fingerprint == fingerprint
                    )
                )
                or (
                    cancellation_problem
                    and obligation.status == "CANCELLED"
                    and event.event_type != "cancellation.contradicted"
                )
            )
            if card is None:
                card = Commitment(
                    id=card_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    commitment_type="renewal"
                    if "renewal" in event.event_type or "trial" in event.event_type
                    else "task",
                    title=f"{label}: {obligation.name}"[:256],
                    description=(
                        "Subscription evidence requires review; "
                        "no cancellation is authorized by this reminder."
                    ),
                    status="candidate" if obligation.status == "CANDIDATE" else "confirmed",
                    priority=3,
                    due_at=_due_at(event, obligation),
                    confidence=float(obligation.confidence),
                    created_by="system",
                    dedupe_key=sha256(f"subscription-event:{event.id}".encode()).hexdigest(),
                    intelligence_metadata={
                        "subscription_id": str(obligation.id),
                        "subscription_event_id": str(event.id),
                        "subscription_event_type": event.event_type,
                        "material_fingerprint": event.payload.get("material_fingerprint"),
                        "resolution": "DOMAIN_EVENT",
                    },
                    last_verified_at=current,
                )
                database.add(card)
                await database.flush()
                event.commitment_id = card.id
            if card.workspace_id != workspace_id or card.user_id != user_id:
                raise PermissionError("Subscription event projection crosses an owner boundary")
            metadata = dict(card.intelligence_metadata or {})
            # A domain Keep/material transition may suppress its own projection. It must
            # never resurrect an independently completed/rejected SPEC-002 card.
            if suppressed and card.status not in {"completed", "rejected"}:
                card.status = "superseded"
                metadata["subscription_suppressed"] = True
            elif not suppressed and metadata.pop("subscription_suppressed", False):
                card.status = "candidate" if obligation.status == "CANDIDATE" else "confirmed"
            card.intelligence_metadata = metadata
            card.valid_from = obligation.review_after
            await _attach_sources(database, obligation=obligation, commitment=card)
        if event.delivery_state == "PENDING":
            event.delivery_state = "DELIVERED"
            event.delivered_at = current
            count += 1
    await database.flush()
    results = await evaluate_workspace_attention(
        database, workspace_id=workspace_id, user_id=user_id, now=current
    )
    reasons: dict[UUID, list[str]] = {}
    for event in rows:
        result = results.get(event.commitment_id) if event.commitment_id is not None else None
        if result is not None and not result.suppressed:
            reasons.setdefault(event.obligation_id, []).append(event.event_type)
    for obligation_id in {row.obligation_id for row in rows}:
        owned = await database.get(RecurringObligation, obligation_id)
        if owned is not None:
            owned.obligation_metadata = {
                **(owned.obligation_metadata or {}),
                "attention_reasons": list(dict.fromkeys(reasons.get(obligation_id, []))),
            }
    await database.flush()
    return count
