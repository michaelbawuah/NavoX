"""Owned feed metadata and attribution; private URLs live only in Secret Broker."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class LmsFeed(Base):
    __tablename__ = "lms_feeds"
    __table_args__ = (UniqueConstraint("connection_id", name="uq_lms_feed_connection"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="CASCADE")
    )
    domain: Mapped[str] = mapped_column(String(253))
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(32))
    timezone: Mapped[str] = mapped_column(String(64))
    all_day_due_time: Mapped[str] = mapped_column(String(5))
    state: Mapped[str] = mapped_column(String(24), default="preview")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    course_matches: Mapped[dict[str, str | None]] = mapped_column(JSON, default=dict)
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_expanded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_manual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsFeedPreview(Base):
    __tablename__ = "lms_feed_previews"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("lms_feeds.id", ondelete="CASCADE"), unique=True
    )
    preview_hash: Mapped[str] = mapped_column(String(64))
    parsed: Mapped[dict[str, object]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[UUID | None] = mapped_column(Uuid)
    confirm_hash: Mapped[str | None] = mapped_column(String(64))


class LmsFeedItem(Base):
    __tablename__ = "lms_feed_items"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "identity_key", name="uq_lms_item_owner_identity"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    identity_key: Mapped[str] = mapped_column(String(64))
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_courses.id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    conflicted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LmsFeedSource(Base):
    __tablename__ = "lms_feed_sources"
    __table_args__ = (UniqueConstraint("feed_id", "item_id", name="uq_lms_source_feed_item"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("lms_feeds.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[UUID] = mapped_column(
        ForeignKey("lms_feed_items.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_courses.id", ondelete="SET NULL")
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    present: Mapped[bool] = mapped_column(Boolean, default=True)
