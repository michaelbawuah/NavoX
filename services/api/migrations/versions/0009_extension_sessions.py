"""Add client type to user sessions for Chrome extension authentication.

Revision ID: 0009_extension_sessions
Revises: 0008_proactive_navox
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_extension_sessions"
down_revision = "0008_proactive_navox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("client_type", sa.String(length=32), nullable=False, server_default="web"),
    )
    op.create_index("ix_user_sessions_client_type", "user_sessions", ["client_type"])


def downgrade() -> None:
    op.drop_index("ix_user_sessions_client_type", table_name="user_sessions")
    op.drop_column("user_sessions", "client_type")
