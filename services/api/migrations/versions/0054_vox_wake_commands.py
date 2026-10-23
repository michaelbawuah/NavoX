"""Add short-lived encrypted wake-question delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0054_vox_wake_commands"
down_revision = "0053_vox_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vox_wake_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("vox_media_connections.id", ondelete="CASCADE"),
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
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("encrypted_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connection_id", "request_id", name="uq_vox_wake_request"),
        sa.CheckConstraint("expires_at >= created_at", name="ck_vox_wake_expiry"),
    )
    op.create_index("ix_vox_wake_commands_connection_id", "vox_wake_commands", ["connection_id"])
    op.create_index("ix_vox_wake_commands_expires_at", "vox_wake_commands", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_vox_wake_commands_expires_at", table_name="vox_wake_commands")
    op.drop_index("ix_vox_wake_commands_connection_id", table_name="vox_wake_commands")
    op.drop_table("vox_wake_commands")
