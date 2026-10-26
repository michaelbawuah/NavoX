"""Encrypted, source-bound semantic learning work and reviewed suggestions."""

import sqlalchemy as sa
from alembic import op

revision = "0065_semantic_memory_learning"
down_revision = "0064_provider_calendar_writes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_learning_jobs",
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
        sa.Column(
            "source_conversation_id",
            sa.Uuid(),
            sa.ForeignKey("memory_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("execution_kind", sa.String(16), nullable=True),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_digest", sa.String(64), nullable=True),
        sa.Column("encrypted_result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_conversation_id", "sequence", name="uq_memory_learning_source"),
        sa.CheckConstraint("generation >= 0 AND sequence > 0", name="ck_memory_learning_revision"),
        sa.CheckConstraint(
            "message_count > 0 AND message_count <= 2000", name="ck_memory_learning_count"
        ),
        sa.CheckConstraint(
            "pending_count >= 0 AND pending_count <= 4", name="ck_memory_learning_pending"
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','completed','failed','superseded')",
            name="ck_memory_learning_status",
        ),
        sa.CheckConstraint(
            "execution_kind IS NULL OR execution_kind IN ('native','background')",
            name="ck_memory_learning_kind",
        ),
    )
    for column in ("user_id", "workspace_id", "source_conversation_id", "status", "expires_at"):
        op.create_index(f"ix_memory_learning_jobs_{column}", "memory_learning_jobs", [column])


def downgrade():
    op.execute(
        "DO $$ BEGIN LOCK TABLE memory_learning_jobs IN ACCESS EXCLUSIVE MODE; "
        "IF EXISTS (SELECT 1 FROM memory_learning_jobs) THEN "
        "RAISE EXCEPTION 'Semantic memory work or reviews prevent this downgrade'; "
        "END IF; END $$"
    )
    op.drop_table("memory_learning_jobs")
