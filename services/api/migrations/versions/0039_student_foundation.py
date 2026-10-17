"""Add explicit student profile, timetable and reviewed import records."""

import sqlalchemy as sa
from alembic import op

revision = "0039_student_foundation"
down_revision = "0038_owner_memory"
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


def schedule_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "student_profiles",
        *owner_columns(),
        sa.Column("school_name", sa.String(120)),
        sa.Column("lms", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(120)),
        sa.Column("term_start", sa.Date()),
        sa.Column("term_end", sa.Date()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_student_profile_owner"),
    )
    op.create_table(
        "student_courses",
        *owner_columns(),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("professor", sa.String(120)),
        sa.Column("term_start", sa.Date(), nullable=False),
        sa.Column("term_end", sa.Date(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "student_class_meetings",
        *owner_columns(),
        *schedule_columns(),
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("student_courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("dates", sa.JSON()),
        sa.Column("import_key", sa.String(128)),
        sa.Column("import_series_key", sa.String(64)),
        sa.Column("building", sa.String(160)),
        sa.Column("room", sa.String(80)),
        sa.Column("instructor", sa.String(120)),
        sa.Column("walk_minutes", sa.Integer()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "import_key", name="uq_student_meeting_import"
        ),
    )
    op.create_table(
        "student_meeting_exceptions",
        *owner_columns(),
        sa.Column(
            "meeting_id",
            sa.Uuid(),
            sa.ForeignKey("student_class_meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("start_time", sa.String(5)),
        sa.Column("end_time", sa.String(5)),
        sa.Column("building", sa.String(160)),
        sa.Column("room", sa.String(80)),
        sa.UniqueConstraint("meeting_id", "date", name="uq_student_meeting_exception_date"),
    )
    op.create_table(
        "student_fixed_blocks",
        *owner_columns(),
        *schedule_columns(),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("term_start", sa.Date(), nullable=False),
        sa.Column("term_end", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "student_timetable_breaks",
        *owner_columns(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "student_import_previews",
        *owner_columns(),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("term_start", sa.Date(), nullable=False),
        sa.Column("term_end", sa.Date(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("request_id", sa.Uuid()),
        sa.Column("confirm_hash", sa.String(64)),
        sa.Column("course_ids", sa.JSON()),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_student_import_request"
        ),
    )
    for table in (
        "student_profiles",
        "student_courses",
        "student_class_meetings",
        "student_meeting_exceptions",
        "student_fixed_blocks",
        "student_timetable_breaks",
        "student_import_previews",
    ):
        for column in ("workspace_id", "user_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_student_class_meetings_course_id", "student_class_meetings", ["course_id"])
    op.create_index(
        "ix_student_class_meetings_import_series_key",
        "student_class_meetings",
        ["import_series_key"],
    )
    op.create_index(
        "ix_student_meeting_exceptions_meeting_id", "student_meeting_exceptions", ["meeting_id"]
    )
    op.create_index(
        "ix_student_import_previews_expires_at", "student_import_previews", ["expires_at"]
    )


def downgrade() -> None:
    # App rollback does not discard student-entered academic records.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_courses) "
        "OR EXISTS (SELECT 1 FROM student_fixed_blocks) "
        "OR EXISTS (SELECT 1 FROM student_profiles) "
        "OR EXISTS (SELECT 1 FROM student_timetable_breaks) "
        "THEN RAISE EXCEPTION 'saved student data prevents this downgrade'; END IF; END $$"
    )
    for table in (
        "student_import_previews",
        "student_timetable_breaks",
        "student_fixed_blocks",
        "student_meeting_exceptions",
        "student_class_meetings",
        "student_courses",
        "student_profiles",
    ):
        op.drop_table(table)
