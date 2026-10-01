"""Tenant-scoped canonical knowledge resources, permissions and chunks.

These tables hold derived, searchable representations. Source systems remain
authoritative, and no row here is evidence of provider authority by itself.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class KnowledgeResource(Base):
    """One canonical resource with its canonical connector provenance binding."""

    __tablename__ = "knowledge_resources"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_knowledge_resources_scope"),
        UniqueConstraint(
            "workspace_id",
            "source_connection_id",
            "external_resource_id",
            name="uq_knowledge_resources_workspace_connection_external",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_connection_id", "owner_user_id"],
            [
                "connector_connections.workspace_id",
                "connector_connections.id",
                "connector_connections.user_id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_resources_source_connection_scope",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_resources_owner_membership",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "source_connection_id",
                "external_resource_id",
                "source_resource_id",
            ],
            [
                "connector_resources.workspace_id",
                "connector_resources.connector_connection_id",
                "connector_resources.external_id",
                "connector_resources.id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_resources_canonical_provenance",
        ),
        CheckConstraint(
            "source_type IN ('EMAIL', 'EMAIL_THREAD', 'CALENDAR_EVENT', 'DOCUMENT', 'FILE',"
            " 'CANVAS_ASSIGNMENT', 'CANVAS_ANNOUNCEMENT', 'MEETING', 'COMMITMENT',"
            " 'SUBSCRIPTION', 'NEWS_STORY', 'TASK', 'OTHER')",
            name="ck_knowledge_resources_source_type",
        ),
        CheckConstraint(
            "sensitivity IN ('PUBLIC', 'INTERNAL', 'PERSONAL', 'SENSITIVE', 'RESTRICTED')",
            name="ck_knowledge_resources_sensitivity",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_connection_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    external_resource_id: Mapped[str] = mapped_column(String(512))
    # Optional provenance pointer: a resource without a live canonical binding is
    # stored here but is never M1 VIEW-eligible.
    source_resource_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # Trusted binding to the exact read capability authority was checked against.
    source_read_capability: Mapped[str] = mapped_column(String(160))
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(32))
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_extraction_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ranking_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_metadata: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeResourcePermission(Base):
    """One independent permission capability row for a knowledge resource."""

    __tablename__ = "knowledge_resource_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_resource_permissions_resource_scope",
        ),
        CheckConstraint(
            "principal_type IN ('USER', 'WORKSPACE', 'GROUP', 'PUBLIC')",
            name="ck_knowledge_resource_permissions_principal_type",
        ),
        CheckConstraint(
            "permission IN ('VIEW', 'COMMENT', 'EDIT', 'OWNER')",
            name="ck_knowledge_resource_permissions_value",
        ),
        CheckConstraint(
            "(principal_type = 'PUBLIC' AND principal_id IS NULL)"
            " OR (principal_type <> 'PUBLIC' AND principal_id IS NOT NULL)",
            name="ck_knowledge_resource_permissions_principal",
        ),
        CheckConstraint(
            "principal_type <> 'WORKSPACE' OR principal_id = workspace_id",
            name="ck_knowledge_resource_permissions_workspace_principal",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="ck_knowledge_resource_permissions_interval",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    principal_type: Mapped[str] = mapped_column(String(16))
    principal_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    permission: Mapped[str] = mapped_column(String(16), index=True)
    inherited: Mapped[bool] = mapped_column(Boolean, default=False)
    source_permission_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeChunk(Base):
    """A chunk of one resource. Chunks inherit resource authority and sensitivity."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("resource_id", "chunk_index", name="uq_knowledge_chunks_resource_index"),
        ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_resource_scope",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index"),
        CheckConstraint("token_count >= 0", name="ck_knowledge_chunks_token_count"),
        CheckConstraint("page_number IS NULL OR page_number > 0", name="ck_knowledge_chunks_page"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeResourceIndex(Base):
    """Derived index state for one knowledge resource.

    Kept beside ``knowledge_resources`` so the canonical resource row keeps its
    reviewed shape: this row carries the fence (the canonical content hash the
    representation was built from) and the honest coverage state of projection.
    """

    __tablename__ = "knowledge_resource_index"
    __table_args__ = (
        UniqueConstraint("resource_id", "workspace_id", name="uq_knowledge_resource_index_scope"),
        ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_resource_index_resource_scope",
        ),
        CheckConstraint(
            "index_state IN ('INDEXED', 'NO_CONTENT', 'STALE')",
            name="ck_knowledge_resource_index_state",
        ),
        CheckConstraint("chunk_count >= 0", name="ck_knowledge_resource_index_chunk_count"),
        CheckConstraint("text_length >= 0", name="ck_knowledge_resource_index_text_length"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    # Canonical revision fence. A changed source hash invalidates text/chunks.
    source_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    index_state: Mapped[str] = mapped_column(String(24), default="INDEXED")
    # Typed structured facts (calendar start/end, assignment or task due date).
    structured_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    structured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    structured_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    text_length: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeExclusion(Base):
    """One user-scoped exclusion applied before any retrieval reads content.

    Scope controls which column carries the target. FOLDER uses the
    source-controlled external parent identity, never a metadata claim.
    """

    __tablename__ = "knowledge_exclusions"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('SOURCE', 'FOLDER', 'RESOURCE', 'TYPE')",
            name="ck_knowledge_exclusions_scope",
        ),
        CheckConstraint(
            "(scope = 'SOURCE' AND source_connection_id IS NOT NULL"
            " AND external_id IS NULL AND resource_id IS NULL AND resource_type IS NULL)"
            " OR (scope = 'FOLDER' AND source_connection_id IS NOT NULL"
            " AND external_id IS NOT NULL AND resource_id IS NULL AND resource_type IS NULL)"
            " OR (scope = 'RESOURCE' AND resource_id IS NOT NULL"
            " AND source_connection_id IS NULL AND external_id IS NULL"
            " AND resource_type IS NULL)"
            " OR (scope = 'TYPE' AND resource_type IS NOT NULL"
            " AND source_connection_id IS NULL AND external_id IS NULL AND resource_id IS NULL)",
            name="ck_knowledge_exclusions_target",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_connection_id"],
            ["connector_connections.workspace_id", "connector_connections.id"],
            ondelete="CASCADE",
            name="fk_knowledge_exclusions_source_scope",
        ),
        ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_exclusions_resource_scope",
        ),
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "scope",
            "source_connection_id",
            "resource_id",
            "resource_type",
            "external_id",
            name="uq_knowledge_exclusions_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    scope: Mapped[str] = mapped_column(String(16))
    source_connection_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeRecentSearch(Base):
    """One bounded, user-scoped recent query. Clearing history never touches the index."""

    __tablename__ = "knowledge_recent_searches"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "mode",
            "query",
            name="uq_knowledge_recent_searches_query",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    mode: Mapped[str] = mapped_column(String(16))
    query: Mapped[str] = mapped_column(String(2000))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeEmbedding(Base):
    """One stored vector, bound to its resource, revision and model namespace."""

    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "chunk_index",
            "provider",
            "model",
            "registry_revision",
            "embedding_version",
            "dimension",
            name="uq_knowledge_embeddings_namespace",
        ),
        ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_embeddings_resource_scope",
        ),
        CheckConstraint(
            "dimension >= 1 AND dimension <= 4096",
            name="ck_knowledge_embeddings_dimension",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_knowledge_embeddings_chunk_index"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    source_content_hash: Mapped[str] = mapped_column(String(64))
    sensitivity: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    registry_revision: Mapped[str] = mapped_column(String(64))
    embedding_version: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[float]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeEmbeddingRequest(Base):
    """One durable paid embedding attempt, reserved before any provider call.

    The row is the reservation: ``(workspace_id, user_id, request_id)`` is
    unique, so a retry can never buy a second provider request, and the
    per-user hourly quota counts these rows instead of trusting client state.
    Only identifiers and the returned namespace are stored; no query or
    document text is persisted here, and the row is not an authority grant.
    """

    __tablename__ = "knowledge_embedding_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "request_id",
            name="uq_knowledge_embedding_requests_request",
        ),
        Index(
            "ix_knowledge_embedding_requests_quota",
            "workspace_id",
            "user_id",
            "created_at",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_embedding_requests_membership",
        ),
        CheckConstraint(
            "scope IN ('QUERY', 'DOCUMENT')",
            name="ck_knowledge_embedding_requests_scope",
        ),
        CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'UNAVAILABLE', 'FAILED', 'DISCARDED')",
            name="ck_knowledge_embedding_requests_status",
        ),
        CheckConstraint(
            "dimension IS NULL OR (dimension >= 1 AND dimension <= 4096)",
            name="ck_knowledge_embedding_requests_dimension",
        ),
        CheckConstraint(
            "chunk_index IS NULL OR (chunk_index >= 0 AND chunk_index <= 200)",
            name="ck_knowledge_embedding_requests_chunk_index",
        ),
        CheckConstraint(
            "cost_micros IS NULL OR cost_micros >= 0",
            name="ck_knowledge_embedding_requests_cost",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    scope: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="RESERVED")
    # Identifier-only audit pointer; never used as authority for a read.
    resource_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # The single bounded chunk this attempt embedded, when it was a document.
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registry_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Non-sensitive coverage/diagnostic code, never provider detail.
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeAnswerRequest(Base):
    """One durable paid Ask attempt, reserved before any provider call.

    The row is the reservation and the replay fence: ``(workspace_id, user_id,
    request_id)`` is unique, so a retry can never buy a second attempt and a
    cleared or deleted session cannot refund the hourly quota. Only identifiers
    and trace metadata are stored here; no question or source text.
    """

    __tablename__ = "knowledge_answer_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "request_id",
            name="uq_knowledge_answer_requests_request",
        ),
        Index(
            "ix_knowledge_answer_requests_quota",
            "workspace_id",
            "user_id",
            "created_at",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_answer_requests_membership",
        ),
        CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'UNAVAILABLE', 'FAILED', 'REFUSED')",
            name="ck_knowledge_answer_requests_status",
        ),
        CheckConstraint(
            "cost_micros IS NULL OR cost_micros >= 0",
            name="ck_knowledge_answer_requests_cost",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), default="RESERVED")
    # Identifier-only pointers; they outlive a cleared or deleted session.
    session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    turn_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registry_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeSession(Base):
    """One owned Ask conversation. History never grants view."""

    __tablename__ = "knowledge_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_sessions_membership",
        ),
        UniqueConstraint("id", "workspace_id", "user_id", name="uq_knowledge_sessions_scope"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_turn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeTurn(Base):
    """One reserved or completed Ask attempt. Snapshots store IDs, never text."""

    __tablename__ = "knowledge_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "request_id", name="uq_knowledge_turns_request"),
        UniqueConstraint("session_id", "sequence", name="uq_knowledge_turns_sequence"),
        ForeignKeyConstraint(
            ["session_id", "workspace_id", "user_id"],
            [
                "knowledge_sessions.id",
                "knowledge_sessions.workspace_id",
                "knowledge_sessions.user_id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_turns_session",
        ),
        CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'UNAVAILABLE', 'FAILED')",
            name="ck_knowledge_turns_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(String(2000))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    answer_state: Mapped[str] = mapped_column(String(24))
    selection: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    ranking_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    freshness: Mapped[str | None] = mapped_column(String(24), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64))
    coverage: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeEntity(Base):
    """A source-cited entity. Names alone never merge identities."""

    __tablename__ = "knowledge_entities"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_knowledge_entities_scope"),
        UniqueConstraint(
            "workspace_id",
            "entity_type",
            "canonical_key",
            name="uq_knowledge_entities_identity",
        ),
        CheckConstraint(
            "state IN ('RESOLVED', 'POSSIBLE_MATCH', 'AMBIGUOUS', 'DISTINCT')",
            name="ck_knowledge_entities_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    entity_type: Mapped[str] = mapped_column(String(48))
    canonical_key: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(24))
    source_type: Mapped[str] = mapped_column(String(32))
    source_resource_id: Mapped[UUID] = mapped_column(Uuid)
    source_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeRelationship(Base):
    """One bounded, source-cited edge between two entities."""

    __tablename__ = "knowledge_relationships"
    __table_args__ = (
        UniqueConstraint(
            "from_entity_id",
            "to_entity_id",
            "relation_type",
            name="uq_knowledge_relationships_edge",
        ),
        ForeignKeyConstraint(
            ["from_entity_id", "workspace_id"],
            ["knowledge_entities.id", "knowledge_entities.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_relationships_from",
        ),
        ForeignKeyConstraint(
            ["to_entity_id", "workspace_id"],
            ["knowledge_entities.id", "knowledge_entities.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_relationships_to",
        ),
        CheckConstraint(
            "state IN ('RESOLVED', 'POSSIBLE_MATCH', 'AMBIGUOUS', 'DISTINCT')",
            name="ck_knowledge_relationships_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    from_entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    to_entity_id: Mapped[UUID] = mapped_column(Uuid)
    relation_type: Mapped[str] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(24))
    evidence_resource_id: Mapped[UUID] = mapped_column(Uuid)
    source_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
