"""Fence event subscription registration, renewal and cleanup.

Revision ID: 0018_connector_subscription_lifecycle
Revises: 0017_import_snapshots
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_connector_subscription_lifecycle"
down_revision = "0017_import_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_hash", sa.String(64), nullable=True),
    ):
        op.add_column("connector_subscriptions", column)


def downgrade() -> None:
    for name in ("generation", "lease_token", "lease_expires_at", "authorization_hash"):
        op.drop_column("connector_subscriptions", name)
