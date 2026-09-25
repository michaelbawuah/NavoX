"""Track plan provenance; existing snapshots require explicit historical review.

Revision ID: 0022_plan_sources
Revises: 0021_watch_expiration
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_plan_sources"
down_revision = "0021_watch_expiration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("source_attributed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "plan_sources",
        sa.Column(
            "plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_plan_sources_connection_id", "plan_sources", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_sources_connection_id", "plan_sources")
    op.drop_table("plan_sources")
    op.drop_column("plans", "source_attributed")
