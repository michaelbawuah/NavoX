"""Conservative source-backed state inference; no external actions are executed."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import AuditEvent, Commitment, Connection
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import OperationalObservationCandidate

TERMINAL_STATUSES = frozenset({"completed", "rejected", "superseded"})
AUTOMATABLE_STATUSES = frozenset({"confirmed", "upcoming", "attention", "waiting"})


@dataclass(frozen=True)
class StateDecision:
    status: str | None
    condition: str | None
    reason: str


def outgoing_source(document: SourceDocument, connection: Connection) -> bool:
    labels = document.metadata.get("label_ids", [])
    return bool(
        isinstance(labels, list)
        and "SENT" in labels
        and document.author
        and document.author.identity_type == "email"
        and connection.external_email
        and document.author.identity_value.casefold() == connection.external_email.casefold()
    )


def infer_state(
    commitment: Commitment,
    *,
    candidate: OperationalObservationCandidate,
    document: SourceDocument,
    connection: Connection,
) -> StateDecision:
    if commitment.status not in AUTOMATABLE_STATUSES:
        return StateDecision(None, None, "User review or terminal state is preserved")
    if candidate.confidence < 0.9:
        return StateDecision(None, None, "State evidence requires high confidence")
    # Only the cited passage is considered. Quoted history, questions, negation,
    # future intention and hypothetical statements cannot prove an outcome.
    evidence = " ".join(span.text for span in candidate.evidence).casefold()
    content = document.content or ""
    quoted_start = re.search(
        r"(?im)^(?:on .{1,160}wrote:|[- ]*forwarded message[- ]*|[- ]*original message[- ]*|\s*>)",
        content,
    )
    if quoted_start and any(
        span.source == "content" and span.end_char > quoted_start.start()
        for span in candidate.evidence
    ):
        return StateDecision(None, None, "Quoted history is not a fresh outcome")
    if any(re.search(r"(?m)^\s*>", span.text) for span in candidate.evidence) or re.search(
        r"\b(not|never|haven't|hasn't|didn't|don't|cannot|can't|if|would|will|might|plan to)\b",
        evidence,
    ):
        return StateDecision(None, None, "Evidence is ambiguous or does not assert an outcome")
    sent = outgoing_source(document, connection)
    if candidate.observation_type == "waiting" and sent:
        if re.search(r"\b(please|request|requesting|awaiting|waiting for|could you)\b", evidence):
            return StateDecision("waiting", "EXTERNAL_RESPONSE", "Request sent; response pending")
    if candidate.observation_type != "completion":
        return StateDecision(None, None, "No explicit state evidence")
    if "?" in evidence:
        return StateDecision(None, None, "A question is not outcome evidence")
    metadata = commitment.intelligence_metadata or {}
    if commitment.status == "waiting":
        author = document.author.identity_value.casefold() if document.author else ""
        expected = metadata.get("waiting_for_identities", [])
        if sent or not author or not isinstance(expected, list) or author not in expected:
            return StateDecision(None, None, "Response is not from the expected counterparty")
        if re.search(r"\b(approved|confirmed|accepted|granted)\b", evidence):
            return StateDecision(
                "completed", "EXTERNAL_RESPONSE", "Expected counterparty confirmed the outcome"
            )
        return StateDecision(None, None, "A reply alone does not satisfy the expected outcome")
    if sent and re.search(
        r"\b(attached|sent|submitted|delivered|completed|here is|here's)\b", evidence
    ):
        # Approval objectives require the response, even if the request was sent.
        if re.search(r"\b(approval|permission|confirmation)\b", commitment.title.casefold()):
            return StateDecision("waiting", "EXTERNAL_RESPONSE", "Request delivery is not approval")
        return StateDecision(
            "completed", "SEMANTIC_OUTCOME", "Sent evidence describes the delivered outcome"
        )
    return StateDecision(None, None, "No sufficiently grounded state transition")


def infer_time_state(commitment: Commitment, *, now: datetime) -> StateDecision:
    """A passed task deadline is attention-worthy, never proof of completion."""
    if commitment.status not in AUTOMATABLE_STATUSES:
        return StateDecision(None, None, "User review or terminal state is preserved")
    metadata = commitment.intelligence_metadata or {}
    end = metadata.get("calendar_end_at")
    if (
        commitment.commitment_type == "meeting"
        and metadata.get("completion_condition") == "EVENT_OCCURRED"
        and isinstance(end, str)
    ):
        try:
            end_at = datetime.fromisoformat(end)
        except ValueError:
            return StateDecision(None, None, "Calendar end is invalid")
        if end_at.tzinfo is not None and end_at <= now:
            return StateDecision("completed", "EVENT_OCCURRED", "Calendar event interval has ended")
    if commitment.due_at and commitment.status != "waiting":
        due_at = commitment.due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)
        if due_at <= now:
            return StateDecision(
                "attention", "DEADLINE_PASSED", "Deadline passed without outcome evidence"
            )
    return StateDecision(None, None, "No time-driven state transition")


async def reevaluate_commitment(
    database: AsyncSession, *, commitment: Commitment, now: datetime
) -> bool:
    """Caller loads a scoped commitment and owns the transaction."""
    decision = infer_time_state(commitment, now=now)
    if decision.status is None or decision.status == commitment.status:
        return False
    previous = commitment.status
    commitment.status = decision.status
    if decision.status == "completed":
        commitment.completed_at = now
    commitment.intelligence_metadata = {
        **(commitment.intelligence_metadata or {}),
        "lifecycle_state": "COMPLETED" if decision.status == "completed" else "ATTENTION_NEEDED",
        "state_condition": decision.condition,
        "state_reason": decision.reason,
    }
    database.add(
        AuditEvent(
            workspace_id=commitment.workspace_id,
            user_id=commitment.user_id,
            event_type="intelligence.time_transition",
            actor_type="system",
            entity_type="commitment",
            entity_id=commitment.id,
            event_metadata={
                "previous_status": previous,
                "status": decision.status,
                "condition": decision.condition,
            },
        )
    )
    await database.flush()
    return True
