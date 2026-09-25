"""Authenticated feedback changes bounded ranking preferences, never authority."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.db.models import Commitment, IntelligenceFeedback, IntelligencePreference, Workspace

FeedbackType = Literal[
    "useful", "not_useful", "too_frequent", "incorrect", "more_like_this", "less_like_this"
]
ADJUSTMENTS = {
    "useful": 0.005,
    "more_like_this": 0.01,
    "not_useful": -0.005,
    "less_like_this": -0.01,
    "too_frequent": -0.01,
    "incorrect": 0.0,
}
KINDS = {"deadline", "meeting", "follow_up", "promise", "renewal", "task"}


class FeedbackTargetNotFound(ValueError):
    pass


class FeedbackConflict(ValueError):
    pass


def bounded_weights(weights: dict[str, object]) -> dict[str, float]:
    return {
        name: round(max(-0.05, min(0.05, float(value))), 4)
        for name, value in weights.items()
        if name in KINDS and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


async def record_feedback(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    target_id: UUID,
    request_id: UUID,
    feedback_type: FeedbackType,
    now: datetime | None = None,
) -> tuple[IntelligenceFeedback, bool, dict[str, float]]:
    current = now or datetime.now(UTC)
    # The workspace row exists before first feedback. Lock it to serialize both
    # first preference creation and request replay across concurrent workers.
    await database.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update(key_share=True)
    )
    commitment = await database.scalar(
        select(Commitment)
        .where(
            Commitment.id == target_id,
            Commitment.workspace_id == workspace_id,
            Commitment.user_id == user_id,
        )
        .with_for_update(key_share=True)
    )
    if commitment is None:
        raise FeedbackTargetNotFound("Commitment not found")
    existing = await database.scalar(
        select(IntelligenceFeedback).where(
            IntelligenceFeedback.workspace_id == workspace_id,
            IntelligenceFeedback.user_id == user_id,
            IntelligenceFeedback.request_id == request_id,
        )
    )
    if existing is not None:
        if existing.target_id != target_id or existing.feedback_type != feedback_type:
            raise FeedbackConflict("This request ID was already used for different feedback")
        saved = existing.feedback_metadata.get("weights", {})
        return existing, False, bounded_weights(saved if isinstance(saved, dict) else {})
    preference = await database.scalar(
        select(IntelligencePreference).where(
            IntelligencePreference.workspace_id == workspace_id,
            IntelligencePreference.user_id == user_id,
        )
    )
    if preference is None:
        preference = IntelligencePreference(workspace_id=workspace_id, user_id=user_id, weights={})
        database.add(preference)
    weights = bounded_weights(preference.weights)
    recent = await database.scalar(
        select(IntelligenceFeedback.id)
        .where(
            IntelligenceFeedback.workspace_id == workspace_id,
            IntelligenceFeedback.user_id == user_id,
            IntelligenceFeedback.target_id == target_id,
            IntelligenceFeedback.feedback_type == feedback_type,
            IntelligenceFeedback.created_at > current - timedelta(days=1),
        )
        .limit(1)
    )
    adjustment = ADJUSTMENTS[feedback_type] if recent is None else 0.0
    kind = commitment.commitment_type
    if kind in KINDS:
        weights[kind] = round(max(-0.05, min(0.05, weights.get(kind, 0.0) + adjustment)), 4)
    preference.weights = dict(weights)
    preference.updated_at = current
    feedback = IntelligenceFeedback(
        workspace_id=workspace_id,
        user_id=user_id,
        target_type="commitment",
        target_id=target_id,
        request_id=request_id,
        feedback_type=feedback_type,
        feedback_metadata={
            "ranking_adjustment": adjustment,
            "weights": weights,
            "learning_version": "bounded-ranking-v1",
        },
        created_at=current,
    )
    database.add(feedback)
    await database.flush()
    add_audit_event(
        database,
        user_id=user_id,
        workspace_id=workspace_id,
        event_type="intelligence.feedback.recorded",
        entity_type="intelligence_feedback",
        entity_id=feedback.id,
        actor_type="user",
        actor_id=str(user_id),
        metadata={
            "feedback_type": feedback_type,
            "target_id": str(target_id),
            "ranking_adjustment": adjustment,
        },
    )
    return feedback, True, weights
