"""Short-lived encrypted recovery receipts with content-free retry tombstones."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class StudentRecoveryPreview(Base):
    __tablename__ = "student_recovery_previews"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_recovery_request"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_recovery_previews.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String(20))
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    preview_hash: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
