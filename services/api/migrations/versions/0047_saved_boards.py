"""Add encrypted authored Board saves and content-free retry fences."""

import sqlalchemy as sa
from alembic import op

revision = "0047_saved_boards"
down_revision = "0046_student_goals"
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
        "student_board_states",
        *owner_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_student_board_state_owner"),
        sa.CheckConstraint("revision >= 0", name="ck_student_board_state_revision"),
        sa.CheckConstraint("generation >= 0", name="ck_student_board_generation"),
    )
    op.create_table(
        "student_saved_boards",
        *owner_columns(),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("student_courses.id", ondelete="CASCADE")),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "user_id", "workspace_id", name="uq_student_saved_board_scope"),
        sa.CheckConstraint("revision >= 1", name="ck_student_saved_board_revision"),
    )
    op.create_table(
        "student_board_commands",
        *owner_columns(),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("board_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid()),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_board_request"
        ),
        sa.CheckConstraint(
            "status IN ('applied', 'erased')", name="ck_student_board_command_status"
        ),
        sa.CheckConstraint(
            "operation IN ('create', 'update', 'delete')", name="ck_student_board_command_operation"
        ),
        sa.CheckConstraint("applied_revision >= 1", name="ck_student_board_command_revision"),
    )
    for table in ("student_board_states", "student_saved_boards", "student_board_commands"):
        for column in ("user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    for table in ("student_saved_boards", "student_board_commands"):
        op.create_index(f"ix_{table}_course_id", table, ["course_id"])
    op.create_index("ix_student_board_commands_board_id", "student_board_commands", ["board_id"])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_saved_boards) "
        "OR EXISTS (SELECT 1 FROM student_board_commands) "
        "OR EXISTS (SELECT 1 FROM student_board_states) THEN RAISE EXCEPTION "
        "'saved private Board data prevents this downgrade'; END IF; END $$"
    )
    op.drop_table("student_board_commands")
    op.drop_table("student_saved_boards")
    op.drop_table("student_board_states")
