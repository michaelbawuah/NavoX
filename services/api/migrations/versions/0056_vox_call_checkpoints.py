"""Add encrypted temporary checkpoints for active Vox calls."""

import sqlalchemy as sa
from alembic import op

revision = "0056_vox_call_checkpoints"
down_revision = "0055_vox_voice_credit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vox_call_checkpoints",
        sa.Column(
            "call_id",
            sa.Uuid(),
            sa.ForeignKey("vox_calls.id", ondelete="CASCADE"),
            primary_key=True,
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
        sa.Column(
            "auth_session_id",
            sa.Uuid(),
            sa.ForeignKey("user_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("encrypted_context", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation >= 0", name="ck_vox_checkpoint_generation"),
        sa.CheckConstraint("revision >= 0", name="ck_vox_checkpoint_revision"),
        sa.CheckConstraint("expires_at >= created_at", name="ck_vox_checkpoint_expiry"),
        sa.CheckConstraint(
            "encrypted_context IS NULL OR length(encrypted_context) <= 3000000",
            name="ck_vox_checkpoint_size",
        ),
    )
    op.create_index("ix_vox_call_checkpoints_user_id", "vox_call_checkpoints", ["user_id"])
    op.create_index("ix_vox_call_checkpoints_expires_at", "vox_call_checkpoints", ["expires_at"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE vox_call_checkpoints IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM vox_call_checkpoints")):
        raise RuntimeError("End active Vox calls and clear checkpoints before downgrading")
    op.drop_index("ix_vox_call_checkpoints_expires_at", table_name="vox_call_checkpoints")
    op.drop_index("ix_vox_call_checkpoints_user_id", table_name="vox_call_checkpoints")
    op.drop_table("vox_call_checkpoints")
