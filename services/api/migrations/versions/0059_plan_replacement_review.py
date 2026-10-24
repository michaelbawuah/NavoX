"""Bind plan approval to every replaced study block."""

import sqlalchemy as sa
from alembic import op

revision = "0059_plan_replacement_review"
down_revision = "0058_noa_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "student_planning_previews", sa.Column("replaced_blocks", sa.JSON(), nullable=True)
    )


def downgrade():
    op.drop_column("student_planning_previews", "replaced_blocks")
