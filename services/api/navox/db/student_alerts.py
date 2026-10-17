"""Opt-in encrypted browser subscriptions and content-free notification outbox."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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


class StudentAlertPreference(Base):
    __tablename__ = "student_alert_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_student_alert_owner"),
        CheckConstraint("lead_minutes BETWEEN 1 AND 60", name="ck_student_alert_lead"),
        CheckConstraint("daily_cap BETWEEN 1 AND 12", name="ck_student_alert_cap"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    next_class: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    leave_now: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    lead_minutes: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    quiet_start: Mapped[str] = mapped_column(String(5), default="23:00", server_default="23:00")
    quiet_end: Mapped[str] = mapped_column(String(5), default="07:00", server_default="07:00")
    daily_cap: Mapped[int] = mapped_column(Integer, default=12, server_default="12")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StudentPushSubscription(Base):
    __tablename__ = "student_push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint_hash", name="uq_student_push_endpoint"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    endpoint_hash: Mapped[str] = mapped_column(String(64))
    encrypted_subscription: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentAlertOutbox(Base):
    __tablename__ = "student_alert_outbox"
    __table_args__ = (
        UniqueConstraint("subscription_id", "bundle_key", name="uq_student_alert_delivery"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_push_subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bundle_key: Mapped[str] = mapped_column(String(64))
    preference_revision: Mapped[int] = mapped_column(Integer)
    # Only stable IDs and start times: no class names, locations, or message content.
    occurrences: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(
        String(24), default="queued", server_default="queued", index=True
    )
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
