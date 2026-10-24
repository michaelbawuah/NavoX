"""Encrypted Outlook reviews and single-execution receipt fences."""

import sqlalchemy as sa
from alembic import op

revision = "0060_outlook_mail_drafts"
down_revision = "0059_plan_replacement_review"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outlook_mail_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mailbox_binding", sa.String(64), nullable=False),
        sa.Column("source_message_id", sa.String(512), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("send_request_id", sa.Uuid(), nullable=True),
        sa.Column("internet_message_id", sa.String(256), nullable=True),
        sa.Column("sent_message_id", sa.String(512), nullable=True),
        sa.Column("receipt_hash", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_outlook_preview_request"
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "send_request_id", name="uq_outlook_send_request"
        ),
    )
    for field in ("workspace_id", "user_id", "connection_id", "mailbox_binding"):
        op.create_index("ix_outlook_mail_drafts_" + field, "outlook_mail_drafts", [field])


def downgrade():
    op.drop_table("outlook_mail_drafts")
