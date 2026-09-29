"""Development-only policy selection and untouched-holdout clustering measurements."""

import argparse
import hashlib
import json
import os
from itertools import product
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from navox.news.cluster_selection import CandidateSet, SelectionPolicy, select_candidate
from navox.news.clustering import ClusterCalibration, ClusterDecision
from navox.news.contracts import Contract


class ClusterExample(Contract):
    query_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    event_group: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    split: Literal["development", "holdout"]
    candidates: CandidateSet
    expected: ClusterDecision
    expected_story_id: UUID | None = None

    @model_validator(mode="after")
    def target_consistency(self) -> "ClusterExample":
        if (self.expected == ClusterDecision.JOIN_EXISTING) != (self.expected_story_id is not None):
            raise ValueError("Only a join label has a target story")
        return self


class ClusterCorpus(Contract):
    reference: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    signal_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    # An identifier for the label record, not a claim that a human approved it.
    label_reference: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    examples: tuple[ClusterExample, ...] = Field(min_length=4, max_length=5000)

    @model_validator(mode="after")
    def disjoint_splits(self) -> "ClusterCorpus":
        seen_queries: set[str] = set()
        groups: dict[str, str] = {}
        stories: dict[UUID, str] = {}
        for example in self.examples:
            if example.query_id in seen_queries:
                raise ValueError("Duplicate query identity")
            seen_queries.add(example.query_id)
            if groups.setdefault(example.event_group, example.split) != example.split:
                raise ValueError("Event group leakage across splits")
            ids = {row.story_id for row in example.candidates.candidates}
            if example.expected_story_id is not None:
                ids.add(example.expected_story_id)
            for identifier in ids:
                if stories.setdefault(identifier, example.split) != example.split:
                    raise ValueError("Story identity leakage across splits")
        for split in ("development", "holdout"):
            labels = {row.expected for row in self.examples if row.split == split}
            if not {ClusterDecision.JOIN_EXISTING, ClusterDecision.CREATE_NEW} <= labels:
                raise ValueError("Each split needs positive and negative clustering labels")
        return self


class CalibrationGrid(Contract):
    join_thresholds: tuple[float, ...] = Field(default=(0.8, 0.9, 0.95), min_length=1, max_length=8)
    new_thresholds: tuple[float, ...] = Field(default=(0.3, 0.4), min_length=1, max_length=8)
    minimum_margins: tuple[float, ...] = Field(default=(0.0, 0.05, 0.1), min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounded_grid(self) -> "CalibrationGrid":
        values = (self.join_thresholds, self.new_thresholds, self.minimum_margins)
        if any(len(set(group)) != len(group) for group in values):
            raise ValueError("Duplicate grid values")
        if len(self.join_thresholds) * len(self.new_thresholds) * len(self.minimum_margins) > 64:
            raise ValueError("Calibration grid exceeds 64 policies")
        for join, new, margin in product(*values):
            make_policy("grid-validation", join, new, margin)
        return self


def make_policy(reference: str, join: float, new: float, margin: float) -> SelectionPolicy:
    return SelectionPolicy(
        calibration=ClusterCalibration(
            evaluation_reference=reference, join_threshold=join, new_threshold=new
        ),
        minimum_margin=margin,
    )


class ClusterMetrics(Contract):
    total: int
    correct: int
    expected_joins: int
    predicted_joins: int
    correct_joins: int
    false_joins: int
    missed_joins: int
    abstentions: int
    target_present: int
    precision: float | None
    recall: float | None
    candidate_recall: float | None
    observed_targets_met: bool


def measure(examples: tuple[ClusterExample, ...], policy: SelectionPolicy) -> ClusterMetrics:
    if not examples:
        raise ValueError("An empty sample cannot establish clustering quality")
    correct = expected = predicted = hits = abstentions = retrieved = 0
    for example in examples:
        result = select_candidate(example.candidates, policy)
        positive = example.expected == ClusterDecision.JOIN_EXISTING
        joined = result.decision == ClusterDecision.JOIN_EXISTING
        hit = positive and joined and result.story_id == example.expected_story_id
        expected += positive
        predicted += joined
        hits += hit
        retrieved += positive and any(
            row.story_id == example.expected_story_id for row in example.candidates.candidates
        )
        abstentions += result.decision == ClusterDecision.UNCERTAIN
        correct += hit or (not positive and result.decision == example.expected)
    precision = hits / predicted if predicted else None
    recall = hits / expected if expected else None
    # Missing candidates and abstentions remain in recall's denominator.
    met = bool(
        precision is not None
        and recall is not None
        and precision >= 0.95
        and recall >= 0.90
        and any(row.expected == ClusterDecision.CREATE_NEW for row in examples)
    )
    return ClusterMetrics(
        total=len(examples),
        correct=correct,
        expected_joins=expected,
        predicted_joins=predicted,
        correct_joins=hits,
        false_joins=predicted - hits,
        missed_joins=expected - hits,
        abstentions=abstentions,
        target_present=retrieved,
        precision=precision,
        recall=recall,
        candidate_recall=retrieved / expected if expected else None,
        observed_targets_met=met,
    )


class CalibrationReport(Contract):
    corpus_sha256: str
    signal_version: str
    candidate_policy: SelectionPolicy
    policies_evaluated: int
    development: ClusterMetrics
    holdout: ClusterMetrics
    # Fixture passes, precomputed scores and opaque label references cannot authorize routing.
    production_qualified: Literal[False] = False
    runtime_activation: Literal[False] = False


def fingerprint(corpus: ClusterCorpus) -> str:
    body = json.dumps(corpus.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def calibrate(corpus: ClusterCorpus, grid: CalibrationGrid | None = None) -> CalibrationReport:
    corpus = ClusterCorpus.model_validate(corpus)
    grid = CalibrationGrid.model_validate(grid) if grid is not None else CalibrationGrid()
    development = tuple(row for row in corpus.examples if row.split == "development")
    holdout = tuple(row for row in corpus.examples if row.split == "holdout")
    # Do not even evaluate holdout decisions until the development winner is frozen.
    proposals = []
    for join, new, margin in product(
        grid.join_thresholds, grid.new_thresholds, grid.minimum_margins
    ):
        policy = make_policy(corpus.reference, join, new, margin)
        metrics = measure(development, policy)
        # Feasible policies first; then correctness, fewer false joins and stricter ties.
        key = (
            metrics.observed_targets_met,
            metrics.correct,
            -metrics.false_joins,
            metrics.correct_joins,
            join,
            margin,
            -new,
        )
        proposals.append((key, policy, metrics))
    _, winner, dev_metrics = max(proposals, key=lambda entry: entry[0])
    return CalibrationReport(
        corpus_sha256=fingerprint(corpus),
        signal_version=corpus.signal_version,
        candidate_policy=winner,
        policies_evaluated=len(proposals),
        development=dev_metrics,
        holdout=measure(holdout, winner),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as stream:
            body = stream.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise ValueError("Corpus is too large")
        report = calibrate(ClusterCorpus.model_validate_json(body))
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(report.model_dump_json(indent=2) + "\n")
    except (ValueError, OSError):
        parser.error("Invalid corpus or unavailable output path; existing files were not replaced")
    passed = report.development.observed_targets_met and report.holdout.observed_targets_met
    print("Observed clustering targets: " + ("PASS" if passed else "FAIL"))
    print("Offline precomputed signals only. Production qualification and activation: false.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
