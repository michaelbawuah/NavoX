from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from navox.intelligence.contracts import SourceDocument

OPERATIONAL_EXTRACTION_SCHEMA_VERSION: Literal["operational-extraction.v1"] = (
    "operational-extraction.v1"
)

ObservationType = Literal[
    "request", "promise", "deadline", "meeting", "follow_up", "task", "completion", "waiting"
]
EvidenceSource = Literal["subject", "content"]
TemporalKind = Literal["deadline", "meeting_start", "follow_up", "event_time", "other"]

INSTRUCTION_LIKE_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "system message",
    "developer message",
    "reveal your credentials",
    "disclose credentials",
    "exfiltrate",
    "grant permission",
    "approve this action",
    "ignore prior instructions",
    "disregard previous instructions",
    "override the system",
    "bypass approval",
    "bypass permissions",
    "disable safety",
    "system prompt",
)


def _reject_instruction_like(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKC", value)
            if unicodedata.category(char) != "Cf"
        )
        .casefold()
        .split()
    )
    if any(marker in normalized for marker in INSTRUCTION_LIKE_MARKERS):
        raise ValueError("Instruction-like content cannot become an operational fact")
    return value


class EvidenceSpan(BaseModel):
    """A bounded exact span in the source subject or content."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    source: EvidenceSource
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_non_empty_range(self) -> EvidenceSpan:
        if self.end_char <= self.start_char:
            raise ValueError("Evidence end_char must be greater than start_char")
        return self


class OperationalObservationCandidate(BaseModel):
    """Action-free operational fact proposed by a model."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    observation_type: ObservationType
    subject_text: str | None = Field(default=None, max_length=256)
    action_text: str | None = Field(default=None, max_length=512)
    object_text: str | None = Field(default=None, max_length=1_000)
    temporal_expression: str | None = Field(default=None, max_length=256)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)

    @field_validator("subject_text", "action_text", "object_text", "temporal_expression")
    @classmethod
    def reject_instruction_like_fact_text(cls, value: str | None) -> str | None:
        return _reject_instruction_like(value)


class PersonMention(BaseModel):
    """Unresolved person mention preserved for deterministic-first M3 resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    identity_type: str | None = Field(default=None, max_length=64)
    identity_value: str | None = Field(default=None, max_length=512)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def require_identity_pair(self) -> PersonMention:
        if (self.identity_type is None) is not (self.identity_value is None):
            raise ValueError("identity_type and identity_value must be supplied together")
        return self


class TemporalMention(BaseModel):
    """Unresolved temporal expression; M2 must not invent a resolved timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    expression: str = Field(min_length=1, max_length=256)
    kind: TemporalKind
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)


class RelationshipCandidate(BaseModel):
    """Unresolved relationship between two source-grounded entities."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    relationship_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    subject_text: str = Field(min_length=1, max_length=256)
    object_text: str = Field(min_length=1, max_length=256)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceSpan] = Field(min_length=1, max_length=8)

    @field_validator("relationship_type", "subject_text", "object_text")
    @classmethod
    def reject_instruction_like_relationship_text(cls, value: str) -> str:
        checked = _reject_instruction_like(value)
        if checked is None:
            raise ValueError("Relationship text cannot be empty")
        return checked


class OperationalExtraction(BaseModel):
    """Strict schema-constrained model output for SPEC-002 M2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["operational-extraction.v1"] = OPERATIONAL_EXTRACTION_SCHEMA_VERSION
    observations: list[OperationalObservationCandidate] = Field(default_factory=list, max_length=64)
    people: list[PersonMention] = Field(default_factory=list, max_length=64)
    temporals: list[TemporalMention] = Field(default_factory=list, max_length=64)
    relationships: list[RelationshipCandidate] = Field(default_factory=list, max_length=64)

    def validate_evidence(self, document: SourceDocument) -> None:
        """Reject hallucinated, stale, or out-of-bounds evidence before persistence."""

        for span in self._all_evidence():
            source_text = document.subject if span.source == "subject" else document.content
            if source_text is None:
                raise ValueError(f"Evidence references missing {span.source}")
            if span.end_char > len(source_text):
                raise ValueError("Evidence span exceeds source bounds")
            if source_text[span.start_char : span.end_char] != span.text:
                raise ValueError("Evidence span does not exactly match the source")
            # A benign paraphrase must not launder an instruction-bearing source span.
            # This is a conservative quality filter; authority is independently blocked
            # by the output schema and downstream permission/approval boundaries.
            _reject_instruction_like(span.text)

        identities = [*document.recipients]
        if document.author is not None:
            identities.append(document.author)
        for person in self.people:
            cited = " ".join(span.text for span in person.evidence).casefold()
            if person.name.casefold() not in cited:
                raise ValueError("Person name is not grounded in its evidence")
            if person.identity_value is not None:
                exact_header_identity = any(
                    identity.identity_type == person.identity_type
                    and identity.identity_value.casefold() == person.identity_value.casefold()
                    and identity.display_name is not None
                    and identity.display_name.casefold() == person.name.casefold()
                    for identity in identities
                )
                if person.identity_value.casefold() not in cited and not exact_header_identity:
                    raise ValueError("Person identity is not grounded in the source")
        for temporal in self.temporals:
            _require_cited(temporal.expression, temporal.evidence, "Temporal expression")
        for observation in self.observations:
            if observation.temporal_expression is not None:
                _require_cited(
                    observation.temporal_expression, observation.evidence, "Temporal expression"
                )
            if observation.object_text:
                _require_cited(observation.object_text, observation.evidence, "Observation object")
        for relationship in self.relationships:
            _require_cited(relationship.subject_text, relationship.evidence, "Relationship subject")
            _require_cited(relationship.object_text, relationship.evidence, "Relationship object")

    def _all_evidence(self) -> list[EvidenceSpan]:
        spans: list[EvidenceSpan] = []
        for observation in self.observations:
            spans.extend(observation.evidence)
        for person in self.people:
            spans.extend(person.evidence)
        for temporal in self.temporals:
            spans.extend(temporal.evidence)
        for relationship in self.relationships:
            spans.extend(relationship.evidence)
        return spans


def _require_cited(value: str, evidence: list[EvidenceSpan], label: str) -> None:
    normalized = " ".join(value.casefold().split())
    if not any(normalized in " ".join(span.text.casefold().split()) for span in evidence):
        raise ValueError(f"{label} is not grounded in its evidence")


@dataclass(frozen=True)
class ModelExtractionResponse:
    """Provider metadata plus untrusted structured model output."""

    output: Mapping[str, Any]
    provider: str
    model: str


class OperationalExtractionGateway(Protocol):
    """Narrow provider-neutral AI gateway surface used by operational extraction."""

    async def extract_operational(self, document: SourceDocument) -> ModelExtractionResponse: ...


@dataclass(frozen=True)
class OperationalExtractionResult:
    extraction: OperationalExtraction
    extractor_version: str
    model_provider: str
    model_name: str
    source_hash: str


class OperationalExtractor:
    """Validates AI proposals without granting them authority or database access."""

    def __init__(
        self,
        gateway: OperationalExtractionGateway,
        *,
        extractor_version: str = "operational-extraction.v1",
    ) -> None:
        self.gateway = gateway
        self.extractor_version = extractor_version

    async def extract(self, document: SourceDocument) -> OperationalExtractionResult:
        response = await self.gateway.extract_operational(document)
        extraction = OperationalExtraction.model_validate(response.output)
        extraction.validate_evidence(document)
        return OperationalExtractionResult(
            extraction=extraction,
            extractor_version=self.extractor_version,
            model_provider=response.provider,
            model_name=response.model,
            source_hash=source_document_hash(document),
        )


def source_document_hash(document: SourceDocument) -> str:
    """Stable hash for bounded provenance without persisting the complete source body."""

    canonical = json.dumps(
        document.model_dump(mode="json", exclude={"id", "retrieved_at"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
