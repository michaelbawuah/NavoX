"""Knowledge index state, user exclusions and recent searches (SPEC-007 phase 2/3)."""

import sqlalchemy as sa
from alembic import op

revision = "0030_knowledge_search"
down_revision = "0029_knowledge_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite scope target for user-scoped exclusions. `id` is already unique,
    # so connection identity and behavior are unchanged.
    op.create_unique_constraint(
        "uq_connector_connections_workspace_id",
        "connector_connections",
        ["workspace_id", "id"],
    )
    op.create_table(
        "knowledge_resource_index",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=True),
        sa.Column("index_state", sa.String(length=24), nullable=False),
        sa.Column("structured_kind", sa.String(length=32), nullable=True),
        sa.Column("structured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("structured_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text_length", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "index_state IN ('INDEXED', 'NO_CONTENT', 'STALE')",
            name="ck_knowledge_resource_index_state",
        ),
        sa.CheckConstraint("chunk_count >= 0", name="ck_knowledge_resource_index_chunk_count"),
        sa.CheckConstraint("text_length >= 0", name="ck_knowledge_resource_index_text_length"),
        sa.ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_resource_index_resource_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id", "workspace_id", name="uq_knowledge_resource_index_scope"
        ),
    )
    op.create_index(
        "ix_knowledge_resource_index_workspace_id", "knowledge_resource_index", ["workspace_id"]
    )
    op.create_index(
        "ix_knowledge_resource_index_resource_id", "knowledge_resource_index", ["resource_id"]
    )
    op.create_index(
        "ix_knowledge_resource_index_structured_at", "knowledge_resource_index", ["structured_at"]
    )
    op.create_table(
        "knowledge_exclusions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("source_connection_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=512), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('SOURCE', 'FOLDER', 'RESOURCE', 'TYPE')",
            name="ck_knowledge_exclusions_scope",
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_connection_id"],
            ["connector_connections.workspace_id", "connector_connections.id"],
            ondelete="CASCADE",
            name="fk_knowledge_exclusions_source_scope",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_exclusions_resource_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
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
    op.create_index(
        "ix_knowledge_exclusions_workspace_id", "knowledge_exclusions", ["workspace_id"]
    )
    op.create_index("ix_knowledge_exclusions_user_id", "knowledge_exclusions", ["user_id"])
    op.create_table(
        "knowledge_recent_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("query", sa.String(length=2000), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "mode",
            "query",
            name="uq_knowledge_recent_searches_query",
        ),
    )
    op.create_index(
        "ix_knowledge_recent_searches_workspace_id", "knowledge_recent_searches", ["workspace_id"]
    )
    op.create_index(
        "ix_knowledge_recent_searches_user_id", "knowledge_recent_searches", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_recent_searches_user_id", table_name="knowledge_recent_searches")
    op.drop_index(
        "ix_knowledge_recent_searches_workspace_id", table_name="knowledge_recent_searches"
    )
    op.drop_table("knowledge_recent_searches")
    op.drop_index("ix_knowledge_exclusions_user_id", table_name="knowledge_exclusions")
    op.drop_index("ix_knowledge_exclusions_workspace_id", table_name="knowledge_exclusions")
    op.drop_table("knowledge_exclusions")
    op.drop_index(
        "ix_knowledge_resource_index_structured_at", table_name="knowledge_resource_index"
    )
    op.drop_index("ix_knowledge_resource_index_resource_id", table_name="knowledge_resource_index")
    op.drop_index("ix_knowledge_resource_index_workspace_id", table_name="knowledge_resource_index")
    op.drop_table("knowledge_resource_index")
    op.drop_constraint(
        "uq_connector_connections_workspace_id",
        "connector_connections",
        type_="unique",
    )
