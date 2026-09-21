"""Add protected Google connection records.

Revision ID: 0003_google_connections
Revises: 0002_password_sessions
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_google_connections"
down_revision = "0002_password_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False, server_default="local-v1"),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_account_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("credential_reference", sa.Uuid(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=256), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["credential_reference"],
            ["connection_credentials.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            name="uq_connections_workspace_provider_external_account",
        ),
    )
    op.create_index("ix_connections_user_id", "connections", ["user_id"])
    op.create_index("ix_connections_workspace_id", "connections", ["workspace_id"])
    op.create_table(
        "oauth_authorization_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_oauth_authorization_attempts_state_hash"),
    )
    op.create_index(
        "ix_oauth_authorization_attempts_user_id",
        "oauth_authorization_attempts",
        ["user_id"],
    )
    op.create_index(
        "ix_oauth_authorization_attempts_workspace_id",
        "oauth_authorization_attempts",
        ["workspace_id"],
    )
    op.create_index(
        "ix_oauth_authorization_attempts_state_hash",
        "oauth_authorization_attempts",
        ["state_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oauth_authorization_attempts_state_hash",
        table_name="oauth_authorization_attempts",
    )
    op.drop_index(
        "ix_oauth_authorization_attempts_workspace_id",
        table_name="oauth_authorization_attempts",
    )
    op.drop_index(
        "ix_oauth_authorization_attempts_user_id",
        table_name="oauth_authorization_attempts",
    )
    op.drop_table("oauth_authorization_attempts")
    op.drop_index("ix_connections_workspace_id", table_name="connections")
    op.drop_index("ix_connections_user_id", table_name="connections")
    op.drop_table("connections")
    op.drop_table("connection_credentials")
