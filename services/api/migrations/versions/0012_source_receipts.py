"""Durable, minimal per-source extraction checkpoints.

Revision ID: 0012_source_receipts
Revises: 0011_intelligence_engine
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_source_receipts"
down_revision = "0011_intelligence_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intelligence_source_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("source_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commitment_ids", sa.JSON(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "connection_id",
            "source",
            "external_id",
            "source_hash",
            "extractor_version",
            name="uq_intelligence_source_receipt",
        ),
    )
    op.create_index(
        "ix_intelligence_source_receipts_connection_id",
        "intelligence_source_receipts",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_table("intelligence_source_receipts")
