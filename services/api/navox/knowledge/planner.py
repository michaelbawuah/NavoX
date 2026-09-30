"""Deterministic request interpretation for connected search.

This is an application-owned baseline, not semantic understanding: it reads
explicit surface cues and reports what it did. Unknown questions fall back to
resource search rather than pretending to answer.
"""

from __future__ import annotations

import re

from navox.knowledge.search_contracts import (
    DateRange,
    Freshness,
    RetrievalIntent,
    RetrievalPlan,
    RetrieverMode,
    SearchMode,
    SearchRequest,
)

_WORD = re.compile(r"[a-z0-9][a-z0-9'._-]*")
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "find",
        "for",
        "from",
        "get",
        "give",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "show",
        "tell",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "this",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)
_QUESTION_CUES = frozenset({"who", "what", "when", "where", "why", "how", "which", "whom", "whose"})
_TIMELINE_CUES = frozenset(
    {"when", "timeline", "schedule", "scheduled", "upcoming", "due", "deadline", "renewal"}
)
_OPERATIONAL_CUES = frozenset(
    {"status", "state", "active", "expired", "cancelled", "canceled", "paused", "renewing"}
)
_AGGREGATION_CUES = frozenset(
    {"how much", "how many", "total", "count", "spend", "spent", "sum", "average"}
)
_RELATIONSHIP_CUES = frozenset(
    {"related", "relationship", "connected", "between", "linked", "associated"}
)
_ENTITY_CUES = frozenset({"who is", "who are", "profile", "contact", "person"})
_FRESH_CUES = frozenset({"latest", "newest", "current", "today", "now", "recent", "live"})


def query_terms(query: str, *, limit: int = 12) -> tuple[str, ...]:
    """Bounded, deterministic, deduplicated search terms."""
    seen: list[str] = []
    for match in _WORD.findall(query.casefold()):
        if len(match) < 2 or match in _STOPWORDS or match in seen:
            continue
        seen.append(match)
        if len(seen) == limit:
            break
    return tuple(seen)


def _intent(question: str, *, has_date_range: bool) -> RetrievalIntent:
    if any(cue in question for cue in _AGGREGATION_CUES):
        return RetrievalIntent.AGGREGATION
    if any(cue in question for cue in _ENTITY_CUES):
        return RetrievalIntent.ENTITY_LOOKUP
    if any(cue in question for cue in _RELATIONSHIP_CUES):
        return RetrievalIntent.RELATIONSHIP
    if any(cue in question for cue in _TIMELINE_CUES) or has_date_range:
        return RetrievalIntent.TIMELINE
    if any(cue in question for cue in _OPERATIONAL_CUES):
        return RetrievalIntent.OPERATIONAL_STATE
    if question.split(" ", 1)[0] in _QUESTION_CUES or question.endswith("?"):
        return RetrievalIntent.QUESTION_ANSWERING
    if len(question.split()) <= 2:
        return RetrievalIntent.ENTITY_LOOKUP
    return RetrievalIntent.FIND_RESOURCE


def interpret(request: SearchRequest) -> RetrievalPlan:
    """Interpret one validated request into a bounded, reportable plan."""
    normalized = " ".join(request.query.casefold().split())
    has_date_range = request.date_range is not None
    intent = _intent(normalized, has_date_range=has_date_range)

    if request.mode is SearchMode.AUTO:
        mode = SearchMode.ASK if intent is RetrievalIntent.QUESTION_ANSWERING else SearchMode.SEARCH
    else:
        mode = request.mode

    retrievers: list[RetrieverMode] = []
    if query_terms(request.query):
        retrievers.append(RetrieverMode.FULLTEXT)
    if (
        has_date_range
        or request.types
        or intent
        in {
            RetrievalIntent.TIMELINE,
            RetrievalIntent.OPERATIONAL_STATE,
            RetrievalIntent.AGGREGATION,
        }
    ):
        retrievers.append(RetrieverMode.STRUCTURED)
    if not retrievers:
        retrievers.append(RetrieverMode.FULLTEXT)

    if intent in {RetrievalIntent.RELATIONSHIP, RetrievalIntent.ENTITY_LOOKUP}:
        retrievers.append(RetrieverMode.GRAPH)

    freshness = (
        Freshness.FRESH if _FRESH_CUES.intersection(_WORD.findall(normalized)) else Freshness.CACHED
    )

    if request.mode is not SearchMode.AUTO:
        rationale = f"explicit {request.mode.value} request"
    elif mode is SearchMode.ASK:
        rationale = "question-shaped query interpreted as ask; answers need a qualified provider"
    else:
        rationale = "resource-shaped query interpreted as search"

    return RetrievalPlan(
        intent=intent,
        mode=mode,
        retrievers=tuple(retrievers),
        freshness=freshness,
        terms=query_terms(request.query),
        source_ids=tuple(request.sources),
        types=tuple(request.types),
        date_range=request.date_range,
        rationale=rationale,
    )


def suggested_followups(plan: RetrievalPlan, *, returned: int) -> tuple[str, ...]:
    """Bounded, honest next steps; never a fake promise of an answer."""
    if returned == 0:
        return (
            "Try fewer words or a different date range.",
            "Check whether the source is connected and currently authorized.",
        )
    suggestions: list[str] = []
    if plan.date_range is not None or plan.intent is RetrievalIntent.TIMELINE:
        suggestions.append("Narrow the date range to the window you mean.")
    if plan.freshness is not Freshness.FRESH:
        suggestions.append("Ask for the latest revision if freshness matters.")
    suggestions.append("Filter to one source or one record type to compare results.")
    return tuple(suggestions[:3])


def date_range_covers(value: DateRange | None, moment: object) -> bool:
    """Shared half-open window predicate for structured date filters."""
    from datetime import datetime

    if not isinstance(moment, datetime):
        return False
    if value is None:
        return True
    if value.start is not None and moment < value.start:
        return False
    return not (value.end is not None and moment >= value.end)
