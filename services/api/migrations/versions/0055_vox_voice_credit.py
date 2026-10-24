"""Reserve bounded voice capacity before audio; retain uncertain crash credits."""

import sqlalchemy as sa
from alembic import op

revision = "0055_vox_voice_credit"
down_revision = "0054_vox_wake_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vox_voice_credits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "stream_id",
            sa.Uuid(),
            sa.ForeignKey("vox_usage_streams.id", ondelete="CASCADE"),
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
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("worker_nonce", sa.Uuid(), nullable=False),
        sa.Column("base_sequence", sa.Integer(), nullable=False),
        sa.Column("base_cumulative", sa.JSON(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("granted_ms", sa.Integer(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("month_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("settled_speaking_ms", sa.Integer()),
        sa.Column("settlement_digest", sa.String(64)),
        sa.CheckConstraint(
            "granted_ms >= 1 AND granted_ms <= 1000", name="ck_vox_voice_credit_bound"
        ),
        sa.CheckConstraint(
            "generation >= 0 AND policy_revision >= 0 AND base_sequence >= 0",
            name="ck_vox_voice_credit_epoch",
        ),
        sa.CheckConstraint("expires_at > granted_at", name="ck_vox_voice_credit_clock"),
        sa.CheckConstraint(
            "(settled_at IS NULL AND settled_speaking_ms IS NULL AND settlement_digest IS NULL) OR "
            "(settled_at IS NOT NULL AND settled_speaking_ms IS NOT NULL AND "
            "settled_speaking_ms >= 0 AND "
            "settled_speaking_ms <= granted_ms AND settlement_digest IS NOT NULL)",
            name="ck_vox_voice_credit_settlement",
        ),
    )
    op.create_index(
        "ix_vox_voice_credit_owner_time", "vox_voice_credits", ["user_id", "granted_at"]
    )
    op.create_index("ix_vox_voice_credit_stream", "vox_voice_credits", ["stream_id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE vox_voice_credits IN ACCESS EXCLUSIVE MODE")
    if (
        op.get_bind().execute(sa.text("SELECT 1 FROM vox_voice_credits LIMIT 1")).first()
        is not None
    ):
        raise RuntimeError("Voice quota evidence must be preserved; downgrade refused")
    op.drop_table("vox_voice_credits")
