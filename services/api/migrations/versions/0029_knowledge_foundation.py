"""Canonical knowledge resources, permissions and chunks (SPEC-007 M1)."""

import sqlalchemy as sa
from alembic import op

revision = "0029_knowledge_foundation"
down_revision = "0028_news_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite targets for the derived knowledge FKs. Both reference columns are
    # already unique, so canonical connector identity is unchanged.
    op.create_unique_constraint(
        "uq_connector_connections_workspace_identity",
        "connector_connections",
        ["workspace_id", "id", "user_id"],
    )
    op.create_unique_constraint(
        "uq_connector_resources_workspace_connection_external_id",
        "connector_resources",
        ["workspace_id", "connector_connection_id", "external_id", "id"],
    )
    op.create_table(
        "knowledge_resources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_connection_id", sa.Uuid(), nullable=False),
        sa.Column("external_resource_id", sa.String(length=512), nullable=False),
        sa.Column("source_resource_id", sa.Uuid(), nullable=True),
        sa.Column("source_read_capability", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("canonical_url", sa.String(length=2048), nullable=True),
        sa.Column("sensitivity", sa.String(length=32), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_version", sa.String(length=256), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("embedding_version", sa.String(length=64), nullable=True),
        sa.Column("entity_extraction_version", sa.String(length=64), nullable=True),
        sa.Column("ranking_version", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            ["workspace_id", "source_connection_id", "owner_user_id"],
            [
                "connector_connections.workspace_id",
                "connector_connections.id",
                "connector_connections.user_id",
            ],
            ondelete="CASCADE",
            name="fk_knowledge_resources_source_connection_scope",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            ondelete="CASCADE",
            name="fk_knowledge_resources_owner_membership",
        ),
        sa.ForeignKeyConstraint(
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
        sa.CheckConstraint(
            "source_type IN ('EMAIL', 'EMAIL_THREAD', 'CALENDAR_EVENT', 'DOCUMENT', 'FILE',"
            " 'CANVAS_ASSIGNMENT', 'CANVAS_ANNOUNCEMENT', 'MEETING', 'COMMITMENT',"
            " 'SUBSCRIPTION', 'NEWS_STORY', 'TASK', 'OTHER')",
            name="ck_knowledge_resources_source_type",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('PUBLIC', 'INTERNAL', 'PERSONAL', 'SENSITIVE', 'RESTRICTED')",
            name="ck_knowledge_resources_sensitivity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_knowledge_resources_scope"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_connection_id",
            "external_resource_id",
            name="uq_knowledge_resources_workspace_connection_external",
        ),
    )
    op.create_index("ix_knowledge_resources_workspace_id", "knowledge_resources", ["workspace_id"])
    op.create_index(
        "ix_knowledge_resources_owner_user_id", "knowledge_resources", ["owner_user_id"]
    )
    op.create_index(
        "ix_knowledge_resources_source_connection_id",
        "knowledge_resources",
        ["source_connection_id"],
    )
    op.create_index("ix_knowledge_resources_deleted_at", "knowledge_resources", ["deleted_at"])
    op.create_table(
        "knowledge_resource_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=True),
        sa.Column("permission", sa.String(length=16), nullable=False),
        sa.Column("inherited", sa.Boolean(), nullable=False),
        sa.Column("source_permission_id", sa.String(length=512), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "principal_type IN ('USER', 'WORKSPACE', 'GROUP', 'PUBLIC')",
            name="ck_knowledge_resource_permissions_principal_type",
        ),
        sa.CheckConstraint(
            "permission IN ('VIEW', 'COMMENT', 'EDIT', 'OWNER')",
            name="ck_knowledge_resource_permissions_value",
        ),
        sa.CheckConstraint(
            "(principal_type = 'PUBLIC' AND principal_id IS NULL)"
            " OR (principal_type <> 'PUBLIC' AND principal_id IS NOT NULL)",
            name="ck_knowledge_resource_permissions_principal",
        ),
        sa.CheckConstraint(
            "principal_type <> 'WORKSPACE' OR principal_id = workspace_id",
            name="ck_knowledge_resource_permissions_workspace_principal",
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="ck_knowledge_resource_permissions_interval",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_resource_permissions_resource_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_resource_permissions_resource_id",
        "knowledge_resource_permissions",
        ["resource_id"],
    )
    op.create_index(
        "ix_knowledge_resource_permissions_workspace_id",
        "knowledge_resource_permissions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_knowledge_resource_permissions_permission",
        "knowledge_resource_permissions",
        ["permission"],
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_index"),
        sa.CheckConstraint("token_count >= 0", name="ck_knowledge_chunks_token_count"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number > 0", name="ck_knowledge_chunks_page"
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "workspace_id"],
            ["knowledge_resources.id", "knowledge_resources.workspace_id"],
            ondelete="CASCADE",
            name="fk_knowledge_chunks_resource_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "resource_id", "chunk_index", name="uq_knowledge_chunks_resource_index"
        ),
    )
    op.create_index("ix_knowledge_chunks_workspace_id", "knowledge_chunks", ["workspace_id"])
    op.create_index("ix_knowledge_chunks_resource_id", "knowledge_chunks", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_resource_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_workspace_id", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index(
        "ix_knowledge_resource_permissions_permission",
        table_name="knowledge_resource_permissions",
    )
    op.drop_index(
        "ix_knowledge_resource_permissions_workspace_id",
        table_name="knowledge_resource_permissions",
    )
    op.drop_index(
        "ix_knowledge_resource_permissions_resource_id",
        table_name="knowledge_resource_permissions",
    )
    op.drop_table("knowledge_resource_permissions")
    op.drop_index("ix_knowledge_resources_deleted_at", table_name="knowledge_resources")
    op.drop_index("ix_knowledge_resources_source_connection_id", table_name="knowledge_resources")
    op.drop_index("ix_knowledge_resources_owner_user_id", table_name="knowledge_resources")
    op.drop_index("ix_knowledge_resources_workspace_id", table_name="knowledge_resources")
    op.drop_table("knowledge_resources")
    op.drop_constraint(
        "uq_connector_resources_workspace_connection_external_id",
        "connector_resources",
        type_="unique",
    )
    op.drop_constraint(
        "uq_connector_connections_workspace_identity",
        "connector_connections",
        type_="unique",
    )
