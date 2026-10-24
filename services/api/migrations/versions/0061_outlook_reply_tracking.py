"""Bounded reply evidence separate from durable send execution receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0061_outlook_reply_tracking"
down_revision = "0060_outlook_mail_drafts"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_conversation_id", sa.String(512), nullable=True),
        sa.Column("reply_recipient_hash", sa.String(64), nullable=True),
        sa.Column("reply_state", sa.String(32), server_default="not_checked", nullable=False),
        sa.Column("reply_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_message_id", sa.String(512), nullable=True),
        sa.Column("reply_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_scan_count", sa.Integer(), nullable=True),
        sa.Column("reply_scan_complete", sa.Boolean(), nullable=True),
    ):
        op.add_column("outlook_mail_drafts", column)


def downgrade():
    for name in (
        "reply_scan_complete",
        "reply_scan_count",
        "reply_received_at",
        "reply_message_id",
        "reply_checked_at",
        "reply_state",
        "reply_recipient_hash",
        "reply_conversation_id",
        "sent_at",
    ):
        op.drop_column("outlook_mail_drafts", name)
