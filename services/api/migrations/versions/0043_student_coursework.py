"""Add private encrypted course materials and explicit source links."""

import sqlalchemy as sa
from alembic import op

revision = "0043_student_coursework"
down_revision = "0042_student_planning"
branch_labels = None
depends_on = None


def owner_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("student_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        "student_course_workspace_states",
        *owner_columns(),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "course_id", "user_id", "workspace_id", name="uq_student_course_workspace"
        ),
    )
    op.create_table(
        "student_course_materials",
        *owner_columns(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("text_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "student_course_email_links",
        *owner_columns(),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("connector_resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("course_id", "source_id", name="uq_student_course_email_source"),
    )
    op.create_table(
        "student_coursework_commands",
        *owner_columns(),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_coursework_request"
        ),
    )
    for table in (
        "student_course_workspace_states",
        "student_course_materials",
        "student_course_email_links",
        "student_coursework_commands",
    ):
        for column in ("course_id", "user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index(
        "ix_student_course_email_links_source_id", "student_course_email_links", ["source_id"]
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_course_materials) "
        "OR EXISTS (SELECT 1 FROM student_course_workspace_states) "
        "OR EXISTS (SELECT 1 FROM student_course_email_links) "
        "OR EXISTS (SELECT 1 FROM student_coursework_commands) THEN RAISE EXCEPTION "
        "'saved private coursework prevents this downgrade'; END IF; END $$"
    )
    for table in (
        "student_coursework_commands",
        "student_course_email_links",
        "student_course_materials",
        "student_course_workspace_states",
    ):
        op.drop_table(table)
