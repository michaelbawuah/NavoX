"""Add content-free voice usage, quota reservations, and operator limits."""

import sqlalchemy as sa
from alembic import op

revision = "0053_vox_usage"
down_revision = "0052_student_calendar_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vox_usage_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1 AND revision >= 1", name="ck_vox_usage_policy_singleton"),
    )
    op.create_table(
        "vox_usage_streams",
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
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(128)),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("cumulative", sa.JSON(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("pricing", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_cost_usd", sa.Numeric(20, 10)),
        sa.Column("reconciliation_id", sa.Uuid()),
        sa.CheckConstraint("kind IN ('transport','provider')", name="ck_vox_usage_stream_kind"),
        sa.CheckConstraint(
            "status IN ('open','complete','uncertain')", name="ck_vox_usage_stream_status"
        ),
        sa.CheckConstraint(
            "sequence >= 0 AND reserved_tokens >= 0", name="ck_vox_usage_stream_nonnegative"
        ),
        sa.CheckConstraint(
            "reconciled_cost_usd IS NULL OR reconciled_cost_usd >= 0",
            name="ck_vox_usage_reconciled_cost",
        ),
    )
    op.create_index(
        "ix_vox_usage_stream_owner_created", "vox_usage_streams", ["user_id", "created_at"]
    )
    op.create_index("ix_vox_usage_stream_call", "vox_usage_streams", ["call_id"])
    op.create_index(
        "uq_vox_usage_call_transport",
        "vox_usage_streams",
        ["call_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'transport'"),
        sqlite_where=sa.text("kind = 'transport'"),
    )
    op.create_table(
        "vox_usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "stream_id",
            sa.Uuid(),
            sa.ForeignKey("vox_usage_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("stream_id", "event_id", name="uq_vox_usage_event"),
        sa.UniqueConstraint("stream_id", "sequence", name="uq_vox_usage_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_vox_usage_event_sequence"),
    )
    op.create_index("ix_vox_usage_events_stream_id", "vox_usage_events", ["stream_id"])
    op.create_table(
        "vox_usage_deltas",
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
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("speaking_ms", sa.BigInteger(), nullable=False),
        sa.Column("idle_ms", sa.BigInteger(), nullable=False),
        sa.Column("connected_ms", sa.BigInteger(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(20, 10)),
        sa.Column("pricing_version", sa.String(64)),
        sa.CheckConstraint(
            "speaking_ms >= 0 AND idle_ms >= 0 AND connected_ms >= 0 AND tokens >= 0",
            name="ck_vox_usage_delta_nonnegative",
        ),
        sa.CheckConstraint("speaking_ms + idle_ms = connected_ms", name="ck_vox_usage_delta_clock"),
        sa.CheckConstraint(
            "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",
            name="ck_vox_usage_delta_estimate",
        ),
    )
    op.create_index("ix_vox_usage_deltas_stream_id", "vox_usage_deltas", ["stream_id"])
    op.create_index("ix_vox_usage_delta_owner_time", "vox_usage_deltas", ["user_id", "occurred_at"])


def downgrade() -> None:
    tables = ("vox_usage_deltas", "vox_usage_events", "vox_usage_streams", "vox_usage_policy")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
    for table in tables:
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(
                "Voice usage and billing evidence must be preserved; downgrade refused"
            )
    for table in tables:
        op.drop_table(table)
