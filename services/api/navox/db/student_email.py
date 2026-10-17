"""Expiring encrypted email reviews and durable task provenance fences."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class StudentEmailReview(Base):
    __tablename__ = "student_email_reviews"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", "request_id", name="uq_student_email_request"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    payload_hash: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_resources.id", ondelete="SET NULL"), index=True
    )
    connector_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="SET NULL"), index=True
    )
    source_hash: Mapped[str | None] = mapped_column(String(64))
    authority_hash: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    accepted_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    acceptance_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailTaskSource(Base):
    __tablename__ = "student_email_task_sources"
    __table_args__ = (UniqueConstraint("task_id", name="uq_student_email_task"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_tasks.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_resources.id", ondelete="SET NULL"), index=True
    )
    connector_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connector_connections.id", ondelete="SET NULL"), index=True
    )
    source_hash: Mapped[str] = mapped_column(String(64))
    document_hash: Mapped[str] = mapped_column(String(64))
    authority_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
