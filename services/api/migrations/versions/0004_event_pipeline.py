"""Add authenticated provider event pipeline tables.

Revision ID: 0004_event_pipeline
Revises: 0003_google_connections
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_event_pipeline"
down_revision = "0003_google_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connections", sa.Column("external_email", sa.String(length=320), nullable=True))
    op.create_index("ix_connections_external_email", "connections", ["external_email"])

    op.create_table(
        "provider_event_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("channel_id", sa.String(length=256), nullable=False),
        sa.Column("channel_token_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", name="uq_provider_event_subscriptions_channel_id"),
    )
    op.create_index(
        "ix_provider_event_subscriptions_connection_id",
        "provider_event_subscriptions",
        ["connection_id"],
    )
    op.create_index(
        "ix_provider_event_subscriptions_user_id",
        "provider_event_subscriptions",
        ["user_id"],
    )
    op.create_index(
        "ix_provider_event_subscriptions_workspace_id",
        "provider_event_subscriptions",
        ["workspace_id"],
    )
    op.create_index(
        "ix_provider_event_subscriptions_channel_id",
        "provider_event_subscriptions",
        ["channel_id"],
    )

    op.create_table(
        "incoming_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("external_event_id", sa.String(length=512), nullable=False),
        sa.Column("external_resource_id", sa.String(length=512), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "provider",
            "external_event_id",
            name="uq_incoming_events_connection_provider_external_event",
        ),
    )
    op.create_index("ix_incoming_events_connection_id", "incoming_events", ["connection_id"])
    op.create_index("ix_incoming_events_user_id", "incoming_events", ["user_id"])
    op.create_index("ix_incoming_events_workspace_id", "incoming_events", ["workspace_id"])
    op.create_index("ix_incoming_events_status", "incoming_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_incoming_events_status", table_name="incoming_events")
    op.drop_index("ix_incoming_events_workspace_id", table_name="incoming_events")
    op.drop_index("ix_incoming_events_user_id", table_name="incoming_events")
    op.drop_index("ix_incoming_events_connection_id", table_name="incoming_events")
    op.drop_table("incoming_events")
    op.drop_index(
        "ix_provider_event_subscriptions_channel_id",
        table_name="provider_event_subscriptions",
    )
    op.drop_index(
        "ix_provider_event_subscriptions_workspace_id",
        table_name="provider_event_subscriptions",
    )
    op.drop_index(
        "ix_provider_event_subscriptions_user_id",
        table_name="provider_event_subscriptions",
    )
    op.drop_index(
        "ix_provider_event_subscriptions_connection_id",
        table_name="provider_event_subscriptions",
    )
    op.drop_table("provider_event_subscriptions")
    op.drop_index("ix_connections_external_email", table_name="connections")
    op.drop_column("connections", "external_email")
