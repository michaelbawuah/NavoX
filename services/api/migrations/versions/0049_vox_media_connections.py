"""Add opaque media epochs and cleanup instructions that survive owner erasure."""

import sqlalchemy as sa
from alembic import op

revision = "0049_vox_media_connections"
down_revision = "0048_vox_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vox_media_connections",
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
            "auth_session_id",
            sa.Uuid(),
            sa.ForeignKey("user_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "call_id", sa.Uuid(), sa.ForeignKey("vox_calls.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "owner_scope_id",
            sa.Uuid(),
            sa.ForeignKey("vox_states.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("room_name", sa.String(64), nullable=False, unique=True),
        sa.Column("client_identity", sa.String(64), nullable=False, unique=True),
        sa.Column("agent_identity", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(24)),
        sa.UniqueConstraint("owner_scope_id", "request_id", name="uq_vox_media_request"),
        sa.CheckConstraint("generation >= 0", name="ck_vox_media_generation"),
        sa.CheckConstraint(
            "status IN ('preparing', 'confirmed', 'revoked')", name="ck_vox_media_status"
        ),
        sa.CheckConstraint("expires_at >= created_at", name="ck_vox_media_expiry"),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND reason IS NOT NULL) OR "
            "(status != 'revoked' AND revoked_at IS NULL AND reason IS NULL)",
            name="ck_vox_media_revoked",
        ),
        sa.CheckConstraint(
            "status != 'confirmed' OR confirmed_at IS NOT NULL", name="ck_vox_media_confirmed"
        ),
    )
    for column in (
        "user_id",
        "workspace_id",
        "auth_session_id",
        "call_id",
        "owner_scope_id",
        "expires_at",
    ):
        op.create_index(f"ix_vox_media_connections_{column}", "vox_media_connections", [column])
    op.create_index(
        "uq_vox_media_current_owner",
        "vox_media_connections",
        ["owner_scope_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('preparing', 'confirmed')"),
        sqlite_where=sa.text("status IN ('preparing', 'confirmed')"),
    )
    op.create_table(
        "vox_media_cleanup",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("owner_scope_id", sa.Uuid(), nullable=False),
        sa.Column("room_name", sa.String(64), nullable=False, unique=True),
        sa.Column("client_identity", sa.String(64), nullable=False, unique=True),
        sa.Column("agent_identity", sa.String(64), nullable=False, unique=True),
        sa.Column("connection_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("claim_id", sa.Uuid()),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('armed', 'pending', 'claimed', 'done')", name="ck_vox_cleanup_status"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_vox_cleanup_attempts"),
        sa.CheckConstraint(
            "(status = 'claimed' AND claim_id IS NOT NULL AND claim_expires_at IS NOT NULL) OR "
            "(status != 'claimed' AND claim_id IS NULL AND claim_expires_at IS NULL)",
            name="ck_vox_cleanup_claim",
        ),
        sa.CheckConstraint(
            "(status = 'armed' AND requested_at IS NULL AND next_attempt_at IS NULL) OR "
            "(status IN ('pending', 'claimed') AND requested_at IS NOT NULL "
            "AND next_attempt_at IS NOT NULL) OR "
            "(status = 'done' AND requested_at IS NOT NULL AND next_attempt_at IS NULL)",
            name="ck_vox_cleanup_schedule",
        ),
        sa.CheckConstraint(
            "(status = 'done' AND completed_at IS NOT NULL) OR "
            "(status != 'done' AND completed_at IS NULL)",
            name="ck_vox_cleanup_complete",
        ),
    )
    for column in ("owner_scope_id", "status", "next_attempt_at"):
        op.create_index(f"ix_vox_media_cleanup_{column}", "vox_media_cleanup", [column])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN LOCK TABLE vox_media_connections, vox_media_cleanup IN ACCESS EXCLUSIVE MODE; "
        "IF EXISTS (SELECT 1 FROM vox_media_connections) "
        "OR EXISTS (SELECT 1 FROM vox_media_cleanup) "
        "THEN RAISE EXCEPTION 'Vox media authority or cleanup prevents this downgrade'; "
        "END IF; END $$"
    )
    op.drop_table("vox_media_connections")
    op.drop_table("vox_media_cleanup")
