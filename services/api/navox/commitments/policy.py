from dataclasses import dataclass

from navox.commitments.schema import ExtractedCommitmentCandidate


@dataclass(frozen=True)
class CommitmentConfidencePolicy:
    """Deterministic policy for whether a validated candidate becomes operational state."""

    moderate_threshold: float
    high_threshold: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.moderate_threshold < self.high_threshold <= 1.0:
            raise ValueError("Commitment confidence thresholds must be ordered within [0, 1]")

    def status_for(self, candidate: ExtractedCommitmentCandidate) -> str | None:
        if candidate.confidence >= self.high_threshold:
            return "confirmed"
        if candidate.confidence >= self.moderate_threshold:
            return "candidate"
        return None
