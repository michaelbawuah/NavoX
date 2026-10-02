"""Add nullable, rights-checked article image metadata without rewriting rows."""

import sqlalchemy as sa
from alembic import op

revision = "0035_news_article_images"
down_revision = "0034_knowledge_email_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("news_items", sa.Column("image", sa.JSON(), nullable=True))


def downgrade() -> None:
    # Do not silently discard licensed image metadata on rollback.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM news_items WHERE image IS NOT NULL) "
        "THEN RAISE EXCEPTION 'article images prevent this downgrade'; END IF; END $$"
    )
    op.drop_column("news_items", "image")
