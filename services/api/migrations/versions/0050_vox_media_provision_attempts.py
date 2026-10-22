"""Keep unknown provider operations distinct from verified media cleanup."""

import sqlalchemy as sa
from alembic import op

revision = "0050_vox_provision_attempts"
down_revision = "0049_vox_media_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vox_media_provision_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('in-flight', 'acknowledged', 'uncertain')",
            name="ck_vox_provision_status",
        ),
        sa.CheckConstraint(
            "(status = 'in-flight' AND finished_at IS NULL) OR "
            "(status != 'in-flight' AND finished_at IS NOT NULL)",
            name="ck_vox_provision_finished",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_vox_provision_time",
        ),
    )
    op.create_index(
        "ix_vox_media_provision_attempts_status", "vox_media_provision_attempts", ["status"]
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN LOCK TABLE vox_media_provision_attempts IN ACCESS EXCLUSIVE MODE; "
        "IF EXISTS (SELECT 1 FROM vox_media_provision_attempts) "
        "THEN RAISE EXCEPTION 'Provider provisioning evidence prevents this downgrade'; "
        "END IF; END $$"
    )
    op.drop_table("vox_media_provision_attempts")
