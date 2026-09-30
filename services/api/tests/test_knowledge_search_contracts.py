"""Planner and public contract regressions for connected search."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from navox.knowledge.contracts import ResourceType
from navox.knowledge.planner import interpret, query_terms, suggested_followups
from navox.knowledge.search_contracts import (
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    DateRange,
    EvidenceBundleV1,
    EvidenceExcerpt,
    EvidenceResource,
    RetrieverMode,
    SearchMode,
    SearchRequest,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def request(**overrides: object) -> SearchRequest:
    values: dict[str, object] = {"query": "quarterly budget"}
    values.update(overrides)
    return SearchRequest(**values)


def test_search_request_rejects_blank_overlong_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        request(query="   ")
    with pytest.raises(ValidationError):
        request(query="x" * 2001)
    with pytest.raises(ValidationError):
        request(workspace_id=uuid4())
    with pytest.raises(ValidationError):
        request(limit=0)
    with pytest.raises(ValidationError):
        request(limit=51)
    assert request(query="  budget  ").query == "budget"


def test_date_range_requires_an_aware_non_empty_window() -> None:
    with pytest.raises(ValidationError):
        DateRange()
    with pytest.raises(ValidationError):
        DateRange(start=NOW, end=NOW)
    with pytest.raises(ValidationError):
        DateRange(start=datetime(2026, 9, 29, 12, 0))
    window = DateRange(start=NOW, end=NOW + timedelta(days=1))
    assert window.start == NOW


def test_auto_interprets_questions_as_ask_and_resource_queries_as_search() -> None:
    question = interpret(request(query="What did the finance team decide?"))
    assert question.mode is SearchMode.ASK
    assert question.intent.value == "QUESTION_ANSWERING"
    listing = interpret(request(query="quarterly budget"))
    assert listing.mode is SearchMode.SEARCH


def test_planner_surface_cues_are_deterministic_and_reported() -> None:
    assert interpret(request(query="When does the trial end?")).intent.value == "TIMELINE"
    assert (
        interpret(request(query="how many subscriptions renew soon")).intent.value == "AGGREGATION"
    )
    assert interpret(request(query="is the subscription canceled")).intent.value == (
        "OPERATIONAL_STATE"
    )
    assert interpret(request(query="Budget")).intent.value == "ENTITY_LOOKUP"
    assert interpret(request(query="quarterly budget review")).intent.value == "FIND_RESOURCE"
    timeline = interpret(request(query="budget", date_range=DateRange(start=NOW)))
    assert RetrieverMode.STRUCTURED in timeline.retrievers
    assert timeline.freshness.value == "CACHED"
    assert interpret(request(query="latest budget")).freshness.value == "FRESH"


def test_explicit_mode_is_never_overridden() -> None:
    assert interpret(request(query="What changed?", mode=SearchMode.SEARCH)).mode is (
        SearchMode.SEARCH
    )
    assert interpret(request(query="budget", mode=SearchMode.ASK)).mode is SearchMode.ASK


def test_query_terms_are_bounded_deduplicated_and_stopworded() -> None:
    terms = query_terms("What is the budget for the budget review of the finance team?")
    assert terms == ("budget", "review", "finance", "team")
    assert len(query_terms(" ".join(f"token{i}" for i in range(50)))) == 12


def test_followups_are_bounded_and_do_not_promise_an_answer() -> None:
    plan = interpret(request(query="quarterly budget"))
    suggestions = suggested_followups(plan, returned=2)
    assert 0 < len(suggestions) <= 3
    assert all("answer" not in item.casefold() for item in suggestions)
    empty = suggested_followups(plan, returned=0)
    assert any("date range" in item.casefold() for item in empty)


def test_evidence_bundle_is_versioned_and_provider_independent() -> None:
    resource = EvidenceResource(
        source_type=ResourceType.EMAIL.value,
        resource_id=uuid4(),
        title="Quarterly planning review",
        excerpts=(EvidenceExcerpt(text="Budget forecast", start=0, end=15),),
        provenance={"connection_id": str(uuid4())},
    )
    bundle = EvidenceBundleV1(
        query="quarterly budget",
        resources=(resource,),
        permission_snapshot_id="snapshot",
        retrieval_trace_id="trace",
    )
    assert bundle.schema_version == EVIDENCE_BUNDLE_SCHEMA_VERSION
    assert bundle.resources[0].key == f"{resource.source_type}:{resource.resource_id}"
    with pytest.raises(ValidationError):
        EvidenceBundleV1(
            query="q",
            permission_snapshot_id="s",
            retrieval_trace_id="t",
            raw_scores={"a": 1.0},
        )


def test_evidence_excerpt_rejects_inverted_spans() -> None:
    with pytest.raises(ValidationError):
        EvidenceExcerpt(text="x", start=5, end=1)
