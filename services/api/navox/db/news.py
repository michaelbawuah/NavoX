"""Tenant-scoped news provenance and immutable content-rights versions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class NewsSource(Base):
    __tablename__ = "news_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "source_key", name="uq_news_source_owner_key"),
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_news_source_scope"),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    source_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(253))
    source_type: Mapped[str] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(32))
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    independence_group: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="disabled")
    config_digest: Mapped[str] = mapped_column(String(64))
    rights_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsContentRights(Base):
    __tablename__ = "news_content_rights"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_news_rights_source_version"),
        UniqueConstraint("id", "source_id", name="uq_news_rights_source_binding"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("news_sources.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict[str, object]] = mapped_column(JSON)
    policy_digest: Mapped[str] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsSourceFeed(Base):
    __tablename__ = "news_source_feeds"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("news_sources.id", ondelete="CASCADE"), unique=True
    )
    feed_type: Mapped[str] = mapped_column(String(32))
    endpoint_reference: Mapped[str] = mapped_column(String(2048))
    category: Mapped[str] = mapped_column(String(32))
    poll_interval_seconds: Mapped[int] = mapped_column(Integer)
    health_status: Mapped[str] = mapped_column(String(32), default="not_started")
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_news_item_external"),
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_news_item_scope"),
        UniqueConstraint("id", "source_id", name="uq_news_item_source"),
        ForeignKeyConstraint(
            ["source_id", "workspace_id", "user_id"],
            ["news_sources.id", "news_sources.workspace_id", "news_sources.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["rights_profile_id", "source_id"],
            ["news_content_rights.id", "news_content_rights.source_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    source_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    rights_profile_id: Mapped[UUID] = mapped_column(Uuid)
    external_id: Mapped[str] = mapped_column(String(512))
    headline: Mapped[str] = mapped_column(String(500))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    author: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(JSON)
    language: Mapped[str] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_digest: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class NewsIngestionReceipt(Base):
    __tablename__ = "news_ingestion_receipts"
    __table_args__ = (
        UniqueConstraint("source_id", "request_id", name="uq_news_ingestion_request"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("news_sources.id", ondelete="CASCADE"))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(32))
    stored_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsStory(Base):
    __tablename__ = "news_story_clusters"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_news_story_scope"),
        ForeignKeyConstraint(
            ["anchor_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    # Titles are projected from this rights-checked item instead of an untracked copy.
    anchor_item_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="DISCOVERED")
    primary_category: Mapped[str] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    importance_review: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)


class NewsStoryItem(Base):
    __tablename__ = "news_story_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cluster_id", "workspace_id", "user_id"],
            [
                "news_story_clusters.id",
                "news_story_clusters.workspace_id",
                "news_story_clusters.user_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
    )
    news_item_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    cluster_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    item_revision: Mapped[int] = mapped_column(Integer)
    url_digest: Mapped[str] = mapped_column(String(64), index=True)
    copy_digest: Mapped[str | None] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    # Audit of a semantic join: the reviewed policy reference and embedding
    # namespace that authorized it. Null for exact identity memberships, and
    # never a copy of source text.
    match_reference: Mapped[str | None] = mapped_column(String(128))
    match_namespace: Mapped[str | None] = mapped_column(String(64))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NewsClaim(Base):
    __tablename__ = "news_claims"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_news_claim_scope"),
        UniqueConstraint(
            "cluster_id", "origin_item_id", "text_digest", name="uq_news_claim_origin"
        ),
        ForeignKeyConstraint(
            ["cluster_id", "workspace_id", "user_id"],
            [
                "news_story_clusters.id",
                "news_story_clusters.workspace_id",
                "news_story_clusters.user_id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["origin_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    origin_item_id: Mapped[UUID] = mapped_column(Uuid)
    origin_revision: Mapped[int] = mapped_column(Integer)
    claim_text: Mapped[str] = mapped_column(String(2000))
    text_digest: Mapped[str] = mapped_column(String(64))
    claim_type: Mapped[str] = mapped_column(String(64))
    attributed_to: Mapped[str | None] = mapped_column(String(200))
    verification_status: Mapped[str] = mapped_column(String(32), default="UNCONFIRMED")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsClaimEvidence(Base):
    __tablename__ = "news_claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "news_item_id", name="uq_news_evidence_claim_item"),
        ForeignKeyConstraint(
            ["claim_id", "workspace_id", "user_id"],
            ["news_claims.id", "news_claims.workspace_id", "news_claims.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["news_item_id", "source_id"],
            ["news_items.id", "news_items.source_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    news_item_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    source_id: Mapped[UUID] = mapped_column(Uuid)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    item_revision: Mapped[int] = mapped_column(Integer)
    # Store offsets, not duplicated source text. Revalidate against this revision at every read.
    quote_field: Mapped[str] = mapped_column(String(32))
    quote_start: Mapped[int] = mapped_column(Integer)
    quote_end: Mapped[int] = mapped_column(Integer)
    relationship: Mapped[str] = mapped_column(String(32))
    evidence_kind: Mapped[str] = mapped_column(String(32), default="UNREVIEWED")
    evidence_strength: Mapped[str] = mapped_column(String(32), default="weak")
    provenance_group: Mapped[str] = mapped_column(String(128))
    independence_group: Mapped[str] = mapped_column(String(128))
    origin_groups: Mapped[list[str]] = mapped_column(JSON)
    review_reference: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsStoryVersion(Base):
    __tablename__ = "news_story_versions"
    __table_args__ = (UniqueConstraint("cluster_id", "version", name="uq_news_story_version"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("news_story_clusters.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    # Content-free history remains visible when underlying evidence is corrected or withdrawn.
    change_kind: Mapped[str] = mapped_column(String(64))
    source_snapshot: Mapped[dict[str, int]] = mapped_column(JSON)
    claim_states: Mapped[dict[str, str]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NewsStoryPreference(Base):
    __tablename__ = "news_story_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cluster_id", "workspace_id", "user_id"],
            [
                "news_story_clusters.id",
                "news_story_clusters.workspace_id",
                "news_story_clusters.user_id",
            ],
            ondelete="CASCADE",
        ),
    )
    cluster_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    followed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_read_version: Mapped[int | None] = mapped_column(Integer)


class NewsPreference(Base):
    __tablename__ = "news_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    entities: Mapped[list[str]] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(32), default="en")
    region: Mapped[str] = mapped_column(String(64), default="world")
    reading_history_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsConversation(Base):
    __tablename__ = "news_conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["story_id", "workspace_id", "user_id"],
            [
                "news_story_clusters.id",
                "news_story_clusters.workspace_id",
                "news_story_clusters.user_id",
            ],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    story_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NewsConversationTurn(Base):
    __tablename__ = "news_conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id", name="uq_news_turn_request"),
        UniqueConstraint("conversation_id", "sequence", name="uq_news_turn_sequence"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("news_conversations.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(String(2000))
    intent: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    # Store selections and provenance IDs only. Every display reloads current source rights.
    selection: Mapped[dict[str, object] | None] = mapped_column(JSON)
    source_snapshot: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    retrieval_plan: Mapped[dict[str, object] | None] = mapped_column(JSON)
    retrieval_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_digest: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[UUID | None] = mapped_column(Uuid)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NewsClusterFeature(Base):
    """Source-cited reviewed identity for one exact item revision and policy.

    Written only by an internal trusted review boundary. No model, provider or
    public request can create, widen or extend a record, and the record stops
    applying when the item revision, retained digest, rights fingerprint or
    review expiry changes.
    """

    __tablename__ = "news_cluster_features"
    __table_args__ = (
        UniqueConstraint("news_item_id", "item_revision", name="uq_news_cluster_feature_revision"),
        ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["news_item_id", "source_id"],
            ["news_items.id", "news_items.source_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    news_item_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    item_revision: Mapped[int] = mapped_column(Integer)
    item_digest: Mapped[str] = mapped_column(String(64))
    rights_fingerprint: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[UUID] = mapped_column(Uuid)
    language: Mapped[str] = mapped_column(String(32))
    entity_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    geographic_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    event_type: Mapped[str | None] = mapped_column(String(64))
    event_identity: Mapped[str | None] = mapped_column(String(128))
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    citations: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    reviewer: Mapped[str] = mapped_column(String(256))
    review_reference: Mapped[str] = mapped_column(String(128))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NewsClusterEmbedding(Base):
    """One stored item vector inside one exact embedding namespace.

    The vector is stored normalized, bound to the item revision, retained
    content digest and rights fingerprint it was produced from, so a rebuilt or
    revoked item can never be compared under its old revision.
    """

    __tablename__ = "news_cluster_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "news_item_id",
            "item_revision",
            "namespace_digest",
            name="uq_news_cluster_embedding_namespace",
        ),
        ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    news_item_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    item_revision: Mapped[int] = mapped_column(Integer)
    item_digest: Mapped[str] = mapped_column(String(64))
    rights_fingerprint: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[UUID] = mapped_column(Uuid)
    namespace_digest: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    registry_revision: Mapped[str] = mapped_column(String(64))
    artifact: Mapped[str] = mapped_column(String(128))
    pipeline_version: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NewsClusterEmbeddingRequest(Base):
    """Durable, per-user, bounded reservation for exactly one paid attempt.

    The reservation is committed before any provider call and is unique per
    request identifier and per item revision plus namespace, so a retry, crash
    or a second worker cannot buy the same embedding twice.
    """

    __tablename__ = "news_cluster_embedding_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_news_cluster_request_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "news_item_id",
            "item_revision",
            "namespace_digest",
            name="uq_news_cluster_attempt_namespace",
        ),
        ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    news_item_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    item_revision: Mapped[int] = mapped_column(Integer)
    namespace_digest: Mapped[str] = mapped_column(String(64))
    # Reproducible namespace reference; identifiers only, never source text.
    namespace_reference: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    registry_revision: Mapped[str | None] = mapped_column(String(64))
    dimension: Mapped[int | None] = mapped_column(Integer)
    cost_micros: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsIntelligenceRun(Base):
    __tablename__ = "news_intelligence_runs"
    __table_args__ = (
        UniqueConstraint("cluster_id", "base_version", name="uq_news_intelligence_version"),
        ForeignKeyConstraint(
            ["cluster_id", "workspace_id", "user_id"],
            [
                "news_story_clusters.id",
                "news_story_clusters.workspace_id",
                "news_story_clusters.user_id",
            ],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    base_version: Mapped[int] = mapped_column(Integer)
    result_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    # Only selections, revisions and trace IDs; never a copied article or generated prose.
    selection: Mapped[dict[str, object] | None] = mapped_column(JSON)
    source_snapshot: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    claim_snapshot: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    trace_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
