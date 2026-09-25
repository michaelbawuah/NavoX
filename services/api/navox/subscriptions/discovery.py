"""Conservative, provider-neutral extraction of recurring-obligation evidence.

External text is always evidence, never capability configuration or authority.
This bounded deterministic extractor deliberately leaves ambiguous money, dates,
identities and unsupported prose for confirmation instead of guessing.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from navox.intelligence.contracts import SourceDocument

EvidenceType = Literal[
    "SIGNUP",
    "RECEIPT",
    "PAYMENT",
    "RENEWAL_NOTICE",
    "PRICE_CHANGE",
    "TRIAL_START",
    "TRIAL_END_NOTICE",
    "CANCELLATION_REQUEST",
    "CANCELLATION_CONFIRMATION",
    "EXPIRATION",
]


class EvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_type: EvidenceType
    merchant_text: str
    website_domain: str | None = None
    plan_text: str | None = None
    account_reference: str | None = None
    obligation_type: str = "SUBSCRIPTION"
    amount: Decimal | None = None
    currency: str | None = None
    billing_interval: str = "UNKNOWN"
    interval_count: int = 1
    effective_at: datetime | None = None
    renewal_at: datetime | None = None
    trial_ends_at: datetime | None = None
    trial_conversion_amount: Decimal | None = None
    trial_conversion_interval: str | None = None
    auto_renew: bool | None = None
    confidence: Decimal = Decimal("0.65")
    needs_confirmation: bool = True
    warnings: list[str] = Field(default_factory=list)


_CURRENCIES = frozenset(
    "USD EUR GBP CAD AUD NZD CHF JPY CNY HKD SGD INR GHS NGN ZAR KES BRL MXN "
    "SEK NOK DKK PLN CZK HUF RON TRY AED SAR ILS KRW TWD THB PHP IDR MYR VND".split()
)
_AMOUNT = r"(?:0|[1-9]\d{0,8})(?:,\d{3})*(?:\.\d{1,4})?"
_DATE = r"(?:\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
_RECURRING = re.compile(
    r"\b(subscription|membership|recurring\s+(?:bill|payment)|"
    r"(?:software\s+)?licen[cs]e|domain\s+renewal|service\s+plan|trial)\b",
    re.IGNORECASE,
)
_PATTERNS: tuple[tuple[EvidenceType, str], ...] = (
    (
        "CANCELLATION_REQUEST",
        r"\b(?:cancellation\s+(?:request\s+(?:received|submitted)|requested|pending)|"
        r"(?:received|submitted)\s+your\s+cancellation\s+request)\b",
    ),
    (
        "CANCELLATION_CONFIRMATION",
        r"\b(?:cancellation\s+(?:confirmed|confirmation|complete)|"
        r"(?:subscription|membership|plan)\s+(?:(?:has\s+been|is|was)\s+)?cancelled|"
        r"(?:subscription|membership|plan)\s+(?:(?:has\s+been|is|was)\s+)?canceled)\b",
    ),
    (
        "PRICE_CHANGE",
        r"\b(?:price\s+(?:change|increase|will\s+(?:change|increase))|"
        r"(?:new|updated)\s+(?:subscription\s+)?price|"
        r"(?:subscription|plan)\s+price\s+is\s+(?:changing|increasing))\b",
    ),
    ("EXPIRATION", r"\b(?:subscription|membership|trial|license)\s+(?:has\s+)?expired\b"),
    ("TRIAL_END_NOTICE", r"\b(?:free\s+)?trial\s+(?:ends|ending|will\s+end|expires)\b"),
    (
        "TRIAL_START",
        r"\b(?:trial\s+(?:started|activated|begins|start)|start\s+of\s+your\s+trial)\b",
    ),
    (
        "RENEWAL_NOTICE",
        r"\b(?:your\s+.{0,80}?\s+(?:renews|will\s+renew)|"
        r"(?:next\s+)?renewal\s+(?:date|notice)|auto-renewal|renewed\s+(?:on|for))\b",
    ),
    ("RECEIPT", r"\b(?:receipt|invoice\s+(?:paid|for)|payment\s+receipt)\b"),
    ("PAYMENT", r"\b(?:payment\s+(?:received|successful|processed)|you\s+paid|charged\s+you)\b"),
    ("SIGNUP", r"\b(?:subscription\s+(?:started|activated|confirmed)|welcome\s+to\s+your)\b"),
)


def _label(text: str, labels: str) -> str | None:
    found = re.search(rf"(?im)^\s*(?:{labels})\s*:\s*([^\r\n]{{1,256}})", text)
    return found.group(1).strip() if found else None


def _date_after(text: str, labels: str, warnings: list[str]) -> datetime | None:
    found = re.search(rf"(?:{labels})\s*(?::|on)?\s*({_DATE})\b", text, re.IGNORECASE)
    if found is None:
        return None
    raw = found.group(1).replace(",", "")
    for pattern in ("%Y-%m-%d", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    warnings.append("invalid_explicit_date")
    return None


def _money(text: str, warnings: list[str]) -> tuple[Decimal | None, str | None]:
    matches = list(
        re.finditer(
            rf"(?<![\w.,+-])(?:(?P<before>[A-Z]{{3}})\s*[$€£¥]?\s*"
            rf"(?P<amount>{_AMOUNT})|(?P<after_amount>{_AMOUNT})\s*(?P<after>[A-Z]{{3}}))"
            rf"(?![\w.,])",
            text,
        )
    )
    pairs: set[tuple[Decimal, str]] = set()
    for match in matches:
        code = match.group("before") or match.group("after")
        if code not in _CURRENCIES:
            continue
        try:
            value = Decimal((match.group("amount") or match.group("after_amount")).replace(",", ""))
        except InvalidOperation:
            continue
        if value <= Decimal("999999999"):
            pairs.add((value, code))
    if len(pairs) == 1:
        return next(iter(pairs))
    if len(pairs) > 1:
        warnings.append("ambiguous_billing_amount")
        return None, None
    if re.search(r"[$€£¥]\s*\d|\b\d+(?:\.\d+)?\s*(?:dollars|euros|pounds)\b", text, re.I):
        warnings.append("currency_not_explicit")
    elif re.search(r"\b(?:" + "|".join(sorted(_CURRENCIES)) + r")\s*\S", text):
        warnings.append("invalid_billing_amount")
    return None, None


def _interval(text: str) -> tuple[str, int]:
    counted = re.search(r"\bevery\s+(\d{1,3})\s+(day|week|month|quarter|year)s?\b", text, re.I)
    if counted and 0 < int(counted.group(1)) <= 120:
        return counted.group(2).upper(), int(counted.group(1))
    patterns = {
        "MONTH": r"\bmonthly\b|\bper\s+month\b|/\s*month\b",
        "YEAR": r"\b(?:annual|annually|yearly)\b|\bper\s+year\b|/\s*year\b",
        "QUARTER": r"\bquarterly\b|\bper\s+quarter\b|/\s*quarter\b",
        "WEEK": r"\bweekly\b|\bper\s+week\b|/\s*week\b",
        "DAY": r"\bdaily\b|\bper\s+day\b|/\s*day\b",
    }
    intervals = [name for name, pattern in patterns.items() if re.search(pattern, text, re.I)]
    return (intervals[0], 1) if len(intervals) == 1 else ("UNKNOWN", 1)


def _merchant(document: SourceDocument, text: str) -> tuple[str | None, str | None]:
    name = _label(text, r"merchant|service|billed\s+by")
    domain: str | None = None
    if document.author and document.author.identity_type in {"email", "email_address"}:
        _, address = parseaddr(document.author.identity_value)
        if re.fullmatch(r"[^\s@]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", address):
            domain = address.rsplit("@", 1)[1].lower()
            name = name or document.author.display_name or domain
    return name, domain


def extract_evidence(document: SourceDocument) -> EvidenceCandidate | None:
    """Return at most one bounded proposal; no side effects or remote/model calls."""
    if document.metadata.get("status") in {"deleted", "cancelled"}:
        return None
    text = "\n".join(part for part in (document.subject, document.content) if part)
    if len(text) > 100_000 or not _RECURRING.search(text):
        return None
    # Solicitation is not evidence of the recipient's current obligation.
    if re.search(
        r"\b(?:subscribe\s+now|sign\s+up\s+now|start\s+your\s+(?:free\s+)?trial|"
        r"limited[- ]time\s+offer|renew\s+now\s+and\s+save|one[- ]time\s+purchase)\b",
        text,
        re.I,
    ):
        return None
    kind = next((kind for kind, pattern in _PATTERNS if re.search(pattern, text, re.I)), None)
    if kind is None:
        return None
    merchant, domain = _merchant(document, text)
    if not merchant:
        return None
    warnings: list[str] = []
    # A price-change notice can contain both old and new prices. Only an explicit
    # new-price phrase can select one; never choose the largest or first amount.
    billing_text = _label(text, r"new\s+price|updated\s+price|billing\s+amount|amount|price")
    if kind == "PRICE_CHANGE" and billing_text is None:
        changed = re.search(
            r"\b(?:new\s+price\s+(?:is|of)|will\s+(?:cost|be))\s+([^\n.]+(?:\.\d+)?)", text, re.I
        )
        billing_text = changed.group(1) if changed else None
    amount, currency = _money(billing_text or text, warnings)
    interval, count = _interval(billing_text or text)
    if interval == "UNKNOWN":
        interval, count = _interval(text)
    renewal = _date_after(
        text, r"next\s+renewal(?:\s+date)?|renewal\s+date|renews|will\s+renew|renewed", warnings
    )
    trial_end = _date_after(
        text, r"trial\s+(?:ends|expires|ending|will\s+end)|trial\s+end(?:\s+date)?", warnings
    )
    effective = _date_after(text, r"effective(?:\s+(?:from|date))?|starting|as\s+of", warnings)
    conversion_text = _label(
        text, r"after\s+trial|post[- ]trial\s+price|trial\s+conversion\s+price"
    )
    conversion, conversion_currency = (
        _money(conversion_text, warnings) if conversion_text else (None, None)
    )
    conversion_interval = _interval(conversion_text)[0] if conversion_text else None
    if conversion_currency and currency and currency != conversion_currency:
        warnings.append("trial_currency_conflict")
        conversion = None
    if conversion_currency and currency is None:
        currency = conversion_currency
    if kind.startswith("TRIAL") and conversion_text and billing_text is None:
        # Conversion pricing is a future consequence, not the current trial bill.
        amount, interval = None, "UNKNOWN"
    recurring_value = amount is not None and currency is not None and interval != "UNKNOWN"
    complete = (
        (
            recurring_value
            or (kind.startswith("TRIAL") and trial_end is not None and conversion is not None)
        )
        and not warnings
        and kind not in {"CANCELLATION_CONFIRMATION", "CANCELLATION_REQUEST", "EXPIRATION"}
    )
    auto_renew = None
    if re.search(
        r"\b(?:auto[- ]renew(?:al)?|automatically\s+renews)\s*(?::\s*)?(?:off|disabled|false)\b",
        text,
        re.I,
    ):
        auto_renew = False
    elif re.search(
        r"\b(?:auto[- ]renew(?:al)?\s*(?::\s*)?(?:on|enabled|true)|automatically\s+renews)\b",
        text,
        re.I,
    ):
        auto_renew = True
    obligation_type = "SUBSCRIPTION"
    for expression, value in (
        (r"\btrial\b", "FREE_TRIAL"),
        (r"\bdomain\s+renewal\b", "DOMAIN_RENEWAL"),
        (r"\bmembership\b", "MEMBERSHIP"),
        (r"\blicen[cs]e\b", "SOFTWARE_LICENSE"),
        (r"\brecurring\s+bill\b", "RECURRING_BILL"),
        (r"\bservice\s+plan\b", "SERVICE_PLAN"),
    ):
        if re.search(expression, text, re.I):
            obligation_type = value
            break
    return EvidenceCandidate(
        evidence_type=kind,
        merchant_text=merchant[:256],
        website_domain=domain,
        plan_text=_label(text, r"plan|plan\s+name"),
        account_reference=_label(
            text, r"subscription\s+(?:id|reference)|account\s+(?:id|reference)"
        ),
        obligation_type=obligation_type,
        amount=amount,
        currency=currency,
        billing_interval=interval,
        interval_count=count,
        effective_at=effective,
        renewal_at=renewal,
        trial_ends_at=trial_end,
        trial_conversion_amount=conversion,
        trial_conversion_interval=conversion_interval,
        auto_renew=auto_renew,
        confidence=Decimal("0.95") if complete else Decimal("0.65"),
        needs_confirmation=not complete,
        warnings=warnings,
    )
