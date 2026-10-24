"""Persist the student's chosen Noa look."""

import sqlalchemy as sa
from alembic import op

revision = "0058_noa_settings"
down_revision = "0057_course_office_hours"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "noa_settings",
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
        sa.Column("look", sa.String(8), nullable=False),
        sa.Column("vibe", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_noa_settings_owner"),
    )
    op.create_index("ix_noa_settings_user_id", "noa_settings", ["user_id"])
    op.create_index("ix_noa_settings_workspace_id", "noa_settings", ["workspace_id"])


def downgrade():
    op.drop_table("noa_settings")
