"""Add exact-action approvals and incremental Google capability grants.

Revision ID: 0007_approval_execution
Revises: 0006_bounded_agent
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_approval_execution"
down_revision = "0006_bounded_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_authorization_attempts",
        sa.Column("purpose", sa.String(length=64), nullable=False, server_default="identity"),
    )
    op.add_column(
        "oauth_authorization_attempts",
        sa.Column("connection_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "oauth_authorization_attempts",
        sa.Column("requested_scopes", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_foreign_key(
        "fk_oauth_authorization_attempts_connection_id",
        "oauth_authorization_attempts",
        "connections",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_oauth_authorization_attempts_connection_id",
        "oauth_authorization_attempts",
        ["connection_id"],
    )

    op.add_column(
        "actions",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("action_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_request_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id", "version", name="uq_approvals_action_version"),
        sa.UniqueConstraint(
            "workspace_id",
            "decision_request_id",
            name="uq_approvals_workspace_decision_request",
        ),
    )
    for column in ("action_id", "user_id", "workspace_id", "status", "expires_at"):
        op.create_index(f"ix_approvals_{column}", "approvals", [column])


def downgrade() -> None:
    for column in ("expires_at", "status", "workspace_id", "user_id", "action_id"):
        op.drop_index(f"ix_approvals_{column}", table_name="approvals")
    op.drop_table("approvals")

    op.drop_column("actions", "verified_at")

    op.drop_index(
        "ix_oauth_authorization_attempts_connection_id",
        table_name="oauth_authorization_attempts",
    )
    op.drop_constraint(
        "fk_oauth_authorization_attempts_connection_id",
        "oauth_authorization_attempts",
        type_="foreignkey",
    )
    op.drop_column("oauth_authorization_attempts", "requested_scopes")
    op.drop_column("oauth_authorization_attempts", "connection_id")
    op.drop_column("oauth_authorization_attempts", "purpose")
