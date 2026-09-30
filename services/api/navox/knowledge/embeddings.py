"""Paid, fenced embedding and the semantic retrieval path (SPEC-007 phase 3).

Every paid attempt is reserved durably before the gateway runs, is bounded by a
per-user hourly quota and a fixed task ceiling, and is discarded when authority,
exclusions or the stored source revision move before the vector is stored or
used. No caller reaches an HTTP adapter directly. Without an eligible registered
model no attempt is made at all and search returns honest lexical and structured
results with explicit incomplete-semantic coverage; hash, random or synonym
vectors are never produced.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import hypot, isfinite
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied, MinimizedContext, reject_credentials
from navox.ai.embedding_contracts import EMBEDDING, EmbeddingInput, EmbeddingOutput
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    JSONDocument,
    LatencyClass,
    Profile,
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
    KnowledgeChunk,
    KnowledgeEmbedding,
    KnowledgeEmbeddingRequest,
    KnowledgeResource,
    KnowledgeResourceIndex,
)
from navox.db.models import ConnectorResource, User, WorkspaceMembership
from navox.knowledge.contracts import aware_utc
from navox.knowledge.exclusions import load_exclusions
from navox.knowledge.permissions import RESOURCE_AUTHORITY_COLUMNS, can_view_resource
from navox.knowledge.planner import interpret
from navox.knowledge.retrieval import SemanticEvidence, semantic_retriever
from navox.knowledge.search_contracts import (
    SEMANTIC_REASON_PARTIAL,
    SEMANTIC_REASON_UNAVAILABLE,
    SEMANTIC_REASON_VECTOR_INVALID,
    RetrievalPlan,
    SearchResponse,
    SemanticKey,
    SemanticSearchRequest,
    utc_now,
)
from navox.knowledge.service import (
    _require_current_account,
    search_knowledge,
)

# The prompt/schema artifact version every stored vector is bound to.
EMBEDDING_VERSION = "knowledge-embedding.v1"
# Hard per-attempt ceiling. This is a code constant, not operator configuration,
# so a paid attempt can never be widened past five cents.
SEMANTIC_MAX_COST = Decimal("0.05")
SEMANTIC_COST_MICROS = 50_000
SEMANTIC_QUOTA_WINDOW = timedelta(hours=1)
# One durable document request embeds exactly one chunk. The index never creates
# more than this many chunks, so the bound is validated rather than iterated.
MAX_CHUNK_INDEX = 200
EMBEDDING_STATUS_COMPLETED = "COMPLETED"


class SemanticDisabled(ValueError):
    """Raised when the paid path is reached while its flag is off."""


class SemanticRejected(ValueError):
    """Raised when a request is refused before any reservation exists."""


class SemanticRequestReplay(ValueError):
    """Raised when a request identifier was already reserved; nothing is bought."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Embedding request {status.lower()}")


class SemanticQuotaExceeded(ValueError):
    """Raised when the caller's hourly paid-attempt quota is already spent."""


class SemanticUnavailable(ValueError):
    """Raised when an embedding cannot be produced or safely stored."""


@dataclass(frozen=True)
class EmbeddingVector:
    """One validated vector with the exact provider trace that produced it."""

    provider: str
    model: str
    registry_revision: str
    embedding_version: str
    vector: tuple[float, ...]
    cost_micros: int | None = None

    @property
    def dimension(self) -> int:
        return len(self.vector)

    def key(self) -> SemanticKey:
        return SemanticKey(
            provider=self.provider,
            model=self.model,
            registry_revision=self.registry_revision,
            embedding_version=self.embedding_version,
            dimension=self.dimension,
        )


@dataclass(frozen=True)
class EmbeddingBinding:
    """Identifier-only binding re-checked before and after a paid document call."""

    resource_id: UUID
    source_content_hash: str
    source_connection_id: UUID
    source_resource_id: UUID | None
    chunk_index: int


@dataclass(frozen=True)
class EmbeddingRequest:
    """One embedding input. The text never appears in any persisted row."""

    workspace_id: UUID
    user_id: UUID
    text: str
    purpose: Literal["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT"]
    sensitivity: Sensitivity
    binding: EmbeddingBinding | None = None
    dimensions: int | None = None


class EmbeddingGateway(Protocol):
    """Provider-neutral paid embedding surface. Selection stays in the gateway."""

    async def eligible(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        sensitivity: Sensitivity,
        text_length: int,
    ) -> bool: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingVector | None: ...


@dataclass(frozen=True)
class ResourceEmbeddingReport:
    """Honest outcome of one bounded document-embedding run."""

    resource_id: UUID
    chunk_index: int
    status: str
    reference: str | None
    key: SemanticKey | None
    chunks_embedded: int
    chunks_total: int
    cost_micros: int | None


def normalize_vector(values: Sequence[float]) -> tuple[float, ...]:
    """Reject zero/NaN/infinite vectors and return a unit vector.

    Storing the stable unit vector keeps every cosine comparison a plain dot
    product, so a ranking cannot drift with rescaling. ``hypot`` is used because
    squaring overflows for large finite values and underflows for tiny ones.
    """
    if not values:
        raise ValueError("An embedding needs at least one dimension")
    checked: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Embedding contains nonnumeric values")
        try:
            number = float(value)
        except OverflowError as error:
            raise ValueError("Embedding is not finite") from error
        if not isfinite(number):
            raise ValueError("Embedding is not finite")
        checked.append(number)
    scale = max(abs(number) for number in checked)
    if scale == 0.0:
        raise ValueError("Embedding has zero magnitude")
    checked = [number / scale for number in checked]
    norm = hypot(*checked)
    if norm <= 0.0 or not isfinite(norm):
        raise ValueError("Embedding has zero magnitude")
    return tuple(number / norm for number in checked)


def sensitivity_of(value: str) -> Sensitivity:
    """Current classification, floored at the most restrictive level when unknown."""
    try:
        return Sensitivity(value)
    except ValueError:
        return Sensitivity.RESTRICTED


def micros(value: Decimal | None) -> int | None:
    """Exact integer micro-dollars; unknown cost stays unknown."""
    if value is None:
        return None
    return int((value * Decimal(1_000_000)).to_integral_value())


def _embedding_task(request: EmbeddingRequest, policy: ProviderPolicy) -> AITask:
    return AITask(
        workspace_id=request.workspace_id,
        user_id=request.user_id,
        task_type=TaskType.EMBED,
        profile=Profile.EMBEDDING,
        capability_requirements=frozenset({Capability.EMBEDDINGS}),
        output_schema=EMBEDDING,
        prompt=EMBEDDING,
        sensitivity=request.sensitivity,
        latency_class=LatencyClass.BACKGROUND,
        quality_class=QualityClass.STANDARD,
        max_cost=SEMANTIC_MAX_COST,
        max_output_tokens=1,
        provider_policy=policy,
    )


def validate_embedding_output(value: object) -> None:
    """Semantic validator used by the gateway; zero/invalid vectors are rejected."""
    EmbeddingOutput.model_validate(value)


async def embedding_authority(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    binding: EmbeddingBinding,
    now: datetime,
) -> str | None:
    """Current sensitivity of an embeddable bound resource, or ``None`` when denied.

    Authority, exclusions, index state and the stored revision are all re-read
    from the database, so a caller-supplied binding can never authorize a read
    and a moved source can never be embedded or served. Only authority and
    classification columns are projected: stored text, titles and vectors are
    never loaded while a decision is pending.
    """
    resource = await database.scalar(
        select(KnowledgeResource)
        .options(
            load_only(
                *RESOURCE_AUTHORITY_COLUMNS,
                KnowledgeResource.sensitivity,
            )
        )
        .where(
            KnowledgeResource.id == binding.resource_id,
            KnowledgeResource.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if resource is None or resource.deleted_at is not None:
        return None
    # The binding must describe the current row, not only a stored identifier.
    if (
        resource.source_connection_id != binding.source_connection_id
        or resource.source_resource_id != binding.source_resource_id
        or resource.source_resource_id is None
    ):
        return None
    index_row = await database.scalar(
        select(KnowledgeResourceIndex)
        .options(
            load_only(
                KnowledgeResourceIndex.resource_id,
                KnowledgeResourceIndex.workspace_id,
                KnowledgeResourceIndex.source_content_hash,
                KnowledgeResourceIndex.index_state,
            )
        )
        .where(
            KnowledgeResourceIndex.resource_id == binding.resource_id,
            KnowledgeResourceIndex.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        index_row is None
        or index_row.index_state != "INDEXED"
        or index_row.source_content_hash is None
        or index_row.source_content_hash != binding.source_content_hash
    ):
        return None
    live_hash = await database.scalar(
        select(ConnectorResource.content_hash)
        .where(
            ConnectorResource.id == binding.source_resource_id,
            ConnectorResource.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if live_hash != binding.source_content_hash:
        return None
    if not await can_view_resource(
        database,
        resource_id=binding.resource_id,
        workspace_id=workspace_id,
        user_id=user_id,
        now=now,
    ):
        return None
    filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)
    if filters.excludes(
        source_type=resource.source_type,
        resource_id=resource.id,
        source_connection_id=resource.source_connection_id,
        external_parent_id=await database.scalar(
            select(ConnectorResource.external_parent_id).where(
                ConnectorResource.id == binding.source_resource_id,
                ConnectorResource.workspace_id == workspace_id,
            )
        ),
    ):
        return None
    if resource.sensitivity not in {item.value for item in Sensitivity}:
        return Sensitivity.RESTRICTED.value
    return resource.sensitivity


class KnowledgeContext:
    """Gateway ``AuthorizedContext`` that re-reads authority on every build.

    A fresh database session is opened per build so a cached object can never
    satisfy the pre-call or post-call fence, and the fence always runs at the
    real current time so a grant that expires mid-call cannot publish. The
    minimized content is rebuilt from the exact current chunk, so altered text
    or a changed classification is rejected rather than embedded.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        request: EmbeddingRequest,
    ) -> None:
        self.factory, self.settings, self.request = factory, settings, request

    async def build(
        self,
        task: AITask,
        documents: object,
        *,
        user_request: str = "",
    ) -> MinimizedContext:
        request = self.request
        if not self.settings.knowledge_enabled or not self.settings.knowledge_semantic_enabled:
            raise ContextDenied("Semantic embedding is not enabled")
        if not (
            task.profile is Profile.EMBEDDING
            and task.task_type is TaskType.EMBED
            and task.prompt == EMBEDDING
            and task.output_schema == EMBEDDING
            and task.capability_requirements == frozenset({Capability.EMBEDDINGS})
        ):
            raise ContextDenied("Embedding context rejected a non-embedding task")
        if task.context_references or documents or user_request:
            raise ContextDenied("Embedding context accepts no documents or references")
        if (task.workspace_id, task.user_id) != (
            request.workspace_id,
            request.user_id,
        ):
            raise ContextDenied("Embedding context scope mismatch")
        if task.sensitivity is not request.sensitivity:
            raise ContextDenied("Embedding context sensitivity mismatch")
        if request.purpose == "RETRIEVAL_QUERY":
            if request.binding is not None:
                raise ContextDenied("A query embedding carries no source binding")
            if request.sensitivity.rank < Sensitivity.PERSONAL.rank:
                raise ContextDenied("A query embedding cannot be below PERSONAL")
        elif request.binding is None:
            raise ContextDenied("A document embedding requires a source binding")
        text = request.text
        async with self.factory() as database:
            await _require_current_account(
                database,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
            )
            binding = request.binding
            if binding is not None:
                current = await embedding_authority(
                    database,
                    workspace_id=request.workspace_id,
                    user_id=request.user_id,
                    binding=binding,
                    now=utc_now(),
                )
                if current is None:
                    raise ContextDenied("Embedding source is unavailable")
                if sensitivity_of(current) != request.sensitivity:
                    raise ContextDenied("Embedding source classification changed")
                # The binding is only a pointer: the embedded text is the exact
                # current chunk, so altered or rebuilt text is never sent.
                stored = await database.scalar(
                    select(KnowledgeChunk.text_content)
                    .where(
                        KnowledgeChunk.resource_id == binding.resource_id,
                        KnowledgeChunk.workspace_id == request.workspace_id,
                        KnowledgeChunk.chunk_index == binding.chunk_index,
                    )
                    .execution_options(populate_existing=True)
                )
                if stored is None or stored != request.text:
                    raise ContextDenied("Embedding source text changed")
                text = stored
        reject_credentials(text, configured_secrets(self.settings))
        try:
            content = EmbeddingInput(
                text=text,
                purpose=request.purpose,
                dimensions=request.dimensions,
            )
        except ValueError as error:  # pydantic ValidationError
            raise ContextDenied("Embedding input is invalid") from error
        return MinimizedContext(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            source_ids=(),
            sensitivity=task.sensitivity,
            content=JSONDocument(text=content.model_dump_json()),
        )


class RegisteredEmbeddingGateway:
    """Production embedding path: the registered gateway selects and qualifies."""

    def __init__(
        self,
        settings: Settings,
        *,
        factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.settings, self.factory = settings, factory

    async def _runtime(self) -> GatewayRuntime:
        return await build_runtime(self.settings, self.factory)

    async def eligible(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        sensitivity: Sensitivity,
        text_length: int,
    ) -> bool:
        """Whether any qualified model exists right now. No provider call is made."""
        try:
            runtime = await self._runtime()
        except AIProviderNotConfigured:
            return False
        policy = runtime.store.operator_policy
        request = EmbeddingRequest(
            workspace_id=workspace_id,
            user_id=user_id,
            text="x",
            purpose="RETRIEVAL_QUERY",
            sensitivity=sensitivity,
        )
        task = _embedding_task(
            request,
            ProviderPolicy(
                workspace_id=workspace_id,
                user_id=user_id,
                revision=1,
                grants=policy.grants,
                allow_fallback=False,
                max_fallbacks=0,
            ),
        )
        try:
            snapshot = await runtime.store.snapshot(task)
        except (PermissionError, ValueError, AIProviderNotConfigured):
            return False
        bound = text_length + 4_096
        budget = min(task.max_cost, *(rule.max_cost for rule in snapshot.rules))
        return bool(
            rank_eligible(
                task,
                snapshot.registry,
                policy=snapshot.policy,
                available=snapshot.available,
                evaluations=snapshot.evaluations,
                input_bound=bound,
                remaining_budget=budget,
                preferred=None,
                weights=snapshot.weights,
            )
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingVector | None:
        """One paid attempt through the registered gateway, or ``None`` if none ran."""
        runtime = await self._runtime()
        policy = runtime.store.operator_policy
        task = _embedding_task(
            request,
            ProviderPolicy(
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                revision=1,
                grants=policy.grants,
                allow_fallback=False,
                max_fallbacks=0,
            ),
        )
        factory = self.factory
        try:
            result = await runtime.execute(
                task,
                context_builder=KnowledgeContext(
                    factory or _default_factory(),
                    settings=self.settings,
                    request=request,
                ),
                documents={},
                semantic_validator=validate_embedding_output,
            )
        except (GatewayUnavailable, ContextDenied):
            return None
        vector = EmbeddingOutput.model_validate_json(result.output.text).vector
        revision = await self._registry_revision(task.id)
        return EmbeddingVector(
            provider=result.provider.value,
            model=result.model,
            registry_revision=revision,
            embedding_version=EMBEDDING_VERSION,
            vector=normalize_vector(vector),
            cost_micros=micros(result.estimated_cost),
        )

    async def _registry_revision(self, task_id: UUID) -> str:
        """Exact registry revision from the gateway trace, never a caller guess."""
        async with (self.factory or _default_factory())() as database:
            revision = await database.scalar(
                select(AITaskRun.registry_revision)
                .where(AITaskRun.task_id == task_id)
                .order_by(AITaskRun.created_at.desc(), AITaskRun.id.desc())
                .limit(1)
            )
        if revision is None:
            raise SemanticUnavailable("The gateway trace has no registry revision")
        return str(revision)


def _default_factory() -> async_sessionmaker[AsyncSession]:
    from navox.db.session import get_session_factory

    return get_session_factory()


def registered_gateway(
    settings: Settings, *, factory: async_sessionmaker[AsyncSession] | None = None
) -> EmbeddingGateway | None:
    """The production gateway, or ``None`` when the registered route is off."""
    if settings.ai_provider.casefold().strip() != "automatic":
        return None
    return RegisteredEmbeddingGateway(settings, factory=factory)


async def reserve_attempt(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request_id: UUID,
    scope: Literal["QUERY", "DOCUMENT"],
    quota: int,
    resource_id: UUID | None = None,
    chunk_index: int | None = None,
    now: datetime | None = None,
) -> tuple[KnowledgeEmbeddingRequest, bool]:
    """Reserve exactly one paid attempt, or return the existing owned row.

    The caller's ``user`` row is locked first (the same order News uses) so the
    hourly count and the insert are serialized per user: two concurrent requests
    with different identifiers cannot both pass the quota check. The reservation
    is committed before this returns, so a duplicate identifier can never start a
    second paid call even when two requests race. ``created_at`` is the checked
    clock rather than a server default, so the window is deterministic.
    """
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
        raise SemanticUnavailable("Embedding reservation is unavailable for this account")
    existing = await database.scalar(
        select(KnowledgeEmbeddingRequest).where(
            KnowledgeEmbeddingRequest.workspace_id == workspace_id,
            KnowledgeEmbeddingRequest.user_id == user_id,
            KnowledgeEmbeddingRequest.request_id == request_id,
        )
    )
    if existing is not None:
        return existing, False
    used = int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeEmbeddingRequest)
            .where(
                KnowledgeEmbeddingRequest.user_id == user_id,
                KnowledgeEmbeddingRequest.created_at >= moment - SEMANTIC_QUOTA_WINDOW,
            )
        )
        or 0
    )
    if used >= quota:
        raise SemanticQuotaExceeded("Hourly semantic search quota is exhausted")
    database.add(
        KnowledgeEmbeddingRequest(
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request_id,
            scope=scope,
            status="RESERVED",
            resource_id=resource_id,
            chunk_index=chunk_index,
            created_at=moment,
        )
    )
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        raced = await database.scalar(
            select(KnowledgeEmbeddingRequest).where(
                KnowledgeEmbeddingRequest.workspace_id == workspace_id,
                KnowledgeEmbeddingRequest.user_id == user_id,
                KnowledgeEmbeddingRequest.request_id == request_id,
            )
        )
        if raced is None:
            raise
        return raced, False
    stored = await database.scalar(
        select(KnowledgeEmbeddingRequest)
        .where(
            KnowledgeEmbeddingRequest.workspace_id == workspace_id,
            KnowledgeEmbeddingRequest.user_id == user_id,
            KnowledgeEmbeddingRequest.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )
    if stored is None:  # pragma: no cover - the insert above just committed
        raise SemanticUnavailable("The embedding reservation was not stored")
    return stored, True


async def _finish_attempt(
    database: AsyncSession,
    row: KnowledgeEmbeddingRequest,
    *,
    status: str,
    reference: str | None = None,
    vector: EmbeddingVector | None = None,
    cost_micros: int | None = None,
    now: datetime | None = None,
) -> None:
    row.status = status
    row.reason = reference
    row.provider = vector.provider if vector is not None else None
    row.model = vector.model if vector is not None else None
    row.registry_revision = vector.registry_revision if vector is not None else None
    row.embedding_version = vector.embedding_version if vector is not None else None
    row.dimension = vector.dimension if vector is not None else None
    row.cost_micros = cost_micros
    row.finished_at = aware_utc(now) if now is not None else utc_now()
    await database.commit()


async def store_embedding(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    resource_id: UUID,
    chunk_index: int,
    source_content_hash: str,
    sensitivity: str,
    vector: EmbeddingVector,
    now: datetime | None = None,
) -> None:
    """Upsert one vector inside its exact namespace; the vector is already normalized."""
    moment = aware_utc(now) if now is not None else utc_now()
    row = await database.scalar(
        select(KnowledgeEmbedding).where(
            KnowledgeEmbedding.resource_id == resource_id,
            KnowledgeEmbedding.chunk_index == chunk_index,
            KnowledgeEmbedding.provider == vector.provider,
            KnowledgeEmbedding.model == vector.model,
            KnowledgeEmbedding.registry_revision == vector.registry_revision,
            KnowledgeEmbedding.embedding_version == vector.embedding_version,
            KnowledgeEmbedding.dimension == vector.dimension,
        )
    )
    if row is None:
        row = KnowledgeEmbedding(
            workspace_id=workspace_id,
            resource_id=resource_id,
            chunk_index=chunk_index,
            source_content_hash=source_content_hash,
            sensitivity=sensitivity,
            provider=vector.provider,
            model=vector.model,
            registry_revision=vector.registry_revision,
            embedding_version=vector.embedding_version,
            dimension=vector.dimension,
            vector=list(vector.vector),
            generated_at=moment,
        )
        database.add(row)
    else:
        row.workspace_id = workspace_id
        row.source_content_hash = source_content_hash
        row.sensitivity = sensitivity
        row.vector = list(vector.vector)
        row.generated_at = moment
    await database.commit()


async def embed_resource(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    request_id: UUID,
    settings: Settings,
    chunk_index: int = 0,
    gateway: EmbeddingGateway | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ResourceEmbeddingReport:
    """Embed exactly one bounded authorized chunk through the registered gateway.

    One durable document request buys at most one provider attempt, bound to an
    explicit ``chunk_index``. Later chunks are separate identifier-only requests
    under the same global user quota, so an aggregate ceiling cannot be exceeded
    by a single call. ``clock`` supplies the post-provider fence time; the fence
    always uses the current time, never the start-of-request instant.
    """
    moment = aware_utc(now) if now is not None else utc_now()
    if not settings.knowledge_enabled or not settings.knowledge_semantic_enabled:
        raise SemanticDisabled("Semantic embedding is not enabled")
    if chunk_index < 0 or chunk_index > MAX_CHUNK_INDEX:
        raise SemanticUnavailable("The requested chunk index is out of bounds")
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    context = await _document_context(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        resource_id=resource_id,
        chunk_index=chunk_index,
        now=moment,
    )
    if context is None:
        raise SemanticUnavailable("The resource is not currently embeddable")
    binding, sensitivity, chunk, chunks_total = context
    client = gateway or registered_gateway(settings)
    if client is None or not await client.eligible(
        workspace_id=workspace_id,
        user_id=user_id,
        sensitivity=sensitivity,
        text_length=len(chunk.text_content or ""),
    ):
        # No eligible model: no reservation and no paid attempt.
        raise SemanticUnavailable("No qualified embedding model is registered")
    row, created = await reserve_attempt(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request_id,
        scope="DOCUMENT",
        quota=settings.knowledge_semantic_hourly_quota,
        resource_id=resource_id,
        chunk_index=chunk_index,
        now=moment,
    )
    if not created:
        raise SemanticRequestReplay(row.status)
    text = chunk.text_content or ""
    try:
        vector = await client.embed(
            EmbeddingRequest(
                workspace_id=workspace_id,
                user_id=user_id,
                text=text,
                purpose="RETRIEVAL_DOCUMENT",
                sensitivity=sensitivity,
                binding=EmbeddingBinding(
                    resource_id=resource_id,
                    source_content_hash=binding.source_content_hash,
                    source_connection_id=binding.source_connection_id,
                    source_resource_id=binding.source_resource_id,
                    chunk_index=chunk_index,
                ),
            )
        )
    except (SemanticUnavailable, GatewayUnavailable, ContextDenied):
        await _finish_attempt(
            database, row, status="FAILED", reference=SEMANTIC_REASON_UNAVAILABLE, now=moment
        )
        raise SemanticUnavailable("The embedding provider failed") from None
    if vector is None:
        await _finish_attempt(
            database,
            row,
            status="UNAVAILABLE",
            reference=SEMANTIC_REASON_UNAVAILABLE,
            now=moment,
        )
        return ResourceEmbeddingReport(
            resource_id=resource_id,
            chunk_index=chunk_index,
            status="UNAVAILABLE",
            reference=SEMANTIC_REASON_UNAVAILABLE,
            key=None,
            chunks_embedded=0,
            chunks_total=chunks_total,
            cost_micros=None,
        )
    # Unknown cost stays unknown; the reserved ceiling is a separate bound and is
    # never reported as an observed price.
    observed_cost = vector.cost_micros
    try:
        normalized = EmbeddingVector(
            provider=vector.provider,
            model=vector.model,
            registry_revision=vector.registry_revision,
            embedding_version=vector.embedding_version,
            vector=normalize_vector(vector.vector),
            cost_micros=observed_cost,
        )
    except ValueError:
        await _finish_attempt(
            database,
            row,
            status="FAILED",
            reference=SEMANTIC_REASON_VECTOR_INVALID,
            cost_micros=observed_cost,
            now=moment,
        )
        raise SemanticUnavailable("The provider returned an unusable vector") from None
    # Post-call fence at the current time: authority, exclusions, revision and
    # classification must still hold, or the vector is discarded unpersisted.
    fence_time = aware_utc(clock()) if clock is not None else utc_now()
    current = await embedding_authority(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        binding=EmbeddingBinding(
            resource_id=resource_id,
            source_content_hash=binding.source_content_hash,
            source_connection_id=binding.source_connection_id,
            source_resource_id=binding.source_resource_id,
            chunk_index=chunk_index,
        ),
        now=fence_time,
    )
    if current is None or sensitivity_of(current) != sensitivity:
        await _finish_attempt(
            database,
            row,
            status="DISCARDED",
            reference=SEMANTIC_REASON_PARTIAL,
            cost_micros=observed_cost,
            now=moment,
        )
        return ResourceEmbeddingReport(
            resource_id=resource_id,
            chunk_index=chunk_index,
            status="DISCARDED",
            reference=SEMANTIC_REASON_PARTIAL,
            key=None,
            chunks_embedded=0,
            chunks_total=chunks_total,
            cost_micros=observed_cost,
        )
    await store_embedding(
        database,
        workspace_id=workspace_id,
        resource_id=resource_id,
        chunk_index=chunk_index,
        source_content_hash=binding.source_content_hash,
        sensitivity=sensitivity.value,
        vector=normalized,
        now=moment,
    )
    # One call covers one chunk: anything beyond it is explicit partial coverage.
    reference = SEMANTIC_REASON_PARTIAL if chunks_total > 1 else None
    await _finish_attempt(
        database,
        row,
        status="COMPLETED",
        reference=reference,
        vector=normalized,
        cost_micros=observed_cost,
        now=moment,
    )
    return ResourceEmbeddingReport(
        resource_id=resource_id,
        chunk_index=chunk_index,
        status="COMPLETED",
        reference=reference,
        key=normalized.key(),
        chunks_embedded=1,
        chunks_total=chunks_total,
        cost_micros=observed_cost,
    )


async def _document_context(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    resource_id: UUID,
    chunk_index: int,
    now: datetime,
) -> tuple[EmbeddingBinding, Sensitivity, KnowledgeChunk, int] | None:
    """Metadata fence first, then one bounded chunk read.

    Authority and classification columns are projected on their own; stored text
    is read only after ``embedding_authority`` has passed at the current time.
    """
    resource = await database.scalar(
        select(KnowledgeResource)
        .options(
            load_only(
                *RESOURCE_AUTHORITY_COLUMNS,
                KnowledgeResource.sensitivity,
            )
        )
        .where(
            KnowledgeResource.id == resource_id,
            KnowledgeResource.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    index_row = await database.scalar(
        select(KnowledgeResourceIndex)
        .options(
            load_only(
                KnowledgeResourceIndex.resource_id,
                KnowledgeResourceIndex.workspace_id,
                KnowledgeResourceIndex.source_content_hash,
                KnowledgeResourceIndex.index_state,
            )
        )
        .where(
            KnowledgeResourceIndex.resource_id == resource_id,
            KnowledgeResourceIndex.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        resource is None
        or resource.deleted_at is not None
        or index_row is None
        or index_row.index_state != "INDEXED"
        or index_row.source_content_hash is None
        or resource.source_resource_id is None
    ):
        return None
    binding = EmbeddingBinding(
        resource_id=resource_id,
        source_content_hash=index_row.source_content_hash,
        source_connection_id=resource.source_connection_id,
        source_resource_id=resource.source_resource_id,
        chunk_index=chunk_index,
    )
    sensitivity_value = await embedding_authority(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        binding=binding,
        now=now,
    )
    if sensitivity_value is None:
        return None
    # Derived text is read only after the fence above passed, and only for the
    # exactly one chunk this attempt will embed.
    chunk = await database.scalar(
        select(KnowledgeChunk)
        .where(
            KnowledgeChunk.resource_id == resource_id,
            KnowledgeChunk.workspace_id == workspace_id,
            KnowledgeChunk.chunk_index == chunk_index,
        )
        .execution_options(populate_existing=True)
    )
    if chunk is None or not (chunk.text_content or "").strip():
        return None
    chunks_total = int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.resource_id == resource_id,
                KnowledgeChunk.workspace_id == workspace_id,
            )
        )
        or 0
    )
    return binding, sensitivity_of(sensitivity_value), chunk, chunks_total


async def semantic_search(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request: SemanticSearchRequest,
    settings: Settings,
    now: datetime | None = None,
    gateway: EmbeddingGateway | None = None,
    record_recent: bool = True,
) -> SearchResponse:
    """One explicit paid semantic query, fused with lexical and structured ranks.

    The identifier reserves exactly one attempt before the gateway runs. Another
    call with the same identifier is refused instead of buying a second request,
    and a request without an eligible model never reserves or pays at all.
    """
    moment = aware_utc(now) if now is not None else utc_now()
    if not settings.knowledge_enabled or not settings.knowledge_semantic_enabled:
        raise SemanticDisabled("Semantic search is not enabled")
    await _require_current_account(database, workspace_id=workspace_id, user_id=user_id)
    needs_recent = record_recent
    try:
        reject_credentials(request.query, configured_secrets(settings))
    except ContextDenied as error:
        raise SemanticRejected(str(error)) from error
    client = gateway or registered_gateway(settings)
    eligible = client is not None and await client.eligible(
        workspace_id=workspace_id,
        user_id=user_id,
        sensitivity=Sensitivity.PERSONAL,
        text_length=len(request.query),
    )
    if not eligible:
        return await search_knowledge(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            request=request.as_search_request(),
            now=moment,
            settings=settings,
            semantic=SemanticEvidence(available=False, reference=SEMANTIC_REASON_UNAVAILABLE),
            record_recent=needs_recent,
        )
    assert client is not None  # narrowed by ``eligible`` above
    row, created = await reserve_attempt(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request.request_id,
        scope="QUERY",
        quota=settings.knowledge_semantic_hourly_quota,
        now=moment,
    )
    if not created:
        raise SemanticRequestReplay(row.status)
    try:
        vector = await client.embed(
            EmbeddingRequest(
                workspace_id=workspace_id,
                user_id=user_id,
                text=request.query,
                purpose="RETRIEVAL_QUERY",
                sensitivity=Sensitivity.PERSONAL,
            )
        )
    except (GatewayUnavailable, ContextDenied):
        vector = None
    if vector is None:
        await _finish_attempt(
            database, row, status="UNAVAILABLE", reference=SEMANTIC_REASON_UNAVAILABLE, now=moment
        )
        return await search_knowledge(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            request=request.as_search_request(),
            now=moment,
            settings=settings,
            semantic=SemanticEvidence(available=False, reference=SEMANTIC_REASON_UNAVAILABLE),
            record_recent=needs_recent,
        )
    try:
        normalized = EmbeddingVector(
            provider=vector.provider,
            model=vector.model,
            registry_revision=vector.registry_revision,
            embedding_version=vector.embedding_version,
            vector=normalize_vector(vector.vector),
            cost_micros=vector.cost_micros,
        )
    except ValueError:
        await _finish_attempt(
            database,
            row,
            status="FAILED",
            reference=SEMANTIC_REASON_VECTOR_INVALID,
            now=moment,
        )
        return await search_knowledge(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            request=request.as_search_request(),
            now=moment,
            settings=settings,
            semantic=SemanticEvidence(available=False, reference=SEMANTIC_REASON_VECTOR_INVALID),
            record_recent=needs_recent,
        )
    plan = _plan_for(request)
    filters = await load_exclusions(database, workspace_id=workspace_id, user_id=user_id)
    evidence = await semantic_retriever(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        plan=plan,
        filters=filters,
        now=moment,
        key=normalized.key(),
        query_vector=normalized.vector,
    )
    await _finish_attempt(
        database,
        row,
        status="COMPLETED",
        reference=evidence.reference,
        vector=normalized,
        cost_micros=normalized.cost_micros,
        now=moment,
    )
    return await search_knowledge(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        request=request.as_search_request(),
        now=moment,
        settings=settings,
        semantic=evidence,
        record_recent=needs_recent,
    )


def _plan_for(request: SemanticSearchRequest) -> RetrievalPlan:
    return interpret(request.as_search_request())


__all__ = [
    "EMBEDDING_VERSION",
    "SEMANTIC_COST_MICROS",
    "SEMANTIC_MAX_COST",
    "SEMANTIC_QUOTA_WINDOW",
    "EmbeddingBinding",
    "EmbeddingGateway",
    "EmbeddingRequest",
    "EmbeddingVector",
    "KnowledgeContext",
    "RegisteredEmbeddingGateway",
    "ResourceEmbeddingReport",
    "SemanticDisabled",
    "SemanticQuotaExceeded",
    "SemanticRejected",
    "SemanticRequestReplay",
    "SemanticUnavailable",
    "embed_resource",
    "embedding_authority",
    "micros",
    "normalize_vector",
    "registered_gateway",
    "reserve_attempt",
    "semantic_search",
    "sensitivity_of",
    "store_embedding",
]
