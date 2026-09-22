from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CommitmentType = Literal["deadline", "meeting", "follow_up", "promise", "renewal", "task"]
RelationType = Literal["blocks", "depends_on", "related_to"]

INSTRUCTION_LIKE_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "system message",
    "developer message",
    "reveal your credentials",
    "disclose credentials",
    "exfiltrate",
)


class ExtractedCommitmentCandidate(BaseModel):
    """Strict, action-free model output for one operational commitment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: CommitmentType
    title: str = Field(min_length=3, max_length=256)
    description: str | None = Field(default=None, max_length=2_000)
    priority: int = Field(default=3, ge=1, le=5)
    due_at: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("title", "description")
    @classmethod
    def reject_instruction_like_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if any(marker in normalized for marker in INSTRUCTION_LIKE_MARKERS):
            raise ValueError("Instruction-like content cannot become a commitment")
        return value

    @field_validator("due_at")
    @classmethod
    def require_timezone_aware_due_dates(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Commitment due_at must include a timezone")
        return value.astimezone(UTC) if value is not None else None


class ExtractedCommitmentRelation(BaseModel):
    """A relationship between two candidate positions in one extraction result."""

    model_config = ConfigDict(extra="forbid")

    from_index: int = Field(ge=0)
    to_index: int = Field(ge=0)
    relation_type: RelationType

    @model_validator(mode="after")
    def reject_self_relation(self) -> "ExtractedCommitmentRelation":
        if self.from_index == self.to_index:
            raise ValueError("A commitment cannot relate to itself")
        return self


class CommitmentExtraction(BaseModel):
    """Validated, bounded model output accepted by the Commitment Engine."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[ExtractedCommitmentCandidate] = Field(min_length=1, max_length=32)
    relations: list[ExtractedCommitmentRelation] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_relation_indexes(self) -> "CommitmentExtraction":
        candidate_count = len(self.candidates)
        for relation in self.relations:
            if relation.from_index >= candidate_count or relation.to_index >= candidate_count:
                raise ValueError("Commitment relation refers to a missing candidate")
        return self
