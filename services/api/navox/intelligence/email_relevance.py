"""Conservative email surfacing policy after schema and exact-evidence validation."""

import re

from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import OperationalExtraction, OperationalObservationCandidate

ALLOWED_BASES = {
    "reply_required": {"direct_request"},
    "action_required": {"direct_request", "assigned_obligation"},
    "important_alert": {
        "security_risk",
        "payment_problem",
        "service_disruption",
        "schedule_change",
    },
    "commitment_update": {"commitment_progress"},
}


def eligible_email_observation(
    candidate: OperationalObservationCandidate, document: SourceDocument
) -> bool:
    """High extraction confidence alone never establishes a personal obligation."""
    relevance = candidate.email_relevance
    if (
        relevance is None
        or not relevance.applies_to_user
        or relevance.confidence < 0.9
        or relevance.basis not in ALLOWED_BASES.get(relevance.intent, set())
    ):
        return False
    state_fact = candidate.observation_type in {"completion", "waiting"}
    if state_fact != (relevance.intent == "commitment_update"):
        return False
    if (candidate.observation_type == "alert") != (relevance.intent == "important_alert"):
        return False
    labels = document.metadata.get("label_ids", [])
    labels = labels if isinstance(labels, list) else []
    if any(label in labels for label in ("SPAM", "TRASH", "DRAFT")):
        return False
    if "SENT" in labels and relevance.intent == "reply_required":
        return False  # The user's outgoing question is not a reply they owe.
    quoted_start = re.search(
        r"(?im)^(?:on .{1,160}wrote:|[- ]*forwarded message[- ]*|[- ]*original message[- ]*|\s*>)",
        document.content or "",
    )
    if quoted_start and any(
        span.source == "content" and span.end_char > quoted_start.start()
        for span in candidate.evidence
    ):
        return False  # Quoted old requests cannot establish a fresh obligation.
    bulk = "CATEGORY_PROMOTIONS" in labels or document.metadata.get("list_unsubscribe") is True
    if bulk:
        # Unsubscribe headers also occur on school/account/service notices. Preserve
        # a body-backed obligation or consequential alert, never a generic CTA.
        transactional = relevance.basis in {
            "assigned_obligation",
            "security_risk",
            "payment_problem",
            "service_disruption",
            "schedule_change",
            "commitment_progress",
        }
        if not transactional or not any(span.source == "content" for span in candidate.evidence):
            return False
    return True


def filter_email_extraction(
    extraction: OperationalExtraction, document: SourceDocument
) -> OperationalExtraction:
    if document.source_type != "gmail_message":
        return extraction
    observations = [
        item for item in extraction.observations if eligible_email_observation(item, document)
    ]
    # Irrelevant emails must not populate Today or accumulate incidental contacts,
    # dates and relationships. Source receipts still make an ignored revision final.
    return extraction.model_copy(
        update={
            "observations": observations,
            "people": extraction.people if observations else [],
            "temporals": extraction.temporals if observations else [],
            "relationships": extraction.relationships if observations else [],
        }
    )
