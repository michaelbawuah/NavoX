"""Fenced connector attempts and transactional revision checkpoints.

Revision ID: 0016_connector_sync_recovery
Revises: 0015_spec_003_connectors
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_connector_sync_recovery"
down_revision = "0015_spec_003_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("sync_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_run_id", sa.Uuid(), nullable=True),
        sa.Column("sync_lease_token", sa.Uuid(), nullable=True),
        sa.Column("sync_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_not_before", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("connector_connections", column)
    for column in (
        sa.Column(
            "consumer_version",
            sa.String(128),
            nullable=False,
            server_default="canonical-consumer.v1",
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("authorization_hash", sa.String(64), nullable=True),
        sa.Column("checkpoint_cursor", sa.Text(), nullable=True),
        sa.Column("pages_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetch_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cursor_hashes", sa.JSON(), nullable=False, server_default="[]"),
    ):
        op.add_column("connector_sync_runs", column)
    op.create_table(
        "connector_sync_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "sync_run_id",
            sa.Uuid(),
            sa.ForeignKey("connector_sync_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "consumer_version",
            sa.String(128),
            nullable=False,
            server_default="canonical-consumer.v1",
        ),
        sa.Column("result_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "sync_run_id", "resource_id", "content_hash", name="uq_connector_sync_receipt"
        ),
    )
    op.create_index(
        "ix_connector_sync_receipts_sync_run_id", "connector_sync_receipts", ["sync_run_id"]
    )
    op.create_index(
        "ix_connector_sync_receipts_workspace_id", "connector_sync_receipts", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_table("connector_sync_receipts")
    for name in (
        "consumer_version",
        "generation",
        "authorization_hash",
        "checkpoint_cursor",
        "pages_completed",
        "fetch_complete",
        "attempt_count",
        "stale_count",
        "result_ids",
        "cursor_hashes",
    ):
        op.drop_column("connector_sync_runs", name)
    for name in (
        "sync_generation",
        "sync_run_id",
        "sync_lease_token",
        "sync_lease_expires_at",
        "retry_not_before",
    ):
        op.drop_column("connector_connections", name)
