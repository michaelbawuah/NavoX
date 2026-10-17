"""Add explicit private email review receipts and accepted-task source fences."""

import sqlalchemy as sa
from alembic import op

revision = "0045_student_email"
down_revision = "0044_student_recovery"
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
        "student_email_reviews",
        *owner_columns(),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("connector_resources.id", ondelete="SET NULL")
        ),
        sa.Column("source_hash", sa.String(64)),
        sa.Column(
            "connector_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="SET NULL"),
        ),
        sa.Column("authority_hash", sa.String(64)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_payload", sa.Text()),
        sa.Column("accepted_task_id", sa.Uuid()),
        sa.Column("acceptance_hash", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_email_request"
        ),
    )
    op.create_table(
        "student_email_task_sources",
        *owner_columns(),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("student_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id", sa.Uuid(), sa.ForeignKey("connector_resources.id", ondelete="SET NULL")
        ),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column(
            "connector_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="SET NULL"),
        ),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("authority_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("task_id", name="uq_student_email_task"),
    )
    for table in ("student_email_reviews", "student_email_task_sources"):
        for column in ("user_id", "workspace_id", "source_id", "connector_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    for column in ("expires_at", "purge_after"):
        op.create_index(f"ix_student_email_reviews_{column}", "student_email_reviews", [column])
    op.create_index(
        "ix_student_email_task_sources_task_id", "student_email_task_sources", ["task_id"]
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_email_reviews) "
        "OR EXISTS (SELECT 1 FROM student_email_task_sources) THEN RAISE EXCEPTION "
        "'saved private email data prevents this downgrade'; END IF; END $$"
    )
    op.drop_table("student_email_task_sources")
    op.drop_table("student_email_reviews")
