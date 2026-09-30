"""Semantic story joins for unindexed News items under a reviewed operator policy.

Exact URL/copy identity stays authoritative and unchanged. A semantic join is
only proposed for an item that has no story membership yet, only inside the one
embedding namespace an operator deployment policy binds, and only when every
candidate in scope has a current vector, current original+current source rights
and a fresh reviewed feature record. Missing, stale, truncated, tied or
incompatible candidates stay UNCERTAIN, and the item falls back to exact
dedupe/new-story indexing, so ingestion can never leave an item invisible.

Paid work is durable and bounded: one committed reservation per item revision
and namespace, a per-user hourly quota and a fixed five-cent ceiling per call.
The embedding text is only permitted headline+snippet, it is never persisted,
and the exact item revision, digest and rights fingerprint are re-read before
the call and again after it before any vector is stored or any membership is
written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.exc import StaleDataError

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
from navox.db.models import User, WorkspaceMembership
from navox.db.news import (
    NewsClusterEmbedding,
    NewsClusterEmbeddingRequest,
    NewsClusterFeature,
    NewsContentRights,
    NewsItem,
    NewsSource,
    NewsStory,
    NewsStoryItem,
)
from navox.intelligence.contracts import SourceDocument
from navox.knowledge.embeddings import micros, normalize_vector
from navox.knowledge.retrieval import cosine_similarity, parse_stored_vector
from navox.news.cluster_contracts import (
    DeploymentPolicy,
    EmbeddingNamespace,
    EmbeddingNamespaceRef,
    FeatureCitation,
    PolicyStatus,
    ReviewedFeatureRecord,
    active_policy,
    policy_digest,
)
from navox.news.cluster_selection import (
    CandidateSet,
    ClusterCandidate,
    ClusterSelection,
    SelectionPolicy,
    select_candidate,
)
from navox.news.clustering import ClusterDecision, ClusterSignals, duplicate_keys
from navox.news.contracts import (
    NewsError,
    NewsItemRead,
    SourceDefinition,
    aware_utc,
    stored_utc,
)
from navox.news.evidence import permitted_summary_item, quote_at
from navox.news.ingestion import item_view
from navox.news.registry import current_rights, owned_source, require_definition
from navox.news.rights import (
    Operation,
    policy_for,
    require_item_retention,
    require_operation,
)

# The registered embedding artifact. News clustering reuses the SPEC-005
# EMBEDDING profile, prompt and schema instead of introducing a second one.
NEWS_CLUSTER_ARTIFACT = "knowledge_embedding@v1"
NEWS_CLUSTER_PIPELINE_VERSION = "news-cluster-embedding.v1"

# Hard per-attempt ceiling: a code constant, never operator configuration.
NEWS_CLUSTER_MAX_COST = Decimal("0.05")
NEWS_CLUSTER_COST_MICROS = 50_000
NEWS_CLUSTER_QUOTA_WINDOW = timedelta(hours=1)
# Scope window for candidate retrieval. The temporal signal uses known event
# time; this window only bounds the cheap pre-filter.
NEWS_CLUSTER_TEMPORAL_HORIZON = timedelta(days=3)
NEWS_CLUSTER_CANDIDATE_LIMIT = 100
# One bounded batch per activity run; the hourly quota is the real ceiling.
NEWS_CLUSTER_ACTIVITY_BOUND = 50
NEWS_CLUSTER_TEXT_LIMIT = 20_000
NEWS_CLUSTER_TASK_BOUND = NEWS_CLUSTER_TEXT_LIMIT + 4_096

STATUS_RESERVED = "RESERVED"
STATUS_COMPLETED = "COMPLETED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_FAILED = "FAILED"
STATUS_DISCARDED = "DISCARDED"

DECISION_SEMANTIC_JOIN = "SEMANTIC_JOIN"
CHANGE_SEMANTIC_MEMBERSHIP = "SEMANTIC_MEMBERSHIP"


class ClusteringDisabled(ValueError):
    """Raised when the paid path is reached while its flag or policy is off."""


class ClusteringUnavailable(ValueError):
    """Raised when an embedding cannot be produced, validated or safely stored."""


class ClusteringRequestReplay(ValueError):
    """Raised when a request identifier was already reserved; nothing is bought."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Clustering request {status.lower()}")


class ClusteringQuotaExceeded(ValueError):
    """Raised when the caller's hourly paid-attempt quota is already spent."""


@dataclass(frozen=True)
class NewsEmbeddingBinding:
    """Identifier-and-digest pointer re-checked before and after the paid call."""

    news_item_id: UUID
    item_revision: int
    item_digest: str
    rights_fingerprint: str


@dataclass(frozen=True)
class NewsEmbeddingRequest:
    """One embedding input. The text never appears in a persisted row."""

    workspace_id: UUID
    user_id: UUID
    text: str
    binding: NewsEmbeddingBinding


@dataclass(frozen=True)
class NewsVector:
    """One validated vector with the exact namespace that produced it."""

    namespace: EmbeddingNamespace
    vector: tuple[float, ...]
    cost_micros: int | None = None


@dataclass(frozen=True)
class ClusterAttempt:
    """Honest, content-free outcome of one clustering decision."""

    news_item_id: UUID
    status: str
    reason: str
    policy_reference: str | None = None
    namespace_digest: str | None = None
    candidate_count: int = 0
    paid_attempts: int = 0
    cost_micros: int | None = None


@dataclass(frozen=True)
class ClusterProposal:
    """A proposed join. The caller must re-validate before writing anything."""

    news_item_id: UUID
    item_revision: int
    story_id: UUID
    policy_reference: str
    # Immutable identity of the exact policy that authorized this proposal: a
    # reference is a label and can be reused by a different policy.
    policy_digest: str
    namespace_digest: str
    score: float


@dataclass
class _AuthorizedItem:
    item: NewsItem
    view: NewsItemRead
    source: NewsSource
    definition: SourceDefinition
    original: NewsContentRights
    current: NewsContentRights
    fingerprint: str


@dataclass
class _SelectionResult:
    candidate_set: CandidateSet
    selection: ClusterSelection
    candidates: int
    incomplete: bool
    truncated: bool
    reasons: list[str] = field(default_factory=list)


class NewsEmbeddingGateway(Protocol):
    """Provider-neutral paid embedding surface. Selection stays in the gateway."""

    async def qualify(
        self, *, workspace_id: UUID, user_id: UUID, text_length: int
    ) -> EmbeddingNamespaceRef | None: ...

    async def embed(self, request: NewsEmbeddingRequest) -> NewsVector | None: ...


def item_text(view: NewsItemRead) -> str:
    """Only the permitted headline and stored snippet; never full text."""
    if not view.description:
        return view.headline[:NEWS_CLUSTER_TEXT_LIMIT]
    return (f"{view.headline}\n{view.description}")[:NEWS_CLUSTER_TEXT_LIMIT]


def rights_fingerprint(
    *,
    view: NewsItemRead,
    definition: SourceDefinition,
    source: NewsSource,
    original: NewsContentRights,
    current: NewsContentRights,
) -> str:
    """Bind the exact reviewed policy, catalog revision and retention deadline."""
    payload = {
        "source_catalog": definition.fingerprint,
        "rights_version": source.rights_version,
        "original_policy": original.policy_digest,
        "current_policy": current.policy_digest,
        "expires_at": stored_utc(view.expires_at).isoformat(),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    first, second = set(left), set(right)
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def temporal_proximity(left: datetime, right: datetime, *, horizon: timedelta) -> float:
    """Known event times only, with a bounded linear decay and no future credit."""
    delta = abs(aware_utc(left) - aware_utc(right))
    if horizon <= timedelta(0):
        return 0.0
    return max(0.0, 1.0 - delta / horizon)


def _event_time(feature: ReviewedFeatureRecord, view: NewsItemRead) -> datetime:
    if feature.event_time is not None:
        return stored_utc(feature.event_time)
    if view.event_started_at is not None:
        return stored_utc(view.event_started_at)
    return stored_utc(view.published_at)


def cluster_signals(
    *,
    query_feature: ReviewedFeatureRecord,
    candidate_feature: ReviewedFeatureRecord,
    query_view: NewsItemRead,
    candidate_view: NewsItemRead,
    query_vector: Sequence[float],
    candidate_vector: Sequence[float],
    horizon: timedelta = NEWS_CLUSTER_TEMPORAL_HORIZON,
) -> ClusterSignals:
    """The exact SPEC-006 weighted signals from reviewed identities and vectors.

    Word overlap is never used as semantic similarity and a capitalized string is
    never promoted to a resolved entity: entity and geography signals come only
    from reviewer-cited canonical identifiers.
    """
    similarity = cosine_similarity(tuple(query_vector), tuple(candidate_vector))
    incompatible = bool(
        query_feature.event_identity
        and candidate_feature.event_identity
        and query_feature.event_identity != candidate_feature.event_identity
    )
    if (
        query_view.event_started_at is not None
        and candidate_view.event_started_at is not None
        and stored_utc(query_view.event_started_at) != stored_utc(candidate_view.event_started_at)
    ):
        incompatible = True
    return ClusterSignals(
        semantic_similarity=max(0.0, min(1.0, similarity or 0.0)),
        entity_overlap=_jaccard(query_feature.entity_ids, candidate_feature.entity_ids),
        temporal_proximity=temporal_proximity(
            _event_time(query_feature, query_view),
            _event_time(candidate_feature, candidate_view),
            horizon=horizon,
        ),
        geographic_overlap=_jaccard(query_feature.geographic_ids, candidate_feature.geographic_ids),
        event_type_similarity=(
            1.0
            if query_feature.event_type and query_feature.event_type == candidate_feature.event_type
            else 0.0
        ),
        topic_overlap=_jaccard(query_view.categories, candidate_view.categories),
        incompatible_events=incompatible,
    )


async def authorized_item(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    news_item_id: UUID,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
    lock: bool = False,
) -> _AuthorizedItem:
    """Owner, membership, pause, catalog, original+current rights and retention."""
    account = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    if lock:
        account = account.with_for_update()
    user = await database.scalar(account)
    membership = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    if user is None or membership is None or user.agent_paused:
        raise NewsError("news_disabled")
    item_query = (
        select(NewsItem)
        .where(
            NewsItem.id == news_item_id,
            NewsItem.workspace_id == workspace_id,
            NewsItem.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        item_query = item_query.with_for_update()
    item = await database.scalar(item_query)
    if item is None:
        raise NewsError("item_unavailable")
    source = await database.scalar(
        select(NewsSource)
        .where(
            NewsSource.id == item.source_id,
            NewsSource.workspace_id == workspace_id,
            NewsSource.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise NewsError("source_unavailable")
    definition = require_definition(source, dict(definitions))
    original = await database.get(NewsContentRights, item.rights_profile_id, populate_existing=True)
    if original is None or original.source_id != source.id:
        raise NewsError("rights_denied")
    current = await current_rights(database, source)
    original_policy = policy_for(original, now=now)
    current_policy = policy_for(current, now=now)
    require_operation(Operation.METADATA, original_policy, current_policy)
    require_item_retention(item, original_policy, current_policy, now=now)
    view = await item_view(database, item, dict(definitions), now=now)
    return _AuthorizedItem(
        item=item,
        view=view,
        source=source,
        definition=definition,
        original=original,
        current=current,
        fingerprint=rights_fingerprint(
            view=view,
            definition=definition,
            source=source,
            original=original,
            current=current,
        ),
    )


async def current_feature(
    database: AsyncSession,
    authorized: _AuthorizedItem,
    *,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
) -> ReviewedFeatureRecord | None:
    """The reviewed feature record that still binds this exact item revision.

    The record only applies while the item is still permitted for summary
    evidence and every cited span still quotes the current permitted text, so a
    revoked, narrowed or rebuilt item can never keep a reviewed identity.
    """
    row = await database.scalar(
        select(NewsClusterFeature)
        .where(
            NewsClusterFeature.news_item_id == authorized.item.id,
            NewsClusterFeature.item_revision == authorized.item.revision,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    try:
        citations = tuple(FeatureCitation.model_validate(value) for value in (row.citations or ()))
        record = ReviewedFeatureRecord(
            news_item_id=row.news_item_id,
            item_revision=row.item_revision,
            item_digest=row.item_digest,
            rights_fingerprint=row.rights_fingerprint,
            source_id=row.source_id,
            language=row.language,
            entity_ids=tuple(row.entity_ids or ()),
            geographic_ids=tuple(row.geographic_ids or ()),
            event_type=row.event_type,
            event_identity=row.event_identity,
            event_time=row.event_time,
            citations=citations,
            reviewer=row.reviewer,
            review_reference=row.review_reference,
            reviewed_at=stored_utc(row.reviewed_at),
            expires_at=stored_utc(row.expires_at),
        )
    except ValueError:
        return None
    if (
        record.item_digest != authorized.item.content_digest
        or record.rights_fingerprint != authorized.fingerprint
        or record.source_id != authorized.item.source_id
        or record.language != authorized.view.language
        or not record.reviewed_at <= now < record.expires_at
    ):
        return None
    for citation in record.citations:
        if not await _citation_holds(
            database,
            record=citation,
            workspace_id=authorized.item.workspace_id,
            user_id=authorized.item.user_id,
            definitions=definitions,
            now=now,
        ):
            return None
    return record


async def _citation_holds(
    database: AsyncSession,
    *,
    record: FeatureCitation,
    workspace_id: UUID,
    user_id: UUID,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
) -> bool:
    """Whether one cited span still quotes permitted, current, unaltered text."""
    try:
        _, view, _source = await permitted_summary_item(
            database,
            record.span.item_id,
            dict(definitions),
            workspace_id=workspace_id,
            user_id=user_id,
            now=now,
        )
        return bool(quote_at(view, record.span))
    except NewsError:
        return False


async def record_feature(
    database: AsyncSession,
    *,
    record: ReviewedFeatureRecord,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
) -> None:
    """Internal trusted review boundary. Never reachable from a model or request.

    The reviewer attests the identities; the server recomputes the retained
    digest and rights fingerprint from the current item, so a submitted
    fingerprint cannot authorize a stale or a revoked item. Each identity value
    must quote an exact permitted span of this item revision.
    """
    stored = await database.get(NewsItem, record.news_item_id, populate_existing=True)
    if stored is None:
        raise NewsError("item_unavailable")
    workspace_id, user_id = stored.workspace_id, stored.user_id
    if record.reviewed_at > now:
        # A review cannot be written before it happened.
        raise NewsError("invalid_reference")
    if not record.reviewed_at <= now < record.expires_at:
        raise NewsError("stale_evidence")
    authorized = await authorized_item(
        database,
        workspace_id=workspace_id,
        user_id=user_id,
        news_item_id=record.news_item_id,
        definitions=definitions,
        now=now,
        lock=True,
    )
    if (
        record.item_revision != authorized.item.revision
        or record.item_digest != authorized.item.content_digest
        or record.rights_fingerprint != authorized.fingerprint
        or record.source_id != authorized.item.source_id
        or record.language != authorized.view.language
    ):
        raise NewsError("stale_evidence")
    try:
        _, view, _source = await permitted_summary_item(
            database,
            record.news_item_id,
            dict(definitions),
            workspace_id=workspace_id,
            user_id=user_id,
            now=now,
        )
    except NewsError as error:
        raise NewsError(error.code) from None
    for citation in record.citations:
        try:
            quote_at(view, citation.span)
        except NewsError:
            raise NewsError("invalid_evidence") from None
    row = await database.scalar(
        select(NewsClusterFeature)
        .where(
            NewsClusterFeature.news_item_id == record.news_item_id,
            NewsClusterFeature.item_revision == record.item_revision,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        row = NewsClusterFeature(
            workspace_id=workspace_id,
            user_id=user_id,
            news_item_id=record.news_item_id,
            item_revision=record.item_revision,
        )
        database.add(row)
    row.item_digest = record.item_digest
    row.rights_fingerprint = record.rights_fingerprint
    row.source_id = record.source_id
    row.language = record.language
    row.entity_ids = list(record.entity_ids)
    row.geographic_ids = list(record.geographic_ids)
    row.event_type = record.event_type
    row.event_identity = record.event_identity
    row.event_time = record.event_time
    row.citations = [citation.model_dump(mode="json") for citation in record.citations]
    row.reviewer = record.reviewer
    row.review_reference = record.review_reference
    row.reviewed_at = record.reviewed_at
    row.expires_at = record.expires_at
    await database.flush()


def _news_embedding_task(*, workspace_id: UUID, user_id: UUID, policy: ProviderPolicy) -> AITask:
    return AITask(
        workspace_id=workspace_id,
        user_id=user_id,
        task_type=TaskType.EMBED,
        profile=Profile.EMBEDDING,
        capability_requirements=frozenset({Capability.EMBEDDINGS}),
        output_schema=EMBEDDING,
        prompt=EMBEDDING,
        sensitivity=Sensitivity.PERSONAL,
        latency_class=LatencyClass.BACKGROUND,
        quality_class=QualityClass.STANDARD,
        max_cost=NEWS_CLUSTER_MAX_COST,
        max_output_tokens=1,
        provider_policy=policy,
    )


def validate_embedding_output(value: object) -> None:
    """Semantic validator used by the gateway; zero/invalid vectors are rejected."""
    EmbeddingOutput.model_validate(value)


class NewsItemEmbeddingContext:
    """Gateway authorizer that re-reads the exact News item on every build.

    A fresh session is opened per build, so a cached object can never satisfy the
    pre-call or post-call fence, and the fence always runs at the real current
    time. The minimized text is rebuilt from the current permitted headline and
    snippet, so a changed revision, digest, rights fingerprint or classification
    is rejected instead of embedded. ``KnowledgeContext`` is deliberately not
    reused: a News item is not a connected knowledge resource.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        request: NewsEmbeddingRequest,
        definitions: Mapping[str, SourceDefinition],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.factory, self.settings = factory, settings
        self.request, self.definitions, self.clock = request, definitions, clock

    async def build(
        self,
        task: AITask,
        documents: Mapping[UUID, SourceDocument],
        *,
        user_request: str = "",
    ) -> MinimizedContext:
        request = self.request
        if (
            not self.settings.news_feed_enabled
            or not self.settings.news_semantic_clustering_enabled
        ):
            raise ContextDenied("Semantic clustering is not enabled")
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
        if (task.workspace_id, task.user_id) != (request.workspace_id, request.user_id):
            raise ContextDenied("Embedding context scope mismatch")
        if task.sensitivity is not Sensitivity.PERSONAL:
            raise ContextDenied("News embedding context requires its own sensitivity")
        now = aware_utc(self.clock()) if self.clock is not None else datetime.now(UTC)
        async with self.factory() as database:
            try:
                authorized = await authorized_item(
                    database,
                    workspace_id=request.workspace_id,
                    user_id=request.user_id,
                    news_item_id=request.binding.news_item_id,
                    definitions=self.definitions,
                    now=now,
                )
            except NewsError as error:
                raise ContextDenied("News item is unavailable") from error
            if (
                authorized.item.revision != request.binding.item_revision
                or authorized.item.content_digest != request.binding.item_digest
                or authorized.fingerprint != request.binding.rights_fingerprint
            ):
                raise ContextDenied("News item changed before embedding")
            text = item_text(authorized.view)
            if text != request.text:
                raise ContextDenied("News text changed before embedding")
        reject_credentials(text, configured_secrets(self.settings))
        try:
            content = EmbeddingInput(text=text, purpose="RETRIEVAL_DOCUMENT", dimensions=None)
        except ValueError as error:
            raise ContextDenied("Embedding input is invalid") from error
        return MinimizedContext(
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            source_ids=(request.binding.news_item_id,),
            sensitivity=Sensitivity.PERSONAL,
            content=JSONDocument(text=content.model_dump_json()),
        )


class RegisteredNewsEmbeddingGateway:
    """Production embedding path: the registered gateway selects and qualifies."""

    def __init__(
        self,
        settings: Settings,
        *,
        definitions: Mapping[str, SourceDefinition],
        factory: async_sessionmaker[AsyncSession] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings, self.definitions = settings, definitions
        self.factory, self.clock = factory, clock

    async def _runtime(self) -> GatewayRuntime:
        return await build_runtime(self.settings, self.factory)

    async def qualify(
        self, *, workspace_id: UUID, user_id: UUID, text_length: int
    ) -> EmbeddingNamespaceRef | None:
        """Whether any qualified model exists right now. No provider call is made."""
        try:
            runtime = await self._runtime()
        except AIProviderNotConfigured:
            return None
        policy = runtime.store.operator_policy
        task = _news_embedding_task(
            workspace_id=workspace_id,
            user_id=user_id,
            policy=ProviderPolicy(
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
            return None
        try:
            budget = min(task.max_cost, *(rule.max_cost for rule in snapshot.rules))
            ranked = rank_eligible(
                task,
                snapshot.registry,
                policy=snapshot.policy,
                available=snapshot.available,
                evaluations=snapshot.evaluations,
                input_bound=text_length + 4_096,
                remaining_budget=budget,
                preferred=None,
                weights=snapshot.weights,
            )
        except (ValueError, KeyError):
            # An inconsistent registry is "no qualified model", never a crash in
            # the caller's ingestion path.
            return None
        if not ranked:
            return None
        model = ranked[0][0]
        return EmbeddingNamespaceRef(
            provider=model.reference.provider.value,
            model=model.reference.model,
            registry_revision=str(snapshot.registry.revision),
            artifact=NEWS_CLUSTER_ARTIFACT,
            pipeline_version=NEWS_CLUSTER_PIPELINE_VERSION,
        )

    async def embed(self, request: NewsEmbeddingRequest) -> NewsVector | None:
        """One paid attempt through the registered gateway, or ``None`` if none ran."""
        try:
            runtime = await self._runtime()
            policy = runtime.store.operator_policy
            task = _news_embedding_task(
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                policy=ProviderPolicy(
                    workspace_id=request.workspace_id,
                    user_id=request.user_id,
                    revision=1,
                    grants=policy.grants,
                    allow_fallback=False,
                    max_fallbacks=0,
                ),
            )
            factory = self.factory
            if factory is None:
                from navox.db.session import get_session_factory

                factory = get_session_factory()
            result = await runtime.execute(
                task,
                context_builder=NewsItemEmbeddingContext(
                    factory,
                    settings=self.settings,
                    request=request,
                    definitions=self.definitions,
                    clock=self.clock,
                ),
                documents={},
                semantic_validator=validate_embedding_output,
            )
            raw = EmbeddingOutput.model_validate_json(result.output.text).vector
            revision = await self._registry_revision(task.id)
            vector = normalize_vector(raw)
        except (
            AIProviderNotConfigured,
            GatewayUnavailable,
            ContextDenied,
            ClusteringUnavailable,
            ValueError,
        ):
            # Known unavailable or invalid paid-path outcomes. Cancellation is not
            # an ``Exception`` and still propagates to the workflow.
            return None
        return NewsVector(
            namespace=EmbeddingNamespace(
                provider=result.provider.value,
                model=result.model,
                registry_revision=revision,
                artifact=NEWS_CLUSTER_ARTIFACT,
                pipeline_version=NEWS_CLUSTER_PIPELINE_VERSION,
                dimension=len(vector),
            ),
            vector=vector,
            cost_micros=micros(result.estimated_cost),
        )

    async def _registry_revision(self, task_id: UUID) -> str:
        """Exact registry revision from the gateway trace, never a caller guess."""
        factory = self.factory
        if factory is None:
            from navox.db.session import get_session_factory

            factory = get_session_factory()
        async with factory() as database:
            revision = await database.scalar(
                select(AITaskRun.registry_revision)
                .where(AITaskRun.task_id == task_id)
                .order_by(AITaskRun.created_at.desc(), AITaskRun.id.desc())
                .limit(1)
            )
        if revision is None:
            raise ClusteringUnavailable("The gateway trace has no registry revision")
        return str(revision)


async def reserve_attempt(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    request_id: UUID,
    news_item_id: UUID,
    item_revision: int,
    namespace: EmbeddingNamespaceRef,
    quota: int,
    now: datetime,
) -> tuple[NewsClusterEmbeddingRequest, bool]:
    """One durable paid attempt, committed before any provider work happens."""
    moment = aware_utc(now)
    user = await database.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    membership = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    if user is None or membership is None or user.agent_paused:
        raise ClusteringUnavailable("The account cannot reserve an embedding attempt")
    existing = await database.scalar(
        select(NewsClusterEmbeddingRequest)
        .where(
            NewsClusterEmbeddingRequest.workspace_id == workspace_id,
            NewsClusterEmbeddingRequest.user_id == user_id,
            NewsClusterEmbeddingRequest.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return existing, False
    duplicate = await database.scalar(
        select(NewsClusterEmbeddingRequest)
        .where(
            NewsClusterEmbeddingRequest.workspace_id == workspace_id,
            NewsClusterEmbeddingRequest.user_id == user_id,
            NewsClusterEmbeddingRequest.news_item_id == news_item_id,
            NewsClusterEmbeddingRequest.item_revision == item_revision,
            NewsClusterEmbeddingRequest.namespace_digest == namespace.digest,
        )
        .execution_options(populate_existing=True)
    )
    if duplicate is not None:
        return duplicate, False
    used = int(
        await database.scalar(
            select(func.count())
            .select_from(NewsClusterEmbeddingRequest)
            .where(
                NewsClusterEmbeddingRequest.workspace_id == workspace_id,
                NewsClusterEmbeddingRequest.user_id == user_id,
                NewsClusterEmbeddingRequest.created_at >= moment - NEWS_CLUSTER_QUOTA_WINDOW,
            )
        )
        or 0
    )
    if used >= quota:
        raise ClusteringQuotaExceeded("Hourly clustering quota is exhausted")
    database.add(
        NewsClusterEmbeddingRequest(
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=request_id,
            news_item_id=news_item_id,
            item_revision=item_revision,
            namespace_digest=namespace.digest,
            namespace_reference=namespace.model_dump(mode="json"),
            status=STATUS_RESERVED,
            created_at=moment,
        )
    )
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        raced = await database.scalar(
            select(NewsClusterEmbeddingRequest)
            .where(
                NewsClusterEmbeddingRequest.workspace_id == workspace_id,
                NewsClusterEmbeddingRequest.user_id == user_id,
                NewsClusterEmbeddingRequest.request_id == request_id,
            )
            .execution_options(populate_existing=True)
        )
        if raced is None:
            raced = await database.scalar(
                select(NewsClusterEmbeddingRequest)
                .where(
                    NewsClusterEmbeddingRequest.workspace_id == workspace_id,
                    NewsClusterEmbeddingRequest.user_id == user_id,
                    NewsClusterEmbeddingRequest.news_item_id == news_item_id,
                    NewsClusterEmbeddingRequest.item_revision == item_revision,
                    NewsClusterEmbeddingRequest.namespace_digest == namespace.digest,
                )
                .execution_options(populate_existing=True)
            )
        if raced is None:
            raise
        return raced, False
    stored = await database.scalar(
        select(NewsClusterEmbeddingRequest)
        .where(
            NewsClusterEmbeddingRequest.workspace_id == workspace_id,
            NewsClusterEmbeddingRequest.user_id == user_id,
            NewsClusterEmbeddingRequest.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )
    if stored is None:  # pragma: no cover - the insert above just committed
        raise ClusteringUnavailable("The embedding reservation was not stored")
    return stored, True


async def finish_attempt(
    database: AsyncSession,
    row: NewsClusterEmbeddingRequest,
    *,
    status: str,
    reason: str | None = None,
    vector: NewsVector | None = None,
    cost_micros: int | None = None,
    now: datetime | None = None,
) -> None:
    row.status = status
    row.reason = reason
    row.provider = vector.namespace.provider if vector is not None else None
    row.model = vector.namespace.model if vector is not None else None
    row.registry_revision = vector.namespace.registry_revision if vector is not None else None
    row.dimension = vector.namespace.dimension if vector is not None else None
    row.cost_micros = cost_micros
    row.finished_at = aware_utc(now) if now is not None else datetime.now(UTC)
    try:
        await database.commit()
    except StaleDataError:
        # A revocation, disconnect or member removal deleted the reservation with
        # its item while the paid call was in flight. There is nothing to record
        # and nothing to retry, so the attempt simply stops here.
        await database.rollback()


async def stored_vector(
    database: AsyncSession,
    *,
    news_item_id: UUID,
    item_revision: int,
    namespace_digest: str,
) -> tuple[float, ...] | None:
    """One stored vector in an exact namespace, or ``None`` when unusable."""
    row = await stored_embedding(
        database,
        news_item_id=news_item_id,
        item_revision=item_revision,
        namespace_digest=namespace_digest,
    )
    if row is None:
        return None
    return parse_stored_vector(row.vector, row.dimension)


async def stored_embedding(
    database: AsyncSession,
    *,
    news_item_id: UUID,
    item_revision: int,
    namespace_digest: str,
) -> NewsClusterEmbedding | None:
    """The stored row, so its revision, digest and rights binding can be checked."""
    row: NewsClusterEmbedding | None = await database.scalar(
        select(NewsClusterEmbedding)
        .where(
            NewsClusterEmbedding.news_item_id == news_item_id,
            NewsClusterEmbedding.item_revision == item_revision,
            NewsClusterEmbedding.namespace_digest == namespace_digest,
        )
        .execution_options(populate_existing=True)
    )
    return row


def _namespace_matches(stored: EmbeddingNamespace, qualified: EmbeddingNamespaceRef) -> bool:
    return (
        stored.reference.provider == qualified.provider
        and stored.reference.model == qualified.model
        and stored.reference.registry_revision == qualified.registry_revision
        and stored.reference.artifact == qualified.artifact
        and stored.reference.pipeline_version == qualified.pipeline_version
    )


def _stored_namespace_matches(row: NewsClusterEmbedding, namespace: EmbeddingNamespaceRef) -> bool:
    """Check the persisted namespace fields, not only the digest column."""
    return (
        row.provider == namespace.provider
        and row.model == namespace.model
        and row.registry_revision == namespace.registry_revision
        and row.artifact == namespace.artifact
        and row.pipeline_version == namespace.pipeline_version
        and row.namespace_digest == namespace.digest
    )


class SemanticClusterer:
    """Bounded semantic join engine for one enabled, reviewed deployment policy."""

    def __init__(
        self,
        settings: Settings,
        *,
        policy: DeploymentPolicy,
        definitions: Mapping[str, SourceDefinition],
        gateway: NewsEmbeddingGateway | None = None,
        clock: Callable[[], datetime] | None = None,
        request_id_factory: Callable[[], UUID] | None = None,
        candidate_limit: int = NEWS_CLUSTER_CANDIDATE_LIMIT,
    ) -> None:
        self.settings, self.policy = settings, policy
        self.definitions = definitions
        self.gateway = gateway
        self.clock = clock
        self.request_id_factory = request_id_factory or uuid4
        self.candidate_limit = candidate_limit
        self.attempts: list[ClusterAttempt] = []

    def _client(self) -> NewsEmbeddingGateway:
        if self.gateway is None:
            self.gateway = RegisteredNewsEmbeddingGateway(
                self.settings,
                definitions=self.definitions,
                clock=self.clock,
            )
        return self.gateway

    def _now(self) -> datetime:
        return aware_utc(self.clock()) if self.clock is not None else datetime.now(UTC)

    def _status(self) -> PolicyStatus:
        return active_policy(settings=self.settings, definitions=self.definitions, now=self._now())

    def _record(self, attempt: ClusterAttempt) -> None:
        self.attempts.append(attempt)

    async def _scoped_source_ids(
        self, database: AsyncSession, *, workspace_id: UUID, user_id: UUID
    ) -> tuple[UUID, ...]:
        return tuple(
            await database.scalars(
                select(NewsSource.id).where(
                    NewsSource.workspace_id == workspace_id,
                    NewsSource.user_id == user_id,
                    NewsSource.source_key.in_(self.policy.source_keys),
                    NewsSource.status == "active",
                )
            )
        )

    async def _ensure_vector(
        self,
        database: AsyncSession,
        authorized: _AuthorizedItem,
        *,
        now: datetime,
    ) -> tuple[NewsVector | None, int, int | None]:
        """Return one stored or newly purchased vector for the exact item revision.

        Returns ``(vector, paid_attempts, cost_micros)``. No attempt is reserved
        when the item is already embedded, when no qualified model exists or when
        the qualified namespace is not the namespace the policy binds.
        """
        # The binding is snapshotted as plain values: a re-read below refreshes
        # the same ORM instance in place, so comparing against it would be
        # vacuous and would let a changed revision keep a paid vector.
        binding = NewsEmbeddingBinding(
            news_item_id=authorized.item.id,
            item_revision=authorized.item.revision,
            item_digest=authorized.item.content_digest,
            rights_fingerprint=authorized.fingerprint,
        )
        namespace = self.policy.namespace
        previous = await stored_embedding(
            database,
            news_item_id=binding.news_item_id,
            item_revision=binding.item_revision,
            namespace_digest=namespace.digest,
        )
        if previous is not None:
            stored_namespace = EmbeddingNamespace(
                provider=previous.provider,
                model=previous.model,
                registry_revision=previous.registry_revision,
                artifact=previous.artifact,
                pipeline_version=previous.pipeline_version,
                dimension=previous.dimension,
            )
            parsed = parse_stored_vector(previous.vector, previous.dimension)
            if (
                parsed is not None
                and previous.item_digest == binding.item_digest
                and previous.rights_fingerprint == binding.rights_fingerprint
                and _stored_namespace_matches(previous, namespace)
                and _namespace_matches(stored_namespace, namespace)
            ):
                return NewsVector(namespace=stored_namespace, vector=parsed), 0, None
            # A corrupt, stale-rights or foreign-namespace row is incomplete
            # coverage, never a reason to buy a second attempt for this revision.
            return None, 0, None
        text = item_text(authorized.view)
        client = self._client()
        qualified = await client.qualify(
            workspace_id=authorized.item.workspace_id,
            user_id=authorized.item.user_id,
            text_length=len(text),
        )
        if qualified is None or qualified.digest != namespace.digest:
            return None, 0, None
        row, created = await reserve_attempt(
            database,
            workspace_id=authorized.item.workspace_id,
            user_id=authorized.item.user_id,
            request_id=self.request_id_factory(),
            news_item_id=binding.news_item_id,
            item_revision=binding.item_revision,
            namespace=qualified,
            quota=self.settings.news_semantic_clustering_hourly_quota,
            now=now,
        )
        if not created:
            # A prior attempt for this item revision and namespace is durable:
            # never buy it twice, and never pretend a failed call has a vector.
            return None, 0, row.cost_micros
        vector = await client.embed(
            NewsEmbeddingRequest(
                workspace_id=authorized.item.workspace_id,
                user_id=authorized.item.user_id,
                text=text,
                binding=binding,
            )
        )
        if vector is None:
            await finish_attempt(
                database,
                row,
                status=STATUS_UNAVAILABLE,
                reason="clustering_unavailable",
                now=now,
            )
            return None, 1, None
        cost = vector.cost_micros
        if not _namespace_matches(vector.namespace, qualified) or vector.namespace.dimension != len(
            vector.vector
        ):
            await finish_attempt(
                database,
                row,
                status=STATUS_FAILED,
                reason="namespace_mismatch",
                cost_micros=cost,
                now=now,
            )
            return None, 1, cost
        fence = self._now()
        try:
            current = await authorized_item(
                database,
                workspace_id=authorized.item.workspace_id,
                user_id=authorized.item.user_id,
                news_item_id=authorized.item.id,
                definitions=self.definitions,
                now=fence,
            )
        except NewsError:
            current = None
        if (
            current is None
            or current.item.revision != binding.item_revision
            or current.item.content_digest != binding.item_digest
            or current.fingerprint != binding.rights_fingerprint
        ):
            await finish_attempt(
                database,
                row,
                status=STATUS_DISCARDED,
                reason="source_changed",
                cost_micros=cost,
                now=now,
            )
            return None, 1, cost
        database.add(
            NewsClusterEmbedding(
                workspace_id=authorized.item.workspace_id,
                user_id=authorized.item.user_id,
                news_item_id=binding.news_item_id,
                item_revision=binding.item_revision,
                item_digest=binding.item_digest,
                rights_fingerprint=binding.rights_fingerprint,
                source_id=authorized.item.source_id,
                namespace_digest=qualified.digest,
                provider=vector.namespace.provider,
                model=vector.namespace.model,
                registry_revision=vector.namespace.registry_revision,
                artifact=vector.namespace.artifact,
                pipeline_version=vector.namespace.pipeline_version,
                dimension=vector.namespace.dimension,
                vector=list(vector.vector),
                generated_at=now,
            )
        )
        await finish_attempt(
            database,
            row,
            status=STATUS_COMPLETED,
            vector=vector,
            cost_micros=cost,
            now=now,
        )
        return vector, 1, cost

    async def _evaluate(
        self,
        database: AsyncSession,
        *,
        authorized: _AuthorizedItem,
        feature: ReviewedFeatureRecord,
        vector: Sequence[float],
        policy: DeploymentPolicy,
        now: datetime,
    ) -> _SelectionResult:
        """Read every scoped candidate; anything unreadable makes the set incomplete."""
        source_ids = await self._scoped_source_ids(
            database,
            workspace_id=authorized.item.workspace_id,
            user_id=authorized.item.user_id,
        )
        published = stored_utc(authorized.view.published_at)
        rows = (
            await database.execute(
                select(NewsStoryItem, NewsItem)
                .join(NewsItem, NewsItem.id == NewsStoryItem.news_item_id)
                .join(NewsStory, NewsStory.id == NewsStoryItem.cluster_id)
                .where(
                    NewsStoryItem.workspace_id == authorized.item.workspace_id,
                    NewsStoryItem.user_id == authorized.item.user_id,
                    NewsStory.suppressed.is_(False),
                    NewsItem.language == authorized.view.language,
                    NewsItem.expires_at > now,
                    NewsItem.source_id.in_(source_ids),
                    NewsItem.id != authorized.item.id,
                    NewsItem.published_at >= published - NEWS_CLUSTER_TEMPORAL_HORIZON,
                    NewsItem.published_at <= published + NEWS_CLUSTER_TEMPORAL_HORIZON,
                )
                .order_by(NewsItem.published_at.desc(), NewsStoryItem.news_item_id)
                .limit(self.candidate_limit + 1)
            )
        ).all()
        truncated = len(rows) > self.candidate_limit
        incomplete = truncated
        reasons = ["candidate_truncated"] if truncated else []
        candidates: list[ClusterCandidate] = []
        count = 0
        for member, candidate_item in rows[: self.candidate_limit]:
            if candidate_item.revision != member.item_revision:
                incomplete = True
                reasons.append("candidate_revision")
                continue
            try:
                candidate = await authorized_item(
                    database,
                    workspace_id=authorized.item.workspace_id,
                    user_id=authorized.item.user_id,
                    news_item_id=candidate_item.id,
                    definitions=self.definitions,
                    now=now,
                )
            except NewsError:
                incomplete = True
                reasons.append("candidate_rights")
                continue
            candidate_feature = await current_feature(
                database, candidate, definitions=self.definitions, now=now
            )
            if candidate_feature is None:
                incomplete = True
                reasons.append("candidate_features")
                continue
            embedding = await stored_embedding(
                database,
                news_item_id=candidate.item.id,
                item_revision=candidate.item.revision,
                namespace_digest=policy.namespace.digest,
            )
            candidate_vector = (
                parse_stored_vector(embedding.vector, embedding.dimension)
                if embedding is not None
                else None
            )
            if candidate_vector is None:
                incomplete = True
                reasons.append("candidate_vector")
                continue
            if (
                embedding is None
                or embedding.item_digest != candidate.item.content_digest
                or embedding.rights_fingerprint != candidate.fingerprint
                or not _stored_namespace_matches(embedding, policy.namespace)
                or len(candidate_vector) != len(vector)
            ):
                # The stored vector was produced under another policy, digest,
                # namespace or dimension: incomplete coverage, never a silently
                # dropped candidate and never a partial-signal comparison.
                incomplete = True
                reasons.append("candidate_binding")
                continue
            signals = cluster_signals(
                query_feature=feature,
                candidate_feature=candidate_feature,
                query_view=authorized.view,
                candidate_view=candidate.view,
                query_vector=vector,
                candidate_vector=candidate_vector,
            )
            candidates.append(ClusterCandidate(story_id=member.cluster_id, signals=signals))
            count += 1
        candidate_set = CandidateSet(candidates=tuple(candidates), complete=not incomplete)
        selection = select_candidate(
            candidate_set,
            SelectionPolicy(calibration=policy.calibration, minimum_margin=policy.minimum_margin),
        )
        return _SelectionResult(
            candidate_set=candidate_set,
            selection=selection,
            candidates=count,
            incomplete=incomplete,
            truncated=truncated,
            reasons=reasons,
        )

    async def propose(
        self,
        database: AsyncSession,
        item: NewsItem,
        definitions: Mapping[str, SourceDefinition],
        *,
        now: datetime,
    ) -> ClusterProposal | None:
        """Buy at most one embedding and propose a join, or return ``None``.

        Nothing is written to a story here: the proposal must be re-validated in
        the join transaction before any membership exists.
        """
        item_id = item.id
        # Never authorize at an instant earlier than the caller's, but always at
        # least the real current time: a stale pre-provider instant cannot keep an
        # expired rights record or policy alive.
        moment = max(aware_utc(now), self._now())
        status = active_policy(settings=self.settings, definitions=definitions, now=moment)
        if status.policy is None:
            self._record(
                ClusterAttempt(
                    news_item_id=item_id, status="UNAVAILABLE", reason=status.reason or "disabled"
                )
            )
            return None
        try:
            authorized = await authorized_item(
                database,
                workspace_id=item.workspace_id,
                user_id=item.user_id,
                news_item_id=item.id,
                definitions=definitions,
                now=moment,
            )
        except NewsError as error:
            self._record(
                ClusterAttempt(
                    news_item_id=item_id,
                    status="UNAVAILABLE",
                    reason=error.code,
                    policy_reference=status.policy.reference,
                )
            )
            return None
        if authorized.definition.key not in status.policy.source_keys:
            self._record(
                ClusterAttempt(
                    news_item_id=item_id,
                    status="UNAVAILABLE",
                    reason="source_scope",
                    policy_reference=status.policy.reference,
                )
            )
            return None
        feature = await current_feature(database, authorized, definitions=definitions, now=moment)
        if feature is None:
            # No reviewed identity: never pretend missing identity is zero overlap.
            self._record(
                ClusterAttempt(
                    news_item_id=item_id,
                    status="UNCERTAIN",
                    reason="features_missing",
                    policy_reference=status.policy.reference,
                    namespace_digest=status.policy.namespace.digest,
                )
            )
            return None
        try:
            vector, paid, cost = await self._ensure_vector(database, authorized, now=moment)
        except (
            ClusteringQuotaExceeded,
            ClusteringUnavailable,
            AIProviderNotConfigured,
            GatewayUnavailable,
            ContextDenied,
            ValueError,
        ):
            self._record(
                ClusterAttempt(
                    news_item_id=item_id,
                    status="UNAVAILABLE",
                    reason="clustering_unavailable",
                    policy_reference=status.policy.reference,
                )
            )
            return None
        if vector is None:
            self._record(
                ClusterAttempt(
                    news_item_id=item_id,
                    status="UNCERTAIN",
                    reason="vector_unavailable",
                    policy_reference=status.policy.reference,
                    namespace_digest=status.policy.namespace.digest,
                    paid_attempts=paid,
                    cost_micros=cost,
                )
            )
            return None
        evaluated = await self._evaluate(
            database,
            authorized=authorized,
            feature=feature,
            vector=vector.vector,
            policy=status.policy,
            now=moment,
        )
        selection = evaluated.selection
        namespace_digest = status.policy.namespace.digest
        if selection.decision != ClusterDecision.JOIN_EXISTING or selection.story_id is None:
            self._record(
                ClusterAttempt(
                    news_item_id=item_id,
                    status="UNCERTAIN"
                    if selection.decision == ClusterDecision.UNCERTAIN
                    else "CREATE_NEW",
                    reason=selection.reason,
                    policy_reference=status.policy.reference,
                    namespace_digest=namespace_digest,
                    candidate_count=evaluated.candidates,
                    paid_attempts=paid,
                    cost_micros=cost,
                )
            )
            return None
        self._record(
            ClusterAttempt(
                news_item_id=item_id,
                status="PROPOSED",
                reason=selection.reason,
                policy_reference=status.policy.reference,
                namespace_digest=namespace_digest,
                candidate_count=evaluated.candidates,
                paid_attempts=paid,
                cost_micros=cost,
            )
        )
        return ClusterProposal(
            news_item_id=item.id,
            item_revision=authorized.item.revision,
            story_id=selection.story_id,
            policy_reference=status.policy.reference,
            policy_digest=policy_digest(status.policy),
            namespace_digest=namespace_digest,
            score=selection.score or 0.0,
        )

    async def commit(
        self,
        database: AsyncSession,
        item: NewsItem,
        definitions: Mapping[str, SourceDefinition],
        *,
        proposal: ClusterProposal,
        now: datetime,
    ) -> NewsStory:
        """Atomic, re-validated first membership write for one unindexed item."""
        from navox.news.stories import owned_story, record_version

        # The commit is authorized at the real current time, never at the
        # pre-provider instant that may already have allowed an expired record.
        moment = max(aware_utc(now), self._now())
        status = active_policy(settings=self.settings, definitions=definitions, now=moment)
        if (
            status.policy is None
            or status.policy.reference != proposal.policy_reference
            or policy_digest(status.policy) != proposal.policy_digest
            or status.policy.namespace.digest != proposal.namespace_digest
        ):
            # A replaced policy can reuse a reference, so the full digest and the
            # exact namespace must both still match this proposal.
            raise NewsError("source_changed")
        policy = status.policy
        authorized = await authorized_item(
            database,
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            news_item_id=item.id,
            definitions=definitions,
            now=moment,
            lock=True,
        )
        # Snapshot immutable values: a later refresh of the same ORM row must
        # never make a revision or policy comparison silently pass.
        revision = authorized.item.revision
        digest = authorized.item.content_digest
        fingerprint = authorized.fingerprint
        if revision != proposal.item_revision:
            raise NewsError("source_changed")
        existing = await database.get(NewsStoryItem, item.id, populate_existing=True)
        if existing is not None:
            # Another worker already made the first membership; return it as-is.
            return await owned_story(
                database,
                existing.cluster_id,
                workspace_id=item.workspace_id,
                user_id=item.user_id,
            )
        owner = await database.scalar(
            select(User)
            .where(User.id == item.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        membership = await database.get(
            WorkspaceMembership, (item.workspace_id, item.user_id), populate_existing=True
        )
        if owner is None or membership is None or owner.agent_paused:
            raise NewsError("news_disabled")
        await owned_source(
            database,
            item.source_id,
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            lock=True,
        )
        await owned_story(
            database,
            proposal.story_id,
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            lock=True,
        )
        feature = await current_feature(database, authorized, definitions=definitions, now=moment)
        if feature is None:
            raise NewsError("source_changed")
        embedding = await stored_embedding(
            database,
            news_item_id=item.id,
            item_revision=revision,
            namespace_digest=policy.namespace.digest,
        )
        vector = (
            parse_stored_vector(embedding.vector, embedding.dimension)
            if embedding is not None
            else None
        )
        if vector is None:
            raise NewsError("source_changed")
        if (
            embedding is None
            or embedding.item_digest != digest
            or embedding.rights_fingerprint != fingerprint
            or not _stored_namespace_matches(embedding, policy.namespace)
            or embedding.dimension != len(vector)
        ):
            # Source rights, retention, content or the exact vector namespace
            # moved after the proposal read.
            raise NewsError("source_changed")
        # The decision is re-derived from current rights, revisions and vectors
        # instead of trusting the proposal that authorized the earlier read.
        evaluated = await self._evaluate(
            database,
            authorized=authorized,
            feature=feature,
            vector=vector,
            policy=policy,
            now=moment,
        )
        if (
            evaluated.selection.decision != ClusterDecision.JOIN_EXISTING
            or evaluated.selection.story_id != proposal.story_id
        ):
            raise NewsError("source_changed")
        story = await owned_story(
            database,
            proposal.story_id,
            workspace_id=item.workspace_id,
            user_id=item.user_id,
            lock=True,
        )
        url_key, copy_key = duplicate_keys(authorized.view)
        try:
            async with database.begin_nested():
                database.add(
                    NewsStoryItem(
                        news_item_id=item.id,
                        cluster_id=story.id,
                        workspace_id=item.workspace_id,
                        user_id=item.user_id,
                        item_revision=authorized.item.revision,
                        url_digest=url_key,
                        copy_digest=copy_key,
                        decision=DECISION_SEMANTIC_JOIN,
                        match_reference=policy.reference,
                        match_namespace=proposal.namespace_digest,
                        joined_at=moment,
                    )
                )
                story.version += 1
                await database.flush()
                await record_version(database, story, CHANGE_SEMANTIC_MEMBERSHIP, now=moment)
        except IntegrityError:
            # Another worker made the first membership between the read above and
            # this insert. Exactly one membership survives; report its story.
            existing = await database.get(NewsStoryItem, item.id, populate_existing=True)
            if existing is None:
                raise NewsError("source_changed") from None
            return await owned_story(
                database,
                existing.cluster_id,
                workspace_id=item.workspace_id,
                user_id=item.user_id,
            )
        self._record(
            ClusterAttempt(
                news_item_id=item.id,
                status="JOINED",
                reason="semantic_join",
                policy_reference=policy.reference,
                namespace_digest=proposal.namespace_digest,
                candidate_count=evaluated.candidates,
            )
        )
        return story


def semantic_clusterer(
    settings: Settings,
    *,
    definitions: Mapping[str, SourceDefinition],
    now: datetime,
    gateway: NewsEmbeddingGateway | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SemanticClusterer | None:
    """A clusterer only when the flag is on and a current policy is valid."""
    if not settings.news_feed_enabled or not settings.news_semantic_clustering_enabled:
        return None
    status = active_policy(settings=settings, definitions=definitions, now=now)
    if status.policy is None:
        return None
    return SemanticClusterer(
        settings,
        policy=status.policy,
        definitions=definitions,
        gateway=gateway,
        clock=clock,
    )


async def vectorize_source(
    database: AsyncSession,
    source_id: UUID,
    definitions: Mapping[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    clusterer: SemanticClusterer,
    now: datetime,
    limit: int = NEWS_CLUSTER_ACTIVITY_BOUND,
) -> int:
    """Backfill bounded item vectors so already indexed stories stay candidates.

    One durable attempt per item revision and namespace, no paid work when the
    policy is inactive, and no story write: an existing membership is untouched.
    """
    rows = await database.scalars(
        select(NewsItem)
        .where(
            NewsItem.source_id == source_id,
            NewsItem.workspace_id == workspace_id,
            NewsItem.user_id == user_id,
            NewsItem.expires_at > now,
            ~select(NewsClusterEmbedding.id)
            .where(
                NewsClusterEmbedding.news_item_id == NewsItem.id,
                NewsClusterEmbedding.item_revision == NewsItem.revision,
                NewsClusterEmbedding.namespace_digest == clusterer.policy.namespace.digest,
            )
            .exists(),
        )
        .order_by(NewsItem.published_at.desc(), NewsItem.id)
        .limit(max(0, limit))
    )
    purchased = 0
    # One consistent instant for the whole backfill pass, never earlier than the
    # caller's instant and never earlier than the real current time.
    moment = max(aware_utc(now), clusterer._now())
    for item in rows:
        try:
            authorized = await authorized_item(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                news_item_id=item.id,
                definitions=definitions,
                now=moment,
            )
        except NewsError:
            continue
        if authorized.definition.key not in clusterer.policy.source_keys:
            continue
        if await current_feature(database, authorized, definitions=definitions, now=moment) is None:
            continue
        _vector, paid, _cost = await clusterer._ensure_vector(database, authorized, now=moment)
        if paid:
            purchased += 1
    return purchased


__all__ = [
    "CHANGE_SEMANTIC_MEMBERSHIP",
    "DECISION_SEMANTIC_JOIN",
    "NEWS_CLUSTER_ACTIVITY_BOUND",
    "NEWS_CLUSTER_ARTIFACT",
    "NEWS_CLUSTER_CANDIDATE_LIMIT",
    "NEWS_CLUSTER_COST_MICROS",
    "NEWS_CLUSTER_MAX_COST",
    "NEWS_CLUSTER_PIPELINE_VERSION",
    "NEWS_CLUSTER_QUOTA_WINDOW",
    "NEWS_CLUSTER_TEMPORAL_HORIZON",
    "ClusterAttempt",
    "ClusterProposal",
    "ClusteringDisabled",
    "ClusteringQuotaExceeded",
    "ClusteringRequestReplay",
    "ClusteringUnavailable",
    "NewsEmbeddingBinding",
    "NewsEmbeddingGateway",
    "NewsEmbeddingRequest",
    "NewsItemEmbeddingContext",
    "NewsVector",
    "RegisteredNewsEmbeddingGateway",
    "SemanticClusterer",
    "authorized_item",
    "cluster_signals",
    "current_feature",
    "finish_attempt",
    "item_text",
    "record_feature",
    "reserve_attempt",
    "rights_fingerprint",
    "semantic_clusterer",
    "stored_vector",
    "temporal_proximity",
    "validate_embedding_output",
    "vectorize_source",
]
