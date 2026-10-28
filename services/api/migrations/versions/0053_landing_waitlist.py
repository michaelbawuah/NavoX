"""Store landing waitlist registrations and shared rate-limit counters."""

import sqlalchemy as sa
from alembic import op

revision = "0053_landing_waitlist"
down_revision = "0052_student_calendar_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist_entries",
        sa.Column("email", sa.String(320), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("school_domain", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "waitlist_rate_limits",
        sa.Column("client_hash", sa.String(64), primary_key=True),
        sa.Column("window", sa.Integer(), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("waitlist_rate_limits")
    op.drop_table("waitlist_entries")
