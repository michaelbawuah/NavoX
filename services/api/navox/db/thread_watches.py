"""Owner-opted-in metadata-only watches of a verified Gmail send."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class ThreadWatch(Base):
    __tablename__ = "thread_watches"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", "action_id", name="uq_thread_watch_action"),
        UniqueConstraint("user_id", "workspace_id", "request_id", name="uq_thread_watch_request"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    action_id: Mapped[UUID] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"))
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    action_payload_hash: Mapped[str] = mapped_column(String(64))
    sender_email: Mapped[str] = mapped_column(String(320))
    recipient_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(256))
    sent_message_id: Mapped[str] = mapped_column(String(512))
    thread_id: Mapped[str] = mapped_column(String(512))
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="watching", index=True)
    reason: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_message_id: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
