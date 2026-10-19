"""Add encrypted student-owned goals, explicit reports and opaque retry fences."""

import sqlalchemy as sa
from alembic import op

revision = "0046_student_goals"
down_revision = "0045_student_email"
branch_labels = None
depends_on = None


def owner_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "student_goal_states",
        *owner_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_student_goal_state_owner"),
    )
    op.create_table(
        "student_goals",
        *owner_columns(),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition_revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "user_id", "workspace_id", name="uq_student_goal_scope"),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'completed')", name="ck_student_goal_status"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_student_goal_revision"),
    )
    op.create_table(
        "student_goal_checkins",
        *owner_columns(),
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["goal_id", "user_id", "workspace_id"],
            ["student_goals.id", "student_goals.user_id", "student_goals.workspace_id"],
            ondelete="CASCADE",
            name="fk_student_goal_checkin_scope",
        ),
        sa.UniqueConstraint("goal_id", "on_date", name="uq_student_goal_checkin_day"),
        sa.CheckConstraint("revision >= 1", name="ck_student_goal_checkin_revision"),
    )
    op.create_table(
        "student_goal_commands",
        *owner_columns(),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("goal_id", sa.Uuid()),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_goal_request"
        ),
    )
    for table in (
        "student_goal_states",
        "student_goals",
        "student_goal_checkins",
        "student_goal_commands",
    ):
        for column in ("user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    for table in ("student_goal_checkins", "student_goal_commands"):
        op.create_index(f"ix_{table}_goal_id", table, ["goal_id"])
    op.create_index("ix_student_goal_checkins_on_date", "student_goal_checkins", ["on_date"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_goals) "
        "OR EXISTS (SELECT 1 FROM student_goal_checkins) "
        "OR EXISTS (SELECT 1 FROM student_goal_commands) "
        "OR EXISTS (SELECT 1 FROM student_goal_states) THEN RAISE EXCEPTION "
        "'saved private goal data prevents this downgrade'; END IF; END $$"
    )
    op.drop_table("student_goal_commands")
    op.drop_table("student_goal_checkins")
    op.drop_table("student_goals")
    op.drop_table("student_goal_states")
