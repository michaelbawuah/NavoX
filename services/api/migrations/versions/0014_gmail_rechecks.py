"""Bounded, resumable previews for older Gmail cards.

Revision ID: 0014_gmail_rechecks
Revises: 0013_gmail_sync_plans
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_gmail_rechecks"
down_revision = "0013_gmail_sync_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gmail_rechecks",
        sa.Column(
            "commitment_id",
            sa.Uuid(),
            sa.ForeignKey("commitments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_gmail_rechecks_connection_id", "gmail_rechecks", ["connection_id"])


def downgrade() -> None:
    op.drop_table("gmail_rechecks")
