"""Source-cited internal importance review, separate from observed trend."""

import sqlalchemy as sa
from alembic import op

revision = "0032_news_importance"
down_revision = "0031_knowledge_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("news_story_clusters", sa.Column("importance_review", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("news_story_clusters", "importance_review")
