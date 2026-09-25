"""Evidence-bounded unwanted renewals prevented, never speculative lifetime savings."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import CancellationAttempt, CancellationEvidence, RecurringObligation
from navox.subscriptions.cancellation_contracts import CancellationPreview


class PreventedCurrencyTotal(BaseModel):
    currency: str
    renewal_amount: Decimal
    renewals: int


class PreventedRenewals(BaseModel):
    label: str = "Unwanted renewals prevented"
    count: int
    unknown_amount_count: int
    currency_totals: list[PreventedCurrencyTotal]
    explanation: str = (
        "User-confirmed cancellations independently verified before the scheduled renewal. "
        "Amounts describe those renewal charges, not refunds or lifetime savings."
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def prevented_renewals(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> PreventedRenewals:
    attempts = await database.scalars(
        select(CancellationAttempt)
        .join(RecurringObligation, RecurringObligation.id == CancellationAttempt.obligation_id)
        .where(
            CancellationAttempt.workspace_id == workspace_id,
            CancellationAttempt.user_id == user_id,
            CancellationAttempt.status == "VERIFIED_CANCELLED",
            CancellationAttempt.verification_status == "VERIFIED_CANCELLED",
            CancellationAttempt.confirmed_at.is_not(None),
            CancellationAttempt.verified_at.is_not(None),
            RecurringObligation.workspace_id == workspace_id,
            RecurringObligation.user_id == user_id,
            RecurringObligation.status == "CANCELLED",
            RecurringObligation.auto_renew.is_(False),
            CancellationAttempt.id.in_(
                select(CancellationEvidence.cancellation_attempt_id).where(
                    CancellationEvidence.workspace_id == workspace_id,
                    CancellationEvidence.user_id == user_id,
                    CancellationEvidence.evidence_type == "PROVIDER_STATE",
                    CancellationEvidence.verification_status == "VERIFIED_CANCELLED",
                )
            ),
        )
        .order_by(CancellationAttempt.verified_at, CancellationAttempt.id)
    )
    seen: set[tuple[UUID, datetime]] = set()
    totals: dict[str, tuple[Decimal, int]] = {}
    unknown = 0
    for attempt in attempts:
        try:
            preview = CancellationPreview.model_validate(attempt.preview)
            target = preview.target
        except ValidationError:
            continue
        if preview.payload.get("sandbox") is True:
            continue
        economics = preview.economics or target
        renewal = economics.next_renewal_at
        if (
            (target.workspace_id, target.user_id, target.obligation_id)
            != (workspace_id, user_id, attempt.obligation_id)
            or renewal is None
            or economics.auto_renew is not True
            or attempt.verified_at is None
            or _aware(attempt.verified_at) >= _aware(renewal)
            or (attempt.obligation_id, _aware(renewal)) in seen
        ):
            continue
        seen.add((attempt.obligation_id, _aware(renewal)))
        if economics.billing_currency is None or economics.billing_amount is None:
            unknown += 1
            continue
        amount, count = totals.get(economics.billing_currency, (Decimal(0), 0))
        totals[economics.billing_currency] = (amount + economics.billing_amount, count + 1)
    return PreventedRenewals(
        count=len(seen),
        unknown_amount_count=unknown,
        currency_totals=[
            PreventedCurrencyTotal(currency=currency, renewal_amount=amount, renewals=count)
            for currency, (amount, count) in sorted(totals.items())
        ],
    )
