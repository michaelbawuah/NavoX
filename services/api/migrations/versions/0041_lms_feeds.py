"""Read-only learning-system feeds and cross-feed source attribution."""

import sqlalchemy as sa
from alembic import op

revision = "0041_lms_feeds"
down_revision = "0040_student_alerts"
branch_labels = None
depends_on = None


def owner() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "lms_feeds",
        *owner(),
        sa.Column(
            "connection_id",
            sa.Uuid(),
            sa.ForeignKey("connector_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("all_day_due_time", sa.String(5), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("course_matches", sa.JSON(), nullable=False),
        sa.Column("etag", sa.String(512)),
        sa.Column("last_modified", sa.String(128)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_expanded_at", sa.DateTime(timezone=True)),
        sa.Column("next_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_manual_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(64)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("connection_id", name="uq_lms_feed_connection"),
    )
    op.create_table(
        "lms_feed_previews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "feed_id",
            sa.Uuid(),
            sa.ForeignKey("lms_feeds.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("preview_hash", sa.String(64), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("request_id", sa.Uuid()),
        sa.Column("confirm_hash", sa.String(64)),
    )
    op.create_table(
        "lms_feed_items",
        *owner(),
        sa.Column("identity_key", sa.String(64), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("student_courses.id", ondelete="SET NULL")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", "identity_key", name="uq_lms_item_owner_identity"
        ),
    )
    op.create_table(
        "lms_feed_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "feed_id", sa.Uuid(), sa.ForeignKey("lms_feeds.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("lms_feed_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("student_courses.id", ondelete="SET NULL")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("feed_id", "item_id", name="uq_lms_source_feed_item"),
    )
    for table in ("lms_feeds", "lms_feed_items"):
        for column in ("user_id", "workspace_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index("ix_lms_feeds_next_sync_at", "lms_feeds", ["next_sync_at"])
    op.create_index("ix_lms_feed_previews_expires_at", "lms_feed_previews", ["expires_at"])
    for column in ("feed_id", "item_id"):
        op.create_index(f"ix_lms_feed_sources_{column}", "lms_feed_sources", [column])


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM lms_feeds) "
        "OR EXISTS (SELECT 1 FROM lms_feed_items) THEN RAISE EXCEPTION "
        "'saved feed data prevents this downgrade'; END IF; END $$"
    )
    for table in ("lms_feed_sources", "lms_feed_items", "lms_feed_previews", "lms_feeds"):
        op.drop_table(table)
