"""Add owner-reviewed tasks, routine constraints and reversible study blocks."""

import sqlalchemy as sa
from alembic import op

revision = "0042_student_planning"
down_revision = "0041_lms_feeds"
branch_labels = None
depends_on = None


def owner_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "student_tasks",
        *owner_columns(),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("student_courses.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("remaining_minutes", sa.Integer(), nullable=False),
        sa.Column("actual_minutes", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("course_weight", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "source_item_id", sa.Uuid(), sa.ForeignKey("lms_feed_items.id", ondelete="CASCADE")
        ),
        sa.Column("source_identity", sa.String(64)),
        sa.Column("source_revision", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "source_identity", name="uq_student_task_source"
        ),
    )
    op.create_table(
        "student_planning_states",
        *owner_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("preferences_revision", sa.Integer(), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("accepted_source_hash", sa.String(64)),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True)),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_student_planning_owner"),
    )
    op.create_table(
        "student_planning_previews",
        *owner_columns(),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "student_study_blocks",
        *owner_columns(),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("student_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("focus_window", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("actual_minutes", sa.Integer(), nullable=False),
        sa.Column("outcome_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "student_planning_commands",
        *owner_columns(),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("undo_snapshot", sa.JSON()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_student_planning_request"
        ),
    )
    for table in (
        "student_tasks",
        "student_planning_states",
        "student_planning_previews",
        "student_study_blocks",
        "student_planning_commands",
    ):
        for column in ("workspace_id", "user_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index(
        "ix_student_planning_previews_expires_at", "student_planning_previews", ["expires_at"]
    )
    for column in ("task_id", "plan_id", "start_at"):
        op.create_index(f"ix_student_study_blocks_{column}", "student_study_blocks", [column])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_tasks) "
        "OR EXISTS (SELECT 1 FROM student_study_blocks) "
        "OR EXISTS (SELECT 1 FROM student_planning_states) "
        "OR EXISTS (SELECT 1 FROM student_planning_previews) "
        "OR EXISTS (SELECT 1 FROM student_planning_commands) "
        "THEN RAISE EXCEPTION 'saved student planning data prevents this downgrade'; END IF; END $$"
    )
    for table in (
        "student_planning_commands",
        "student_study_blocks",
        "student_planning_previews",
        "student_planning_states",
        "student_tasks",
    ):
        op.drop_table(table)
