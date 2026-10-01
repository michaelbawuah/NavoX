"""Synthetic semantic-clustering regressions: policy, fences, attempts, fallback.

Every scenario uses authored fixtures, an isolated database and a synthetic
provider transport double. No credential, live model, network call or owner
configuration is used, and no fixture claims measured production quality.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from temporalio.exceptions import ActivityError, CancelledError
from test_knowledge_semantic import FakeAdapter, publish_embedding_registry
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, definition, seed

from navox.ai import configured
from navox.ai.embedding_contracts import EmbeddingOutput
from navox.ai.foundation.contracts import Provider
from navox.core.settings import Settings
from navox.db.models import User
from navox.db.news import (
    NewsClusterEmbedding,
    NewsClusterEmbeddingRequest,
    NewsClusterFeature,
    NewsContentRights,
    NewsItem,
    NewsSource,
    NewsStory,
    NewsStoryItem,
    NewsStoryVersion,
)
from navox.news.cluster_contracts import (
    DeploymentPolicy,
    EmbeddingNamespace,
    EmbeddingNamespaceRef,
    FeatureCitation,
    PolicyRejected,
    ReviewedFeatureRecord,
    active_policy,
    calibration_digest,
    validate_deployment_policy,
)
from navox.news.cluster_evaluation import CalibrationReport, calibrate
from navox.news.contracts import NewsError, SourceDefinition, Verification
from navox.news.evidence import ClaimSpan
from navox.news.jobs import NewsSourceWork, NewsWorkResult
from navox.news.registry import activate_source, current_rights, revoke_rights
from navox.news.rights import policy_digest
from navox.news.semantic_clustering import (
    NEWS_CLUSTER_ARTIFACT,
    NEWS_CLUSTER_COST_MICROS,
    NEWS_CLUSTER_MAX_COST,
    NEWS_CLUSTER_PIPELINE_VERSION,
    ClusterAttempt,
    ClusteringQuotaExceeded,
    NewsEmbeddingRequest,
    NewsVector,
    RegisteredNewsEmbeddingGateway,
    SemanticClusterer,
    authorized_item,
    record_feature,
    reserve_attempt,
    semantic_clusterer,
    vectorize_source,
)
from navox.news.stories import index_item, story_view

REVIEWER = "reviewer@example.org"


class FixtureActivityClock(datetime):
    """Keep activity fixtures inside their authored retention window."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def _corpus(*, reference: str, authority: str = "reviewer"):
    from datetime import datetime

    from test_news_cluster_evaluation import example

    from navox.news.cluster_evaluation import ClusterCorpus, CorpusProvenance

    reviewer = REVIEWER if authority == "reviewer" else None
    return ClusterCorpus(
        reference=reference,
        signal_version="news-cluster-signals.v1",
        label_reference="reviewed-holdout-v1",
        provenance=CorpusProvenance(
            source_identity="independent-reviewed-cluster-corpus",
            captured_at=datetime(2026, 9, 20, 12, tzinfo=NOW.tzinfo),
            rights_basis="independent reviewer-labelled corpus under licence; not shipped",
            label_authority=authority,
            reviewer_identity=reviewer,
            content_digest="1" * 64,
        ),
        examples=(
            example(1, "development", True),
            example(2, "development", False),
            example(3, "holdout", True),
            example(4, "holdout", False),
        ),
    )


def reviewed_report(reference: str = "news-cluster-policy-v1") -> CalibrationReport:
    return calibrate(_corpus(reference=reference))


def deployment_policy(
    report: CalibrationReport,
    *,
    source_keys: tuple[str, ...],
    registry_revision: str,
    reference: str | None = None,
    language: str = "en",
    provider: str = Provider.OPENAI.value,
    model: str = "fixture-embedding-v1",
    reviewer: str = REVIEWER,
    reviewed_at=NOW - timedelta(hours=1),
    expires_at=NOW + timedelta(days=2),
    corpus_manifest_sha256: str | None = None,
    calibration_report_digest: str | None = None,
    minimum_margin: float | None = None,
) -> DeploymentPolicy:
    return DeploymentPolicy(
        reference=reference or report.candidate_policy.calibration.evaluation_reference,
        deployment_reference="news-cluster-deployment-2026-09-28",
        reviewer=reviewer,
        extractor_version=report.signal_version,
        namespace=EmbeddingNamespaceRef(
            provider=provider,
            model=model,
            registry_revision=registry_revision,
            artifact=NEWS_CLUSTER_ARTIFACT,
            pipeline_version=NEWS_CLUSTER_PIPELINE_VERSION,
        ),
        source_keys=source_keys,
        language=language,
        corpus_manifest_sha256=corpus_manifest_sha256 or report.corpus_sha256,
        calibration_report_digest=calibration_report_digest or calibration_digest(report),
        calibration=report.candidate_policy.calibration,
        minimum_margin=(
            report.candidate_policy.minimum_margin if minimum_margin is None else minimum_margin
        ),
        reviewed_at=reviewed_at,
        expires_at=expires_at,
    )


def settings_for(
    definitions: dict[str, SourceDefinition],
    *,
    policy: DeploymentPolicy | None = None,
    report: CalibrationReport | None = None,
    rules=None,
    enabled: bool = True,
    quota: int = 20,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="test",
        news_feed_enabled=True,
        news_semantic_clustering_enabled=enabled,
        news_semantic_clustering_hourly_quota=quota,
        news_semantic_deployment_policy=(
            policy.model_dump(mode="json") if policy is not None else None
        ),
        news_semantic_calibration_report=(
            report.model_dump(mode="json") if report is not None else None
        ),
        news_source_catalog=[
            definition.model_dump(mode="json") for definition in definitions.values()
        ],
        ai_provider="automatic",
        openai_api_key=SecretStr("fixture"),
        ai_provider_policy=(rules or _no_rules()).model_dump(mode="json"),
    )


def _no_rules():
    from navox.ai.routing import PolicyRules

    return PolicyRules(
        grants=(),
        max_cost=Decimal("0.05"),
    )


def reviewed_span(authorized, *, description: bool = False) -> ClaimSpan:
    """The whole permitted headline or snippet, exactly as it is stored now."""
    view = authorized.view
    text = view.description if description else view.headline
    assert text is not None
    return ClaimSpan(
        item_id=view.id,
        item_revision=view.revision,
        field="description" if description else "headline",
        start=0,
        end=len(text),
    )


def feature_kwargs(
    authorized,
    *,
    entities: tuple[str, ...] = ("storm",),
    geography: tuple[str, ...] = ("us",),
    event_type: str | None = "weather",
    event_identity: str | None = None,
    event_time=None,
    reviewer: str = REVIEWER,
    now=NOW,
    citations=None,
) -> dict[str, object]:
    if citations is None:
        span = reviewed_span(authorized)
        fields = ["entity_ids"]
        if geography:
            fields.append("geographic_ids")
        if event_type:
            fields.append("event_type")
        if event_identity:
            fields.append("event_identity")
        if event_time is not None:
            fields.append("event_time")
        citations = tuple(FeatureCitation(field=name, span=span) for name in fields)
    return dict(
        news_item_id=authorized.item.id,
        item_revision=authorized.item.revision,
        item_digest=authorized.item.content_digest,
        rights_fingerprint=authorized.fingerprint,
        source_id=authorized.item.source_id,
        language=authorized.view.language,
        entity_ids=entities,
        geographic_ids=geography,
        event_type=event_type,
        event_identity=event_identity,
        event_time=event_time,
        citations=citations,
        reviewer=reviewer,
        review_reference="reviewed-cluster-fixture",
        reviewed_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=2),
    )


def reviewed_feature(authorized, **overrides) -> ReviewedFeatureRecord:
    return ReviewedFeatureRecord(**feature_kwargs(authorized, **overrides))


@dataclass
class StubGateway:
    """Synthetic paid surface: no HTTP, no credential, no live model."""

    namespace: EmbeddingNamespaceRef
    vector: tuple[float, ...] = (1.0, 0.0, 0.0)
    cost_micros: int | None = 1234
    fail: bool = False
    mutate: object | None = None
    embed_calls: int = 0
    qualify_calls: int = 0

    async def qualify(self, *, workspace_id: UUID, user_id: UUID, text_length: int):
        self.qualify_calls += 1
        return self.namespace

    async def embed(self, request: NewsEmbeddingRequest) -> NewsVector | None:
        self.embed_calls += 1
        if self.mutate is not None:
            await self.mutate()  # type: ignore[misc]
        if self.fail:
            return None
        return NewsVector(
            namespace=EmbeddingNamespace(
                **self.namespace.model_dump(),
                dimension=len(self.vector),
            ),
            vector=self.vector,
            cost_micros=self.cost_micros,
        )


async def build_env(
    factory,
    *,
    sources: int = 2,
    retention_days: int = 2,
    rules=None,
    quota: int = 20,
    candidate_limit: int = 100,
    enabled: bool = True,
    policy=None,
    report: CalibrationReport | None = None,
) -> dict[str, object]:
    """Two owned, permitted sources with a valid reviewed policy by default."""
    configs = []
    rows = []
    async with factory() as database:
        for index, key in enumerate(("one", "two", "three")[:sources]):
            config = definition(
                summary_generation_allowed=True, retention_days=retention_days
            ).model_copy(
                update={
                    "key": f"source-{key}",
                    "name": f"Source {key}",
                    "domain": f"{key}.example.com",
                    "endpoint": f"https://{key}.example.com/feed",
                    "article_domains": (f"{key}.example.com",),
                    "independence_group": f"origin-{key}",
                }
            )
            source = await activate_source(
                database, config, workspace_id=WORKSPACE, user_id=USER, now=NOW
            )
            from navox.news.contracts import NewsItemInput
            from navox.news.ingestion import store_item

            rows.append(
                await store_item(
                    database,
                    source,
                    await current_rights(database, source),
                    config,
                    NewsItemInput(
                        external_id=key,
                        headline=f"Storm warning issued for the coast {index}",
                        description="Officials issued a warning for the coast.",
                        canonical_url=f"https://{key}.example.com/report",
                        published_at=NOW,
                        categories=("science",),
                    ),
                    now=NOW,
                )
            )
            configs.append(config)
        await database.commit()
    definitions = {config.key: config for config in configs}
    if policy is None:
        policy = deployment_policy(
            report or reviewed_report(),
            source_keys=tuple(definitions),
            registry_revision="2",
        )
    settings = settings_for(
        definitions,
        policy=policy,
        report=report or reviewed_report(),
        rules=rules,
        enabled=enabled,
        quota=quota,
    )
    return {
        "definitions": definitions,
        "configs": configs,
        "rows": rows,
        "policy": policy,
        "settings": settings,
        "candidate_limit": candidate_limit,
    }


def clusterer_for(
    env: dict[str, object],
    *,
    gateway,
    factory,
    clock=lambda: NOW,
) -> SemanticClusterer:
    return SemanticClusterer(
        env["settings"],  # type: ignore[arg-type]
        policy=env["policy"],  # type: ignore[arg-type]
        definitions=env["definitions"],  # type: ignore[arg-type]
        gateway=gateway,
        clock=clock,
        candidate_limit=env["candidate_limit"],  # type: ignore[arg-type]
    )


async def add_feature(database, row, env, *, now=NOW, **overrides):
    authorized = await authorized_item(
        database,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        news_item_id=row.id,
        definitions=env["definitions"],
        now=now,
    )
    record = reviewed_feature(authorized, now=now, **overrides)
    await record_feature(database, record=record, definitions=env["definitions"], now=now)
    return record


# --------------------------------------------------------------------------- policy


def test_deployment_policy_must_bind_the_reviewed_report_and_catalog_scope() -> None:
    report = reviewed_report()
    definitions = {definition().key: definition()}
    policy = deployment_policy(report, source_keys=tuple(definitions), registry_revision="2")
    validate_deployment_policy(policy=policy, report=report, definitions=definitions, now=NOW)

    authored = calibrate(_corpus(reference="news-cluster-policy-v1", authority="authored"))
    with pytest.raises(PolicyRejected, match="authored_corpus"):
        validate_deployment_policy(policy=policy, report=authored, definitions=definitions, now=NOW)
    with pytest.raises(PolicyRejected, match="manifest_mismatch"):
        validate_deployment_policy(
            policy=deployment_policy(
                report,
                source_keys=tuple(definitions),
                registry_revision="2",
                corpus_manifest_sha256="9" * 64,
            ),
            report=report,
            definitions=definitions,
            now=NOW,
        )
    with pytest.raises(PolicyRejected, match="report_mismatch"):
        validate_deployment_policy(
            policy=deployment_policy(
                report,
                source_keys=tuple(definitions),
                registry_revision="2",
                calibration_report_digest="8" * 64,
            ),
            report=report,
            definitions=definitions,
            now=NOW,
        )
    with pytest.raises(PolicyRejected, match="threshold_mismatch"):
        validate_deployment_policy(
            policy=deployment_policy(
                report,
                source_keys=tuple(definitions),
                registry_revision="2",
                minimum_margin=0.0,
            ),
            report=report,
            definitions=definitions,
            now=NOW,
        )
    with pytest.raises(PolicyRejected, match="expired"):
        validate_deployment_policy(
            policy=policy, report=report, definitions=definitions, now=NOW + timedelta(days=3)
        )
    with pytest.raises(PolicyRejected, match="source_scope"):
        validate_deployment_policy(
            policy=deployment_policy(
                report, source_keys=("unknown-source",), registry_revision="2"
            ),
            report=report,
            definitions=definitions,
            now=NOW,
        )
    with pytest.raises(PolicyRejected, match="source_scope"):
        validate_deployment_policy(
            policy=deployment_policy(
                report, source_keys=tuple(definitions), registry_revision="2", language="fr"
            ),
            report=report,
            definitions=definitions,
            now=NOW,
        )
    authored_policy = policy.model_copy(update={"reviewer": "someone@example.org"})
    with pytest.raises(PolicyRejected, match="authored_corpus"):
        validate_deployment_policy(
            policy=authored_policy, report=report, definitions=definitions, now=NOW
        )

    settings = settings_for(definitions, policy=policy, report=report, enabled=True)
    assert active_policy(settings=settings, definitions=definitions, now=NOW).active
    off = settings.model_copy(update={"news_semantic_clustering_enabled": False})
    status = active_policy(settings=off, definitions=definitions, now=NOW)
    assert status.policy is None and status.reason == "disabled"
    missing = settings.model_copy(update={"news_semantic_deployment_policy": None})
    status = active_policy(settings=missing, definitions=definitions, now=NOW)
    assert status.policy is None and status.reason == "policy_missing"
    expired = settings.model_copy(update={"news_semantic_deployment_policy": None})
    assert active_policy(settings=expired, definitions=definitions, now=NOW).policy is None


def test_feature_record_requires_canonical_cited_and_expiring_identity() -> None:
    with pytest.raises(ValueError):
        ReviewedFeatureRecord(
            news_item_id=uuid4(),
            item_revision=1,
            item_digest="0" * 64,
            rights_fingerprint="0" * 64,
            source_id=uuid4(),
            language="en",
            entity_ids=("Storm",),
            citations=(FeatureCitation(field="entity_ids", reference="review"),),
            reviewer=REVIEWER,
            review_reference="reviewed-cluster-fixture",
            reviewed_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    with pytest.raises(ValueError):
        ReviewedFeatureRecord(
            news_item_id=uuid4(),
            item_revision=1,
            item_digest="0" * 64,
            rights_fingerprint="0" * 64,
            source_id=uuid4(),
            language="en",
            entity_ids=("storm",),
            citations=(),
            reviewer=REVIEWER,
            review_reference="reviewed-cluster-fixture",
            reviewed_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )


# ------------------------------------------------------------------- paid pipeline


@pytest.mark.asyncio
async def test_semantic_join_uses_the_registered_gateway_with_bounded_attempts(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    revision, rules, reference = await publish_embedding_registry(ai_database)
    adapter = FakeAdapter(output=EmbeddingOutput(vector=(1.0, 0.0, 0.0)).model_dump_json())
    monkeypatch.setattr(
        configured, "configured_adapters", lambda settings, registry: {Provider.OPENAI: adapter}
    )
    report = reviewed_report()
    env = await build_env(ai_database, rules=rules)
    env["policy"] = deployment_policy(
        report, source_keys=tuple(env["definitions"]), registry_revision=str(revision)
    )
    env["settings"] = settings_for(
        env["definitions"], policy=env["policy"], report=report, rules=rules
    )
    gateway = RegisteredNewsEmbeddingGateway(
        env["settings"], definitions=env["definitions"], factory=ai_database, clock=lambda: NOW
    )
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env)
        await database.commit()
        first_story = await index_item(
            database, first, env["definitions"], now=NOW, clustering=clusterer
        )
        second_story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert second_story.id == first_story.id
        assert first_story.version == 2
        view = await story_view(database, first_story, env["definitions"], now=NOW)
        assert view.source_count == 2
        assert view.evidence_pending and view.verification_status is not Verification.VERIFIED
        membership = await database.get(NewsStoryItem, second.id)
        assert membership is not None
        assert membership.decision == "SEMANTIC_JOIN"
        assert membership.match_reference == env["policy"].reference
        assert membership.match_namespace == env["policy"].namespace.digest
        assert second.headline not in str(membership.url_digest)
        assert (
            await database.scalar(
                select(func.count())
                .select_from(NewsStoryVersion)
                .where(
                    NewsStoryVersion.cluster_id == first_story.id,
                    NewsStoryVersion.change_kind == "SEMANTIC_MEMBERSHIP",
                )
            )
            == 1
        )
        assert (
            await database.scalar(
                select(func.count())
                .select_from(NewsClusterEmbedding)
                .where(NewsClusterEmbedding.namespace_digest == env["policy"].namespace.digest)
            )
            == 2
        )
        attempts = list(
            await database.scalars(
                select(NewsClusterEmbeddingRequest).order_by(NewsClusterEmbeddingRequest.created_at)
            )
        )
        assert [row.status for row in attempts] == ["COMPLETED", "COMPLETED"]
        assert all(row.provider == Provider.OPENAI.value for row in attempts)
        assert all(row.registry_revision == str(revision) for row in attempts)
    assert len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_missing_features_buy_nothing_and_never_join(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await database.commit()
        first_story = await index_item(
            database, first, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        second_story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert second_story.id != first_story.id
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2
    assert gateway.embed_calls == 1
    reasons = {attempt.news_item_id: attempt for attempt in clusterer.attempts}
    assert reasons[second.id].status == "UNCERTAIN"
    assert reasons[second.id].reason == "features_missing"
    assert reasons[second.id].paid_attempts == 0


@pytest.mark.asyncio
async def test_incomplete_candidate_coverage_stays_uncertain(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=3, candidate_limit=1)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second, third = env["rows"]
    async with ai_database() as database:
        for row in (first, second, third):
            await add_feature(database, row, env, event_identity=f"event-{row.external_id}")
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        await index_item(database, second, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        story = await index_item(database, third, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 3
        assert story.id == (await database.get(NewsStoryItem, third.id)).cluster_id
    attempt = {row.news_item_id: row for row in clusterer.attempts}[third.id]
    assert attempt.status == "UNCERTAIN"
    assert attempt.reason == "incomplete_candidates"
    assert attempt.candidate_count == 1


@pytest.mark.asyncio
async def test_stale_namespace_and_incompatible_events_never_join(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env, event_identity="event-alpha")
        await add_feature(database, second, env, event_identity="event-beta")
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        # A candidate vector in another namespace is incomplete coverage.
        embedding = await database.scalar(
            select(NewsClusterEmbedding).where(NewsClusterEmbedding.news_item_id == first.id)
        )
        assert embedding is not None
        embedding.namespace_digest = "7" * 64
        await database.commit()
        second_story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2
        assert second_story.id != first.id
    attempt = {row.news_item_id: row for row in clusterer.attempts}[second.id]
    assert attempt.status == "UNCERTAIN"


@pytest.mark.asyncio
async def test_cross_owner_items_are_never_candidates(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    ours = env["rows"][0]
    async with ai_database() as database:
        other_config = definition(summary_generation_allowed=True).model_copy(
            update={
                "key": "source-other",
                "name": "Other",
                "domain": "other.example.com",
                "endpoint": "https://other.example.com/feed",
                "article_domains": ("other.example.com",),
                "independence_group": "origin-other",
            }
        )
        from navox.news.contracts import NewsItemInput
        from navox.news.ingestion import store_item

        other_source = await activate_source(
            database, other_config, workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER, now=NOW
        )
        theirs = await store_item(
            database,
            other_source,
            await current_rights(database, other_source),
            other_config,
            NewsItemInput(
                external_id="theirs",
                headline="Storm warning issued for the coast 0",
                description="Officials issued a warning for the coast.",
                canonical_url="https://other.example.com/report",
                published_at=NOW,
                categories=("science",),
            ),
            now=NOW,
        )
        await database.commit()
        their_env = {"definitions": {other_config.key: other_config}}
        their_authorized = await authorized_item(
            database,
            workspace_id=OTHER_WORKSPACE,
            user_id=OTHER_USER,
            news_item_id=theirs.id,
            definitions=their_env["definitions"],
            now=NOW,
        )
        await record_feature(
            database,
            record=reviewed_feature(their_authorized),
            definitions=their_env["definitions"],
            now=NOW,
        )
        await database.commit()
        their_story = await index_item(
            database, theirs, their_env["definitions"], now=NOW, clustering=None
        )
        await database.commit()
        await add_feature(database, ours, env)
        await database.commit()
        our_story = await index_item(
            database, ours, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert our_story.id != their_story.id
        assert (
            await database.scalar(
                select(func.count())
                .select_from(NewsStoryItem)
                .where(NewsStoryItem.cluster_id == their_story.id)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_provider_failure_creates_the_story_without_a_retry(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    gateway = StubGateway(namespace=env["policy"].namespace, fail=True)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    (row,) = env["rows"]
    async with ai_database() as database:
        await add_feature(database, row, env)
        await database.commit()
        story = await index_item(database, row, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        attempt = await database.scalar(select(NewsClusterEmbeddingRequest))
        assert attempt is not None and attempt.status == "UNAVAILABLE"
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 0
        assert story.id == (await database.get(NewsStoryItem, row.id)).cluster_id
        # Re-indexing the same revision never buys a second attempt.
        await index_item(database, row, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
    assert gateway.embed_calls == 1


@pytest.mark.asyncio
async def test_reservations_are_idempotent_per_revision_and_namespace(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    (row,) = env["rows"]
    request_id = uuid4()
    async with ai_database() as database:
        first, created = await reserve_attempt(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            request_id=request_id,
            news_item_id=row.id,
            item_revision=row.revision,
            namespace=env["policy"].namespace,
            quota=20,
            now=NOW,
        )
        assert created
        # The durable audit is identifiers only: never source text or a snippet.
        assert set(first.namespace_reference) == {
            "provider",
            "model",
            "registry_revision",
            "artifact",
            "pipeline_version",
        }
        replay, created = await reserve_attempt(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            request_id=request_id,
            news_item_id=row.id,
            item_revision=row.revision,
            namespace=env["policy"].namespace,
            quota=20,
            now=NOW,
        )
        assert not created and replay.id == first.id
        duplicate, created = await reserve_attempt(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            request_id=uuid4(),
            news_item_id=row.id,
            item_revision=row.revision,
            namespace=env["policy"].namespace,
            quota=20,
            now=NOW,
        )
        assert not created and duplicate.id == first.id
        assert (
            await database.scalar(select(func.count()).select_from(NewsClusterEmbeddingRequest))
            == 1
        )
        with pytest.raises(ClusteringQuotaExceeded):
            await reserve_attempt(
                database,
                workspace_id=WORKSPACE,
                user_id=USER,
                request_id=uuid4(),
                news_item_id=row.id,
                item_revision=row.revision + 1,
                namespace=env["policy"].namespace,
                quota=1,
                now=NOW,
            )


@pytest.mark.asyncio
async def test_hourly_quota_bounds_paid_attempts_across_items(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, quota=1)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        for row in (first, second):
            await add_feature(database, row, env, event_identity=f"event-{row.external_id}")
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        await index_item(database, second, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        assert (
            await database.scalar(select(func.count()).select_from(NewsClusterEmbeddingRequest))
            == 1
        )
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2
    assert gateway.embed_calls == 1


@pytest.mark.asyncio
async def test_inflight_pause_discards_the_vector_and_still_indexes(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    revision, rules, _reference = await publish_embedding_registry(ai_database)

    async def pause() -> None:
        async with ai_database() as other:
            owner = await other.get(User, USER, populate_existing=True)
            assert owner is not None
            owner.agent_paused = True
            await other.commit()

    adapter = PausingAdapter(pause)
    monkeypatch.setattr(
        configured, "configured_adapters", lambda settings, registry: {Provider.OPENAI: adapter}
    )
    env = await build_env(ai_database, sources=1, rules=rules)
    env["policy"] = deployment_policy(
        reviewed_report(), source_keys=tuple(env["definitions"]), registry_revision=str(revision)
    )
    env["settings"] = settings_for(
        env["definitions"], policy=env["policy"], report=reviewed_report(), rules=rules
    )
    gateway = RegisteredNewsEmbeddingGateway(
        env["settings"], definitions=env["definitions"], factory=ai_database, clock=lambda: NOW
    )
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    (row,) = env["rows"]
    async with ai_database() as database:
        await add_feature(database, row, env)
        await database.commit()
        story = await index_item(database, row, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        attempt = await database.scalar(select(NewsClusterEmbeddingRequest))
        assert attempt is not None
        assert attempt.status in {"UNAVAILABLE", "DISCARDED"}
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 0
        membership = await database.get(NewsStoryItem, row.id)
        assert membership is not None and membership.cluster_id == story.id
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["source_disabled", "rights_revoked", "revision_bumped"])
async def test_post_call_fence_discards_a_vector_after_authority_changes(
    ai_database, mutation: str
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    (row,) = env["rows"]

    async def mutate() -> None:
        async with ai_database() as other:
            if mutation == "source_disabled":
                source = await other.get(NewsSource, row.source_id, populate_existing=True)
                assert source is not None
                source.status = "disabled"
            elif mutation == "rights_revoked":
                source = await other.get(NewsSource, row.source_id, populate_existing=True)
                assert source is not None
                await revoke_rights(other, source, now=NOW)
            else:
                stored = await other.get(NewsItem, row.id, populate_existing=True)
                assert stored is not None
                stored.revision += 1
                stored.content_digest = "5" * 64
            await other.commit()

    gateway = StubGateway(namespace=env["policy"].namespace, mutate=mutate)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    async with ai_database() as database:
        await add_feature(database, row, env)
        await database.commit()
        proposal = await clusterer.propose(database, row, env["definitions"], now=NOW)
        assert proposal is None
        attempt = await database.scalar(select(NewsClusterEmbeddingRequest))
        if mutation == "rights_revoked":
            # Revocation deletes the item; the durable reservation cascades with
            # it (PostgreSQL) or is left DISCARDED (SQLite's read snapshot).
            assert attempt is None or attempt.status == "DISCARDED"
        else:
            assert attempt is not None and attempt.status == "DISCARDED"
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 0
        assert await database.scalar(select(func.count()).select_from(NewsStoryItem)) == 0


@pytest.mark.asyncio
async def test_vector_backfill_makes_an_older_story_a_candidate(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    first, second = env["rows"]
    off = env["settings"].model_copy(update={"news_semantic_clustering_enabled": False})
    assert semantic_clusterer(off, definitions=env["definitions"], now=NOW) is None
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env)
        await database.commit()
        # Indexed while the pipeline was off: a story exists with no vector.
        await index_item(database, first, env["definitions"], now=NOW, clustering=None)
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 0
        purchased = await vectorize_source(
            database,
            first.source_id,
            env["definitions"],
            workspace_id=WORKSPACE,
            user_id=USER,
            clusterer=clusterer,
            now=NOW,
        )
        await database.commit()
        assert purchased == 1
        second_story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert second_story.id == (await database.get(NewsStoryItem, first.id)).cluster_id
    assert gateway.embed_calls == 2


@pytest.mark.asyncio
async def test_expired_policy_performs_no_paid_work(ai_database) -> None:
    await seed(ai_database)
    report = reviewed_report()
    env = await build_env(ai_database, sources=1)
    expired = deployment_policy(
        report,
        source_keys=tuple(env["definitions"]),
        registry_revision="2",
        reviewed_at=NOW - timedelta(days=3),
        expires_at=NOW - timedelta(hours=1),
    )
    settings = settings_for(env["definitions"], policy=expired, report=report)
    status = active_policy(settings=settings, definitions=env["definitions"], now=NOW)
    assert status.policy is None and status.reason == "policy_invalid:expired"
    assert semantic_clusterer(settings, definitions=env["definitions"], now=NOW) is None
    gateway = StubGateway(namespace=expired.namespace)
    (row,) = env["rows"]
    async with ai_database() as database:
        story = await index_item(database, row, env["definitions"], now=NOW, clustering=None)
        await database.commit()
        membership = await database.get(NewsStoryItem, row.id)
        assert membership is not None and membership.cluster_id == story.id
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 0
    assert gateway.embed_calls == 0


@pytest.mark.asyncio
async def test_clustering_activity_ends_with_exact_fallback_when_gateway_is_absent(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    from navox.news import activities

    monkeypatch.setattr(activities, "datetime", FixtureActivityClock)
    disabled = env["settings"].model_copy(update={"ai_provider": "disabled"})
    monkeypatch.setattr(activities, "get_settings", lambda: disabled)
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    for row in env["rows"]:
        result = await activities.news_clustering_activity(
            activities.NewsSourceWork(
                source_id=str(row.source_id),
                workspace_id=str(WORKSPACE),
                user_id=str(USER),
                request_id=str(uuid4()),
            )
        )
        assert result.status == "COMPLETED"
    async with ai_database() as database:
        assert await database.scalar(select(func.count()).select_from(NewsStoryItem)) == 2
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2
        assert (
            await database.scalar(select(func.count()).select_from(NewsClusterEmbeddingRequest))
            == 0
        )


@pytest.mark.asyncio
async def test_vectorize_source_skips_items_without_reviewed_identity(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    async with ai_database() as database:
        purchased = await vectorize_source(
            database,
            env["rows"][0].source_id,
            env["definitions"],
            workspace_id=WORKSPACE,
            user_id=USER,
            clusterer=clusterer,
            now=NOW,
        )
        await database.commit()
    assert purchased == 0
    assert gateway.embed_calls == 0


@pytest.mark.asyncio
async def test_source_revocation_deletes_stored_vectors_with_the_item(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    (row,) = env["rows"]
    async with ai_database() as database:
        await add_feature(database, row, env)
        await database.commit()
        await index_item(database, row, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        source = await database.get(NewsSource, row.source_id, populate_existing=True)
        assert source is not None
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 1
        await revoke_rights(database, source, now=NOW)
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(NewsClusterEmbedding)) == 0
        assert await database.scalar(select(func.count()).select_from(NewsClusterFeature)) == 0


@pytest.mark.asyncio
async def test_postgres_concurrent_requests_reserve_exactly_one_attempt(ai_database) -> None:
    if not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"):
        pytest.skip("Concurrency requires PostgreSQL row locks")
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    (row,) = env["rows"]
    namespace = env["policy"].namespace

    async def reserve() -> bool:
        async with ai_database() as database:
            _attempt, created = await reserve_attempt(
                database,
                workspace_id=WORKSPACE,
                user_id=USER,
                request_id=uuid4(),
                news_item_id=row.id,
                item_revision=row.revision,
                namespace=namespace,
                quota=20,
                now=NOW,
            )
            return created

    first, second = await asyncio.gather(reserve(), reserve())
    assert sorted([first, second]) == [False, True]
    async with ai_database() as database:
        assert (
            await database.scalar(select(func.count()).select_from(NewsClusterEmbeddingRequest))
            == 1
        )


# ---------------------------------------------------------- catalog / concurrency


@pytest.mark.asyncio
async def test_catalog_change_blocks_new_paid_work_for_that_source(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    (row,) = env["rows"]
    definitions = dict(env["definitions"])
    config = next(iter(definitions.values()))
    definitions[config.key] = config.model_copy(update={"name": "Renamed fixture source"})
    async with ai_database() as database:
        await add_feature(database, row, env)
        await database.commit()
        proposal = await clusterer.propose(database, row, definitions, now=NOW)
        assert proposal is None
    assert gateway.embed_calls == 0
    assert clusterer.attempts[-1].reason == "source_changed"


@pytest.mark.asyncio
async def test_narrowed_current_rights_invalidate_a_candidate_vector(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env)
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        source = await database.get(NewsSource, first.source_id, populate_existing=True)
        assert source is not None
        narrowed = definition(summary_generation_allowed=True).rights.model_copy(
            update={"snippet_storage_allowed": False}
        )
        database.add(
            NewsContentRights(
                source_id=source.id,
                version=source.rights_version + 1,
                policy=narrowed.model_dump(mode="json"),
                policy_digest=policy_digest(narrowed),
            )
        )
        source.rights_version += 1
        await database.commit()
        story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert story.id != first.id
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2
    attempt = {row.news_item_id: row for row in clusterer.attempts}[second.id]
    assert attempt.status == "UNCERTAIN"


@pytest.mark.asyncio
async def test_postgres_concurrent_first_membership_keeps_one_row(ai_database) -> None:
    if not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"):
        pytest.skip("Concurrent membership requires PostgreSQL")
    await seed(ai_database)
    env = await build_env(ai_database)
    first, second = env["rows"]
    gateway = StubGateway(namespace=env["policy"].namespace)
    async with ai_database() as setup:
        await add_feature(setup, first, env)
        await add_feature(setup, second, env)
        await setup.commit()
        await index_item(setup, first, env["definitions"], now=NOW, clustering=None)
        await setup.commit()

    async def join() -> UUID:
        async with ai_database() as database:
            clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
            story = await index_item(
                database, second, env["definitions"], now=NOW, clustering=clusterer
            )
            await database.commit()
            return story.id

    results = await asyncio.gather(join(), join(), return_exceptions=True)
    assert all(isinstance(result, UUID) for result in results), results
    assert len(set(results)) == 1
    async with ai_database() as database:
        assert (
            await database.scalar(
                select(func.count())
                .select_from(NewsStoryItem)
                .where(NewsStoryItem.news_item_id == second.id)
            )
            == 1
        )


def test_clustering_activity_is_registered_with_the_worker() -> None:
    from navox.news import activities
    from navox.workflows import worker

    assert worker.news_clustering_activity is activities.news_clustering_activity


class PausingAdapter(FakeAdapter):
    """Transport double that mutates authority while the call is in flight."""

    def __init__(self, mutate) -> None:
        super().__init__(output=EmbeddingOutput(vector=(1.0, 0.0, 0.0)).model_dump_json())
        self.mutate = mutate

    async def execute(self, request):
        await self.mutate()
        return await super().execute(request)


def test_attempt_records_never_contain_source_text() -> None:
    assert NEWS_CLUSTER_COST_MICROS == int(NEWS_CLUSTER_MAX_COST * 1_000_000)
    attempt = ClusterAttempt(
        news_item_id=uuid4(),
        status="UNCERTAIN",
        reason="features_missing",
        policy_reference="news-cluster-policy-v1",
        namespace_digest="a" * 64,
    )
    assert "storm" not in repr(attempt)


def test_clustering_errors_are_fixed_codes_not_source_text() -> None:
    with pytest.raises(NewsError) as caught:
        raise NewsError("source_changed")
    assert caught.value.code == "source_changed"


# -------------------------------------------- deferral, recovery and lock ordering


class ExplodingGateway:
    """Paid surface double whose every call fails unexpectedly."""

    async def qualify(self, **kwargs):
        raise RuntimeError("unexpected provider failure")

    async def embed(self, request):
        raise RuntimeError("unexpected provider failure")


async def activity_work(env, *, defer: bool) -> NewsSourceWork:
    return NewsSourceWork(
        source_id=str(env["rows"][0].source_id),
        workspace_id=str(WORKSPACE),
        user_id=str(USER),
        request_id=str(uuid4()),
        defer=defer,
    )


def test_pre_patch_payloads_and_results_never_defer() -> None:
    payload = NewsSourceWork("source", "workspace", "user", "request")
    assert payload.defer is False
    assert NewsWorkResult("COMPLETED").deferred is False
    assert NewsWorkResult("COMPLETED", 1, 0).deferred is False


@pytest.mark.asyncio
async def test_clustering_activity_survives_an_unexpected_paid_failure(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    from navox.news import activities

    monkeypatch.setattr(activities, "datetime", FixtureActivityClock)
    monkeypatch.setattr(activities, "get_settings", lambda: env["settings"])
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    exploding = SemanticClusterer(
        env["settings"],
        policy=env["policy"],
        definitions=env["definitions"],
        gateway=ExplodingGateway(),
        clock=lambda: NOW,
    )
    monkeypatch.setattr(activities, "semantic_clusterer", lambda *args, **kwargs: exploding)
    result = await activities.news_clustering_activity(await activity_work(env, defer=True))
    assert result.status == "COMPLETED"
    async with ai_database() as database:
        # The exact pass still finished every row for the source.
        assert await database.scalar(select(func.count()).select_from(NewsStoryItem)) == 1


@pytest.mark.asyncio
async def test_clustering_activity_reports_deferred_when_exact_indexing_fails(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    from navox.news import activities

    monkeypatch.setattr(activities, "get_settings", lambda: env["settings"])
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)

    async def broken(*args, **kwargs):
        raise RuntimeError("indexing infrastructure unavailable")

    monkeypatch.setattr(activities, "index_source", broken)
    result = await activities.news_clustering_activity(await activity_work(env, defer=True))
    assert result.status == "COMPLETED"
    assert result.deferred is True


@pytest.mark.asyncio
async def test_clustering_activity_reads_the_source_without_a_lock(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    from navox.news import activities
    from navox.news.registry import owned_source as real_owned_source

    locks = []

    async def recording(database, source_id, **kwargs):
        locks.append(kwargs.get("lock"))
        return await real_owned_source(database, source_id, **kwargs)

    monkeypatch.setattr(activities, "get_settings", lambda: env["settings"])
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(activities, "owned_source", recording)
    await activities.news_clustering_activity(await activity_work(env, defer=True))
    assert locks == [False]


@pytest.mark.asyncio
async def test_exact_recovery_activity_indexes_without_any_paid_call(
    ai_database, monkeypatch
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    from navox.news import activities

    monkeypatch.setattr(activities, "datetime", FixtureActivityClock)
    disabled = env["settings"].model_copy(update={"ai_provider": "disabled"})
    monkeypatch.setattr(activities, "get_settings", lambda: disabled)
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    for row in env["rows"]:
        result = await activities.news_exact_index_activity(
            NewsSourceWork(
                source_id=str(row.source_id),
                workspace_id=str(WORKSPACE),
                user_id=str(USER),
                request_id=str(uuid4()),
                defer=True,
            )
        )
        assert result.status == "COMPLETED"
    async with ai_database() as database:
        assert await database.scalar(select(func.count()).select_from(NewsStoryItem)) == 2
        assert (
            await database.scalar(select(func.count()).select_from(NewsClusterEmbeddingRequest))
            == 0
        )


@pytest.mark.asyncio
async def test_workflow_runs_exact_recovery_when_clustering_fails(monkeypatch) -> None:
    from navox.news import activities
    from navox.workflows import news

    calls = []
    payload = NewsSourceWork(str(uuid4()), str(WORKSPACE), str(USER), str(uuid4()), defer=True)

    async def execute(fn, received, **kwargs):
        calls.append(fn)
        if fn is activities.ingest_news_source_activity:
            return NewsWorkResult("COMPLETED", 1, 0, True)
        if fn is activities.news_clustering_activity:
            raise ActivityError(
                "clustering unavailable",
                scheduled_event_id=1,
                started_event_id=2,
                identity="worker",
                activity_type="news_clustering_activity",
                activity_id="1",
                retry_state=None,
            )
        return NewsWorkResult("COMPLETED", 1)

    monkeypatch.setattr(news.workflow, "execute_activity", execute)
    monkeypatch.setattr(news.workflow, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)
    await news.NewsSourceIngestionWorkflow().run(payload)
    assert calls[:3] == [
        activities.ingest_news_source_activity,
        activities.news_clustering_activity,
        activities.news_exact_index_activity,
    ]


@pytest.mark.asyncio
async def test_workflow_skips_recovery_when_clustering_finished(monkeypatch) -> None:
    from navox.news import activities
    from navox.workflows import news

    calls = []
    payload = NewsSourceWork(str(uuid4()), str(WORKSPACE), str(USER), str(uuid4()), defer=True)

    async def execute(fn, received, **kwargs):
        calls.append(fn)
        if fn is activities.ingest_news_source_activity:
            return NewsWorkResult("COMPLETED", 1, 0, True)
        return NewsWorkResult("COMPLETED", 2)

    monkeypatch.setattr(news.workflow, "execute_activity", execute)
    monkeypatch.setattr(news.workflow, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)
    await news.NewsSourceIngestionWorkflow().run(payload)
    assert activities.news_clustering_activity in calls
    assert activities.news_exact_index_activity not in calls


@pytest.mark.asyncio
async def test_workflow_skips_recovery_when_nothing_was_deferred(monkeypatch) -> None:
    from navox.news import activities
    from navox.workflows import news

    calls = []
    payload = NewsSourceWork(str(uuid4()), str(WORKSPACE), str(USER), str(uuid4()))

    async def execute(fn, received, **kwargs):
        calls.append(fn)
        if fn is activities.ingest_news_source_activity:
            return NewsWorkResult("COMPLETED", 1)
        return NewsWorkResult("COMPLETED", 1)

    monkeypatch.setattr(news.workflow, "execute_activity", execute)
    monkeypatch.setattr(news.workflow, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)
    await news.NewsSourceIngestionWorkflow().run(payload)
    assert activities.news_exact_index_activity not in calls
    assert activities.news_clustering_activity not in calls


@pytest.mark.asyncio
async def test_workflow_propagates_clustering_cancellation_without_recovery(
    monkeypatch,
) -> None:
    from navox.news import activities
    from navox.workflows import news

    calls = []
    payload = NewsSourceWork(str(uuid4()), str(WORKSPACE), str(USER), str(uuid4()), defer=True)

    async def execute(fn, received, **kwargs):
        calls.append(fn)
        if fn is activities.ingest_news_source_activity:
            return NewsWorkResult("COMPLETED", 1, 0, True)
        raise CancelledError("clustering cancelled")

    monkeypatch.setattr(news.workflow, "execute_activity", execute)
    monkeypatch.setattr(news.workflow, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)
    with pytest.raises(CancelledError):
        await news.NewsSourceIngestionWorkflow().run(payload)
    assert activities.news_exact_index_activity not in calls


@pytest.mark.asyncio
async def test_postgres_lock_order_is_user_before_source(ai_database, monkeypatch) -> None:
    if not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"):
        pytest.skip("Row-lock ordering requires PostgreSQL")
    await seed(ai_database)
    from sqlalchemy import event

    from navox.news import activities

    # The activity uses the real clock, so this fixture's policy and reviews are
    # anchored at the real current time rather than at the fixed test instant.
    moment = datetime.now(UTC)
    env = await build_env(ai_database, retention_days=30)
    report = reviewed_report()
    policy = deployment_policy(
        report,
        source_keys=tuple(env["definitions"]),
        registry_revision="2",
        reviewed_at=moment - timedelta(hours=1),
        expires_at=moment + timedelta(days=2),
    )
    env["policy"] = policy
    env["settings"] = settings_for(env["definitions"], policy=policy, report=report)
    gateway = StubGateway(namespace=policy.namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    clusterer.clock = lambda: moment
    async with ai_database() as database:
        for row in env["rows"]:
            await add_feature(
                database,
                row,
                env,
                now=moment,
                event_identity=f"event-{row.external_id}",
            )
        await database.commit()
    engine = ai_database.kw["bind"]
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(activities, "get_settings", lambda: env["settings"])
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(activities, "semantic_clusterer", lambda *args, **kwargs: clusterer)
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        await activities.news_clustering_activity(await activity_work(env, defer=True))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    locks = [statement.lower() for statement in statements if "for update" in statement.lower()]
    assert locks, "the clustering pass must take row locks"
    # The deadlock-prone order is source -> user; the source must never be locked
    # before the user row that reserve_attempt and commit lock first.
    assert "news_sources" not in locks[0]
    assert any("users" in statement for statement in locks)
    users_at = next(index for index, statement in enumerate(locks) if "users" in statement)
    assert all(
        index > users_at for index, statement in enumerate(locks) if "news_sources" in statement
    )


# --------------------------------------------------- citations, fenced identities


@pytest.mark.asyncio
async def test_feature_citations_must_be_real_permitted_spans(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    (row,) = env["rows"]
    async with ai_database() as database:
        authorized = await authorized_item(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            news_item_id=row.id,
            definitions=env["definitions"],
            now=NOW,
        )
        # A span from another item or revision is rejected at the boundary.
        foreign = ClaimSpan(
            item_id=uuid4(),
            item_revision=row.revision,
            field="headline",
            start=0,
            end=len(row.headline),
        )
        with pytest.raises(ValueError):
            ReviewedFeatureRecord(
                **feature_kwargs(
                    authorized,
                    citations=(FeatureCitation(field="entity_ids", span=foreign),),
                )
            )
        # A span outside the stored text cannot be admitted.
        overlong = ClaimSpan(
            item_id=row.id,
            item_revision=row.revision,
            field="headline",
            start=0,
            end=len(row.headline) + 5,
        )
        with pytest.raises(NewsError, match="invalid_evidence"):
            await record_feature(
                database,
                record=ReviewedFeatureRecord(
                    **feature_kwargs(
                        authorized,
                        geography=(),
                        event_type=None,
                        citations=(FeatureCitation(field="entity_ids", span=overlong),),
                    )
                ),
                definitions=env["definitions"],
                now=NOW,
            )


@pytest.mark.asyncio
async def test_a_cited_feature_stops_applying_when_the_span_leaves_the_permit(
    ai_database,
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        for row in (first, second):
            authorized = await authorized_item(
                database,
                workspace_id=WORKSPACE,
                user_id=USER,
                news_item_id=row.id,
                definitions=env["definitions"],
                now=NOW,
            )
            # The identity is cited from the snippet, not from the headline.
            await record_feature(
                database,
                record=ReviewedFeatureRecord(
                    **feature_kwargs(
                        authorized,
                        citations=(
                            FeatureCitation(
                                field="entity_ids",
                                span=reviewed_span(authorized, description=True),
                            ),
                            FeatureCitation(
                                field="geographic_ids",
                                span=reviewed_span(authorized, description=True),
                            ),
                            FeatureCitation(
                                field="event_type",
                                span=reviewed_span(authorized, description=True),
                            ),
                        ),
                    )
                ),
                definitions=env["definitions"],
                now=NOW,
            )
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        # Narrowing the snippet permission removes the cited text, so the record
        # must stop applying and the candidate set becomes incomplete.
        source = await database.get(NewsSource, first.source_id, populate_existing=True)
        assert source is not None
        narrowed = definition(summary_generation_allowed=True).rights.model_copy(
            update={"snippet_storage_allowed": False}
        )
        database.add(
            NewsContentRights(
                source_id=source.id,
                version=source.rights_version + 1,
                policy=narrowed.model_dump(mode="json"),
                policy_digest=policy_digest(narrowed),
            )
        )
        source.rights_version += 1
        await database.commit()
        story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert story.id != first.id
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2
    attempt = {row.news_item_id: row for row in clusterer.attempts}[second.id]
    assert attempt.status == "UNCERTAIN"


@pytest.mark.asyncio
async def test_future_and_expired_reviews_are_rejected(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database, sources=1)
    (row,) = env["rows"]
    async with ai_database() as database:
        authorized = await authorized_item(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            news_item_id=row.id,
            definitions=env["definitions"],
            now=NOW,
        )
        with pytest.raises(NewsError, match="invalid_reference"):
            await record_feature(
                database,
                record=ReviewedFeatureRecord(
                    **(
                        feature_kwargs(authorized)
                        | {
                            "reviewed_at": NOW + timedelta(minutes=5),
                            "expires_at": NOW + timedelta(hours=1),
                        }
                    )
                ),
                definitions=env["definitions"],
                now=NOW,
            )
        with pytest.raises(NewsError, match="stale_evidence"):
            await record_feature(
                database,
                record=ReviewedFeatureRecord(
                    **(
                        feature_kwargs(authorized)
                        | {
                            "reviewed_at": NOW - timedelta(hours=3),
                            "expires_at": NOW - timedelta(hours=1),
                        }
                    )
                ),
                definitions=env["definitions"],
                now=NOW,
            )


# ----------------------------------------------- policy identity, vector metadata


@pytest.mark.asyncio
async def test_same_reference_policy_replacement_blocks_the_commit(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env)
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        proposal = await clusterer.propose(database, second, env["definitions"], now=NOW)
        assert proposal is not None
        # A same-reference policy with different thresholds is a different policy.
        replaced = deployment_policy(
            reviewed_report(),
            source_keys=tuple(env["definitions"]),
            registry_revision="2",
            minimum_margin=0.0,
        )
        assert replaced.reference == env["policy"].reference
        clusterer.settings = env["settings"].model_copy(
            update={"news_semantic_deployment_policy": replaced.model_dump(mode="json")}
        )
        with pytest.raises(NewsError):
            await clusterer.commit(database, second, env["definitions"], proposal=proposal, now=NOW)
        assert await database.scalar(select(func.count()).select_from(NewsStoryItem)) == 1


@pytest.mark.asyncio
async def test_candidate_vector_namespace_metadata_mismatch_stays_incomplete(
    ai_database,
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env)
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        embedding = await database.scalar(
            select(NewsClusterEmbedding).where(NewsClusterEmbedding.news_item_id == first.id)
        )
        assert embedding is not None
        # Fields disagree with the digest column: the vector is not trustworthy.
        embedding.artifact = "other-artifact@v9"
        await database.commit()
        story = await index_item(
            database, second, env["definitions"], now=NOW, clustering=clusterer
        )
        await database.commit()
        assert story.id != first.id
        assert await database.scalar(select(func.count()).select_from(NewsStory)) == 2


@pytest.mark.asyncio
async def test_query_vector_dimension_mismatch_blocks_the_commit(ai_database) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env)
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        proposal = await clusterer.propose(database, second, env["definitions"], now=NOW)
        assert proposal is not None
        embedding = await database.scalar(
            select(NewsClusterEmbedding).where(NewsClusterEmbedding.news_item_id == second.id)
        )
        assert embedding is not None
        embedding.dimension = len(embedding.vector) + 1
        await database.commit()
        with pytest.raises(NewsError):
            await clusterer.commit(database, second, env["definitions"], proposal=proposal, now=NOW)


@pytest.mark.asyncio
async def test_commit_uses_the_current_time_not_the_pre_provider_instant(
    ai_database,
) -> None:
    await seed(ai_database)
    env = await build_env(ai_database)
    gateway = StubGateway(namespace=env["policy"].namespace)
    clusterer = clusterer_for(env, gateway=gateway, factory=ai_database)
    first, second = env["rows"]
    async with ai_database() as database:
        await add_feature(database, first, env)
        await add_feature(database, second, env, event_identity="event-second")
        await database.commit()
        await index_item(database, first, env["definitions"], now=NOW, clustering=clusterer)
        await database.commit()
        proposal = await clusterer.propose(database, second, env["definitions"], now=NOW)
        assert proposal is not None
        # The reviewed record expires two hours after NOW; a stale pre-provider
        # instant must not authorize it once the real clock has moved past it.
        clusterer.clock = lambda: NOW + timedelta(hours=3)
        with pytest.raises(NewsError):
            await clusterer.commit(database, second, env["definitions"], proposal=proposal, now=NOW)
