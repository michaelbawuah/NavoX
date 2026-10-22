"""Content-free calendar-purpose consent and reset/replay fences."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class StudentCalendarState(Base):
    __tablename__ = "student_calendar_states"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_student_calendar_owner"),
        CheckConstraint("generation >= 0 AND revision >= 0", name="ck_student_calendar_state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class StudentCalendarGrant(Base):
    __tablename__ = "student_calendar_grants"
    __table_args__ = (
        UniqueConstraint("owner_scope_id", "request_id", name="uq_student_calendar_request"),
        UniqueConstraint("oauth_attempt_id", name="uq_student_calendar_oauth_attempt"),
        CheckConstraint(
            "generation >= 0 AND revision >= 1", name="ck_student_calendar_grant_revision"
        ),
        CheckConstraint(
            "status IN ('pending','active','revoked')", name="ck_student_calendar_status"
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(status != 'revoked' AND revoked_at IS NULL)",
            name="ck_student_calendar_revoked",
        ),
        CheckConstraint(
            "status != 'active' OR connection_id IS NOT NULL", name="ck_student_calendar_active"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_scope_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_calendar_states.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    oauth_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("oauth_authorization_attempts.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    consent_version: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentCalendarWriteGrant(Base):
    """Scheduling consent never follows merely from a broad provider scope."""

    __tablename__ = "student_calendar_write_grants"
    __table_args__ = (
        UniqueConstraint("owner_scope_id", "request_id", name="uq_calendar_write_request"),
        UniqueConstraint("oauth_attempt_id", name="uq_calendar_write_oauth"),
        CheckConstraint("generation >= 0 AND revision >= 1", name="ck_calendar_write_revision"),
        CheckConstraint(
            "status IN ('pending','active','revoked')", name="ck_calendar_write_status"
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(status != 'revoked' AND revoked_at IS NULL)",
            name="ck_calendar_write_revoked",
        ),
        CheckConstraint(
            "status != 'active' OR connection_id IS NOT NULL", name="ck_calendar_write_active"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_scope_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_calendar_states.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    oauth_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("oauth_authorization_attempts.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    consent_version: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentCalendarEvent(Base):
    """Immutable encrypted drafts and durable uncertain side-effect receipts."""

    __tablename__ = "student_calendar_events"
    __table_args__ = (
        UniqueConstraint("owner_scope_id", "request_id", name="uq_calendar_event_request"),
        CheckConstraint("generation >= 0 AND revision >= 1", name="ck_calendar_event_revision"),
        CheckConstraint(
            "status IN ('pending','executing','uncertain','created','changed',"
            "'undoing','undone','failed')",
            name="ck_calendar_event_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_scope_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_calendar_states.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_calendar_write_grants.id", ondelete="SET NULL"), nullable=True
    )
    auth_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    grant_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    digest: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
