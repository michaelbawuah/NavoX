"""Add explicit verified-send thread watches, without changing provider grants."""

import sqlalchemy as sa
from alembic import op

revision = "0036_thread_watches"
down_revision = "0035_news_article_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "thread_watches",
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
            "action_id", sa.Uuid(), sa.ForeignKey("actions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("action_payload_hash", sa.String(64), nullable=False),
        sa.Column("sender_email", sa.String(320), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("sent_message_id", sa.String(512), nullable=False),
        sa.Column("thread_id", sa.String(512), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
        sa.Column("reply_message_id", sa.String(512)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "workspace_id", "action_id", name="uq_thread_watch_action"),
        sa.UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_thread_watch_request"
        ),
    )
    for column in ("user_id", "workspace_id", "connection_id", "status", "next_check_at"):
        op.create_index(f"ix_thread_watches_{column}", "thread_watches", [column])


def downgrade() -> None:
    # An explicit administrative rollback may remove these reminder preferences.
    op.drop_table("thread_watches")
