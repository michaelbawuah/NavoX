"""Durable, owner-scoped news intelligence with content-free selections."""

import sqlalchemy as sa
from alembic import op

revision = "0027_news_intelligence"
down_revision = "0026_news_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_intelligence_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=False),
        sa.Column("claim_snapshot", sa.JSON(), nullable=False),
        sa.Column("trace_ids", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cluster_id", "workspace_id", "user_id"],
            [
                "news_story_clusters.id",
                "news_story_clusters.workspace_id",
                "news_story_clusters.user_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "base_version", name="uq_news_intelligence_version"),
    )
    for field in ("cluster_id", "workspace_id", "expires_at"):
        op.create_index(f"ix_news_intelligence_runs_{field}", "news_intelligence_runs", [field])


def downgrade() -> None:
    op.drop_table("news_intelligence_runs")
