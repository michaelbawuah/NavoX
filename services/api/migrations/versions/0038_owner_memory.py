"""Add explicit owner-saved memory without importing or deriving any existing data."""

import sqlalchemy as sa
from alembic import op

revision = "0038_owner_memory"
down_revision = "0037_personal_routines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.Uuid(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("preference_key", sa.String(32), nullable=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("content", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provenance", sa.String(32), nullable=False, server_default="user_saved"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "preference_key", name="uq_owner_memory_preference"
        ),
        sa.CheckConstraint(
            "kind IN ('reply_length', 'tone', 'signoff', 'study_hours', 'project_fact')",
            name="ck_owner_memory_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'project_fact' AND preference_key IS NULL) OR "
            "(kind <> 'project_fact' AND preference_key IS NOT NULL AND preference_key = kind)",
            name="ck_owner_memory_preference_key",
        ),
        sa.CheckConstraint("length(label) BETWEEN 1 AND 80", name="ck_owner_memory_label"),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 500", name="ck_owner_memory_content"),
        sa.CheckConstraint("provenance = 'user_saved'", name="ck_owner_memory_provenance"),
    )
    op.create_index("ix_owner_memories_workspace_id", "owner_memories", ["workspace_id"])
    op.create_index("ix_owner_memories_user_id", "owner_memories", ["user_id"])


def downgrade() -> None:
    # Owner text must be explicitly forgotten rather than silently lost on rollback.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM owner_memories) "
        "THEN RAISE EXCEPTION 'saved owner memory prevents this downgrade'; END IF; END $$"
    )
    op.drop_index("ix_owner_memories_user_id", table_name="owner_memories")
    op.drop_index("ix_owner_memories_workspace_id", table_name="owner_memories")
    op.drop_table("owner_memories")
