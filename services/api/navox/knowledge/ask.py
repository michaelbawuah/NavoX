"""Grounded Ask: selection-only answers over currently authorized evidence.

The provider never sees an unrestricted corpus and never writes prose. It
receives a minimized, freshly rebuilt set of authorized excerpts and structured
facts, and may only select those indices back. Rendering is server-side and
extractive, from the exact stored spans re-read at that moment.

Bindings are strong: every candidate carries the application-validated canonical
content hash, connection/resource identity, classification, title digest and
exact excerpt locators (chunk index, span, text digest). Every context build and
every response path rebuilds current evidence in a fresh session at the real
current time and refuses anything that changed, so no cached private text can
reach the provider or the caller.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied, MinimizedContext, reject_credentials
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    JSONDocument,
    LatencyClass,
    Profile,
    ProviderGrant,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
)
from navox.ai.routing import rank_eligible
from navox.ai.runtime import GatewayRuntime, GatewayUnavailable
from navox.core.settings import Settings
from navox.db.ai_registry import AITaskRun
from navox.db.knowledge import (
    KnowledgeAnswerRequest,
    KnowledgeChunk,
    KnowledgeResource,
    KnowledgeSession,
    KnowledgeTurn,
)
from navox.db.models import User, WorkspaceMembership
from navox.knowledge.ask_contracts import (
    ANSWER_PROMPT,
    ASK_REASON_INCOMPLETE,
    ASK_REASON_INSUFFICIENT,
    ASK_REASON_INVALID_SELECTION,
    ASK_REASON_NATIVE_EXCLUDED,
    ASK_REASON_UNAVAILABLE,
    ASK_REASON_WITHHELD,
    AnswerCitation,
    AnswerSelection,
    AnswerState,
    AskCoverage,
    AskRequest,
    AskResponse,
    AskSessionSummary,
    AskSessionView,
    AskStatus,
    AskTurnView,
    KnowledgeAnswer,
    SearchTurnRequest,
    answer_validator,
    aware_utc,
    utc_now,
)
from navox.knowledge.contracts import ResourceType, stored_utc
from navox.knowledge.exclusions import ExclusionFilters, load_exclusions
from navox.knowledge.permissions import RESOURCE_AUTHORITY_COLUMNS
from navox.knowledge.planner import interpret
from navox.knowledge.retrieval import (
    _candidate_rows,
    connected_facts,
    hash_fence,
    publication_check,
)
from navox.knowledge.search_contracts import (
    EvidenceExcerpt,
    EvidenceResource,
    SearchMode,
    SearchRequest,
    SourceIssue,
    StructuredFact,
)
from navox.knowledge.service import _require_current_account, search_knowledge

ASK_MAX_COST = Decimal("0.05")
ASK_COST_MICROS = 50_000
ASK_QUOTA_WINDOW = timedelta(hours=1)
ASK_RETENTION = timedelta(days=30)
ASK_MAX_OUTPUT_TOKENS = 700
MAX_SESSIONS = 30
MAX_TURNS_PER_SESSION = 40
ASK_RANKING_VERSION = "knowledge-ask.v1"
_REFERENT = re.compile(r"#(\d{1,3})")

# Application-owned, non-sensitive answer notes. No model text is ever stored.
ASK_NOTE_INSUFFICIENT = "The authorized sources do not quote this question."
ASK_NOTE_INCOMPLETE = "Part of the selected evidence was unusable and discarded."
ASK_NOTE_UNAVAILABLE = "No qualified answer model was available."
ASK_NOTE_WITHHELD = "A selected source changed or was revoked."
ASK_NOTE_INVALID = "The model selection did not match the supplied evidence."


class AskDisabled(ValueError):
    """Raised when the paid path is reached while its flag is off."""


class AskRejected(ValueError):
    """Raised when a request is refused before any reservation exists."""


class AskQuotaExceeded(ValueError):
    """Raised when the caller's hourly paid-question quota is spent."""


class AskRequestReplay(ValueError):
    """Raised when a request identifier was already reserved; nothing is bought."""

    def __init__(self, status: str, turn: KnowledgeTurn | None = None) -> None:
        self.status, self.turn = status, turn
        super().__init__(f"Ask request {status.lower()}")


class AskUnavailable(ValueError):
    """Raised when the requesting account cannot use Ask."""


class ReferentInvalid(ValueError):
    """A follow-up referent that is not a valid, owned, completed turn."""


class ReferentStale(ReferentInvalid):
    """A follow-up referent whose displayed evidence is no longer current."""


class TurnVanished(RuntimeError):
    """The owned session or turn was cleared while a provider call was running."""


@dataclass(frozen=True)
class ExcerptLocator:
    """Exact stored span: chunk identity, offsets and a text digest."""

    chunk_index: int | None
    start: int
    end: int
    digest: str
    text: str


@dataclass(frozen=True)
class AskCandidate:
    """One selectable, currently authorized resource with strong bindings."""

    resource_id: UUID
    source_type: str
    title: str | None
    title_digest: str | None
    canonical_url: str | None
    source_version: str | None
    source_updated_at: datetime | None
    sensitivity: str
    content_hash: str
    connection_id: UUID | None
    external_resource_id: str | None
    excerpts: tuple[ExcerptLocator, ...]
    facts: tuple[StructuredFact, ...]
    rank: int
    parent_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source_type}:{self.resource_id}"

    def binding(self) -> tuple[object, ...]:
        """Everything that must still hold for this candidate to be usable."""
        return (
            self.key,
            self.content_hash,
            str(self.connection_id) if self.connection_id else None,
            self.external_resource_id,
            self.sensitivity,
            self.title_digest,
            self.source_version,
            _digest(self.canonical_url),
            self.source_updated_at.isoformat() if self.source_updated_at else None,
            self.parent_id,
            tuple((item.chunk_index, item.start, item.end, item.digest) for item in self.excerpts),
            tuple((fact.fact_id, _digest(fact.value)) for fact in self.facts),
        )


@dataclass(frozen=True)
class BoundEvidence:
    """One fenced resource with its current canonical binding."""

    resource: EvidenceResource
    content_hash: str
    connection_id: UUID | None
    external_resource_id: str | None
    facts: tuple[StructuredFact, ...]
    parent_id: str | None = None


@dataclass(frozen=True)
class AskGatewayRequest:
    workspace_id: UUID
    user_id: UUID
    question: str
    sensitivity: Sensitivity
    candidates: tuple[AskCandidate, ...]


@dataclass(frozen=True)
class AskGatewayOutput:
    selection: KnowledgeAnswer
    provider: str
    model: str
    registry_revision: str
    prompt_version: str
    cost_micros: int | None


class AskGateway(Protocol):
    """Provider-neutral paid Ask surface. The gateway alone selects a model."""

    async def eligible(
        self, *, workspace_id: UUID, user_id: UUID, sensitivity: Sensitivity
    ) -> bool: ...

    async def answer(self, request: AskGatewayRequest) -> AskGatewayOutput | None: ...


def _digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()


def _jsonable(value: object) -> object:
    """Tuples become lists so a binding compares exactly after JSON storage."""
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _micros(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * Decimal(1_000_000)).to_integral_value())


def _task(request: AskGatewayRequest, policy: ProviderPolicy) -> AITask:
    return AITask(
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        task_type=TaskType.REASON,
        profile=Profile.ASSISTANT_INTERACTIVE,
        capability_requirements=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
        output_schema=ANSWER_PROMPT,
        prompt=ANSWER_PROMPT,
        sensitivity=request.sensitivity,
        latency_class=LatencyClass.INTERACTIVE,
        quality_class=QualityClass.STANDARD,
        max_cost=ASK_MAX_COST,
        max_output_tokens=ASK_MAX_OUTPUT_TOKENS,
        provider_policy=policy,
    )


def _policy(workspace_id: UUID, user_id: UUID, grants: tuple[ProviderGrant, ...]) -> ProviderPolicy:
    return ProviderPolicy(
        workspace_id=workspace_id,
        user_id=user_id,
        revision=1,
        grants=grants,
        allow_fallback=False,
        max_fallbacks=0,
    )


def _context_payload(request: AskGatewayRequest) -> str:
    """Minimized evidence: stable indices and exact current spans only."""
    sources = [
        {
            "source_id": str(candidate.resource_id),
            "kind": candidate.source_type,
            "title": candidate.title,
            "excerpts": [
                {"index": index, "text": locator.text}
                for index, locator in enumerate(candidate.excerpts)
            ],
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "label": fact.label,
                    "value": fact.value,
                    "authority": fact.authority,
                }
                for fact in candidate.facts
            ],
        }
        for candidate in request.candidates
    ]
    return json.dumps({"question": request.question, "evidence": sources}, ensure_ascii=False)


EXCERPTS_PER_RESOURCE = 3


def _locators(chunks: Sequence[KnowledgeChunk], query: str = "") -> tuple[ExcerptLocator, ...]:
    """Deterministic excerpt order: ascending chunk index, first N non-empty."""
    terms = set(re.findall(r"[\w'-]{3,}", query.casefold()))
    chosen = sorted(
        (chunk for chunk in chunks if (getattr(chunk, "text_content", None) or "").strip()),
        key=lambda chunk: (
            -sum(term in (chunk.text_content or "").casefold() for term in terms),
            chunk.chunk_index,
        ),
    )[:EXCERPTS_PER_RESOURCE]
    locators: list[ExcerptLocator] = []
    for chunk in chosen:
        text = getattr(chunk, "text_content", "") or ""
        locators.append(
            ExcerptLocator(
                chunk_index=getattr(chunk, "chunk_index", None),
                start=0,
                end=len(text),
                digest=_digest(text) or "",
                text=text,
            )
        )
    return tuple(locators)


async def bound_evidence_for_ids(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_ids: list[UUID],
    now: datetime,
    query: str = "",
) -> list[BoundEvidence]:
    """Current authorized evidence for explicit ids, fenced before and after reads.

    Authority, exclusions, revision fence and index state run before any stored
    text is read; the same checks run again after the text read so a mid-read
    move, revocation or reclassification publishes nothing.
    """
    if not resource_ids:
        return []
    plan = interpret(SearchRequest(query="evidence", mode=SearchMode.SEARCH))
    candidates, _truncated = await _candidate_rows(
        database,
        workspace_id=workspace_id,
        plan=plan,
        filters=await _filters(database, workspace_id=workspace_id, user_id=user_id),
        eligible_ids=list(dict.fromkeys(resource_ids)),
        structured_only=False,
        limit=max(len(resource_ids), 1),
        match_terms=False,
    )
    permitted, _stale = await hash_fence(database, workspace_id=workspace_id, candidates=candidates)
    searchable = [item for item in permitted if item.index_state == "INDEXED"]
    if not searchable:
        return []
    identifiers = [item.knowledge_resource_id for item in searchable]
    rows = await _metadata_rows(database, identifiers)
    metadata_before = {key: _metadata_binding(row) for key, row in rows.items()}
    chunks = await _chunk_rows(database, identifiers)
    locators_before = {key: _locators(value, query) for key, value in chunks.items()}
    # Post-read fence: the exact same decision must still hold, and the exact
    # metadata/chunk payload must be identical before and after the text read.
    filters = await _filters(database, workspace_id=workspace_id, user_id=user_id)
    rows_after = await _metadata_rows(database, identifiers)
    chunks_after = await _chunk_rows(database, identifiers)
    current_candidates, _ = await _candidate_rows(
        database,
        workspace_id=workspace_id,
        plan=plan,
        filters=filters,
        eligible_ids=list(dict.fromkeys(resource_ids)),
        structured_only=False,
        limit=max(len(resource_ids), 1),
        match_terms=False,
    )
    current_by_id = {item.knowledge_resource_id: item for item in current_candidates}
    kept, _dropped = await publication_check(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
        candidates={item.key: item for item in searchable},
        filters=filters,
    )
    bound: list[BoundEvidence] = []
    for candidate in searchable:
        if (
            candidate.key not in kept
            or current_by_id.get(candidate.knowledge_resource_id) != candidate
        ):
            continue
        row = rows.get(candidate.knowledge_resource_id)
        row_after = rows_after.get(candidate.knowledge_resource_id)
        if (
            row is None
            or row_after is None
            or candidate.indexed_content_hash is None
            or metadata_before.get(candidate.knowledge_resource_id) != _metadata_binding(row_after)
        ):
            continue
        locators = locators_before.get(row.id, ())
        fresh_locators = _locators(chunks_after.get(row.id, []), query)
        if (not locators and not connected_facts(candidate, row)) or _digests(locators) != _digests(
            fresh_locators
        ):
            continue
        resource = EvidenceResource(
            source_type=candidate.source_type,
            resource_id=row.id,
            title=row.title,
            excerpts=tuple(_excerpt(locator) for locator in locators),
            canonical_url=row.canonical_url,
            source_updated_at=row.source_updated_at,
            source_version=row.source_version,
            provenance={
                "connection_id": str(candidate.source_connection_id),
                "external_resource_id": candidate.external_resource_id,
                "sensitivity": row.sensitivity,
            },
            origin="CONNECTED",
        )
        bound.append(
            BoundEvidence(
                resource=resource,
                content_hash=candidate.indexed_content_hash,
                connection_id=candidate.source_connection_id,
                external_resource_id=candidate.external_resource_id,
                facts=tuple(connected_facts(candidate, row)),
                parent_id=candidate.external_parent_id,
            )
        )
    return bound


def _excerpt(locator: ExcerptLocator) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        text=locator.text,
        start=locator.start,
        end=locator.end,
        chunk_index=locator.chunk_index,
    )


async def _metadata_rows(
    database: AsyncSession, identifiers: list[UUID]
) -> dict[UUID, KnowledgeResource]:
    """Fresh metadata-only projection; stored bodies are never loaded here."""
    if not identifiers:
        return {}
    rows = await database.scalars(
        select(KnowledgeResource)
        .options(
            load_only(
                *RESOURCE_AUTHORITY_COLUMNS,
                KnowledgeResource.title,
                KnowledgeResource.sensitivity,
                KnowledgeResource.canonical_url,
                KnowledgeResource.source_version,
                KnowledgeResource.source_updated_at,
            )
        )
        .where(KnowledgeResource.id.in_(identifiers))
        .execution_options(populate_existing=True)
    )
    return {row.id: row for row in rows}


async def _chunk_rows(
    database: AsyncSession, identifiers: list[UUID]
) -> dict[UUID, list[KnowledgeChunk]]:
    if not identifiers:
        return {}
    rows = await database.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.resource_id.in_(identifiers))
        .order_by(KnowledgeChunk.resource_id, KnowledgeChunk.chunk_index)
        .execution_options(populate_existing=True)
    )
    grouped: dict[UUID, list[KnowledgeChunk]] = {}
    for row in rows:
        grouped.setdefault(row.resource_id, []).append(row)
    return grouped


def _metadata_binding(row: KnowledgeResource) -> tuple[object, ...]:
    fields = (
        "title",
        "sensitivity",
        "canonical_url",
        "source_version",
        "source_updated_at",
        "deleted_at",
        "source_connection_id",
        "source_resource_id",
        "source_type",
        "source_read_capability",
    )
    return tuple(getattr(row, name) for name in fields)


def _digests(locators: tuple[ExcerptLocator, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (locator.chunk_index, locator.start, locator.end, locator.digest) for locator in locators
    )


async def _filters(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> ExclusionFilters:
    return await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)


async def evidence_for_ids(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_ids: list[UUID],
    now: datetime,
) -> list[EvidenceResource]:
    bound = await bound_evidence_for_ids(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_ids=resource_ids,
        now=now,
    )
    return [item.resource for item in bound]


def candidates_from(bound: list[BoundEvidence]) -> tuple[list[AskCandidate], list[str]]:
    """Selectable candidates. Native domains never enter an answer context."""
    reasons: list[str] = []
    if any(item.resource.origin == "NATIVE" for item in bound):
        reasons.append(ASK_REASON_NATIVE_EXCLUDED)
    candidates: list[AskCandidate] = []
    for rank, item in enumerate(bound):
        resource = item.resource
        if resource.origin != "CONNECTED":
            continue
        locators = tuple(
            ExcerptLocator(
                chunk_index=excerpt.chunk_index,
                start=excerpt.start,
                end=excerpt.end,
                digest=_digest(excerpt.text) or "",
                text=excerpt.text,
            )
            for excerpt in resource.excerpts
        )
        if not locators and not item.facts:
            continue
        candidates.append(
            AskCandidate(
                resource_id=resource.resource_id,
                source_type=resource.source_type,
                title=resource.title,
                title_digest=_digest(resource.title),
                canonical_url=resource.canonical_url,
                source_version=resource.source_version,
                source_updated_at=resource.source_updated_at,
                sensitivity=resource.provenance.get("sensitivity", "PERSONAL"),
                content_hash=item.content_hash,
                connection_id=item.connection_id,
                external_resource_id=item.external_resource_id,
                excerpts=locators,
                facts=item.facts,
                rank=rank,
                parent_id=item.parent_id,
            )
        )
    return candidates, reasons


def max_sensitivity(candidates: list[AskCandidate]) -> Sensitivity:
    """PERSONAL floor for the question; evidence can only raise the task level."""
    highest = Sensitivity.PERSONAL
    for candidate in candidates:
        try:
            value = Sensitivity(candidate.sensitivity)
        except ValueError:
            value = Sensitivity.RESTRICTED
        if value.rank > highest.rank:
            highest = value
    return highest


def build_citations(
    selection: KnowledgeAnswer, *, candidates: list[AskCandidate]
) -> tuple[AnswerCitation, ...]:
    """Render validated selections from the freshly rebuilt candidates only."""
    by_id = {candidate.resource_id: candidate for candidate in candidates}
    citations: list[AnswerCitation] = []
    seen_resources: set[UUID] = set()
    seen_facts: set[str] = set()
    for item in selection.selections:
        if item.resource_id in seen_resources:
            raise AskRejected("Duplicate evidence selection")
        seen_resources.add(item.resource_id)
        candidate = by_id.get(item.resource_id)
        if candidate is None:
            raise AskRejected("Selected evidence was not supplied")
        for index in item.excerpt_indices:
            if index >= len(candidate.excerpts):
                raise AskRejected("Selected excerpt index is out of range")
            locator = candidate.excerpts[index]
            citations.append(
                AnswerCitation(
                    resource_id=candidate.resource_id,
                    source_type=ResourceType(candidate.source_type),
                    title=candidate.title,
                    canonical_url=candidate.canonical_url,
                    source_version=candidate.source_version,
                    source_updated_at=candidate.source_updated_at,
                    excerpt_index=index,
                    excerpt_text=locator.text,
                    authority="Source system",
                    sensitivity=candidate.sensitivity,
                    origin="CONNECTED",
                )
            )
        for fact_id in item.fact_ids:
            if fact_id in seen_facts:
                raise AskRejected("Duplicate structured fact selection")
            fact = next(
                (item_fact for item_fact in candidate.facts if item_fact.fact_id == fact_id),
                None,
            )
            if fact is None:
                raise AskRejected("Selected structured fact was not supplied")
            seen_facts.add(fact_id)
            citations.append(
                AnswerCitation(
                    resource_id=candidate.resource_id,
                    source_type=ResourceType(candidate.source_type),
                    title=candidate.title,
                    canonical_url=candidate.canonical_url,
                    source_version=candidate.source_version,
                    source_updated_at=candidate.source_updated_at,
                    fact_id=fact.fact_id,
                    fact_label=fact.label,
                    fact_value=fact.value,
                    authority=fact.authority,
                    sensitivity=candidate.sensitivity,
                    origin="CONNECTED",
                )
            )
    if not citations:
        raise AskRejected("The answer selected no citable evidence")
    return tuple(citations)


class AskContext:
    """Gateway ``AuthorizedContext`` rebuilding evidence on every build."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        request: AskGatewayRequest,
    ) -> None:
        self.factory, self.settings, self.request = factory, settings, request

    async def build(
        self, task: AITask, documents: object, *, user_request: str = ""
    ) -> MinimizedContext:
        request = self.request
        if not self.settings.knowledge_enabled or not self.settings.knowledge_ask_enabled:
            raise ContextDenied("Grounded Ask is not enabled")
        if not (
            task.profile is Profile.ASSISTANT_INTERACTIVE
            and task.task_type is TaskType.REASON
            and task.prompt == ANSWER_PROMPT
            and task.output_schema == ANSWER_PROMPT
            and task.capability_requirements
            == frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT})
        ):
            raise ContextDenied("Ask context rejected a non-ask task")
        if task.context_references or documents or user_request:
            raise ContextDenied("Ask context accepts no documents or references")
        if (task.workspace_id, task.user_id) != (request.workspace_id, request.user_id):
            raise ContextDenied("Ask context scope mismatch")
        if task.sensitivity is not request.sensitivity:
            raise ContextDenied("Ask context sensitivity mismatch")
        async with self.factory() as database:
            await _require_current_account(
                database,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
            )
            bound = await bound_evidence_for_ids(
                database,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                resource_ids=[item.resource_id for item in request.candidates],
                now=utc_now(),
                query=request.question,
            )
        current, _reasons = candidates_from(bound)
        by_id = {item.resource_id: item for item in current}
        for candidate in request.candidates:
            rebuilt = by_id.get(candidate.resource_id)
            if rebuilt is None or rebuilt.binding() != candidate.binding():
                raise ContextDenied("Ask evidence changed before use")
        if max_sensitivity(current) is not request.sensitivity:
            raise ContextDenied("Ask evidence classification changed")
        fresh = AskGatewayRequest(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            question=request.question,
            sensitivity=request.sensitivity,
            candidates=tuple(current),
        )
        payload = _context_payload(fresh)
        reject_credentials(payload, configured_secrets(self.settings))
        return MinimizedContext(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            source_ids=tuple(item.resource_id for item in current),
            sensitivity=task.sensitivity,
            content=JSONDocument(text=payload),
        )


class RegisteredAskGateway:
    """Production Ask path: registered selection, no fallback purchases."""

    def __init__(
        self, settings: Settings, *, factory: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self.settings, self.factory = settings, factory

    async def _runtime(self) -> GatewayRuntime:
        return await build_runtime(self.settings, self.factory)

    async def eligible(
        self, *, workspace_id: UUID, user_id: UUID, sensitivity: Sensitivity
    ) -> bool:
        try:
            runtime = await self._runtime()
        except AIProviderNotConfigured:
            return False
        grants = runtime.store.operator_policy.grants
        request = AskGatewayRequest(
            workspace_id=workspace_id,
            user_id=user_id,
            question="x",
            sensitivity=sensitivity,
            candidates=(),
        )
        task = _task(request, _policy(workspace_id, user_id, grants))
        try:
            snapshot = await runtime.store.snapshot(task)
        except (PermissionError, ValueError, AIProviderNotConfigured):
            return False
        budget = min(task.max_cost, *(rule.max_cost for rule in snapshot.rules))
        return bool(
            rank_eligible(
                task,
                snapshot.registry,
                policy=snapshot.policy,
                available=snapshot.available,
                evaluations=snapshot.evaluations,
                input_bound=4_096,
                remaining_budget=budget,
                preferred=None,
                weights=snapshot.weights,
            )
        )

    async def answer(self, request: AskGatewayRequest) -> AskGatewayOutput | None:
        runtime = await self._runtime()
        task = _task(
            request,
            _policy(request.workspace_id, request.user_id, runtime.store.operator_policy.grants),
        )
        try:
            result = await runtime.execute(
                task,
                context_builder=AskContext(
                    self.factory or _default_factory(), settings=self.settings, request=request
                ),
                documents={},
                semantic_validator=answer_validator,
            )
        except (GatewayUnavailable, ContextDenied):
            return None
        selection = KnowledgeAnswer.model_validate_json(result.output.text)
        revision = await self._registry_revision(task.id)
        return AskGatewayOutput(
            selection=selection,
            provider=result.provider.value,
            model=result.model,
            registry_revision=revision,
            prompt_version=f"{ANSWER_PROMPT.name}@{ANSWER_PROMPT.version}",
            cost_micros=_micros(result.estimated_cost),
        )

    async def _registry_revision(self, task_id: UUID) -> str:
        async with (self.factory or _default_factory())() as database:
            revision = await database.scalar(
                select(AITaskRun.registry_revision)
                .where(AITaskRun.task_id == task_id)
                .order_by(AITaskRun.created_at.desc(), AITaskRun.id.desc())
                .limit(1)
            )
        if revision is None:
            raise AskUnavailable("The gateway trace has no registry revision")
        return str(revision)


def _default_factory() -> async_sessionmaker[AsyncSession]:
    from navox.db.session import get_session_factory

    return get_session_factory()


def registered_gateway(
    settings: Settings, *, factory: async_sessionmaker[AsyncSession] | None = None
) -> AskGateway | None:
    if settings.ai_provider.casefold().strip() != "automatic":
        return None
    return RegisteredAskGateway(settings, factory=factory)


async def find_attempt(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, request_id: UUID
) -> KnowledgeAnswerRequest | None:
    found: KnowledgeAnswerRequest | None = await database.scalar(
        select(KnowledgeAnswerRequest).where(
            KnowledgeAnswerRequest.workspace_id == workspace_id,
            KnowledgeAnswerRequest.user_id == user_id,
            KnowledgeAnswerRequest.request_id == request_id,
        )
    )
    return found


async def reserve_ask(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request_id: UUID,
    quota: int,
    session_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[KnowledgeAnswerRequest, bool]:
    """Reserve one paid question, serialized on the freshly read user row."""
    moment = aware_utc(now) if now is not None else utc_now()
    user = await database.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        user is None
        or user.agent_paused
        or await database.get(WorkspaceMembership, (workspace_id, user_id), populate_existing=True)
        is None
    ):
        raise AskUnavailable("Ask is unavailable for this account")
    existing = await find_attempt(
        database, workspace_id=workspace_id, user_id=user_id, request_id=request_id
    )
    if existing is not None:
        return existing, False
    used = int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeAnswerRequest)
            .where(
                KnowledgeAnswerRequest.user_id == user_id,
                KnowledgeAnswerRequest.created_at >= moment - ASK_QUOTA_WINDOW,
            )
        )
        or 0
    )
    if used >= quota:
        raise AskQuotaExceeded("Hourly Ask quota is exhausted")
    database.add(
        KnowledgeAnswerRequest(
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request_id,
            session_id=session_id,
            status="RESERVED",
            created_at=moment,
        )
    )
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        raced = await find_attempt(
            database, workspace_id=workspace_id, user_id=user_id, request_id=request_id
        )
        if raced is None:
            raise
        return raced, False
    stored = await find_attempt(
        database, workspace_id=workspace_id, user_id=user_id, request_id=request_id
    )
    if stored is None:  # pragma: no cover - just committed
        raise AskUnavailable("The Ask reservation was not stored")
    return stored, True


async def _finish_ask(
    database: AsyncSession,
    row: KnowledgeAnswerRequest,
    *,
    status: str,
    turn: KnowledgeTurn | None = None,
    output: AskGatewayOutput | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> None:
    row.status = status
    row.reason = reason
    if turn is not None:
        row.turn_id = turn.id
    if output is not None:
        row.provider, row.model = output.provider, output.model
        row.registry_revision, row.prompt_version = (
            output.registry_revision,
            output.prompt_version,
        )
        row.cost_micros = output.cost_micros
    row.finished_at = aware_utc(now) if now is not None else utc_now()
    await database.commit()


async def owned_session(
    database: AsyncSession,
    *,
    session_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    lock: bool = False,
) -> KnowledgeSession | None:
    statement = select(KnowledgeSession).where(
        KnowledgeSession.id == session_id,
        KnowledgeSession.workspace_id == workspace_id,
        KnowledgeSession.user_id == user_id,
    )
    if lock:
        statement = statement.with_for_update()
    found: KnowledgeSession | None = await database.scalar(
        statement.execution_options(populate_existing=True)
    )
    return found


async def _bounded_session(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, question: str, now: datetime
) -> KnowledgeSession:
    """Create an owned session under the user lock, pruning at the bound."""
    user = await database.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.agent_paused:
        raise AskUnavailable("Ask is unavailable for this account")
    existing = list(
        await database.scalars(
            select(KnowledgeSession)
            .where(
                KnowledgeSession.workspace_id == workspace_id,
                KnowledgeSession.user_id == user_id,
            )
            .order_by(KnowledgeSession.last_turn_at, KnowledgeSession.id)
        )
    )
    while len(existing) >= MAX_SESSIONS:
        oldest = existing.pop(0)
        await database.execute(delete(KnowledgeTurn).where(KnowledgeTurn.session_id == oldest.id))
        await database.delete(oldest)
    session = KnowledgeSession(
        workspace_id=workspace_id,
        user_id=user_id,
        title=question[:200],
        created_at=now,
        updated_at=now,
        last_turn_at=now,
    )
    database.add(session)
    await database.flush()
    return session


async def reserve_turn(
    database: AsyncSession,
    *,
    session: KnowledgeSession,
    workspace_id: UUID,
    user_id: UUID,
    request: AskRequest,
    now: datetime,
) -> KnowledgeTurn:
    """Reserve the next turn sequence under the session row lock, before paying."""
    locked = await owned_session(
        database,
        session_id=session.id,
        workspace_id=workspace_id,
        user_id=user_id,
        lock=True,
    )
    if locked is None:
        raise AskRejected("That session is not available")
    highest = await database.scalar(
        select(func.max(KnowledgeTurn.sequence)).where(KnowledgeTurn.session_id == session.id)
    )
    sequence = int(highest or 0) + 1
    if sequence > MAX_TURNS_PER_SESSION:
        raise AskRejected("This session has reached its turn limit")
    turn = KnowledgeTurn(
        session_id=session.id,
        workspace_id=workspace_id,
        user_id=user_id,
        sequence=sequence,
        question=request.question,
        mode=SearchMode.ASK.value,
        status=AskStatus.RESERVED.value,
        request_id=request.request_id,
        answer_state=AnswerState.UNAVAILABLE.value,
        selection={"ranking_version": ASK_RANKING_VERSION, "displayed": [], "resources": []},
        ranking_version=ASK_RANKING_VERSION,
        freshness="CACHED",
        trace_id=uuid4().hex,
        coverage={},
        created_at=now,
    )
    database.add(turn)
    session.last_turn_at = now
    session.updated_at = now
    await database.commit()
    return turn


async def finalize_turn(
    database: AsyncSession,
    turn: KnowledgeTurn,
    *,
    status: str,
    answer_state: AnswerState,
    selection: dict[str, object],
    coverage: AskCoverage,
    now: datetime,
) -> None:
    """Atomically transition a RESERVED turn under owned row locks.

    Raises ``TurnVanished`` when the session or turn was cleared/deleted, or the turn
    was already finalized, so a caller can never resurrect erased history or hit
    a stale-instance update error.
    """
    await database.scalar(select(User.id).where(User.id == turn.user_id).with_for_update())
    session_row = await owned_session(
        database,
        session_id=turn.session_id,
        workspace_id=turn.workspace_id,
        user_id=turn.user_id,
        lock=True,
    )
    locked = await database.scalar(
        select(KnowledgeTurn)
        .where(
            KnowledgeTurn.id == turn.id,
            KnowledgeTurn.user_id == turn.user_id,
            KnowledgeTurn.workspace_id == turn.workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or session_row is None or locked.status != AskStatus.RESERVED.value:
        await database.rollback()
        raise TurnVanished("The owned turn was cleared or already finalized")
    locked.status = status
    locked.answer_state = answer_state.value
    locked.selection = selection
    locked.coverage = coverage.model_dump(mode="json")
    locked.ranking_version = ASK_RANKING_VERSION
    locked.freshness = "CACHED"
    await database.commit()
    del now


def _coverage_from(data: dict[str, object]) -> AskCoverage:
    def as_int(key: str) -> int:
        value = data.get(key, 0)
        return int(value) if isinstance(value, int) else 0

    raw_reasons = data.get("partial_reasons")
    reasons: tuple[str, ...] = ()
    if isinstance(raw_reasons, (list, tuple)):
        reasons = tuple(item for item in raw_reasons if isinstance(item, str))
    raw_issues = data.get("source_issues")
    return AskCoverage(
        source_issues=tuple(
            SourceIssue.model_validate(item) for item in raw_issues if isinstance(item, dict)
        )
        if isinstance(raw_issues, list)
        else (),
        examined=as_int("examined"),
        returned=as_int("returned"),
        truncated=bool(data.get("truncated", False)),
        evidence_resources=as_int("evidence_resources"),
        partial_reasons=reasons,
    )


def _with_reasons(coverage: AskCoverage, reasons: list[str]) -> AskCoverage:
    unique = tuple(dict.fromkeys(reasons))
    return AskCoverage(
        source_issues=coverage.source_issues,
        examined=coverage.examined,
        returned=coverage.returned,
        truncated=bool(unique) or coverage.truncated,
        evidence_resources=coverage.evidence_resources,
        partial_reasons=unique,
    )


def _response(
    turn: KnowledgeTurn,
    *,
    citations: tuple[AnswerCitation, ...] = (),
    results: tuple[EvidenceResource, ...] = (),
    replay: bool = False,
) -> AskResponse:
    data = turn.coverage if isinstance(turn.coverage, dict) else {}
    state = AnswerState(turn.answer_state)
    return AskResponse(
        session_id=turn.session_id,
        turn_id=turn.id,
        sequence=turn.sequence,
        status=AskStatus(turn.status),
        answer_state=state,
        citations=citations,
        results=results,
        coverage=_coverage_from(data),
        suggested_followups=_followups(turn.sequence, state is AnswerState.READY),
        trace_id=turn.trace_id,
        replay=replay,
    )


def _followups(sequence: int, answered: bool) -> tuple[str, ...]:
    if not answered:
        return ("Ask about #1 with different wording",)
    return (
        "What else does #1 support?",
        "What else do these sources say?",
    )


async def _owned_turn(
    database: AsyncSession, *, turn_id: UUID, user_id: UUID
) -> KnowledgeTurn | None:
    found: KnowledgeTurn | None = await database.scalar(
        select(KnowledgeTurn).where(KnowledgeTurn.id == turn_id, KnowledgeTurn.user_id == user_id)
    )
    return found


async def _turn_for_request(
    database: AsyncSession, *, session_id: UUID, request_id: UUID
) -> KnowledgeTurn | None:
    found: KnowledgeTurn | None = await database.scalar(
        select(KnowledgeTurn).where(
            KnowledgeTurn.session_id == session_id,
            KnowledgeTurn.request_id == request_id,
        )
    )
    return found


async def _turn_for_user_request(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, request_id: UUID
) -> KnowledgeTurn | None:
    """Owned turn for one request identifier, in any session of that user."""
    found: KnowledgeTurn | None = await database.scalar(
        select(KnowledgeTurn)
        .where(
            KnowledgeTurn.user_id == user_id,
            KnowledgeTurn.workspace_id == workspace_id,
            KnowledgeTurn.request_id == request_id,
        )
        .order_by(KnowledgeTurn.created_at.desc(), KnowledgeTurn.id.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return found


async def _previous_completed_turn(
    database: AsyncSession, *, session_id: UUID
) -> KnowledgeTurn | None:
    found: KnowledgeTurn | None = await database.scalar(
        select(KnowledgeTurn)
        .where(
            KnowledgeTurn.session_id == session_id,
            KnowledgeTurn.status == AskStatus.COMPLETED.value,
        )
        .order_by(KnowledgeTurn.sequence.desc())
        .limit(1)
    )
    return found


def _displayed_ids(turn: KnowledgeTurn | None) -> list[UUID]:
    """Displayed resource order of one turn, newest-first order preserved."""
    if turn is None or not isinstance(turn.selection, dict):
        return []
    raw = turn.selection.get("displayed")
    if not isinstance(raw, list):
        return []
    identifiers: list[UUID] = []
    for item in raw:
        try:
            identifiers.append(UUID(str(item)))
        except ValueError:
            continue
    return identifiers


async def resolve_focus(
    database: AsyncSession,
    *,
    session_id: UUID,
    request: AskRequest,
) -> tuple[KnowledgeTurn, UUID] | None:
    """Resolve ``#N`` to the Nth displayed resource of the previous answer.

    ``#N`` counts displayed results of the immediately previous completed turn,
    never the Nth turn in history. Ambiguous, conflicting, out-of-range or
    missing referents fail closed; there is no broad-search fallback.
    """
    tokens = _REFERENT.findall(request.question)
    if len(set(tokens)) > 1:
        raise ReferentInvalid("Mention only one #N referent")
    explicit = request.referent[1:] if request.referent is not None else None
    inferred = tokens[0] if tokens else None
    if explicit is not None and inferred is not None and explicit != inferred:
        raise ReferentInvalid("Conflicting follow-up referents")
    wanted = explicit or inferred
    if wanted is None and request.followup_of is None:
        return None
    previous = await _previous_completed_turn(database, session_id=session_id)
    if previous is None:
        raise ReferentInvalid("There is no previous answer to follow up on")
    if request.followup_of is not None and request.followup_of != previous.id:
        raise ReferentInvalid("A follow-up must target the previous answer")
    displayed = _displayed_ids(previous)
    index = 0 if wanted is None else int(wanted) - 1
    if index < 0 or index >= len(displayed):
        raise ReferentInvalid("That numbered source is not in the previous answer")
    return previous, displayed[index]


async def _results_now(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_ids: list[UUID],
    now: datetime,
) -> tuple[EvidenceResource, ...]:
    """Every displayed result, re-fenced at the caller's current time."""
    # Fresh pause/membership on every response path, including failures.
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    bound = await bound_evidence_for_ids(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_ids=resource_ids,
        now=now,
    )
    order = {identifier: index for index, identifier in enumerate(resource_ids)}
    bound.sort(key=lambda item: order.get(item.resource.resource_id, len(order)))
    return tuple(
        item.resource.model_copy(
            update={
                "provenance": {
                    **item.resource.provenance,
                    "display_number": str(order[item.resource.resource_id] + 1),
                }
            }
        )
        for item in bound
    )


async def _restore_citations(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    selection: dict[str, object],
    now: datetime,
    question: str,
) -> tuple[list[AnswerCitation], bool]:
    """Rebuild a stored answer only while every selected binding still holds."""
    resources = selection.get("resources")
    if not isinstance(resources, list):
        return [], True
    if not resources:
        return [], False
    entries: list[dict[str, object]] = []
    identifiers: list[UUID] = []
    for item in resources:
        if not isinstance(item, dict):
            return [], True
        try:
            identifiers.append(UUID(str(item.get("resource_id"))))
        except ValueError:
            return [], True
        entries.append(item)
    bound = await bound_evidence_for_ids(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_ids=identifiers,
        now=now,
        query=question,
    )
    candidates, _reasons = candidates_from(bound)
    by_id = {candidate.resource_id: candidate for candidate in candidates}
    selections: list[AnswerSelection] = []
    for item in entries:
        resource_id = UUID(str(item.get("resource_id")))
        candidate = by_id.get(resource_id)
        if candidate is None:
            return [], True
        stored_binding = item.get("binding")
        if not isinstance(stored_binding, list):
            return [], True
        # The full non-text binding must match exactly: hash, identity, scope,
        # classification, title digest, version, locators and fact values.
        if _jsonable(candidate.binding()) != stored_binding:
            return [], True
        raw_indices = item.get("excerpt_indices")
        raw_facts = item.get("fact_ids")
        indices = raw_indices if isinstance(raw_indices, (list, tuple)) else ()
        fact_ids = raw_facts if isinstance(raw_facts, (list, tuple)) else ()
        selections.append(
            AnswerSelection(
                resource_id=resource_id,
                excerpt_indices=tuple(value for value in indices if isinstance(value, int)),
                fact_ids=tuple(value for value in fact_ids if isinstance(value, str)),
            )
        )
    try:
        citations = build_citations(
            KnowledgeAnswer(selections=tuple(selections)), candidates=candidates
        )
    except (AskRejected, ValueError):
        return [], True
    return list(citations), False


def _snapshot(
    selection: KnowledgeAnswer,
    *,
    candidates: list[AskCandidate],
    displayed: list[UUID],
    turn: KnowledgeTurn | None,
) -> dict[str, object]:
    """ID-only snapshot with strong bindings; never raw text."""
    by_id = {candidate.resource_id: candidate for candidate in candidates}
    return {
        "ranking_version": ASK_RANKING_VERSION,
        "followup_of": str(turn.id) if turn is not None else None,
        "displayed": [str(item) for item in displayed],
        "resources": [
            {
                "resource_id": str(item.resource_id),
                # Full non-text binding: hash, identity, scope, classification,
                # title digest, version, locator digests and fact values.
                "binding": _jsonable(by_id[item.resource_id].binding()),
                "rank": by_id[item.resource_id].rank,
                "excerpt_indices": list(item.excerpt_indices),
                "fact_ids": list(item.fact_ids),
            }
            for item in selection.selections
            if item.resource_id in by_id
        ],
    }


async def answer_question(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request: AskRequest,
    settings: Settings,
    gateway: AskGateway | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AskResponse:
    """One paid question; an owned turn cleared mid-call is refused, never restored."""
    try:
        return await _answer_question(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            request=request,
            settings=settings,
            gateway=gateway,
            now=now,
            clock=clock,
        )
    except TurnVanished as error:
        row = await find_attempt(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request.request_id,
        )
        if row is not None and row.status == "RESERVED":
            await _finish_ask(
                database,
                row,
                status="COMPLETED",
                reason=ASK_REASON_WITHHELD,
                now=utc_now(),
            )
        raise AskRejected("This answer was cleared before it finished") from error


async def _answer_question(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request: AskRequest,
    settings: Settings,
    gateway: AskGateway | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AskResponse:
    """One explicit paid question over currently authorized evidence."""
    moment = aware_utc(now) if now is not None else utc_now()
    if not settings.knowledge_enabled or not settings.knowledge_ask_enabled:
        raise AskDisabled("Grounded Ask is not enabled")
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    try:
        reject_credentials(request.question, configured_secrets(settings))
    except ContextDenied as error:
        raise AskRejected(str(error)) from error

    def fence_time() -> datetime:
        return aware_utc(clock()) if clock is not None else utc_now()

    await database.scalar(select(User.id).where(User.id == user_id).with_for_update())
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    # Early idempotency: nothing is created or purchased for a known identifier,
    # even after qualification disappears or history changes.
    replayed = await _turn_for_user_request(
        database, workspace_id=workspace_id, user_id=user_id, request_id=request.request_id
    )
    existing = await find_attempt(
        database, workspace_id=workspace_id, user_id=user_id, request_id=request.request_id
    )
    if replayed is not None:
        restored_citations, changed = await _restore_citations(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            selection=replayed.selection if isinstance(replayed.selection, dict) else {},
            now=fence_time(),
            question=replayed.question,
        )
        if changed:
            # The owned turn exists but its bindings no longer hold: withhold it.
            return AskResponse(
                session_id=replayed.session_id,
                turn_id=replayed.id,
                sequence=replayed.sequence,
                status=AskStatus(replayed.status),
                answer_state=AnswerState.WITHHELD,
                citations=(),
                results=(),
                coverage=_with_reasons(
                    _coverage_from(
                        replayed.coverage if isinstance(replayed.coverage, dict) else {}
                    ),
                    [ASK_REASON_WITHHELD],
                ),
                suggested_followups=_followups(replayed.sequence, False),
                trace_id=replayed.trace_id,
                replay=True,
            )
        return _response(
            replayed,
            citations=tuple(restored_citations),
            results=await _results_now(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                resource_ids=_displayed_ids(replayed),
                now=fence_time(),
            ),
            replay=True,
        )
    if existing is not None:
        # A reservation whose turn was cleared cannot buy another attempt.
        raise AskRequestReplay(existing.status)
    if request.session_id is not None:
        session = await owned_session(
            database,
            session_id=request.session_id,
            workspace_id=workspace_id,
            user_id=user_id,
            lock=True,
        )
        if session is None:
            raise AskRejected("That session is not available")
    else:
        session = await _bounded_session(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            question=request.question,
            now=moment,
        )
    focus = await resolve_focus(database, session_id=session.id, request=request)
    turn = await reserve_turn(
        database,
        session=session,
        workspace_id=workspace_id,
        user_id=user_id,
        request=request,
        now=moment,
    )
    reasons: list[str] = []
    search_issues: tuple[SourceIssue, ...] = ()
    if focus is not None:
        referenced, target = focus
        bound = await bound_evidence_for_ids(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_ids=[target],
            now=moment,
            query=request.question,
        )
        if len(bound) != 1:
            raise ReferentStale("The source this follow-up refers to is not current")
    else:
        referenced = None
        search = await search_knowledge(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            request=SearchRequest(query=request.question, mode=SearchMode.ASK, limit=request.limit),
            now=moment,
            settings=settings,
            record_recent=False,
        )
        if any(resource.origin == "NATIVE" for resource in search.results):
            reasons.append(ASK_REASON_NATIVE_EXCLUDED)
        ranked_ids = [resource.resource_id for resource in search.results]
        bound = await bound_evidence_for_ids(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_ids=ranked_ids,
            now=moment,
            query=request.question,
        )
        order = {identifier: index for index, identifier in enumerate(ranked_ids)}
        bound.sort(key=lambda item: order.get(item.resource.resource_id, len(order)))
        search_issues = search.coverage.source_issues
        if search.coverage.truncated or search_issues:
            reasons.append(ASK_REASON_INCOMPLETE)
    candidates, native_reasons = candidates_from(bound)
    reasons.extend(native_reasons)
    if not candidates and not reasons:
        reasons.append(ASK_REASON_INSUFFICIENT)
    sensitivity = max_sensitivity(candidates)
    displayed = [item.resource.resource_id for item in bound]
    coverage = AskCoverage(
        source_issues=search_issues,
        examined=len(bound),
        returned=len(candidates),
        truncated=bool(reasons),
        evidence_resources=len(candidates),
        partial_reasons=tuple(dict.fromkeys(reasons)),
    )
    client = gateway or registered_gateway(settings)
    eligible = (
        bool(candidates)
        and client is not None
        and await client.eligible(
            workspace_id=workspace_id, user_id=user_id, sensitivity=sensitivity
        )
    )
    if not eligible:
        state = AnswerState.UNAVAILABLE if candidates else AnswerState.INSUFFICIENT
        if state is AnswerState.UNAVAILABLE:
            reasons.append(ASK_REASON_UNAVAILABLE)
        coverage = _with_reasons(coverage, reasons)
        await finalize_turn(
            database,
            turn,
            status=AskStatus.UNAVAILABLE.value,
            answer_state=state,
            selection={
                "ranking_version": ASK_RANKING_VERSION,
                "displayed": [str(item) for item in displayed],
                "resources": [],
            },
            coverage=coverage,
            now=moment,
        )
        return _response(
            turn,
            results=await _results_now(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                resource_ids=displayed,
                now=fence_time(),
            ),
        )
    assert client is not None
    row, created = await reserve_ask(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request.request_id,
        quota=settings.knowledge_ask_hourly_quota,
        session_id=session.id,
        now=moment,
    )
    if not created:
        raise AskRequestReplay(row.status, None)
    try:
        output = await client.answer(
            AskGatewayRequest(
                workspace_id=workspace_id,
                user_id=user_id,
                question=request.question,
                sensitivity=sensitivity,
                candidates=tuple(candidates),
            )
        )
    except (AskUnavailable, GatewayUnavailable, ContextDenied):
        output = None
    results = await _results_now(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_ids=displayed,
        now=fence_time(),
    )
    # History may have been cleared or deleted while the provider ran. The turn
    # is re-read and the answer is withheld rather than restored.
    if await _owned_turn(database, turn_id=turn.id, user_id=user_id) is None:
        withheld = _with_reasons(coverage, [ASK_REASON_WITHHELD])
        await _finish_ask(
            database,
            row,
            status="COMPLETED",
            output=output,
            reason=ASK_REASON_WITHHELD,
            now=moment,
        )
        return AskResponse(
            session_id=session.id,
            turn_id=turn.id,
            sequence=turn.sequence,
            status=AskStatus.COMPLETED,
            answer_state=AnswerState.WITHHELD,
            citations=(),
            # Erased history returns nothing, not the evidence read before the clear.
            results=(),
            coverage=withheld,
            suggested_followups=(),
            trace_id=turn.trace_id,
        )
    if output is None:
        reasons.append(ASK_REASON_UNAVAILABLE)
        coverage = _with_reasons(coverage, reasons)
        await finalize_turn(
            database,
            turn,
            status=AskStatus.UNAVAILABLE.value,
            answer_state=AnswerState.UNAVAILABLE,
            selection={
                "ranking_version": ASK_RANKING_VERSION,
                "displayed": [str(item) for item in displayed],
                "resources": [],
            },
            coverage=coverage,
            now=moment,
        )
        await _finish_ask(
            database,
            row,
            status="UNAVAILABLE",
            turn=turn,
            reason=ASK_REASON_UNAVAILABLE,
            now=moment,
        )
        return _response(turn, results=results)
    if output.selection.insufficient_context:
        reasons.append(ASK_REASON_INSUFFICIENT)
        coverage = _with_reasons(coverage, reasons)
        await finalize_turn(
            database,
            turn,
            status=AskStatus.COMPLETED.value,
            answer_state=AnswerState.INSUFFICIENT,
            selection={
                "ranking_version": ASK_RANKING_VERSION,
                "displayed": [str(item) for item in displayed],
                "resources": [],
            },
            coverage=coverage,
            now=moment,
        )
        await _finish_ask(database, row, status="COMPLETED", turn=turn, output=output, now=moment)
        return _response(turn, results=results)
    fresh = await bound_evidence_for_ids(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_ids=[candidate.resource_id for candidate in candidates],
        now=fence_time(),
        query=request.question,
    )
    fresh_candidates, _fresh_reasons = candidates_from(fresh)
    fresh_by_id = {candidate.resource_id: candidate for candidate in fresh_candidates}
    selected_ids = {item.resource_id for item in output.selection.selections}
    changed = any(
        candidate.resource_id in selected_ids
        and (
            candidate.resource_id not in fresh_by_id
            or fresh_by_id[candidate.resource_id].binding() != candidate.binding()
        )
        for candidate in candidates
    )
    if changed:
        reasons.append(ASK_REASON_WITHHELD)
        coverage = _with_reasons(coverage, reasons)
        await finalize_turn(
            database,
            turn,
            status=AskStatus.COMPLETED.value,
            answer_state=AnswerState.WITHHELD,
            selection={
                "ranking_version": ASK_RANKING_VERSION,
                "displayed": [str(item) for item in displayed],
                "resources": [],
            },
            coverage=coverage,
            now=moment,
        )
        await _finish_ask(
            database,
            row,
            status="COMPLETED",
            turn=turn,
            output=output,
            reason=ASK_REASON_WITHHELD,
            now=moment,
        )
        return _response(turn, results=results)
    try:
        citations = build_citations(output.selection, candidates=fresh_candidates)
    except AskRejected:
        reasons.append(ASK_REASON_INVALID_SELECTION)
        coverage = _with_reasons(coverage, reasons)
        await finalize_turn(
            database,
            turn,
            status=AskStatus.FAILED.value,
            answer_state=AnswerState.INCOMPLETE,
            selection={
                "ranking_version": ASK_RANKING_VERSION,
                "displayed": [str(item) for item in displayed],
                "resources": [],
            },
            coverage=coverage,
            now=moment,
        )
        await _finish_ask(
            database,
            row,
            status="FAILED",
            turn=turn,
            output=output,
            reason=ASK_REASON_INVALID_SELECTION,
            now=moment,
        )
        return _response(turn, results=results)
    await finalize_turn(
        database,
        turn,
        status=AskStatus.COMPLETED.value,
        answer_state=AnswerState.READY,
        selection=_snapshot(
            output.selection, candidates=fresh_candidates, displayed=displayed, turn=referenced
        ),
        coverage=coverage,
        now=moment,
    )
    await _finish_ask(database, row, status="COMPLETED", turn=turn, output=output, now=moment)
    return _response(turn, citations=citations, results=results)


async def record_search_turn(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    payload: SearchTurnRequest,
    settings: Settings,
    now: datetime | None = None,
) -> AskResponse:
    """Record a free retrieval turn in an owned session. Never buys a call."""
    moment = aware_utc(now) if now is not None else utc_now()
    if not settings.knowledge_enabled:
        raise AskDisabled("Connected search is not enabled")
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    try:
        reject_credentials(payload.query, configured_secrets(settings))
    except ContextDenied as error:
        raise AskRejected(str(error)) from error
    await database.scalar(select(User.id).where(User.id == user_id).with_for_update())
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    # Idempotency is per owned request identifier, before any session work.
    replay = await _turn_for_user_request(
        database, workspace_id=workspace_id, user_id=user_id, request_id=payload.request_id
    )
    if replay is not None:
        return _response(
            replay,
            results=await _results_now(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                resource_ids=_displayed_ids(replay),
                now=moment,
            ),
            replay=True,
        )
    if payload.session_id is not None:
        session = await owned_session(
            database,
            session_id=payload.session_id,
            workspace_id=workspace_id,
            user_id=user_id,
            lock=True,
        )
        if session is None:
            raise AskRejected("That session is not available")
    else:
        session = await _bounded_session(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            question=payload.query,
            now=moment,
        )
    turn = await reserve_turn(
        database,
        session=session,
        workspace_id=workspace_id,
        user_id=user_id,
        request=AskRequest(
            question=payload.query, request_id=payload.request_id, session_id=session.id
        ),
        now=moment,
    )
    search = await search_knowledge(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        request=SearchRequest(query=payload.query, mode=SearchMode.SEARCH, limit=20),
        now=moment,
        settings=settings,
        record_recent=False,
    )
    ranked_ids = [resource.resource_id for resource in search.results]
    displayed = [str(item) for item in ranked_ids]
    coverage = AskCoverage(
        examined=search.coverage.examined,
        returned=search.coverage.returned,
        truncated=search.coverage.truncated,
        evidence_resources=len(search.results),
        partial_reasons=search.coverage.partial_reasons,
    )
    try:
        await finalize_turn(
            database,
            turn,
            status=AskStatus.COMPLETED.value,
            answer_state=AnswerState.NOT_REQUESTED,
            selection={
                "ranking_version": search.trace_id,
                "displayed": displayed,
                "resources": [],
            },
            coverage=coverage,
            now=moment,
        )
    except TurnVanished as error:
        raise AskRejected("This session was cleared before the search finished") from error
    return _response(
        turn,
        results=await _results_now(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_ids=ranked_ids,
            now=moment,
        ),
    )


async def list_sessions(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> list[AskSessionSummary]:
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    rows = list(
        await database.scalars(
            select(KnowledgeSession)
            .where(
                KnowledgeSession.workspace_id == workspace_id,
                KnowledgeSession.user_id == user_id,
            )
            .order_by(KnowledgeSession.last_turn_at.desc(), KnowledgeSession.id)
            .limit(MAX_SESSIONS)
        )
    )
    if not rows:
        return []
    rows_counts = (
        await database.execute(
            select(KnowledgeTurn.session_id, func.count())
            .where(KnowledgeTurn.session_id.in_([row.id for row in rows]))
            .group_by(KnowledgeTurn.session_id)
        )
    ).all()
    counts: dict[UUID, int] = {row[0]: int(row[1]) for row in rows_counts}
    return [
        AskSessionSummary(
            id=row.id,
            title=row.title,
            turn_count=int(counts.get(row.id, 0)),
            created_at=row.created_at,
            last_turn_at=row.last_turn_at,
        )
        for row in rows
    ]


async def read_session(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    session_id: UUID,
    now: datetime | None = None,
) -> AskSessionView | None:
    """Reconstruct the currently safe turns of one owned session."""
    moment = aware_utc(now) if now is not None else utc_now()
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    session = await owned_session(
        database, session_id=session_id, workspace_id=workspace_id, user_id=user_id
    )
    if session is None:
        return None
    turns = list(
        await database.scalars(
            select(KnowledgeTurn)
            .where(KnowledgeTurn.session_id == session.id)
            .order_by(KnowledgeTurn.sequence)
        )
    )
    views: list[AskTurnView] = []
    for turn in turns:
        data = turn.coverage if isinstance(turn.coverage, dict) else {}
        coverage = _coverage_from(data)
        selection = turn.selection if isinstance(turn.selection, dict) else {}
        state = AnswerState(turn.answer_state)
        citations: tuple[AnswerCitation, ...] = ()
        if state is AnswerState.READY:
            restored, changed = await _restore_citations(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                selection=selection,
                now=moment,
                question=turn.question,
            )
            if changed or not restored:
                state = AnswerState.WITHHELD
                coverage = _with_reasons(coverage, [ASK_REASON_WITHHELD])
            else:
                citations = tuple(restored)
        views.append(
            AskTurnView(
                id=turn.id,
                session_id=turn.session_id,
                sequence=turn.sequence,
                question=turn.question,
                status=AskStatus(turn.status),
                answer_state=state,
                created_at=turn.created_at,
                trace_id=turn.trace_id,
                citations=citations,
                coverage=coverage,
            )
        )
    return AskSessionView(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        last_turn_at=session.last_turn_at,
        turns=tuple(views),
    )


async def clear_history(database: AsyncSession, *, workspace_id: UUID, user_id: UUID) -> int:
    """Delete owned sessions and turns. Durable reservations are untouched."""
    await database.scalar(select(User.id).where(User.id == user_id).with_for_update())
    identifiers = list(
        await database.scalars(
            select(KnowledgeSession.id).where(
                KnowledgeSession.workspace_id == workspace_id,
                KnowledgeSession.user_id == user_id,
            )
        )
    )
    if not identifiers:
        return 0
    await database.execute(delete(KnowledgeTurn).where(KnowledgeTurn.session_id.in_(identifiers)))
    await database.execute(delete(KnowledgeSession).where(KnowledgeSession.id.in_(identifiers)))
    await database.commit()
    return len(identifiers)


async def delete_session(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, session_id: UUID
) -> bool:
    await database.scalar(select(User.id).where(User.id == user_id).with_for_update())
    session = await owned_session(
        database, session_id=session_id, workspace_id=workspace_id, user_id=user_id, lock=True
    )
    if session is None:
        return False
    await database.execute(delete(KnowledgeTurn).where(KnowledgeTurn.session_id == session.id))
    await database.delete(session)
    await database.commit()
    return True


async def purge_expired_history(
    database: AsyncSession,
    *,
    now: datetime | None = None,
    retention: timedelta = ASK_RETENTION,
    turn_limit: int = 500,
    session_limit: int = 200,
) -> int:
    """Bounded retention cleanup; safe with every feature flag off.

    Deletes up to ``turn_limit`` expired turns first, then up to
    ``session_limit`` expired or now-empty owned sessions. Paid reservations keep
    identifiers only, so quota and replay stay durable. The caller owns the
    transaction: this helper never commits.
    """
    if not 1 <= turn_limit <= 5000 or not 1 <= session_limit <= 1000:
        raise ValueError("Retention batch limits are out of bounds")
    moment = aware_utc(now) if now is not None else utc_now()
    cutoff = moment - retention
    # Lock parent sessions first, matching finalization/deletion order. Never
    # acquire User afterward, and skip sessions being changed by another worker.
    sessions = list(
        await database.scalars(
            select(KnowledgeSession)
            .where(
                or_(
                    exists(
                        select(KnowledgeTurn.id).where(
                            KnowledgeTurn.session_id == KnowledgeSession.id,
                            KnowledgeTurn.created_at < cutoff,
                        )
                    ),
                    (KnowledgeSession.created_at < cutoff) & KnowledgeSession.title.is_not(None),
                    func.coalesce(KnowledgeSession.last_turn_at, KnowledgeSession.created_at)
                    < cutoff,
                )
            )
            .order_by(KnowledgeSession.id)
            .limit(session_limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    )
    identifiers = [session.id for session in sessions]
    if not identifiers:
        return 0
    expired_turns = list(
        await database.scalars(
            select(KnowledgeTurn.id)
            .where(
                KnowledgeTurn.session_id.in_(identifiers),
                KnowledgeTurn.created_at < cutoff,
            )
            .order_by(KnowledgeTurn.created_at, KnowledgeTurn.id)
            .limit(turn_limit)
        )
    )
    if expired_turns:
        await database.execute(delete(KnowledgeTurn).where(KnowledgeTurn.id.in_(expired_turns)))
    remaining = set(
        await database.scalars(
            select(KnowledgeTurn.session_id).where(KnowledgeTurn.session_id.in_(identifiers))
        )
    )
    removed = 0
    for session in sessions:
        if stored_utc(session.created_at) < cutoff:
            session.title = None
        if (
            session.id not in remaining
            and stored_utc(session.last_turn_at or session.created_at) < cutoff
        ):
            await database.delete(session)
            removed += 1
    await database.flush()
    return removed


async def turn_response(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    session_id: UUID,
    turn_id: UUID,
    replay: bool = True,
    now: datetime | None = None,
) -> AskResponse | None:
    """Read one owned turn back through the same safe reconstruction path."""
    moment = aware_utc(now) if now is not None else utc_now()
    view = await read_session(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        now=moment,
    )
    if view is None:
        return None
    for turn in view.turns:
        if turn.id != turn_id:
            continue
        stored = await _owned_turn(database, turn_id=turn.id, user_id=user_id)
        results = await _results_now(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_ids=_displayed_ids(stored),
            now=moment,
        )
        return AskResponse(
            session_id=view.id,
            turn_id=turn.id,
            sequence=turn.sequence,
            status=turn.status,
            answer_state=turn.answer_state,
            citations=turn.citations,
            results=results,
            coverage=turn.coverage,
            suggested_followups=_followups(turn.sequence, turn.answer_state is AnswerState.READY),
            trace_id=turn.trace_id,
            replay=replay,
        )
    return None


__all__ = [
    "ASK_COST_MICROS",
    "ASK_MAX_COST",
    "ASK_QUOTA_WINDOW",
    "ASK_RETENTION",
    "AskCandidate",
    "AskContext",
    "AskDisabled",
    "AskGateway",
    "AskGatewayOutput",
    "AskGatewayRequest",
    "AskQuotaExceeded",
    "AskRejected",
    "AskRequestReplay",
    "AskUnavailable",
    "BoundEvidence",
    "ReferentInvalid",
    "ReferentStale",
    "RegisteredAskGateway",
    "answer_question",
    "bound_evidence_for_ids",
    "build_citations",
    "candidates_from",
    "clear_history",
    "delete_session",
    "evidence_for_ids",
    "find_attempt",
    "list_sessions",
    "owned_session",
    "purge_expired_history",
    "read_session",
    "record_search_turn",
    "registered_gateway",
    "reserve_ask",
    "reserve_turn",
    "resolve_focus",
    "turn_response",
]
