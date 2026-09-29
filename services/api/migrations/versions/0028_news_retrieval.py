"""Persist explicit retrieval plans, bounded snapshots and generation reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0028_news_retrieval"
down_revision = "0027_news_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing turns remain readable under their original conservative defaults.
    op.add_column("news_conversation_turns", sa.Column("retrieval_plan", sa.JSON(), nullable=True))
    op.add_column(
        "news_conversation_turns", sa.Column("retrieval_metadata", sa.JSON(), nullable=True)
    )
    op.add_column(
        "news_conversation_turns",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("news_conversation_turns", "processing_started_at")
    op.drop_column("news_conversation_turns", "retrieval_metadata")
    op.drop_column("news_conversation_turns", "retrieval_plan")
