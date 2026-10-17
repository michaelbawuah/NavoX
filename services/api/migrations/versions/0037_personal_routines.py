"""Opt-in previewed personal routines and bounded saved-data runs."""

import sqlalchemy as sa
from alembic import op

revision = "0037_personal_routines"
down_revision = "0036_thread_watches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_routines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("recipe", sa.String(32), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        sa.Column("days_of_week", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schedule_status", sa.String(32), nullable=False),
        sa.Column("dispatch_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_dispatch_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_personal_routines_user_id", "personal_routines", ["user_id"])
    op.create_index("ix_personal_routines_workspace_id", "personal_routines", ["workspace_id"])
    op.create_index(
        "ix_personal_routines_dispatch", "personal_routines", ["enabled", "next_dispatch_check_at"]
    )
    op.create_table(
        "personal_routine_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "routine_id",
            sa.Uuid(),
            sa.ForeignKey("personal_routines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("notice", sa.String(256), nullable=False),
        sa.UniqueConstraint("routine_id", "revision", "scheduled_for", name="uq_routine_run_slot"),
    )
    op.create_index("ix_personal_routine_runs_routine_id", "personal_routine_runs", ["routine_id"])
    op.create_table(
        "personal_routine_previews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_personal_routine_previews_user_id", "personal_routine_previews", ["user_id"]
    )


def downgrade() -> None:
    op.drop_table("personal_routine_previews")
    op.drop_table("personal_routine_runs")
    op.drop_table("personal_routines")
