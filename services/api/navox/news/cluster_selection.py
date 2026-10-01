"""Bounded candidate selection; a numeric policy is not deployment authorization."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from navox.news.clustering import (
    ClusterCalibration,
    ClusterDecision,
    ClusterSignals,
    decide_cluster,
)
from navox.news.contracts import Contract


class ClusterCandidate(Contract):
    story_id: UUID
    signals: ClusterSignals


class CandidateSet(Contract):
    candidates: tuple[ClusterCandidate, ...] = Field(default=(), max_length=100)
    # False when retrieval hit a limit, failed or cannot establish its complete scope.
    complete: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def unique_stories(self) -> "CandidateSet":
        if len({row.story_id for row in self.candidates}) != len(self.candidates):
            raise ValueError("Candidate story identities must be unique")
        return self


class SelectionPolicy(Contract):
    calibration: ClusterCalibration
    minimum_margin: float = Field(default=0.05, ge=0, le=1, strict=True)


class ClusterSelection(Contract):
    decision: ClusterDecision
    story_id: UUID | None = None
    reason: Literal[
        "uncalibrated",
        "incomplete_candidates",
        "no_compatible_candidate",
        "below_new_threshold",
        "insufficient_signal",
        "ambiguous_candidates",
        "clear_match",
    ]
    score: float | None = None


def select_candidate(candidates: CandidateSet, policy: SelectionPolicy | None) -> ClusterSelection:
    """Evaluate all supplied stories, never use traversal order to break ambiguity."""
    candidates = CandidateSet.model_validate(candidates)
    if policy is None:
        return ClusterSelection(decision=ClusterDecision.UNCERTAIN, reason="uncalibrated")
    policy = SelectionPolicy.model_validate(policy)
    if not candidates.complete:
        return ClusterSelection(decision=ClusterDecision.UNCERTAIN, reason="incomplete_candidates")
    ranked = sorted(
        (row for row in candidates.candidates if not row.signals.incompatible_events),
        key=lambda row: (-row.signals.score, row.story_id.hex),
    )
    if not ranked:
        return ClusterSelection(
            decision=ClusterDecision.CREATE_NEW, reason="no_compatible_candidate"
        )
    best = ranked[0]
    decision = decide_cluster(best.signals, policy.calibration)
    if decision == ClusterDecision.JOIN_EXISTING:
        gap = best.signals.score - ranked[1].signals.score if len(ranked) > 1 else None
        # Even a configured zero margin must not turn an exact tie into a join.
        if gap is not None and (gap <= 1e-12 or gap < policy.minimum_margin):
            return ClusterSelection(
                decision=ClusterDecision.UNCERTAIN,
                reason="ambiguous_candidates",
                score=best.signals.score,
            )
        return ClusterSelection(
            decision=decision,
            story_id=best.story_id,
            reason="clear_match",
            score=best.signals.score,
        )
    if all(
        decide_cluster(row.signals, policy.calibration) == ClusterDecision.CREATE_NEW
        for row in ranked
    ):
        return ClusterSelection(
            decision=ClusterDecision.CREATE_NEW,
            reason="below_new_threshold",
            score=best.signals.score,
        )
    # Leave uncertain evidence unmerged for later review.
    return ClusterSelection(
        decision=ClusterDecision.UNCERTAIN,
        reason="insufficient_signal",
        score=best.signals.score,
    )
