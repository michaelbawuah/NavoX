"""Persist authenticated connector event locators and targeted-sync dispatch state.

Revision ID: 0019_event_receipts
Revises: 0018_connector_subscriptions
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_event_receipts"
down_revision = "0018_connector_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_event_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey("connector_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("external_event_id", sa.String(512), nullable=False),
        sa.Column("external_resource_id", sa.String(512), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "subscription_id", "external_event_id", name="uq_connector_event_delivery"
        ),
    )
    for column in ("subscription_id", "connector_connection_id", "workspace_id", "status"):
        op.create_index(
            f"ix_connector_event_receipts_{column}", "connector_event_receipts", [column]
        )


def downgrade() -> None:
    op.drop_table("connector_event_receipts")
