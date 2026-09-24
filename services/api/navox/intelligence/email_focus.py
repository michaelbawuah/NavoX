"""Read-time eligibility for saved email work, including pre-triage extractions.

No source reads, model calls, deletions, or lifecycle transitions occur here.
An AI 'confirmed' status is not evidence that the owner chose to track an item.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import AuditEvent, Commitment, CommitmentSource, IntelligenceFeedback
from navox.intelligence.email_relevance import ALLOWED_BASES, actionable_title
from navox.intelligence.extraction import EmailRelevance

UNDATED_EMAIL_DAYS = 14


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def email_hold_reason(commitment: Commitment, *, now: datetime) -> str | None:
    metadata = commitment.intelligence_metadata or {}
    try:
        relevance = EmailRelevance.model_validate(metadata.get("email_relevance"))
    except ValidationError:
        return "Email action has not been verified"
    if (
        not relevance.applies_to_user
        or relevance.confidence < 0.9
        or relevance.basis not in ALLOWED_BASES.get(relevance.intent, set())
        or not actionable_title(commitment.title)
    ):
        return "No clear personal action established"
    if metadata.get("evidence_review_reason") == "subject_only":
        return "Email subject alone does not establish a task"
    if commitment.status == "candidate" or commitment.confidence < 0.9:
        return "Suggested email action is still uncertain"
    # Do not turn long-past event attendance into a permanently overdue task.
    if (
        commitment.commitment_type == "meeting"
        and commitment.due_at
        and utc(commitment.due_at) < utc(now) - timedelta(days=1)
    ):
        return "Email refers to a past meeting"
    # Keep dated obligations (including overdue work). For undated AI suggestions,
    # source time is meaningful; ingestion time or a score refresh is not freshness.
    if commitment.due_at is None:
        verified = commitment.last_verified_at
        if verified is None or utc(verified) < utc(now) - timedelta(days=UNDATED_EMAIL_DAYS):
            return "Older email with no current due date"
    return None


async def email_holds(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    commitments: list[Commitment],
    now: datetime,
) -> dict[UUID, str]:
    """Batch reads; only Gmail-derived AI work is subject to this default view rule."""
    email_ids = set(
        await database.scalars(
            select(CommitmentSource.commitment_id)
            .join(Commitment)
            .where(
                Commitment.user_id == user_id,
                Commitment.workspace_id == workspace_id,
                Commitment.created_by == "ai",
                CommitmentSource.provider == "google",
                CommitmentSource.source_type == "gmail_message",
            )
        )
    )
    if not email_ids:
        return {}
    chosen_ids = set(
        await database.scalars(
            select(AuditEvent.entity_id).where(
                AuditEvent.user_id == user_id,
                AuditEvent.workspace_id == workspace_id,
                AuditEvent.actor_type == "user",
                AuditEvent.entity_type == "commitment",
                AuditEvent.event_type.in_(
                    ("commitment.confirmed", "commitment.kept", "commitment.state_changed")
                ),
            )
        )
    )
    feedback = await database.scalars(
        select(IntelligenceFeedback)
        .where(
            IntelligenceFeedback.user_id == user_id,
            IntelligenceFeedback.workspace_id == workspace_id,
            IntelligenceFeedback.target_type == "commitment",
        )
        .order_by(IntelligenceFeedback.created_at.desc(), IntelligenceFeedback.id.desc())
    )
    seen_feedback: set[UUID] = set()
    for item in feedback:
        if item.target_id not in seen_feedback:
            seen_feedback.add(item.target_id)
            if item.feedback_type in {"useful", "more_like_this"}:
                chosen_ids.add(item.target_id)
    held = {}
    for commitment in commitments:
        if commitment.id not in email_ids or commitment.id in chosen_ids:
            continue
        reason = email_hold_reason(commitment, now=now)
        if reason:
            held[commitment.id] = reason
    return held
