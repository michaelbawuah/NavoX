"""Add separate calendar-purpose consent and retained reset fencing."""

import sqlalchemy as sa
from alembic import op

revision = "0051_student_calendar_consent"
down_revision = "0050_vox_provision_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_calendar_states",
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
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_student_calendar_owner"),
        sa.CheckConstraint("generation >= 0 AND revision >= 0", name="ck_student_calendar_state"),
    )
    for column in ("user_id", "workspace_id"):
        op.create_index(f"ix_student_calendar_states_{column}", "student_calendar_states", [column])
    op.create_table(
        "student_calendar_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_scope_id",
            sa.Uuid(),
            sa.ForeignKey("student_calendar_states.id", ondelete="CASCADE"),
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
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("connections.id", ondelete="CASCADE")),
        sa.Column(
            "oauth_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("oauth_authorization_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "auth_session_id", sa.Uuid(), sa.ForeignKey("user_sessions.id", ondelete="SET NULL")
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("consent_version", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_scope_id", "request_id", name="uq_student_calendar_request"),
        sa.UniqueConstraint("oauth_attempt_id", name="uq_student_calendar_oauth_attempt"),
        sa.CheckConstraint(
            "generation >= 0 AND revision >= 1", name="ck_student_calendar_grant_revision"
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','revoked')", name="ck_student_calendar_status"
        ),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(status != 'revoked' AND revoked_at IS NULL)",
            name="ck_student_calendar_revoked",
        ),
        sa.CheckConstraint(
            "status != 'active' OR connection_id IS NOT NULL", name="ck_student_calendar_active"
        ),
    )
    for column in (
        "owner_scope_id",
        "user_id",
        "workspace_id",
        "connection_id",
        "oauth_attempt_id",
        "auth_session_id",
    ):
        op.create_index(f"ix_student_calendar_grants_{column}", "student_calendar_grants", [column])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "LOCK TABLE student_calendar_states, student_calendar_grants IN ACCESS EXCLUSIVE MODE"
        )
    # Reset generations and permission tombstones are authority. Even a withdrawn
    # grant cannot be silently dropped by a schema downgrade.
    for table in ("student_calendar_grants", "student_calendar_states"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("Calendar consent records must be preserved; downgrade refused")
    op.drop_table("student_calendar_grants")
    op.drop_table("student_calendar_states")
