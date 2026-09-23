"""Resumable Gmail read plans without storing source bodies.

Revision ID: 0013_gmail_sync_plans
Revises: 0012_source_receipts
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_gmail_sync_plans"
down_revision = "0012_source_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_sync_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("initial_cursor", sa.Text(), nullable=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("page_token", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("reset", sa.Boolean(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("commitment_ids", sa.JSON(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("next_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("connection_id", name="uq_gmail_sync_plan_connection"),
    )
    op.create_index("ix_gmail_sync_plans_connection_id", "gmail_sync_plans", ["connection_id"])


def downgrade() -> None:
    op.drop_table("gmail_sync_plans")
