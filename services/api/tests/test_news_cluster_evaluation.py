"""Synthetic algorithm regressions, never a measured news accuracy corpus."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from itertools import permutations
from uuid import UUID

import pytest
from pydantic import ValidationError

from navox.news.cluster_evaluation import (
    CalibrationGrid,
    CalibrationReport,
    ClusterCorpus,
    ClusterExample,
    CorpusProvenance,
    calibrate,
    make_policy,
    measure,
)
from navox.news.cluster_selection import CandidateSet, ClusterCandidate, select_candidate
from navox.news.clustering import ClusterDecision, ClusterSignals


def candidate(identifier, score, **overrides):
    values = (
        dict(
            semantic_similarity=score,
            entity_overlap=score,
            temporal_proximity=score,
            geographic_overlap=score,
            event_type_similarity=score,
            topic_overlap=score,
        )
        | overrides
    )
    return ClusterCandidate(story_id=UUID(int=identifier), signals=ClusterSignals(**values))


def example(number, split, positive, score=None):
    identifier = number * 10 + 1
    return ClusterExample(
        query_id=f"query_{number}",
        event_group=f"event_{number}",
        split=split,
        candidates=CandidateSet(
            candidates=(
                candidate(identifier, score if score is not None else (0.9 if positive else 0.1)),
            ),
            complete=True,
        ),
        expected=ClusterDecision.JOIN_EXISTING if positive else ClusterDecision.CREATE_NEW,
        expected_story_id=UUID(int=identifier) if positive else None,
    )


def corpus():
    return ClusterCorpus(
        reference="synthetic-regression-only",
        signal_version="fixture-v1",
        label_reference="test-authored-labels-not-human-acceptance",
        provenance=CorpusProvenance(
            source_identity="authored-cluster-fixture",
            captured_at=datetime(2026, 9, 29, 12, tzinfo=UTC),
            rights_basis="authored synthetic fixture; no publisher text is captured",
            label_authority="authored",
            content_digest="0" * 64,
        ),
        examples=(
            example(1, "development", True),
            example(2, "development", False),
            example(3, "holdout", True),
            example(4, "holdout", False),
        ),
    )


def policy():
    return make_policy("synthetic-policy", 0.9, 0.3, 0.05)


@pytest.mark.parametrize("complete", [False, True])
def test_no_policy_always_abstains(complete):
    result = select_candidate(CandidateSet(candidates=(candidate(1, 1),), complete=complete), None)
    assert result.decision == ClusterDecision.UNCERTAIN and result.story_id is None


def test_incomplete_retrieval_never_creates_or_joins():
    for rows in [(), (candidate(1, 1),), (candidate(1, 0),)]:
        result = select_candidate(CandidateSet(candidates=rows), policy())
        assert result.reason == "incomplete_candidates" and result.story_id is None


def test_order_cannot_choose_a_different_story_or_break_ties():
    for rows in permutations([candidate(1, 0.98), candidate(2, 0.1), candidate(3, 0.2)]):
        result = select_candidate(CandidateSet(candidates=rows, complete=True), policy())
        assert result.decision == ClusterDecision.JOIN_EXISTING and result.story_id == UUID(int=1)
    for margin in (0.0, 0.05):
        result = select_candidate(
            CandidateSet(candidates=(candidate(1, 1), candidate(2, 1)), complete=True),
            make_policy("tie", 0.9, 0.3, margin),
        )
        assert result.reason == "ambiguous_candidates" and result.story_id is None


def test_compatibility_entities_and_empty_candidates():
    for rows in [(), (candidate(1, 1, incompatible_events=True),)]:
        assert (
            select_candidate(CandidateSet(candidates=rows, complete=True), policy()).decision
            == ClusterDecision.CREATE_NEW
        )
    result = select_candidate(
        CandidateSet(candidates=(candidate(1, 1, entity_overlap=0),), complete=True),
        make_policy("no-entity", 0.7, 0.3, 0.0),
    )
    assert result.decision == ClusterDecision.UNCERTAIN


@pytest.mark.parametrize(
    "values",
    [
        {"candidates": (candidate(1, 1), candidate(1, 0.5))},
        {"candidates": tuple(candidate(i + 1, 1) for i in range(101))},
        {"complete": "true"},
    ],
)
def test_candidate_contract_rejects_ambiguous_or_unbounded_input(values):
    with pytest.raises(ValidationError):
        CandidateSet(**values)


def test_missing_target_and_wrong_join_stay_in_denominators():
    first = example(1, "development", True)
    missing = example(2, "development", True).model_copy(
        update={"candidates": CandidateSet(complete=True)}
    )
    wrong = example(3, "development", True).model_copy(
        update={"candidates": CandidateSet(candidates=(candidate(999, 1),), complete=True)}
    )
    metrics = measure((first, missing, wrong, example(4, "development", False)), policy())
    assert metrics.expected_joins == 3 and metrics.predicted_joins == 2
    assert metrics.correct_joins == 1 and metrics.false_joins == 1 and metrics.missed_joins == 2
    assert metrics.precision == 0.5 and metrics.recall == pytest.approx(1 / 3)
    assert metrics.candidate_recall == pytest.approx(1 / 3) and not metrics.observed_targets_met


def test_zero_join_predictions_are_not_perfect_precision():
    metrics = measure(
        (example(1, "development", True, 0.1), example(2, "development", False)), policy()
    )
    assert metrics.precision is None and metrics.recall == 0 and not metrics.observed_targets_met
    with pytest.raises(ValueError):
        measure((), policy())


@pytest.mark.parametrize("kind", ["query", "event", "story", "no-negative", "target"])
def test_corpus_leakage_and_label_integrity(kind):
    body = corpus().model_dump(mode="json")
    rows = body["examples"]
    if kind == "query":
        rows[2]["query_id"] = rows[0]["query_id"]
    if kind == "event":
        rows[2]["event_group"] = rows[0]["event_group"]
    if kind == "story":
        rows[2]["candidates"]["candidates"][0]["story_id"] = rows[0]["expected_story_id"]
    if kind == "no-negative":
        rows.pop()
    if kind == "target":
        rows[0]["expected_story_id"] = None
    with pytest.raises(ValidationError):
        ClusterCorpus.model_validate(body)


def test_holdout_cannot_change_the_selected_policy():
    before = calibrate(corpus())
    changed = corpus().model_dump(mode="json")
    for entry in changed["examples"]:
        if entry["split"] == "holdout":
            for row in entry["candidates"]["candidates"]:
                row["signals"] = candidate(999, 0.05).signals.model_dump(mode="json")
    after = calibrate(ClusterCorpus.model_validate(changed))
    assert before.candidate_policy == after.candidate_policy
    assert before.development == after.development and before.holdout != after.holdout
    assert not before.production_qualified and not before.runtime_activation
    assert before.corpus_sha256 != after.corpus_sha256


def test_corpus_requires_provenance():
    body = corpus().model_dump(mode="json")
    del body["provenance"]
    with pytest.raises(ValidationError):
        ClusterCorpus.model_validate(body)


def test_reviewer_labels_need_a_recorded_reviewer_identity():
    body = corpus().model_dump(mode="json")
    body["provenance"]["label_authority"] = "reviewer"
    with pytest.raises(ValidationError):
        ClusterCorpus.model_validate(body)
    body["provenance"]["reviewer_identity"] = "named-fixture-reviewer"
    assert (
        ClusterCorpus.model_validate(body).provenance.reviewer_identity == "named-fixture-reviewer"
    )
    for authority in ("authored", "machine"):
        body["provenance"]["label_authority"] = authority
        with pytest.raises(ValidationError):
            ClusterCorpus.model_validate(body)


def test_report_without_provenance_is_impossible():
    report = calibrate(corpus())
    assert report.provenance == corpus().provenance
    assert report.provenance.label_authority == "authored"
    assert report.provenance.reviewer_identity is None
    body = report.model_dump(mode="json")
    del body["provenance"]
    with pytest.raises(ValidationError):
        CalibrationReport.model_validate(body)


@pytest.mark.parametrize(
    "values",
    [
        {"join_thresholds": (0.9, 0.9)},
        {"join_thresholds": (float("nan"),)},
        {"minimum_margins": (-1,)},
        {"new_thresholds": (0.8,)},
        {"join_thresholds": ()},
        {
            "join_thresholds": tuple(0.71 + i / 100 for i in range(8)),
            "new_thresholds": tuple(i / 100 for i in range(8)),
            "minimum_margins": (0.0, 0.05),
        },
    ],
)
def test_invalid_search_grid_rejected(values):
    with pytest.raises(ValueError):
        CalibrationGrid(**values)


def test_cli_writes_private_report_without_overwriting(tmp_path):
    source, output = tmp_path / "corpus.json", tmp_path / "report.json"
    source.write_text(corpus().model_dump_json())
    command = [
        sys.executable,
        "-m",
        "navox.news.cluster_evaluation",
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    saved = output.read_bytes()
    report = json.loads(saved)
    assert not report["production_qualified"] and not report["runtime_activation"]
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert output.read_bytes() == saved
