"""SPEC-003 universal connector foundation.

Revision ID: 0015_spec_003_connectors
Revises: 0014_gmail_rechecks
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_spec_003_connectors"
down_revision = "0014_gmail_rechecks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connector_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("connector_class", sa.String(32), nullable=False),
        sa.Column("trust_level", sa.String(32), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "connector_key",
            "version",
            name="uq_connector_definitions_key_version",
        ),
    )
    op.create_index(
        "ix_connector_definitions_connector_key",
        "connector_definitions",
        ["connector_key"],
    )

    op.create_table(
        "connector_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connector_definition_id",
            sa.Uuid(),
            sa.ForeignKey("connector_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "legacy_connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_account_id", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="CONNECTED"),
        sa.Column("health_state", sa.String(32), nullable=False, server_default="CONNECTED"),
        sa.Column("authorized_capabilities", sa.JSON(), nullable=False),
        sa.Column("provider_capabilities", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column(
            "credential_reference",
            sa.Uuid(),
            sa.ForeignKey("connection_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sync_cursor", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "workspace_id",
            "connector_definition_id",
            "external_account_id",
            name="uq_connector_connections_workspace_definition_account",
        ),
    )
    op.create_index(
        "ix_connector_connections_workspace_id",
        "connector_connections",
        ["workspace_id"],
    )
    op.create_index(
        "ix_connector_connections_user_id",
        "connector_connections",
        ["user_id"],
    )
    op.create_index(
        "ix_connector_connections_legacy_connection_id",
        "connector_connections",
        ["legacy_connection_id"],
    )

    op.create_table(
        "connector_resources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connector_connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("external_parent_id", sa.String(512), nullable=True),
        sa.Column("version", sa.String(256), nullable=True),
        sa.Column("canonical", sa.JSON(), nullable=False),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "connector_connection_id",
            "resource_type",
            "external_id",
            name="uq_connector_resources_connection_type_external",
        ),
    )
    op.create_index("ix_connector_resources_workspace_id", "connector_resources", ["workspace_id"])
    op.create_index(
        "ix_connector_resources_connection_id",
        "connector_resources",
        ["connector_connection_id"],
    )

    op.create_table(
        "connector_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connector_connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subscription_key", sa.String(256), nullable=False),
        sa.Column("external_id", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="CONNECTED"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "connector_connection_id",
            "subscription_key",
            name="uq_connector_subscriptions_connection_key",
        ),
    )
    op.create_index(
        "ix_connector_subscriptions_connection_id",
        "connector_subscriptions",
        ["connector_connection_id"],
    )

    op.create_table(
        "connector_sync_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
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
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("resource_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "connector_connection_id",
            "request_id",
            name="uq_connector_sync_runs_connection_request",
        ),
    )
    op.create_index("ix_connector_sync_runs_workspace_id", "connector_sync_runs", ["workspace_id"])
    op.create_index(
        "ix_connector_sync_runs_connection_id",
        "connector_sync_runs",
        ["connector_connection_id"],
    )


def downgrade() -> None:
    op.drop_table("connector_sync_runs")
    op.drop_table("connector_subscriptions")
    op.drop_table("connector_resources")
    op.drop_table("connector_connections")
    op.drop_table("connector_definitions")
