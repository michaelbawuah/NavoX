"""Short-lived encrypted delivery queue; never conversation history."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class VoxWakeCommand(Base):
    __tablename__ = "vox_wake_commands"
    __table_args__ = (
        UniqueConstraint("connection_id", "request_id", name="uq_vox_wake_request"),
        CheckConstraint("expires_at >= created_at", name="ck_vox_wake_expiry"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("vox_media_connections.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    digest: Mapped[str] = mapped_column(String(64))
    encrypted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
