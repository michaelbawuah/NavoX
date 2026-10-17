"""Encrypted private course content and content-free mutation receipts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
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


class CourseWorkspaceState(Base):
    __tablename__ = "student_course_workspace_states"
    __table_args__ = (
        UniqueConstraint(
            "course_id", "user_id", "workspace_id", name="uq_student_course_workspace"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_payload: Mapped[str] = mapped_column(Text)


class CourseMaterial(Base):
    __tablename__ = "student_course_materials"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    byte_size: Mapped[int] = mapped_column(Integer)
    text_size: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CourseEmailLink(Base):
    __tablename__ = "student_course_email_links"
    __table_args__ = (
        UniqueConstraint("course_id", "source_id", name="uq_student_course_email_source"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("connector_resources.id", ondelete="CASCADE"), index=True
    )
    source_hash: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class CourseworkCommand(Base):
    __tablename__ = "student_coursework_commands"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "workspace_id", "request_id", name="uq_student_coursework_request"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
