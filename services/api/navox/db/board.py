"""Encrypted authored Board snapshots and content-free privacy retry fences."""

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class SavedBoardState(Base):
    __tablename__ = "student_board_states"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_student_board_state_owner"),
        CheckConstraint("revision >= 0", name="ck_student_board_state_revision"),
        CheckConstraint("generation >= 0", name="ck_student_board_generation"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)


class SavedBoard(Base):
    __tablename__ = "student_saved_boards"
    __table_args__ = (
        UniqueConstraint("id", "user_id", "workspace_id", name="uq_student_saved_board_scope"),
        CheckConstraint("revision >= 1", name="ck_student_saved_board_revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_courses.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SavedBoardCommand(Base):
    __tablename__ = "student_board_commands"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", "request_id", name="uq_student_board_request"),
        CheckConstraint("status IN ('applied', 'erased')", name="ck_student_board_command_status"),
        CheckConstraint(
            "operation IN ('create', 'update', 'delete')", name="ck_student_board_command_operation"
        ),
        CheckConstraint("applied_revision >= 1", name="ck_student_board_command_revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(16))
    payload_hash: Mapped[str] = mapped_column(String(64))
    # These are opaque retry fences, intentionally retained after content erasure.
    board_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    course_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    applied_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="applied")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
