"""Reviewed semantic clustering: vectors, durable attempts, feature records."""

import sqlalchemy as sa
from alembic import op

revision = "0033_news_semantic_clustering"
down_revision = "0032_news_importance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "news_story_items", sa.Column("match_reference", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "news_story_items", sa.Column("match_namespace", sa.String(length=64), nullable=True)
    )
    op.create_table(
        "news_cluster_features",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("news_item_id", sa.Uuid(), nullable=False),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column("item_digest", sa.String(length=64), nullable=False),
        sa.Column("rights_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("entity_ids", sa.JSON(), nullable=False),
        sa.Column("geographic_ids", sa.JSON(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("event_identity", sa.String(length=128), nullable=True),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("reviewer", sa.String(length=256), nullable=False),
        sa.Column("review_reference", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            name="fk_news_cluster_features_item_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["news_item_id", "source_id"],
            ["news_items.id", "news_items.source_id"],
            name="fk_news_cluster_features_item_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "news_item_id", "item_revision", name="uq_news_cluster_feature_revision"
        ),
    )
    op.create_index(
        "ix_news_cluster_features_workspace_id", "news_cluster_features", ["workspace_id"]
    )
    op.create_index(
        "ix_news_cluster_features_news_item_id", "news_cluster_features", ["news_item_id"]
    )
    op.create_index("ix_news_cluster_features_expires_at", "news_cluster_features", ["expires_at"])
    op.create_table(
        "news_cluster_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("news_item_id", sa.Uuid(), nullable=False),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column("item_digest", sa.String(length=64), nullable=False),
        sa.Column("rights_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_digest", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("registry_revision", sa.String(length=64), nullable=False),
        sa.Column("artifact", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "dimension >= 1 AND dimension <= 4096", name="ck_news_cluster_embeddings_dimension"
        ),
        sa.ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            name="fk_news_cluster_embeddings_item_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "news_item_id",
            "item_revision",
            "namespace_digest",
            name="uq_news_cluster_embedding_namespace",
        ),
    )
    op.create_index(
        "ix_news_cluster_embeddings_workspace_id", "news_cluster_embeddings", ["workspace_id"]
    )
    op.create_index(
        "ix_news_cluster_embeddings_news_item_id", "news_cluster_embeddings", ["news_item_id"]
    )
    op.create_index(
        "ix_news_cluster_embeddings_namespace_digest",
        "news_cluster_embeddings",
        ["namespace_digest"],
    )
    op.create_table(
        "news_cluster_embedding_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("news_item_id", sa.Uuid(), nullable=False),
        sa.Column("item_revision", sa.Integer(), nullable=False),
        sa.Column("namespace_digest", sa.String(length=64), nullable=False),
        sa.Column("namespace_reference", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("registry_revision", sa.String(length=64), nullable=True),
        sa.Column("dimension", sa.Integer(), nullable=True),
        sa.Column("cost_micros", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["news_item_id", "workspace_id", "user_id"],
            ["news_items.id", "news_items.workspace_id", "news_items.user_id"],
            name="fk_news_cluster_embedding_requests_item_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_news_cluster_request_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            "news_item_id",
            "item_revision",
            "namespace_digest",
            name="uq_news_cluster_attempt_namespace",
        ),
    )
    op.create_index(
        "ix_news_cluster_embedding_requests_workspace_id",
        "news_cluster_embedding_requests",
        ["workspace_id"],
    )
    op.create_index(
        "ix_news_cluster_embedding_requests_news_item_id",
        "news_cluster_embedding_requests",
        ["news_item_id"],
    )
    op.create_index(
        "ix_news_cluster_embedding_requests_created_at",
        "news_cluster_embedding_requests",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_cluster_embedding_requests_created_at",
        table_name="news_cluster_embedding_requests",
    )
    op.drop_index(
        "ix_news_cluster_embedding_requests_news_item_id",
        table_name="news_cluster_embedding_requests",
    )
    op.drop_index(
        "ix_news_cluster_embedding_requests_workspace_id",
        table_name="news_cluster_embedding_requests",
    )
    op.drop_table("news_cluster_embedding_requests")
    op.drop_index(
        "ix_news_cluster_embeddings_namespace_digest", table_name="news_cluster_embeddings"
    )
    op.drop_index("ix_news_cluster_embeddings_news_item_id", table_name="news_cluster_embeddings")
    op.drop_index("ix_news_cluster_embeddings_workspace_id", table_name="news_cluster_embeddings")
    op.drop_table("news_cluster_embeddings")
    op.drop_index("ix_news_cluster_features_expires_at", table_name="news_cluster_features")
    op.drop_index("ix_news_cluster_features_news_item_id", table_name="news_cluster_features")
    op.drop_index("ix_news_cluster_features_workspace_id", table_name="news_cluster_features")
    op.drop_table("news_cluster_features")
    op.drop_column("news_story_items", "match_namespace")
    op.drop_column("news_story_items", "match_reference")
