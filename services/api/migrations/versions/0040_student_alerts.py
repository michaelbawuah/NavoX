"""Opt-in class reminders, encrypted device subscriptions, and durable delivery state."""

import sqlalchemy as sa
from alembic import op

revision = "0040_student_alerts"
down_revision = "0039_student_foundation"
branch_labels = None
depends_on = None


def owner_columns() -> list[sa.Column]:
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
        "student_alert_preferences",
        *owner_columns(),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_class", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("leave_now", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lead_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("quiet_start", sa.String(5), nullable=False, server_default="23:00"),
        sa.Column("quiet_end", sa.String(5), nullable=False, server_default="07:00"),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_student_alert_owner"),
        sa.CheckConstraint("lead_minutes BETWEEN 1 AND 60", name="ck_student_alert_lead"),
        sa.CheckConstraint("daily_cap BETWEEN 1 AND 12", name="ck_student_alert_cap"),
    )
    op.create_table(
        "student_push_subscriptions",
        *owner_columns(),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("user_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_subscription", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("endpoint_hash", name="uq_student_push_endpoint"),
    )
    op.create_table(
        "student_alert_outbox",
        *owner_columns(),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey("student_push_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("bundle_key", sa.String(64), nullable=False),
        sa.Column("preference_revision", sa.Integer(), nullable=False),
        sa.Column("occurrences", sa.JSON(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("subscription_id", "bundle_key", name="uq_student_alert_delivery"),
    )
    for table in (
        "student_alert_preferences",
        "student_push_subscriptions",
        "student_alert_outbox",
    ):
        for column in ("user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index(
        "ix_student_push_subscriptions_session_id", "student_push_subscriptions", ["session_id"]
    )
    for column in ("subscription_id", "scheduled_for", "state"):
        op.create_index(f"ix_student_alert_outbox_{column}", "student_alert_outbox", [column])


def downgrade() -> None:
    # A rollback must not silently discard an owner's enabled notification choice.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM student_alert_preferences) "
        "OR EXISTS (SELECT 1 FROM student_push_subscriptions) THEN RAISE EXCEPTION "
        "'saved notification settings prevent this downgrade'; END IF; END $$"
    )
    for table in (
        "student_alert_outbox",
        "student_push_subscriptions",
        "student_alert_preferences",
    ):
        op.drop_table(table)
