"""Keep provider identity and non-OAuth write consent explicit in native receipts.

After a non-Google action is admitted, retain provider-aware readers even when
the schema is not downgraded. Old f95/8b readers cannot interpret those receipts.
This migration grants no consent and enables no provider configuration.
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_provider_calendar_writes"
down_revision = "0063_apple_calendar_consent"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "student_calendar_write_grants",
        sa.Column("provider", sa.String(16), nullable=False, server_default="google"),
    )
    op.alter_column(
        "student_calendar_write_grants", "oauth_attempt_id", existing_type=sa.Uuid(), nullable=True
    )
    op.create_check_constraint(
        "ck_calendar_write_provider",
        "student_calendar_write_grants",
        "provider IN ('google','microsoft','icloud')",
    )
    op.create_check_constraint(
        "ck_calendar_write_authorization_kind",
        "student_calendar_write_grants",
        "(provider = 'icloud' AND oauth_attempt_id IS NULL) OR "
        "(provider IN ('google','microsoft') AND oauth_attempt_id IS NOT NULL)",
    )
    op.add_column(
        "student_calendar_events",
        sa.Column("provider", sa.String(16), nullable=False, server_default="google"),
    )
    op.create_check_constraint(
        "ck_calendar_event_provider",
        "student_calendar_events",
        "provider IN ('google','microsoft','icloud')",
    )


def downgrade():
    # Encrypted event receipts may outlive a consent grant, reset, or account
    # disconnect. Keep their content-free provider identity independently so a
    # downgrade cannot reinterpret a held external outcome as a Google event.
    op.execute(
        "DO $$ BEGIN "
        "LOCK TABLE student_calendar_events, student_calendar_write_grants "
        "IN ACCESS EXCLUSIVE MODE; "
        "IF EXISTS (SELECT 1 FROM student_calendar_events WHERE provider <> 'google') "
        "OR EXISTS (SELECT 1 FROM student_calendar_write_grants "
        "WHERE provider <> 'google' OR oauth_attempt_id IS NULL) THEN "
        "RAISE EXCEPTION 'Provider calendar receipts prevent this downgrade'; "
        "END IF; END $$"
    )
    op.drop_constraint("ck_calendar_event_provider", "student_calendar_events", type_="check")
    op.drop_column("student_calendar_events", "provider")
    op.drop_constraint(
        "ck_calendar_write_authorization_kind", "student_calendar_write_grants", type_="check"
    )
    op.drop_constraint("ck_calendar_write_provider", "student_calendar_write_grants", type_="check")
    op.alter_column(
        "student_calendar_write_grants", "oauth_attempt_id", existing_type=sa.Uuid(), nullable=False
    )
    op.drop_column("student_calendar_write_grants", "provider")
