"""SPEC-002 incremental processing and explainable attention.

Revision ID: 0011_intelligence_engine
Revises: 0010_spec_002_m1_intelligence
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_intelligence_engine"
down_revision = "0010_spec_002_m1_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incoming_events",
        sa.Column("intelligence_status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.create_index(
        "ix_incoming_events_intelligence_status", "incoming_events", ["intelligence_status"]
    )
    op.add_column("intelligence_feedback", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_feedback_request", "intelligence_feedback", ["workspace_id", "user_id", "request_id"]
    )
    op.add_column(
        "commitments",
        sa.Column(
            "intelligence_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column(
        "commitments",
        sa.Column("attention_score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "commitments",
        sa.Column("attention_factors", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "commitments",
        sa.Column("attention_band", sa.String(32), nullable=False, server_default="SUPPRESS"),
    )
    op.create_table(
        "intelligence_cursors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("connection_id", "source", name="uq_intelligence_cursor"),
    )
    op.create_index(
        "ix_intelligence_cursors_connection_id", "intelligence_cursors", ["connection_id"]
    )
    op.create_table(
        "intelligence_preferences",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("weights", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "workspace_display_preferences",
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("clock_format", sa.String(8), nullable=False, server_default="12h"),
        sa.Column("temperature_unit", sa.String(16), nullable=False, server_default="celsius"),
        sa.Column("weather_visible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("weather_city", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("workspace_display_preferences")
    op.drop_table("intelligence_preferences")
    op.drop_table("intelligence_cursors")
    for name in ("attention_band", "attention_factors", "attention_score", "intelligence_metadata"):
        op.drop_column("commitments", name)
    op.drop_constraint("uq_feedback_request", "intelligence_feedback", type_="unique")
    op.drop_column("intelligence_feedback", "request_id")
    op.drop_index("ix_incoming_events_intelligence_status", "incoming_events")
    op.drop_column("incoming_events", "intelligence_status")
