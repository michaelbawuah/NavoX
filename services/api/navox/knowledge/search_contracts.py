"""Provider-independent public search, evidence and coverage contracts (SPEC-007).

Nothing here confers authorization. Scope is bound to the authenticated
workspace/user internally and is never accepted from a client payload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from navox.knowledge.conflict_contracts import KnowledgeConflict
from navox.knowledge.contracts import Contract, ResourceType, aware_utc

MAX_QUERY_LENGTH = 2_000
MAX_RESULT_LIMIT = 50
EVIDENCE_BUNDLE_SCHEMA_VERSION: Literal["evidence-bundle.v1"] = "evidence-bundle.v1"

# Non-sensitive coverage codes for the optional paid semantic path. They say
# whether an exact-namespace vector ranking contributed, never how or where.
SEMANTIC_REASON_UNAVAILABLE = "SEMANTIC_UNAVAILABLE"
SEMANTIC_REASON_NAMESPACE = "SEMANTIC_NAMESPACE_UNAVAILABLE"
SEMANTIC_REASON_NO_VECTORS = "SEMANTIC_NO_VECTORS"
SEMANTIC_REASON_OUTDATED = "SEMANTIC_OUTDATED_VECTORS"
SEMANTIC_REASON_MISSING = "SEMANTIC_MISSING_VECTORS"
SEMANTIC_REASON_PARTIAL = "SEMANTIC_PARTIAL_COVERAGE"
SEMANTIC_REASON_VECTOR_INVALID = "SEMANTIC_VECTOR_INVALID"


class SearchMode(StrEnum):
    AUTO = "AUTO"
    SEARCH = "SEARCH"
    ASK = "ASK"


class RetrievalIntent(StrEnum):
    FIND_RESOURCE = "FIND_RESOURCE"
    QUESTION_ANSWERING = "QUESTION_ANSWERING"
    ENTITY_LOOKUP = "ENTITY_LOOKUP"
    TIMELINE = "TIMELINE"
    RELATIONSHIP = "RELATIONSHIP"
    OPERATIONAL_STATE = "OPERATIONAL_STATE"
    AGGREGATION = "AGGREGATION"


class RetrieverMode(StrEnum):
    FULLTEXT = "FULLTEXT"
    STRUCTURED = "STRUCTURED"
    SEMANTIC = "SEMANTIC"
    GRAPH = "GRAPH"


class Freshness(StrEnum):
    CACHED = "CACHED"
    FRESH = "FRESH"
    LIVE_IF_NEEDED = "LIVE_IF_NEEDED"


class AnswerState(StrEnum):
    """Honest answer availability. This phase has no qualified provider."""

    NOT_REQUESTED = "NOT_REQUESTED"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPLETE = "INCOMPLETE"


class ExclusionScope(StrEnum):
    SOURCE = "SOURCE"
    FOLDER = "FOLDER"
    RESOURCE = "RESOURCE"
    TYPE = "TYPE"


class DateRange(Contract):
    """Explicit aware window; at least one bound is required."""

    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def validate_bounds(cls, value: datetime | None) -> datetime | None:
        return None if value is None else aware_utc(value)

    @model_validator(mode="after")
    def validate_window(self) -> DateRange:
        if self.start is None and self.end is None:
            raise ValueError("A date range needs a start or an end")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("A date range must be non-empty")
        return self


class SearchRequest(Contract):
    """One bounded search request. Identifiers never imply grants."""

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    mode: SearchMode = SearchMode.AUTO
    sources: tuple[UUID, ...] = ()
    types: tuple[ResourceType, ...] = ()
    date_range: DateRange | None = None
    limit: int = Field(default=20, ge=1, le=MAX_RESULT_LIMIT)
    offset: int = Field(default=0, ge=0, le=10_000)
    session_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("A query is required")
        return trimmed


class SemanticKey(Contract):
    """The exact vector namespace. Other namespaces are never compared."""

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    registry_revision: str = Field(min_length=1, max_length=64)
    embedding_version: str = Field(min_length=1, max_length=64)
    dimension: int = Field(ge=1, le=4096)

    @property
    def label(self) -> str:
        """Non-sensitive diagnostic label; no provider credential or body text."""
        return (
            f"{self.provider}:{self.model}:{self.registry_revision}:"
            f"{self.embedding_version}:{self.dimension}"
        )


class SemanticSearchRequest(Contract):
    """One explicit paid semantic request; the identifier reserves one attempt."""

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    request_id: UUID
    sources: tuple[UUID, ...] = ()
    types: tuple[ResourceType, ...] = ()
    date_range: DateRange | None = None
    limit: int = Field(default=20, ge=1, le=MAX_RESULT_LIMIT)
    offset: int = Field(default=0, ge=0, le=10_000)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("A query is required")
        return trimmed

    def as_search_request(self) -> SearchRequest:
        """The same filters as an ordinary search; semantic adds no authority."""
        return SearchRequest(
            query=self.query,
            mode=SearchMode.SEARCH,
            sources=self.sources,
            types=self.types,
            date_range=self.date_range,
            limit=self.limit,
            offset=self.offset,
        )


class RetrievalPlan(Contract):
    """Internal, deterministic, reportable plan for one request."""

    intent: RetrievalIntent
    mode: SearchMode
    retrievers: tuple[RetrieverMode, ...]
    freshness: Freshness
    terms: tuple[str, ...]
    source_ids: tuple[UUID, ...]
    types: tuple[ResourceType, ...]
    date_range: DateRange | None = None
    rationale: str


class EvidenceExcerpt(Contract):
    """An exact stored span, never a synthesized sentence."""

    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    chunk_index: int | None = None
    section_title: str | None = None

    @model_validator(mode="after")
    def validate_span(self) -> EvidenceExcerpt:
        if self.end < self.start:
            raise ValueError("An excerpt span must be ordered")
        return self


class EvidenceResource(Contract):
    """One typed evidence reference: connected resource or native domain record."""

    source_type: str = Field(min_length=1, max_length=32)
    resource_id: UUID
    title: str | None = Field(default=None, max_length=500)
    excerpts: tuple[EvidenceExcerpt, ...] = ()
    canonical_url: str | None = Field(default=None, max_length=2048)
    source_updated_at: datetime | None = None
    source_version: str | None = Field(default=None, max_length=256)
    fresh_until: datetime | None = None
    indexed_at: datetime | None = None
    provenance: dict[str, str] = Field(default_factory=dict)
    origin: Literal["CONNECTED", "NATIVE"] = "CONNECTED"

    @property
    def key(self) -> str:
        """Unique in (source_type, resource_id), so native and connected cannot alias."""
        return f"{self.source_type}:{self.resource_id}"


class StructuredFact(Contract):
    """A typed domain fact with its own provenance and authority label."""

    fact_id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(max_length=512)
    source_type: str = Field(min_length=1, max_length=32)
    resource_id: UUID
    source_updated_at: datetime | None = None
    authority: str = Field(min_length=1, max_length=64)


class RelationshipView(Contract):
    """Source-cited relationship between currently permitted evidence resources."""

    from_key: str = Field(min_length=1, max_length=160)
    to_key: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=64)
    evidence_keys: tuple[str, ...] = ()


class SourceIssue(Contract):
    connection_id: UUID
    source_label: str
    state: Literal["DEGRADED", "AUTH_EXPIRED", "RATE_LIMITED", "SYNC_FAILED"]


class Coverage(Contract):
    """Truthful bounds, computed only over rows the caller may currently view.

    ``examined`` counts authorized candidates, never private matches, so a
    count can never become an existence oracle. ``partial_reasons`` carries
    non-sensitive reason codes for coverage this phase cannot serve.
    """

    source_issues: tuple[SourceIssue, ...] = ()
    candidate_bound: int = Field(ge=0)
    examined: int = Field(ge=0)
    returned: int = Field(ge=0)
    truncated: bool
    stale_dropped: int = Field(default=0, ge=0)
    exclusions_applied: int = Field(default=0, ge=0)
    not_searchable: int = Field(default=0, ge=0)
    partial_reasons: tuple[str, ...] = ()


class EvidenceBundleV1(Contract):
    """Minimized, provider-independent evidence bundle consumable by SPEC-008."""

    schema_version: Literal["evidence-bundle.v1"] = EVIDENCE_BUNDLE_SCHEMA_VERSION
    query: str
    resources: tuple[EvidenceResource, ...] = ()
    structured_facts: tuple[StructuredFact, ...] = ()
    relationships: tuple[RelationshipView, ...] = ()
    conflicts: tuple[KnowledgeConflict, ...] = ()
    freshness_summary: dict[str, int] = Field(default_factory=dict)
    permission_snapshot_id: str = Field(min_length=1, max_length=64)
    retrieval_trace_id: str = Field(min_length=1, max_length=64)


class SearchResponse(Contract):
    interpreted_mode: SearchMode
    intent: RetrievalIntent
    results: tuple[EvidenceResource, ...] = ()
    structured_facts: tuple[StructuredFact, ...] = ()
    relationships: tuple[RelationshipView, ...] = ()
    conflicts: tuple[KnowledgeConflict, ...] = ()
    answer: str | None = None
    answer_state: AnswerState = AnswerState.NOT_REQUESTED
    suggested_followups: tuple[str, ...] = ()
    trace_id: str = Field(min_length=1, max_length=64)
    session_id: UUID | None = None
    coverage: Coverage
    unavailable_modes: tuple[RetrieverMode, ...] = ()
    exclusion_count: int = Field(default=0, ge=0)
    refresh: SearchRefresh | None = None


class SearchRefresh(Contract):
    """Bounded connected-source refresh, never a claim that returned text is fresh."""

    state: Literal["NOT_NEEDED", "REFRESHING", "UNAVAILABLE", "PARTIAL"]
    queued: int = Field(default=0, ge=0, le=3)
    unavailable: int = Field(default=0, ge=0, le=3)
    bounded: bool = False


class LiveSearchRequest(Contract):
    request_id: UUID
    search: SearchRequest


class RecentSearchView(Contract):
    id: UUID
    query: str
    mode: SearchMode
    result_count: int
    created_at: datetime


class ExclusionView(Contract):
    id: UUID
    scope: ExclusionScope
    source_connection_id: UUID | None = None
    external_id: str | None = None
    resource_id: UUID | None = None
    resource_type: str | None = None
    created_at: datetime


class ExclusionCreate(Contract):
    scope: ExclusionScope
    source_connection_id: UUID | None = None
    external_id: str | None = Field(default=None, max_length=512)
    resource_id: UUID | None = None
    resource_type: ResourceType | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ExclusionCreate:
        """Reject mixed or incomplete targets before any write is attempted."""
        if self.scope is ExclusionScope.SOURCE:
            valid = (
                self.source_connection_id is not None
                and self.external_id is None
                and self.resource_id is None
                and self.resource_type is None
            )
            requirement = "a connection"
        elif self.scope is ExclusionScope.FOLDER:
            valid = (
                self.source_connection_id is not None
                and bool(self.external_id)
                and self.resource_id is None
                and self.resource_type is None
            )
            requirement = "a connection and a folder identity"
        elif self.scope is ExclusionScope.RESOURCE:
            valid = (
                self.resource_id is not None
                and self.source_connection_id is None
                and self.external_id is None
                and self.resource_type is None
            )
            requirement = "a connected resource"
        else:
            valid = (
                self.resource_type is not None
                and self.source_connection_id is None
                and self.external_id is None
                and self.resource_id is None
            )
            requirement = "a type"
        if not valid:
            raise ValueError(f"A {self.scope.value} exclusion requires exactly {requirement}")
        return self


class ResourceDetailChunk(Contract):
    chunk_index: int = Field(ge=0)
    section_title: str | None = None
    page_number: int | None = None
    text_content: str | None = None
    token_count: int = Field(ge=0)


class ResourceDetail(Contract):
    resource_id: UUID
    source_type: ResourceType
    title: str | None = None
    canonical_url: str | None = None
    sensitivity: str
    source_updated_at: datetime | None = None
    source_version: str | None = None
    fresh_until: datetime | None = None
    indexed_at: datetime | None = None
    index_state: str | None = None
    structured_kind: str | None = None
    structured_at: datetime | None = None
    chunks: tuple[ResourceDetailChunk, ...] = ()
    provenance: dict[str, str] = Field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(UTC)
