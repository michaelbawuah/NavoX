"""Owner-generation fences and expiring, content-free Vox call reservations."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class VoxState(Base):
    __tablename__ = "vox_states"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_vox_state_owner"),
        CheckConstraint("generation >= 0", name="ck_vox_state_generation"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=0)


class VoxCall(Base):
    __tablename__ = "vox_calls"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "workspace_id", "client_call_id", name="uq_vox_call_client_owner"
        ),
        CheckConstraint("generation >= 0", name="ck_vox_call_generation"),
        CheckConstraint("status IN ('reserved', 'ended')", name="ck_vox_call_status"),
        CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('hang-up', 'sign-out', 'expired')",
            name="ck_vox_call_end_reason",
        ),
        CheckConstraint(
            "(status = 'reserved' AND ended_at IS NULL AND end_reason IS NULL) OR "
            "(status = 'ended' AND ended_at IS NOT NULL AND end_reason IS NOT NULL)",
            name="ck_vox_call_terminal_state",
        ),
        CheckConstraint("expires_at >= created_at", name="ck_vox_call_expiry"),
        Index(
            "uq_vox_reserved_owner",
            "user_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'reserved'"),
            sqlite_where=text("status = 'reserved'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    client_call_id: Mapped[UUID] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    end_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
