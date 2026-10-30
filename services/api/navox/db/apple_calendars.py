"""Apple app-password consent is not represented as a fabricated OAuth attempt."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class AppleCalendarGrant(Base):
    __tablename__ = "apple_calendar_grants"
    __table_args__ = (
        UniqueConstraint("owner_scope_id", "request_id", name="uq_apple_calendar_request"),
        UniqueConstraint("connection_id", name="uq_apple_calendar_connection"),
        CheckConstraint("generation >= 0 AND revision >= 1", name="ck_apple_calendar_revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_scope_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_calendar_states.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    request_digest: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    consent_version: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
