"""Tenant-scoped recurring evidence resolution and historical reconstruction."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    CancellationAttempt,
    Connection,
    Merchant,
    MerchantAlias,
    ObligationPriceHistory,
    RecurringObligation,
    RecurringObligationEvidence,
    User,
)
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import source_document_hash
from navox.subscriptions.discovery import EvidenceCandidate, extract_evidence
from navox.subscriptions.events import queue_event
from navox.subscriptions.schemas import SubscriptionCreate


@dataclass(frozen=True)
class ResolutionResult:
    obligation_id: UUID | None
    outcome: str
    evidence_id: UUID | None = None
    duplicate: bool = False


class SubscriptionRebuildRequired(ValueError):
    """Surviving evidence cannot safely reconstruct all retained facts."""


async def rebuild_obligation_from_evidence(
    db: AsyncSession,
    *,
    obligation: RecurringObligation,
    evidence: list[RecurringObligationEvidence],
) -> None:
    """Replace derived state using only surviving facts; never use old snapshots.

    Caller removes the source's dependent records in the same transaction and
    must roll back when this raises. Partial corrections assert only their named
    fields, not a copied snapshot of all formerly discovered personal data.
    """
    state: dict[str, object] = {"status": "CANDIDATE"}
    overrides: set[str] = set()
    history_inputs: list[tuple[RecurringObligationEvidence, EvidenceCandidate]] = []
    merchant_name: str | None = None
    merchant_domain: str | None = None
    confidence = Decimal("0.65")
    source_identity: str | None = None
    review_state = "UNREVIEWED"
    review_after: datetime | None = None
    manually_created = False
    last_verified: datetime | None = None
    last_evidence: datetime | None = None
    price_fields = {"billing_amount", "billing_currency", "billing_interval", "interval_count"}
    ordered = sorted(evidence, key=lambda item: (_aware(item.observed_at), str(item.id)))
    for item in ordered:
        if (
            item.workspace_id != obligation.workspace_id
            or item.user_id != obligation.user_id
            or item.obligation_id != obligation.id
        ):
            raise SubscriptionRebuildRequired("surviving_evidence_ownership_mismatch")
        metadata = item.evidence_metadata
        if item.evidence_type in {"MANUAL", "USER_OVERRIDE"} and item.connection_id is None:
            snapshot = metadata.get("snapshot")
            if not isinstance(snapshot, dict):
                raise SubscriptionRebuildRequired("surviving_manual_snapshot_missing")
            if item.evidence_type == "MANUAL":
                state = dict(snapshot)
                overrides = set(snapshot)
                manually_created = True
                review_state = "CONFIRMED"
                confidence = Decimal(1)
                source_identity = None
                last_verified = item.observed_at
                canonical_name = metadata.get("merchant_canonical_name")
                if not isinstance(canonical_name, str):
                    raise SubscriptionRebuildRequired("surviving_merchant_identity_missing")
                merchant_name = canonical_name
                merchant_domain = None
                changed = set(snapshot)
            else:
                raw_changed = metadata.get("changed_fields", [])
                if not isinstance(raw_changed, list) or any(
                    not isinstance(field, str) for field in raw_changed
                ):
                    raise SubscriptionRebuildRequired("surviving_correction_invalid")
                changed = {field for field in raw_changed if isinstance(field, str)}
                if any(
                    field not in SubscriptionCreate.model_fields or field not in snapshot
                    for field in changed
                ):
                    raise SubscriptionRebuildRequired("surviving_correction_incomplete")
                for field in changed:
                    state[field] = snapshot[field]
                overrides |= changed
                decision = metadata.get("review_decision")
                if decision == "NOT_MINE":
                    review_state = "NOT_MINE"
                elif decision == "CONFIRM":
                    review_state = "CONFIRMED"
                    if state.get("status") == "CANDIDATE":
                        state["status"] = "ACTIVE"
                elif decision == "SNOOZE":
                    review_state = (
                        "UNREVIEWED"  # Safe reset when no independent snooze date exists.
                    )
            if changed & price_fields:
                try:
                    checked = SubscriptionCreate.model_validate(state)
                except ValueError as exc:
                    raise SubscriptionRebuildRequired("surviving_billing_incomplete") from exc
                history_inputs.append(
                    (
                        item,
                        EvidenceCandidate(
                            evidence_type="RECEIPT",
                            merchant_text=merchant_name or checked.name,
                            amount=checked.billing_amount,
                            currency=checked.billing_currency,
                            billing_interval=checked.billing_interval,
                            interval_count=checked.interval_count,
                            effective_at=item.effective_at or item.observed_at,
                        ),
                    )
                )
            continue
        raw_candidate = metadata.get("extracted_candidate")
        if item.connection_id is None or not isinstance(raw_candidate, dict):
            raise SubscriptionRebuildRequired("surviving_source_snapshot_missing")
        try:
            candidate = EvidenceCandidate.model_validate(raw_candidate)
        except ValueError as exc:
            raise SubscriptionRebuildRequired("surviving_source_snapshot_invalid") from exc
        merchant_name = merchant_name or candidate.merchant_text
        merchant_domain = merchant_domain or candidate.website_domain
        updates: dict[str, object] = {
            "name": candidate.merchant_text,
            "plan_name": candidate.plan_text,
            "obligation_type": candidate.obligation_type,
            "next_renewal_at": candidate.renewal_at,
            "trial_ends_at": candidate.trial_ends_at,
            "trial_conversion_amount": candidate.trial_conversion_amount,
            "trial_conversion_interval": candidate.trial_conversion_interval,
            "auto_renew": candidate.auto_renew,
        }
        if candidate.currency is not None:
            updates["billing_currency"] = candidate.currency
        if (
            candidate.effective_at is None or _aware(candidate.effective_at) <= datetime.now(UTC)
        ) and candidate.amount is not None:
            updates.update(
                {
                    "billing_amount": candidate.amount,
                    "billing_interval": candidate.billing_interval,
                    "interval_count": candidate.interval_count,
                }
            )
        if (
            not candidate.needs_confirmation
            and not candidate.evidence_type.startswith("CANCELLATION")
            and candidate.evidence_type != "EXPIRATION"
        ):
            updates["status"] = "TRIAL" if candidate.evidence_type.startswith("TRIAL") else "ACTIVE"
        for field, value in updates.items():
            if field not in overrides and value is not None:
                state[field] = value
        if (
            candidate.amount is not None
            and candidate.currency
            and candidate.billing_interval != "UNKNOWN"
        ):
            history_inputs.append((item, candidate))
        confidence = max(confidence, candidate.confidence)
        source_identity = (
            _source_identity(item.connection_id, candidate) if not manually_created else None
        )
        last_evidence = item.observed_at
    if not merchant_name or "name" not in state:
        raise SubscriptionRebuildRequired("surviving_identity_insufficient")
    try:
        restored = SubscriptionCreate.model_validate(state)
    except ValueError as exc:
        raise SubscriptionRebuildRequired("surviving_registry_state_incomplete") from exc
    merchant, ambiguous = await _merchant(
        db,
        obligation.workspace_id,
        obligation.user_id,
        EvidenceCandidate(
            evidence_type="SIGNUP", merchant_text=merchant_name, website_domain=merchant_domain
        ),
    )
    if ambiguous:
        raise SubscriptionRebuildRequired("surviving_merchant_identity_ambiguous")
    for field, value in restored.model_dump(exclude={"request_id", "merchant_name"}).items():
        setattr(obligation, field, value)
    obligation.merchant_id = merchant.id
    obligation.confidence = confidence
    obligation.source_identity = source_identity
    obligation.last_verified_at = last_verified
    obligation.review_state = review_state
    obligation.review_after = review_after
    obligation.kept_cycle_fingerprint = None
    obligation.obligation_metadata = {
        "user_overrides": sorted(overrides),
        "manually_created": manually_created,
        "needs_confirmation": restored.status == "CANDIDATE",
        "last_evidence_at": last_evidence.isoformat() if last_evidence else None,
    }
    obligation.revision += 1
    await db.execute(
        delete(ObligationPriceHistory).where(ObligationPriceHistory.obligation_id == obligation.id)
    )
    for item, candidate in history_inputs:
        await _record_price(db, obligation, item, candidate, datetime.now(UTC), overrides)
        await db.flush()
    await db.flush()


def _normalized(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _merchant(
    db: AsyncSession, workspace_id: UUID, user_id: UUID, candidate: EvidenceCandidate
) -> tuple[Merchant, bool]:
    name = _normalized(candidate.merchant_text)
    row = await db.scalar(
        select(Merchant).where(
            Merchant.workspace_id == workspace_id,
            Merchant.user_id == user_id,
            Merchant.normalized_name == name,
        )
    )
    ambiguous = False
    if row is not None and candidate.website_domain and row.website_domain:
        if candidate.website_domain != row.website_domain:
            # Matching display names from different senders are not an alias proof.
            ambiguous = True
            name = f"{name[:180]}@{candidate.website_domain[:70]}"
            row = await db.scalar(
                select(Merchant).where(
                    Merchant.workspace_id == workspace_id,
                    Merchant.user_id == user_id,
                    Merchant.normalized_name == name,
                )
            )
    if row is None and not ambiguous:
        alias = await db.scalar(
            select(MerchantAlias).where(
                MerchantAlias.workspace_id == workspace_id,
                MerchantAlias.user_id == user_id,
                MerchantAlias.alias == name,
                MerchantAlias.alias_type == "normalized_name",
                MerchantAlias.confidence >= Decimal("0.97"),
            )
        )
        if alias:
            row = await db.scalar(
                select(Merchant).where(
                    Merchant.id == alias.merchant_id,
                    Merchant.workspace_id == workspace_id,
                    Merchant.user_id == user_id,
                )
            )
    if row is None:
        row = Merchant(
            workspace_id=workspace_id,
            user_id=user_id,
            canonical_name=candidate.merchant_text,
            normalized_name=name,
            # A sender domain is an identity hint, never a verified website URL.
            website_domain=candidate.website_domain,
            merchant_metadata={"source": "discovered", "domain_verified": False},
        )
        db.add(row)
        await db.flush()
    return row, ambiguous


def _source_identity(connection_id: UUID, candidate: EvidenceCandidate) -> str:
    reference = _normalized(candidate.account_reference)
    if not reference:
        reference = "plan:" + _normalized(candidate.plan_text)
    return str(connection_id) + ":" + sha256(reference.encode()).hexdigest()


async def _obligation(
    db: AsyncSession,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    merchant: Merchant,
    candidate: EvidenceCandidate,
    merchant_ambiguous: bool,
) -> tuple[RecurringObligation, bool, bool]:
    identity = _source_identity(connection_id, candidate)
    rows = list(
        (
            await db.scalars(
                select(RecurringObligation).where(
                    RecurringObligation.workspace_id == workspace_id,
                    RecurringObligation.user_id == user_id,
                    RecurringObligation.merchant_id == merchant.id,
                )
            )
        ).all()
    )
    exact = [row for row in rows if row.source_identity == identity]
    if len(exact) == 1:
        return exact[0], False, merchant_ambiguous
    plan_rows = [
        row for row in rows if _normalized(row.plan_name) == _normalized(candidate.plan_text)
    ]
    # Existing manual records can be matched by the exact merchant, plan and
    # billing signature; contradictory or multiple targets require confirmation.
    compatible = [
        row
        for row in plan_rows
        if row.source_identity is None
        and row.billing_currency == candidate.currency
        and row.billing_interval == candidate.billing_interval
        and row.interval_count == candidate.interval_count
        and row.billing_amount == candidate.amount
    ]
    if len(compatible) == 1 and len(plan_rows) == 1 and not merchant_ambiguous:
        return compatible[0], False, False
    ambiguous = merchant_ambiguous or bool(exact) or bool(compatible)
    # A different explicit account reference is a different subscription. A
    # missing account reference cannot choose between existing account records.
    if candidate.account_reference is None and plan_rows:
        ambiguous = True
    row = RecurringObligation(
        workspace_id=workspace_id,
        user_id=user_id,
        merchant_id=merchant.id,
        obligation_type=candidate.obligation_type,
        name=candidate.merchant_text,
        plan_name=candidate.plan_text,
        status="CANDIDATE",
        confidence=Decimal("0.65") if ambiguous else candidate.confidence,
        source_identity=identity,
        obligation_metadata={
            "discovered": True,
            "resolution_ambiguous": ambiguous,
            "needs_confirmation": candidate.needs_confirmation or ambiguous,
        },
    )
    db.add(row)
    await db.flush()
    return row, True, ambiguous


async def _record_price(
    db: AsyncSession,
    obligation: RecurringObligation,
    evidence: RecurringObligationEvidence,
    candidate: EvidenceCandidate,
    now: datetime,
    overrides: set[str],
) -> bool:
    if (
        candidate.amount is None
        or candidate.currency is None
        or candidate.billing_interval == "UNKNOWN"
    ):
        return False
    effective = candidate.effective_at or evidence.observed_at
    history = list(
        (
            await db.scalars(
                select(ObligationPriceHistory)
                .where(ObligationPriceHistory.obligation_id == obligation.id)
                .order_by(ObligationPriceHistory.effective_from)
            )
        ).all()
    )
    signature = (
        candidate.amount,
        candidate.currency,
        candidate.billing_interval,
        candidate.interval_count,
    )
    preceding = [row for row in history if _aware(row.effective_from) <= _aware(effective)]
    latest = preceding[-1] if preceding else None
    changed = (
        latest is not None
        and (latest.amount, latest.currency, latest.billing_interval, latest.interval_count)
        != signature
    )
    if latest is None or changed:
        history.append(
            ObligationPriceHistory(
                workspace_id=obligation.workspace_id,
                user_id=obligation.user_id,
                obligation_id=obligation.id,
                amount=candidate.amount,
                currency=candidate.currency,
                billing_interval=candidate.billing_interval,
                interval_count=candidate.interval_count,
                effective_from=effective,
                evidence_id=evidence.id,
            )
        )
        db.add(history[-1])
        history.sort(key=lambda row: _aware(row.effective_from))
        for index, row in enumerate(history):
            row.effective_until = (
                history[index + 1].effective_from if index + 1 < len(history) else None
            )
    current = [row for row in history if _aware(row.effective_from) <= now]
    if current:
        price = current[-1]
        for field, value in (
            ("billing_amount", price.amount),
            ("billing_currency", price.currency),
            ("billing_interval", price.billing_interval),
            ("interval_count", price.interval_count),
        ):
            if field not in overrides:
                setattr(obligation, field, value)
    return changed


async def _contradiction(
    db: AsyncSession,
    obligation: RecurringObligation,
    candidate: EvidenceCandidate,
    evidence: RecurringObligationEvidence,
    occurred_at: datetime,
) -> bool:
    if candidate.evidence_type not in {"RECEIPT", "PAYMENT", "RENEWAL_NOTICE"}:
        return False
    verified_at = obligation.last_verified_at
    if (
        verified_at is None
        or _aware(occurred_at) <= _aware(verified_at)
        or obligation.status not in {"CANCELLED", "UNKNOWN"}
    ):
        return False
    verified_attempts = list(
        (
            await db.scalars(
                select(CancellationAttempt).where(
                    CancellationAttempt.workspace_id == obligation.workspace_id,
                    CancellationAttempt.user_id == obligation.user_id,
                    CancellationAttempt.obligation_id == obligation.id,
                    CancellationAttempt.verification_status.in_(
                        ["VERIFIED_CANCELLED", "CONTRADICTED"]
                    ),
                )
            )
        ).all()
    )
    if obligation.status != "CANCELLED" and not verified_attempts:
        return False
    obligation.status = "UNKNOWN"
    obligation.obligation_metadata = {
        **obligation.obligation_metadata,
        "verification_status": "CONTRADICTED",
        "needs_confirmation": True,
    }
    for attempt in verified_attempts:
        attempt.verification_status = "CONTRADICTED"
        attempt.status = "VERIFICATION_PENDING"
        attempt.attempt_metadata = {
            **attempt.attempt_metadata,
            "verification_rechecks": 0,
            "verification_next_at": datetime.now(UTC).isoformat(),
        }
    for event_type in ("cancellation.contradicted", "cancellation.verification_pending"):
        await queue_event(
            db,
            obligation,
            event_type,
            f"{event_type}:{evidence.id}",
            {"evidence_id": str(evidence.id), "reverification_required": True},
        )
    return True


async def ingest_source_document(
    db: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    connection_id: UUID,
    document: SourceDocument,
) -> ResolutionResult:
    """Apply one authorized source revision in the caller's transaction.

    Ingestion callers must freshly authorize their source capability. This
    boundary additionally enforces workspace, owner, provider and active source
    identity. It never executes actions, copies URLs, or configures capabilities.
    """
    from navox.subscriptions.service import validate_source_connection

    if document.workspace_id != workspace_id:
        raise PermissionError("subscription_source_workspace_mismatch")
    provenance_id = await validate_source_connection(
        db, workspace_id=workspace_id, user_id=user_id, connection_id=connection_id
    )
    connection = await db.get(Connection, provenance_id)
    if (
        connection is None
        or connection.status != "active"
        or connection.provider != document.provider
    ):
        raise PermissionError("subscription_source_provider_mismatch")
    await db.scalar(select(User).where(User.id == user_id).with_for_update())
    key = sha256(
        f"subscription.v1:{provenance_id}:{document.source_type}:{document.external_id}:"
        f"{source_document_hash(document)}".encode()
    ).hexdigest()
    previous = await db.scalar(
        select(RecurringObligationEvidence).where(
            RecurringObligationEvidence.workspace_id == workspace_id,
            RecurringObligationEvidence.user_id == user_id,
            RecurringObligationEvidence.dedupe_key == key,
        )
    )
    if previous:
        return ResolutionResult(previous.obligation_id, "ATTACH_EVIDENCE", previous.id, True)
    candidate = extract_evidence(document)
    if candidate is None:
        return ResolutionResult(None, "IGNORE")
    merchant, merchant_ambiguous = await _merchant(db, workspace_id, user_id, candidate)
    obligation, created, ambiguous = await _obligation(
        db, workspace_id, user_id, provenance_id, merchant, candidate, merchant_ambiguous
    )
    evidence = RecurringObligationEvidence(
        workspace_id=workspace_id,
        user_id=user_id,
        obligation_id=obligation.id,
        evidence_type=candidate.evidence_type,
        source_type=document.source_type,
        connection_id=provenance_id,
        external_resource_id=document.external_id,
        dedupe_key=key,
        merchant_text=candidate.merchant_text,
        plan_text=candidate.plan_text,
        amount=candidate.amount,
        currency=candidate.currency,
        billing_interval=candidate.billing_interval,
        interval_count=candidate.interval_count,
        effective_at=candidate.effective_at,
        renewal_at=candidate.renewal_at,
        confidence=candidate.confidence,
        observed_at=document.occurred_at,
        evidence_metadata={
            "source_hash": source_document_hash(document),
            "source_provider": document.provider,
            "extractor_version": "subscription-evidence.v1",
            "warnings": candidate.warnings,
            "needs_confirmation": candidate.needs_confirmation or ambiguous,
            "date_precision": "day",
            "extracted_candidate": candidate.model_dump(mode="json"),
        },
    )
    db.add(evidence)
    await db.flush()
    raw_overrides = obligation.obligation_metadata.get("user_overrides", [])
    overrides = (
        {value for value in raw_overrides if isinstance(value, str)}
        if isinstance(raw_overrides, list)
        else set()
    )
    now = datetime.now(UTC)
    changed_price = await _record_price(db, obligation, evidence, candidate, now, overrides)
    contradicted = await _contradiction(db, obligation, candidate, evidence, document.occurred_at)
    previous_observed = obligation.obligation_metadata.get("last_evidence_at")
    try:
        latest = (
            datetime.fromisoformat(previous_observed)
            if isinstance(previous_observed, str)
            else None
        )
    except ValueError:
        latest = None
    newest = latest is None or _aware(document.occurred_at) >= _aware(latest)
    event_type = "obligation.discovered" if created else "obligation.updated"
    outcome = "CREATE_OBLIGATION" if created else "ATTACH_EVIDENCE"
    if newest and not ambiguous:
        old_status = obligation.status
        # Cancellation signals remain unverified. An email does not grant the
        # right to cancel nor independently prove a provider-side state change.
        if candidate.evidence_type.startswith("CANCELLATION"):
            obligation.obligation_metadata = {
                **obligation.obligation_metadata,
                "cancellation_signal": candidate.evidence_type,
                "needs_confirmation": True,
            }
            outcome = "CANCELLATION_SIGNAL"
        elif candidate.evidence_type == "EXPIRATION":
            outcome = "EXPIRATION_SIGNAL"
            obligation.obligation_metadata = {
                **obligation.obligation_metadata,
                "expiration_signal": True,
                "needs_confirmation": True,
            }
        elif not candidate.needs_confirmation and not contradicted:
            if "status" not in overrides and old_status not in {
                "CANCELLED",
                "CANCEL_PENDING",
                "CANCELLATION_REQUESTED",
            }:
                obligation.status = (
                    "TRIAL" if candidate.evidence_type.startswith("TRIAL") else "ACTIVE"
                )
            if candidate.evidence_type.startswith("TRIAL"):
                event_type = "trial.started" if created else "trial.ending"
            elif old_status == "TRIAL" and candidate.evidence_type in {"PAYMENT", "RECEIPT"}:
                event_type = "trial.converted"
            elif candidate.evidence_type == "RENEWAL_NOTICE":
                event_type = (
                    "obligation.renewed"
                    if re.search(r"\brenewed\b", document.content or "", re.I)
                    else "obligation.updated"
                )
        for field, value in (
            ("next_renewal_at", candidate.renewal_at),
            ("trial_ends_at", candidate.trial_ends_at),
            ("trial_conversion_amount", candidate.trial_conversion_amount),
            ("trial_conversion_interval", candidate.trial_conversion_interval),
            ("auto_renew", candidate.auto_renew),
        ):
            if value is not None and field not in overrides:
                setattr(obligation, field, value)
        obligation.obligation_metadata = {
            **obligation.obligation_metadata,
            "last_evidence_at": document.occurred_at.isoformat(),
        }
    if changed_price:
        event_type = "obligation.price_changed"
        outcome = "PRICE_CHANGE"
    if created:
        obligation.started_at = document.occurred_at
    else:
        obligation.revision += 1
    if candidate.needs_confirmation or ambiguous:
        outcome = (
            "NEEDS_CONFIRMATION"
            if outcome not in {"CANCELLATION_SIGNAL", "EXPIRATION_SIGNAL"}
            else outcome
        )
    await queue_event(
        db,
        obligation,
        event_type,
        f"{event_type}:{evidence.id}",
        {"evidence_id": str(evidence.id), "source_connection_id": str(provenance_id)},
    )
    await db.flush()
    return ResolutionResult(obligation.id, outcome, evidence.id)
