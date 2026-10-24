"""Store owner-entered course office hours."""

import sqlalchemy as sa
from alembic import op

revision = "0057_course_office_hours"
down_revision = "0056_vox_call_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_courses", sa.Column("office_hours", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("student_courses", "office_hours")
