"""Distinguish provider-confirmed expiry from provisional watch deadlines.

Revision ID: 0021_watch_expiration
Revises: 0020_identity_sources
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_watch_expiration"
down_revision = "0020_identity_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older rows may contain the ten-minute registration retry deadline. Never
    # infer provider confirmation from an existing expires_at value.
    op.add_column(
        "provider_event_subscriptions",
        sa.Column("expiration_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("provider_event_subscriptions", "expiration_confirmed_at")
