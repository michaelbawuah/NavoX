"""Track connection support for new person identities without guessing legacy provenance.

Revision ID: 0020_identity_sources
Revises: 0019_event_receipts
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_identity_sources"
down_revision = "0019_event_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "person_identities",
        sa.Column("source_attributed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "person_identity_sources",
        sa.Column(
            "identity_id",
            sa.Uuid(),
            sa.ForeignKey("person_identities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_person_identity_sources_connection_id", "person_identity_sources", ["connection_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_person_identity_sources_connection_id", "person_identity_sources")
    op.drop_table("person_identity_sources")
    op.drop_column("person_identities", "source_attributed")
