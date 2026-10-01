"""Vectors, sessions, turns, entities and relationships (SPEC-007 phase 3)."""

import sqlalchemy as sa
from alembic import op

revision = "0031_knowledge_intelligence"
down_revision = "0030_knowledge_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("registry_revision", sa.String(length=64), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dimension >= 1 AND dimension <= 4096", name="ck_knowledge_embeddings_dimension"
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_embeddings_chunk_index"),
        sa.ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_embeddings_resource_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id",
            "chunk_index",
            "provider",
            "model",
            "registry_revision",
            "embedding_version",
            "dimension",
            name="uq_knowledge_embeddings_namespace",
        ),
    )
    op.create_index(
        "ix_knowledge_embeddings_workspace_id", "knowledge_embeddings", ["workspace_id"]
    )
    op.create_index("ix_knowledge_embeddings_resource_id", "knowledge_embeddings", ["resource_id"])
    op.create_table(
        "knowledge_embedding_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("registry_revision", sa.String(length=64), nullable=True),
        sa.Column("embedding_version", sa.String(length=64), nullable=True),
        sa.Column("dimension", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("cost_micros", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope IN ('QUERY', 'DOCUMENT')",
            name="ck_knowledge_embedding_requests_scope",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'UNAVAILABLE', 'FAILED', 'DISCARDED')",
            name="ck_knowledge_embedding_requests_status",
        ),
        sa.CheckConstraint(
            "dimension IS NULL OR (dimension >= 1 AND dimension <= 4096)",
            name="ck_knowledge_embedding_requests_dimension",
        ),
        sa.CheckConstraint(
            "chunk_index IS NULL OR (chunk_index >= 0 AND chunk_index <= 200)",
            name="ck_knowledge_embedding_requests_chunk_index",
        ),
        sa.CheckConstraint(
            "cost_micros IS NULL OR cost_micros >= 0",
            name="ck_knowledge_embedding_requests_cost",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_embedding_requests_membership",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "request_id",
            name="uq_knowledge_embedding_requests_request",
        ),
    )
    op.create_index(
        "ix_knowledge_embedding_requests_workspace_id",
        "knowledge_embedding_requests",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_embedding_requests_user_id",
        "knowledge_embedding_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_knowledge_embedding_requests_quota",
        "knowledge_embedding_requests",
        ["workspace_id", "user_id", "created_at"],
    )
    op.create_table(
        "knowledge_answer_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("registry_revision", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("cost_micros", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'UNAVAILABLE', 'FAILED', 'REFUSED')",
            name="ck_knowledge_answer_requests_status",
        ),
        sa.CheckConstraint(
            "cost_micros IS NULL OR cost_micros >= 0",
            name="ck_knowledge_answer_requests_cost",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_answer_requests_membership",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "request_id",
            name="uq_knowledge_answer_requests_request",
        ),
    )
    op.create_index(
        "ix_knowledge_answer_requests_workspace_id",
        "knowledge_answer_requests",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_answer_requests_user_id",
        "knowledge_answer_requests",
        ["user_id"],
    )
    op.create_index(
        "ix_knowledge_answer_requests_quota",
        "knowledge_answer_requests",
        ["workspace_id", "user_id", "created_at"],
    )
    op.create_table(
        "knowledge_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_sessions_membership",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", "user_id", name="uq_knowledge_sessions_scope"),
    )
    op.create_index("ix_knowledge_sessions_workspace_id", "knowledge_sessions", ["workspace_id"])
    op.create_index("ix_knowledge_sessions_user_id", "knowledge_sessions", ["user_id"])
    op.create_table(
        "knowledge_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question", sa.String(length=2000), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("answer_state", sa.String(length=24), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("ranking_version", sa.String(length=64), nullable=True),
        sa.Column("freshness", sa.String(length=24), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'UNAVAILABLE', 'FAILED')",
            name="ck_knowledge_turns_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "workspace_id", "user_id"],
            [
                "knowledge_sessions.id",
                "knowledge_sessions.workspace_id",
                "knowledge_sessions.user_id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_turns_session",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "request_id", name="uq_knowledge_turns_request"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_knowledge_turns_sequence"),
    )
    op.create_index("ix_knowledge_turns_workspace_id", "knowledge_turns", ["workspace_id"])
    op.create_index("ix_knowledge_turns_user_id", "knowledge_turns", ["user_id"])
    op.create_index("ix_knowledge_turns_session_id", "knowledge_turns", ["session_id"])
    op.create_table(
        "knowledge_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=48), nullable=False),
        sa.Column("canonical_key", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_resource_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('RESOLVED', 'POSSIBLE_MATCH', 'AMBIGUOUS', 'DISTINCT')",
            name="ck_knowledge_entities_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_knowledge_entities_scope"),
        sa.UniqueConstraint(
            "workspace_id", "entity_type", "canonical_key", name="uq_knowledge_entities_identity"
        ),
    )
    op.create_index("ix_knowledge_entities_workspace_id", "knowledge_entities", ["workspace_id"])
    op.create_table(
        "knowledge_relationships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("from_entity_id", sa.Uuid(), nullable=False),
        sa.Column("to_entity_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=48), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("evidence_resource_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.String(length=256), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('RESOLVED', 'POSSIBLE_MATCH', 'AMBIGUOUS', 'DISTINCT')",
            name="ck_knowledge_relationships_state",
        ),
        sa.ForeignKeyConstraint(
            ["from_entity_id", "workspace_id"],
            ["knowledge_entities.id", "knowledge_entities.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_relationships_from",
        ),
        sa.ForeignKeyConstraint(
            ["to_entity_id", "workspace_id"],
            ["knowledge_entities.id", "knowledge_entities.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_relationships_to",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_entity_id",
            "to_entity_id",
            "relation_type",
            name="uq_knowledge_relationships_edge",
        ),
    )
    op.create_index(
        "ix_knowledge_relationships_workspace_id", "knowledge_relationships", ["workspace_id"]
    )
    op.create_index(
        "ix_knowledge_relationships_from_entity_id",
        "knowledge_relationships",
        ["from_entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_answer_requests_quota", table_name="knowledge_answer_requests")
    op.drop_index("ix_knowledge_answer_requests_user_id", table_name="knowledge_answer_requests")
    op.drop_index(
        "ix_knowledge_answer_requests_workspace_id", table_name="knowledge_answer_requests"
    )
    op.drop_table("knowledge_answer_requests")
    op.drop_index(
        "ix_knowledge_embedding_requests_quota", table_name="knowledge_embedding_requests"
    )
    op.drop_index(
        "ix_knowledge_embedding_requests_user_id", table_name="knowledge_embedding_requests"
    )
    op.drop_index(
        "ix_knowledge_embedding_requests_workspace_id", table_name="knowledge_embedding_requests"
    )
    op.drop_table("knowledge_embedding_requests")
    op.drop_index("ix_knowledge_relationships_from_entity_id", table_name="knowledge_relationships")
    op.drop_index("ix_knowledge_relationships_workspace_id", table_name="knowledge_relationships")
    op.drop_table("knowledge_relationships")
    op.drop_index("ix_knowledge_entities_workspace_id", table_name="knowledge_entities")
    op.drop_table("knowledge_entities")
    op.drop_index("ix_knowledge_turns_session_id", table_name="knowledge_turns")
    op.drop_index("ix_knowledge_turns_user_id", table_name="knowledge_turns")
    op.drop_index("ix_knowledge_turns_workspace_id", table_name="knowledge_turns")
    op.drop_table("knowledge_turns")
    op.drop_index("ix_knowledge_sessions_user_id", table_name="knowledge_sessions")
    op.drop_index("ix_knowledge_sessions_workspace_id", table_name="knowledge_sessions")
    op.drop_table("knowledge_sessions")
    op.drop_index("ix_knowledge_embeddings_resource_id", table_name="knowledge_embeddings")
    op.drop_index("ix_knowledge_embeddings_workspace_id", table_name="knowledge_embeddings")
    op.drop_table("knowledge_embeddings")
