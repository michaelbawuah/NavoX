"""Real Apple app-password authorization independent of OAuth records."""

import sqlalchemy as sa
from alembic import op

revision = "0063_apple_calendar_consent"
down_revision = "0062_conversation_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "apple_calendar_grants",
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
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "auth_session_id",
            sa.Uuid(),
            sa.ForeignKey("user_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("consent_version", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_scope_id", "request_id", name="uq_apple_calendar_request"),
        sa.UniqueConstraint("connection_id", name="uq_apple_calendar_connection"),
        sa.CheckConstraint("generation >= 0 AND revision >= 1", name="ck_apple_calendar_revision"),
    )
    for name in ("owner_scope_id", "user_id", "workspace_id", "connection_id", "auth_session_id"):
        op.create_index(f"ix_apple_calendar_grants_{name}", "apple_calendar_grants", [name])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM apple_calendar_grants LIMIT 1")).first():
        raise RuntimeError("Disconnect Apple calendar access before downgrading")
    op.drop_table("apple_calendar_grants")
