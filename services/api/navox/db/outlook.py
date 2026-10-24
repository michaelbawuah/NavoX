"""Encrypted Outlook reviews and durable single-execution mail receipts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class OutlookMailDraft(Base):
    __tablename__ = "outlook_mail_drafts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_outlook_preview_request"
        ),
        UniqueConstraint(
            "workspace_id", "user_id", "send_request_id", name="uq_outlook_send_request"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="CASCADE"), index=True
    )
    mailbox_binding: Mapped[str] = mapped_column(String(64), index=True)
    source_message_id: Mapped[str] = mapped_column(String(512))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="review")
    send_request_id: Mapped[UUID | None] = mapped_column(Uuid)
    internet_message_id: Mapped[str | None] = mapped_column(String(256))
    sent_message_id: Mapped[str | None] = mapped_column(String(512))
    receipt_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_conversation_id: Mapped[str | None] = mapped_column(String(512))
    reply_recipient_hash: Mapped[str | None] = mapped_column(String(64))
    reply_state: Mapped[str] = mapped_column(
        String(32), default="not_checked", server_default="not_checked"
    )
    reply_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_message_id: Mapped[str | None] = mapped_column(String(512))
    reply_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_scan_count: Mapped[int | None] = mapped_column(Integer)
    reply_scan_complete: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
