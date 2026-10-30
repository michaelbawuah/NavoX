"""Explicitly disclosed encrypted conversation history and source-linked memory."""

import sqlalchemy as sa
from alembic import op

revision = "0062_conversation_memory"
down_revision = "0061_outlook_reply_tracking"
branch_labels = None
depends_on = None


def owner_columns():
    return [
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


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "memory_privacy",
        *owner_columns(),
        sa.Column("history_enabled", sa.Boolean(), nullable=False),
        sa.Column("off_the_record", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("disclosure_version", sa.Integer(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "workspace_id"),
        sa.CheckConstraint("generation >= 0", name="ck_memory_privacy_generation"),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days IN (30, 90)", name="ck_memory_retention"
        ),
    )
    op.create_table(
        "memory_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *owner_columns(),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("encrypted_transcript", sa.Text(), nullable=True),
        sa.Column("encrypted_summary", sa.Text(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        *timestamps(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "workspace_id",
            "source_kind",
            "source_id",
            name="uq_memory_conversation_source",
        ),
        sa.CheckConstraint("source_kind IN ('vox', 'chat')", name="ck_memory_conversation_kind"),
        sa.CheckConstraint(
            "message_count >= 0 AND message_count <= 2000", name="ck_memory_message_count"
        ),
    )
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *owner_columns(),
        sa.Column(
            "source_conversation_id",
            sa.Uuid(),
            sa.ForeignKey("memory_conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("encrypted_content", sa.Text(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "memory_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *owner_columns(),
        sa.Column(
            "source_conversation_id",
            sa.Uuid(),
            sa.ForeignKey("memory_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("encrypted_content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *timestamps(),
    )
    for table in ("memory_conversations", "memory_facts", "memory_threads"):
        for column in ("user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_memory_conversations_expires_at", "memory_conversations", ["expires_at"])
    for table in ("memory_facts", "memory_threads"):
        op.create_index(f"ix_{table}_source_conversation_id", table, ["source_conversation_id"])
    op.add_column(
        "vox_call_checkpoints", sa.Column("memory_generation", sa.Integer(), nullable=True)
    )
    op.add_column(
        "vox_call_checkpoints",
        sa.Column("off_the_record", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade():
    for table in (
        "memory_privacy",
        "memory_conversations",
        "memory_facts",
        "memory_threads",
        "vox_call_checkpoints",
    ):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("Memory or active-call data exists; preserve it before downgrading")
    op.drop_column("vox_call_checkpoints", "off_the_record")
    op.drop_column("vox_call_checkpoints", "memory_generation")
    for table in ("memory_threads", "memory_facts", "memory_conversations", "memory_privacy"):
        op.drop_table(table)
