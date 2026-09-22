"""Add proactive intelligence persistence.

Revision ID: 0008_proactive_navox
Revises: 0007_approval_execution
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_proactive_navox"
down_revision = "0007_approval_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "commitments",
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_commitments_waiting_since", "commitments", ["waiting_since"])

    op.create_table(
        "proactive_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quiet_hours_start", sa.String(length=5), nullable=False, server_default="22:00"),
        sa.Column("quiet_hours_end", sa.String(length=5), nullable=False, server_default="07:00"),
        sa.Column("daily_briefing_hour", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("notify_threshold", sa.Integer(), nullable=False, server_default="85"),
        sa.Column("briefing_threshold", sa.Integer(), nullable=False, server_default="65"),
        sa.Column("dashboard_threshold", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("max_interruptions_per_day", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="240"),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_proactive_preferences_workspace_user",
        ),
    )
    op.create_index("ix_proactive_preferences_user_id", "proactive_preferences", ["user_id"])
    op.create_index(
        "ix_proactive_preferences_workspace_id",
        "proactive_preferences",
        ["workspace_id"],
    )

    op.create_table(
        "proactive_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), nullable=True),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("tier", sa.String(length=32), nullable=False, server_default="dashboard"),
        sa.Column("attention_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_components", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("what_happening", sa.Text(), nullable=False),
        sa.Column("why_matters", sa.Text(), nullable=False),
        sa.Column("suggested_capability", sa.String(length=128), nullable=True),
        sa.Column(
            "last_evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_surfaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("surface_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["commitment_id"], ["commitments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="uq_proactive_signals_workspace_fingerprint",
        ),
    )
    for column in (
        "user_id",
        "workspace_id",
        "commitment_id",
        "signal_type",
        "status",
        "tier",
        "attention_score",
        "last_surfaced_at",
        "snoozed_until",
    ):
        op.create_index(f"ix_proactive_signals_{column}", "proactive_signals", [column])

    op.create_table(
        "briefing_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.String(length=10), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("signal_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "request_id",
            name="uq_briefing_snapshots_workspace_request",
        ),
    )
    for column in ("user_id", "workspace_id", "local_date", "generated_at"):
        op.create_index(f"ix_briefing_snapshots_{column}", "briefing_snapshots", [column])


def downgrade() -> None:
    for column in ("generated_at", "local_date", "workspace_id", "user_id"):
        op.drop_index(f"ix_briefing_snapshots_{column}", table_name="briefing_snapshots")
    op.drop_table("briefing_snapshots")

    for column in (
        "snoozed_until",
        "last_surfaced_at",
        "attention_score",
        "tier",
        "status",
        "signal_type",
        "commitment_id",
        "workspace_id",
        "user_id",
    ):
        op.drop_index(f"ix_proactive_signals_{column}", table_name="proactive_signals")
    op.drop_table("proactive_signals")

    op.drop_index("ix_proactive_preferences_workspace_id", table_name="proactive_preferences")
    op.drop_index("ix_proactive_preferences_user_id", table_name="proactive_preferences")
    op.drop_table("proactive_preferences")

    op.drop_index("ix_commitments_waiting_since", table_name="commitments")
    op.drop_column("commitments", "waiting_since")
