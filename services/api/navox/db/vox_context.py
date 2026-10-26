"""Temporary active-call execution state; no audio, saved memory or history API."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class VoxCallCheckpoint(Base):
    __tablename__ = "vox_call_checkpoints"
    __table_args__ = (
        CheckConstraint("generation >= 0", name="ck_vox_checkpoint_generation"),
        CheckConstraint("revision >= 0", name="ck_vox_checkpoint_revision"),
        CheckConstraint("expires_at >= created_at", name="ck_vox_checkpoint_expiry"),
        CheckConstraint(
            "encrypted_context IS NULL OR length(encrypted_context) <= 3000000",
            name="ck_vox_checkpoint_size",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(
        ForeignKey("vox_calls.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    auth_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE")
    )
    generation: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    memory_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    off_the_record: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    encrypted_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
