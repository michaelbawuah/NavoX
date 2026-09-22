"""Add commitment engine operational state and provenance.

Revision ID: 0005_commitment_engine
Revises: 0004_event_pipeline
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_commitment_engine"
down_revision = "0004_event_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "objectives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_objectives_user_id", "objectives", ["user_id"])
    op.create_index("ix_objectives_workspace_id", "objectives", ["workspace_id"])

    op.create_table(
        "commitments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("objective_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="candidate"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_by", sa.String(length=32), nullable=False, server_default="ai"),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["objective_id"], ["objectives.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_commitments_workspace_dedupe_key",
        ),
    )
    op.create_index("ix_commitments_user_id", "commitments", ["user_id"])
    op.create_index("ix_commitments_workspace_id", "commitments", ["workspace_id"])
    op.create_index("ix_commitments_objective_id", "commitments", ["objective_id"])
    op.create_index("ix_commitments_status", "commitments", ["status"])
    op.create_index("ix_commitments_due_at", "commitments", ["due_at"])

    op.create_table(
        "commitment_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("incoming_event_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("external_resource_id", sa.String(length=512), nullable=True),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["commitment_id"], ["commitments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["incoming_event_id"], ["incoming_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "commitment_id",
            "incoming_event_id",
            name="uq_commitment_sources_commitment_incoming_event",
        ),
    )
    op.create_index("ix_commitment_sources_commitment_id", "commitment_sources", ["commitment_id"])
    op.create_index("ix_commitment_sources_connection_id", "commitment_sources", ["connection_id"])
    op.create_index(
        "ix_commitment_sources_incoming_event_id", "commitment_sources", ["incoming_event_id"]
    )

    op.create_table(
        "commitment_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_commitment_id", sa.Uuid(), nullable=False),
        sa.Column("to_commitment_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["from_commitment_id"], ["commitments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_commitment_id"], ["commitments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_commitment_id",
            "to_commitment_id",
            "relation_type",
            name="uq_commitment_relations_from_to_type",
        ),
    )
    op.create_index(
        "ix_commitment_relations_from_commitment_id", "commitment_relations", ["from_commitment_id"]
    )
    op.create_index(
        "ix_commitment_relations_to_commitment_id", "commitment_relations", ["to_commitment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_commitment_relations_to_commitment_id", table_name="commitment_relations")
    op.drop_index("ix_commitment_relations_from_commitment_id", table_name="commitment_relations")
    op.drop_table("commitment_relations")
    op.drop_index("ix_commitment_sources_incoming_event_id", table_name="commitment_sources")
    op.drop_index("ix_commitment_sources_connection_id", table_name="commitment_sources")
    op.drop_index("ix_commitment_sources_commitment_id", table_name="commitment_sources")
    op.drop_table("commitment_sources")
    op.drop_index("ix_commitments_due_at", table_name="commitments")
    op.drop_index("ix_commitments_status", table_name="commitments")
    op.drop_index("ix_commitments_objective_id", table_name="commitments")
    op.drop_index("ix_commitments_workspace_id", table_name="commitments")
    op.drop_index("ix_commitments_user_id", table_name="commitments")
    op.drop_table("commitments")
    op.drop_index("ix_objectives_workspace_id", table_name="objectives")
    op.drop_index("ix_objectives_user_id", table_name="objectives")
    op.drop_table("objectives")
