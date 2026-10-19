"""Add expiring, encrypted, owner-bound recovery proposals."""

import sqlalchemy as sa
from alembic import op

revision = "0044_student_recovery"
down_revision = "0043_student_coursework"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_recovery_previews",
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
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "parent_id",
            sa.Uuid(),
            sa.ForeignKey("student_recovery_previews.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_recovery_request"
        ),
    )
    for column in ("user_id", "workspace_id", "expires_at", "parent_id"):
        op.create_index(
            f"ix_student_recovery_previews_{column}", "student_recovery_previews", [column]
        )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_recovery_previews) THEN RAISE EXCEPTION "
        "'saved student recovery receipts prevent this downgrade'; END IF; END $$"
    )
    op.drop_table("student_recovery_previews")
