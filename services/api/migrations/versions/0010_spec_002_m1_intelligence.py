"""Add SPEC-002 operational intelligence contracts persistence.

Revision ID: 0010_spec_002_m1_intelligence
Revises: 0009_extension_sessions
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_spec_002_m1_intelligence"
down_revision = "0009_extension_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
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
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workspace_id", "canonical_name", "status"):
        op.create_index(f"ix_people_{column}", "people", [column])

    op.create_table(
        "person_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("identity_type", sa.String(length=64), nullable=False),
        sa.Column("identity_value", sa.String(length=512), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
            server_default="1.000",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "identity_type",
            "identity_value",
            name="uq_person_identities_workspace_type_value",
        ),
    )
    for column in (
        "workspace_id",
        "person_id",
        "provider",
        "identity_type",
        "identity_value",
    ):
        op.create_index(f"ix_person_identities_{column}", "person_identities", [column])

    op.create_table(
        "operational_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("observation_type", sa.String(length=64), nullable=False),
        sa.Column("subject_person_id", sa.Uuid(), nullable=True),
        sa.Column("object_person_id", sa.Uuid(), nullable=True),
        sa.Column("action_text", sa.Text(), nullable=True),
        sa.Column("object_text", sa.Text(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["object_person_id"],
            ["people.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subject_person_id"],
            ["people.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "workspace_id",
        "user_id",
        "observation_type",
        "subject_person_id",
        "object_person_id",
        "effective_at",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_operational_observations_{column}",
            "operational_observations",
            [column],
        )

    op.create_table(
        "observation_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("external_resource_id", sa.String(length=512), nullable=False),
        sa.Column("evidence_locator", sa.JSON(), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["operational_observations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "observation_id",
            "connection_id",
            "provider",
            "source_type",
            "external_resource_id",
            "source_hash",
            name="uq_observation_evidence_observation_source_hash",
        ),
    )
    for column in (
        "observation_id",
        "connection_id",
        "provider",
        "source_type",
        "external_resource_id",
        "source_hash",
        "observed_at",
    ):
        op.create_index(
            f"ix_observation_evidence_{column}",
            "observation_evidence",
            [column],
        )

    op.create_table(
        "intelligence_feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("feedback_type", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "workspace_id",
        "user_id",
        "target_type",
        "target_id",
        "feedback_type",
        "created_at",
    ):
        op.create_index(
            f"ix_intelligence_feedback_{column}",
            "intelligence_feedback",
            [column],
        )


def downgrade() -> None:
    for column in (
        "created_at",
        "feedback_type",
        "target_id",
        "target_type",
        "user_id",
        "workspace_id",
    ):
        op.drop_index(
            f"ix_intelligence_feedback_{column}",
            table_name="intelligence_feedback",
        )
    op.drop_table("intelligence_feedback")

    for column in (
        "observed_at",
        "source_hash",
        "external_resource_id",
        "source_type",
        "provider",
        "connection_id",
        "observation_id",
    ):
        op.drop_index(
            f"ix_observation_evidence_{column}",
            table_name="observation_evidence",
        )
    op.drop_table("observation_evidence")

    for column in (
        "created_at",
        "status",
        "effective_at",
        "object_person_id",
        "subject_person_id",
        "observation_type",
        "user_id",
        "workspace_id",
    ):
        op.drop_index(
            f"ix_operational_observations_{column}",
            table_name="operational_observations",
        )
    op.drop_table("operational_observations")

    for column in (
        "identity_value",
        "identity_type",
        "provider",
        "person_id",
        "workspace_id",
    ):
        op.drop_index(
            f"ix_person_identities_{column}",
            table_name="person_identities",
        )
    op.drop_table("person_identities")

    for column in ("status", "canonical_name", "workspace_id"):
        op.drop_index(f"ix_people_{column}", table_name="people")
    op.drop_table("people")
