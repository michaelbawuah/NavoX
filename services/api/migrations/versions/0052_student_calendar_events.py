"""Add separately approved scheduling grants and encrypted event receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0052_student_calendar_events"
down_revision = "0051_student_calendar_consent"
branch_labels = None
depends_on = None


def owner_columns() -> list[sa.Column]:
    return [
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
        sa.Column(
            "auth_session_id", sa.Uuid(), sa.ForeignKey("user_sessions.id", ondelete="SET NULL")
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "student_calendar_write_grants",
        *owner_columns(),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("connections.id", ondelete="CASCADE")),
        sa.Column(
            "oauth_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("oauth_authorization_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consent_version", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_scope_id", "request_id", name="uq_calendar_write_request"),
        sa.UniqueConstraint("oauth_attempt_id", name="uq_calendar_write_oauth"),
        sa.CheckConstraint("generation >= 0 AND revision >= 1", name="ck_calendar_write_revision"),
        sa.CheckConstraint(
            "status IN ('pending','active','revoked')", name="ck_calendar_write_status"
        ),
        sa.CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(status != 'revoked' AND revoked_at IS NULL)",
            name="ck_calendar_write_revoked",
        ),
        sa.CheckConstraint(
            "status != 'active' OR connection_id IS NOT NULL", name="ck_calendar_write_active"
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
        op.create_index(
            f"ix_student_calendar_write_grants_{column}", "student_calendar_write_grants", [column]
        )
    op.create_table(
        "student_calendar_events",
        *owner_columns(),
        sa.Column(
            "grant_id",
            sa.Uuid(),
            sa.ForeignKey("student_calendar_write_grants.id", ondelete="SET NULL"),
        ),
        sa.Column("grant_revision", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_scope_id", "request_id", name="uq_calendar_event_request"),
        sa.CheckConstraint("generation >= 0 AND revision >= 1", name="ck_calendar_event_revision"),
        sa.CheckConstraint(
            "status IN ('pending','executing','uncertain','created','changed',"
            "'undoing','undone','failed')",
            name="ck_calendar_event_status",
        ),
    )
    for column in ("owner_scope_id", "user_id", "workspace_id"):
        op.create_index(f"ix_student_calendar_events_{column}", "student_calendar_events", [column])


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "LOCK TABLE student_calendar_events, student_calendar_write_grants "
            "IN ACCESS EXCLUSIVE MODE"
        )
    for table in ("student_calendar_events", "student_calendar_write_grants"):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(
                "Calendar scheduling and uncertain receipts must be preserved; downgrade refused"
            )
    op.drop_table("student_calendar_events")
    op.drop_table("student_calendar_write_grants")
