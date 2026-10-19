"""Add content-free, expiring Vox reservations and owner-generation fences."""

import sqlalchemy as sa
from alembic import op

revision = "0048_vox_calls"
down_revision = "0047_saved_boards"
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
        "vox_states",
        *owner_columns(),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_vox_state_owner"),
        sa.CheckConstraint("generation >= 0", name="ck_vox_state_generation"),
    )
    op.create_table(
        "vox_calls",
        *owner_columns(),
        sa.Column(
            "auth_session_id",
            sa.Uuid(),
            sa.ForeignKey("user_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_call_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("end_reason", sa.String(16)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "client_call_id", name="uq_vox_call_client_owner"
        ),
        sa.CheckConstraint("generation >= 0", name="ck_vox_call_generation"),
        sa.CheckConstraint("status IN ('reserved', 'ended')", name="ck_vox_call_status"),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('hang-up', 'sign-out', 'expired')",
            name="ck_vox_call_end_reason",
        ),
        sa.CheckConstraint(
            "(status = 'reserved' AND ended_at IS NULL AND end_reason IS NULL) OR "
            "(status = 'ended' AND ended_at IS NOT NULL AND end_reason IS NOT NULL)",
            name="ck_vox_call_terminal_state",
        ),
        sa.CheckConstraint("expires_at >= created_at", name="ck_vox_call_expiry"),
    )
    for table in ("vox_states", "vox_calls"):
        for column in ("user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_vox_calls_auth_session_id", "vox_calls", ["auth_session_id"])
    op.create_index(
        "uq_vox_reserved_owner",
        "vox_calls",
        ["user_id", "workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'reserved'"),
        sqlite_where=sa.text("status = 'reserved'"),
    )


def downgrade() -> None:
    # Both tables are locked before the guard so concurrent reservations cannot
    # arrive between the emptiness check and drop. Alembic's PostgreSQL DDL
    # transaction retains the old version and tables if the guard raises.
    op.execute(
        "DO $$ BEGIN LOCK TABLE vox_calls, vox_states IN ACCESS EXCLUSIVE MODE; "
        "IF EXISTS (SELECT 1 FROM vox_calls) OR EXISTS (SELECT 1 FROM vox_states) "
        "THEN RAISE EXCEPTION 'Vox reservation data prevents this downgrade'; "
        "END IF; END $$"
    )
    op.drop_table("vox_calls")
    op.drop_table("vox_states")
