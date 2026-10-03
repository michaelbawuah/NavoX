"""Tag communication drafts by source binding and allow the knowledge-email path."""

import sqlalchemy as sa
from alembic import op

revision = "0034_knowledge_email_drafts"
down_revision = "0033_news_semantic_clustering"
branch_labels = None
depends_on = None

COMMITMENT_BINDING = (
    "(binding_kind = 'COMMITMENT' AND commitment_id IS NOT NULL)"
    " OR (binding_kind = 'KNOWLEDGE_EMAIL' AND commitment_id IS NULL"
    " AND source_external_id IS NOT NULL)"
)


def upgrade() -> None:
    # Existing rows keep the commitment binding; the server default backfills them.
    op.add_column(
        "communication_drafts",
        sa.Column(
            "binding_kind",
            sa.String(length=32),
            nullable=False,
            server_default="COMMITMENT",
        ),
    )
    op.add_column(
        "communication_drafts",
        sa.Column("source_external_id", sa.String(length=512), nullable=True),
    )
    op.alter_column(
        "communication_drafts",
        "commitment_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_communication_drafts_binding_kind",
        "communication_drafts",
        "binding_kind IN ('COMMITMENT', 'KNOWLEDGE_EMAIL')",
    )
    op.create_check_constraint(
        "ck_communication_drafts_binding_commitment",
        "communication_drafts",
        COMMITMENT_BINDING,
    )


def downgrade() -> None:
    # Reverting the schema would discard knowledge-email drafts. Refuse a
    # downgrade with live rows so an operator can export or resolve them first.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM communication_drafts WHERE commitment_id IS NULL) "
        "THEN RAISE EXCEPTION 'knowledge-email drafts prevent this downgrade'; "
        "END IF; END $$"
    )
    op.drop_constraint(
        "ck_communication_drafts_binding_commitment", "communication_drafts", type_="check"
    )
    op.drop_constraint(
        "ck_communication_drafts_binding_kind", "communication_drafts", type_="check"
    )
    op.alter_column(
        "communication_drafts",
        "commitment_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column("communication_drafts", "source_external_id")
    op.drop_column("communication_drafts", "binding_kind")
