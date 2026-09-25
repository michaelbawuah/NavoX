"""Fixed precision equivalents retain the actual interval and original currency."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from navox.db.models import RecurringObligation


def monthly_equivalent(
    amount: Decimal | None, interval: str, interval_count: int = 1
) -> Decimal | None:
    if amount is None or interval_count < 1 or not amount.is_finite() or amount < 0:
        return None
    annual_periods = {
        "DAY": Decimal(365),
        "WEEK": Decimal(52),
        "MONTH": Decimal(12),
        "QUARTER": Decimal(4),
        "YEAR": Decimal(1),
    }
    frequency = annual_periods.get(interval)
    if frequency is None:
        return None
    return amount * frequency / (Decimal(12) * interval_count)


def equivalents(
    amount: Decimal | None, interval: str, interval_count: int = 1
) -> tuple[Decimal | None, Decimal | None]:
    monthly = monthly_equivalent(amount, interval, interval_count)
    if monthly is None:
        return None, None
    return (
        monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        (monthly * 12).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def cycle_fingerprint(obligation: RecurringObligation) -> str:
    fields = (
        "name",
        "plan_name",
        "billing_amount",
        "billing_currency",
        "billing_interval",
        "interval_count",
        "next_renewal_at",
        "trial_ends_at",
        "auto_renew",
        "trial_conversion_amount",
        "trial_conversion_interval",
        "trial_conversion_interval_count",
    )
    material: dict[str, object] = {}
    for name in fields:
        value = getattr(obligation, name)
        if isinstance(value, datetime):
            value = utc(value).isoformat()
        elif isinstance(value, Decimal):
            value = str(value.normalize())
        material[name] = value
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
