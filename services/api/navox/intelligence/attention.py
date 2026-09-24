"""Deterministic ranking of stored facts, never an execution or permission decision."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import (
    Commitment,
    IntelligencePreference,
    Objective,
    ProactiveSignal,
)

FEATURE_WEIGHTS = {
    "urgency": 0.23,
    "consequence": 0.16,
    "objective_relevance": 0.14,
    "user_priority": 0.12,
    "actionability": 0.12,
    "waiting_duration": 0.08,
    "relationship_importance": 0.05,
    "confidence": 0.10,
    "interruption_cost": -0.08,
    "notification_fatigue": -0.08,
}
ACTIVE = {"candidate", "confirmed", "upcoming", "attention", "waiting", "waiting_on_external"}
WAITING = {"waiting", "waiting_on_external"}
CONSEQUENCE = {
    "deadline": 0.9,
    "renewal": 0.8,
    "meeting": 0.7,
    "promise": 0.7,
    "follow_up": 0.5,
    "task": 0.6,
}


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def bounded(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return min(1.0, max(0.0, number)) if isfinite(number) else default


def band_for(score: int) -> str:
    for threshold, band in ((90, "NOW"), (70, "TODAY_HIGH"), (50, "TODAY"), (30, "DASHBOARD")):
        if score >= threshold:
            return band
    return "SUPPRESS"


def email_intent(commitment: Commitment) -> str | None:
    relevance = (commitment.intelligence_metadata or {}).get("email_relevance")
    intent = relevance.get("intent") if isinstance(relevance, dict) else None
    return intent if isinstance(intent, str) else None


@dataclass(frozen=True)
class AttentionResult:
    score: int
    raw_score: int
    band: str
    factors: dict[str, float]
    reasons: tuple[str, ...]
    suggested_capability: str | None
    suppressed: bool = False


def temporal_boundary(commitment: Commitment) -> tuple[datetime | None, bool]:
    """A bounded date window can rank work without becoming an invented exact due time."""
    if commitment.due_at is not None:
        return aware(commitment.due_at), False
    metadata = commitment.intelligence_metadata or {}
    temporal = metadata.get("temporal")
    if isinstance(temporal, dict):
        end = temporal.get("end_at") or temporal.get("start_at")
        if isinstance(end, str):
            try:
                return aware(datetime.fromisoformat(end)), True
            except ValueError:
                pass
    return None, False


def score_commitment(
    commitment: Commitment,
    *,
    now: datetime,
    objective_active: bool = False,
    relationship_importance: float = 0.0,
    interruption_cost: float = 0.0,
    notification_fatigue: float = 0.0,
    duplicate: bool = False,
    preferences: dict[str, object] | None = None,
    dismissed: bool = False,
    snoozed: bool = False,
) -> AttentionResult:
    current = aware(now)
    due, window = temporal_boundary(commitment)
    hours = (due - current).total_seconds() / 3600 if due is not None else None
    urgency = 0.0
    reasons: list[str] = []
    if hours is not None:
        urgency = (
            1.0
            if hours <= 0
            else 0.95
            if hours <= 2
            else 0.85
            if hours <= 24
            else 0.65
            if hours <= 72
            else 0.4
            if hours <= 168
            else 0.1
        )
        if window:
            reasons.append("Estimated date window; exact time is unconfirmed")
        elif hours < 0:
            reasons.append("Overdue and unresolved")
        elif hours <= 24:
            reasons.append("Due within 24 hours")
        elif hours <= 168:
            reasons.append("Due within 7 days")
    waiting = commitment.status in WAITING
    since = commitment.waiting_since or commitment.updated_at or commitment.created_at or current
    waiting_days = max(0.0, (current - aware(since)).total_seconds() / 86400) if waiting else 0.0
    confidence = bounded(float(commitment.confidence))
    factors = {
        "urgency": urgency,
        "consequence": CONSEQUENCE.get(commitment.commitment_type, 0.5),
        "objective_relevance": 1.0 if objective_active else 0.0,
        "user_priority": bounded(commitment.priority / 5),
        "actionability": 0.25 if waiting else 0.5 if commitment.status == "candidate" else 1.0,
        "waiting_duration": min(1.0, waiting_days / 14),
        "relationship_importance": bounded(relationship_importance),
        "confidence": confidence,
        "interruption_cost": bounded(interruption_cost),
        "notification_fatigue": bounded(notification_fatigue),
    }
    raw_score = round(100 * sum(FEATURE_WEIGHTS[key] * value for key, value in factors.items()))
    raw_score = max(0, min(100, raw_score))
    # Learning adjusts priority by at most five points, after fact-based scoring.
    learned = (preferences or {}).get(commitment.commitment_type, 0.0)
    adjustment = max(-0.05, min(0.05, float(learned))) if isinstance(learned, (int, float)) else 0.0
    score = max(0, min(100, raw_score + round(adjustment * 100)))
    capability: str | None = "commitment.handle"
    intent = email_intent(commitment)
    if intent == "reply_required":
        reasons.append("Reply requested in email")
    elif intent == "important_alert":
        reasons.append("Important email alert")
        if confidence >= 0.9 and commitment.status != "candidate":
            score = max(score, 70)
    if commitment.priority >= 4:
        reasons.append(
            "You marked this as high priority"
            if commitment.created_by == "user"
            else "High priority saved commitment"
        )
    if objective_active:
        reasons.append("Linked to an active objective")
    if waiting:
        reasons.append("Waiting on an external response")
        if waiting_days < 1 and (hours is None or hours > 24):
            score = min(score, 49)
            reasons.append("Response window is still open")
    if (
        commitment.commitment_type == "meeting"
        and not window
        and hours is not None
        and 0 <= hours <= 2
    ):
        if confidence >= 0.9 and commitment.status != "candidate":
            score = max(score, 70)
            reasons.append("Meeting starts within two hours; preparation is useful now")
            capability = "meeting.prepare"
    if commitment.status == "candidate" or confidence < 0.9:
        score = min(score, 69)
        capability = "commitment.review"
        reasons.append("Needs your confirmation")
    if factors["notification_fatigue"] >= 0.75:
        score = min(score, 49)
        reasons.append("Recent reminders reduced interruption priority")
    if factors["interruption_cost"] >= 0.75:
        score = min(score, 89)
        reasons.append("Current interruption policy limits immediate notifications")
    suppressed = False
    if confidence < 0.7 and commitment.created_by != "user":
        score, suppressed, capability = 0, True, None
        reasons.append("Evidence confidence is below the surfacing threshold")
    if duplicate or dismissed or snoozed:
        score, suppressed, capability = 0, True, None
        reasons.append(
            "Duplicate item suppressed"
            if duplicate
            else "Snoozed by you"
            if snoozed
            else "Dismissed by you"
        )
    if commitment.status not in ACTIVE:
        score, suppressed, capability = 0, True, None
        reasons.append("This commitment is no longer active")
    if (commitment.valid_from and aware(commitment.valid_from) > current) or (
        commitment.valid_until and aware(commitment.valid_until) < current
    ):
        score, suppressed, capability = 0, True, None
        reasons.append("Outside the commitment's validity window")
    if not reasons:
        reasons.append("Active commitment with no immediate time pressure")
    return AttentionResult(
        score, raw_score, band_for(score), factors, tuple(reasons), capability, suppressed
    )


async def workspace_attention(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    now: datetime | None = None,
) -> tuple[list[Commitment], dict[UUID, AttentionResult]]:
    current = aware(now or datetime.now(UTC))
    commitments = list(
        await database.scalars(
            select(Commitment).where(
                Commitment.workspace_id == workspace_id,
                Commitment.user_id == user_id,
            )
        )
    )
    objectives = set(
        await database.scalars(
            select(Objective.id).where(
                Objective.workspace_id == workspace_id,
                Objective.user_id == user_id,
                Objective.status == "active",
            )
        )
    )
    preference = await database.scalar(
        select(IntelligencePreference).where(
            IntelligencePreference.workspace_id == workspace_id,
            IntelligencePreference.user_id == user_id,
        )
    )
    signals = list(
        await database.scalars(
            select(ProactiveSignal).where(
                ProactiveSignal.workspace_id == workspace_id,
                ProactiveSignal.user_id == user_id,
                ProactiveSignal.status != "resolved",
            )
        )
    )
    by_commitment: dict[UUID, list[ProactiveSignal]] = {}
    for signal in signals:
        if signal.commitment_id is not None:
            by_commitment.setdefault(signal.commitment_id, []).append(signal)
    results: dict[UUID, AttentionResult] = {}
    # Duplicate suppression uses explicit canonical state, never fuzzy title guesses.
    for commitment in commitments:
        own_signals = by_commitment.get(commitment.id, [])
        recent = [
            signal
            for signal in own_signals
            if signal.last_surfaced_at
            and current - aware(signal.last_surfaced_at) < timedelta(hours=4)
        ]
        metadata = commitment.intelligence_metadata or {}
        result = score_commitment(
            commitment,
            now=current,
            objective_active=commitment.objective_id in objectives,
            notification_fatigue=min(1.0, sum(signal.surface_count for signal in recent) / 3),
            duplicate=bool(metadata.get("duplicate_of")),
            preferences=preference.weights if preference else {},
            dismissed=any(signal.status == "dismissed" for signal in own_signals),
            snoozed=any(
                signal.snoozed_until and aware(signal.snoozed_until) > current
                for signal in own_signals
            ),
        )
        results[commitment.id] = result
    return commitments, results


async def evaluate_workspace_attention(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    now: datetime | None = None,
) -> dict[UUID, AttentionResult]:
    commitments, results = await workspace_attention(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        now=now,
    )
    for commitment in commitments:
        result = results[commitment.id]
        commitment.attention_score = result.score
        commitment.attention_band = result.band
        commitment.attention_factors = dict(result.factors)
    await database.flush()
    return results
