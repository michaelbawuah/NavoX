from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base
from navox.db.lms_feeds import LmsFeedItem


class StudentTask(Base):
    __tablename__ = "student_tasks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "source_identity", name="uq_student_task_source"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("student_courses.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_minutes: Mapped[int] = mapped_column(Integer)
    remaining_minutes: Mapped[int] = mapped_column(Integer)
    actual_minutes: Mapped[int] = mapped_column(Integer, default=0)
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    course_weight: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(LmsFeedItem.id, ondelete="CASCADE")
    )
    source_identity: Mapped[str | None] = mapped_column(String(64))
    source_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentPlanningState(Base):
    __tablename__ = "student_planning_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_student_planning_owner"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    preferences_revision: Mapped[int] = mapped_column(Integer, default=0)
    preferences: Mapped[dict[str, object]] = mapped_column(JSON)
    accepted_source_hash: Mapped[str | None] = mapped_column(String(64))
    needs_review: Mapped[bool] = mapped_column(default=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentPlanningPreview(Base):
    __tablename__ = "student_planning_previews"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    preview_hash: Mapped[str] = mapped_column(String(64))
    base_revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentStudyBlock(Base):
    __tablename__ = "student_study_blocks"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_tasks.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    title: Mapped[str] = mapped_column(String(200))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int] = mapped_column(Integer)
    focus_window: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    actual_minutes: Mapped[int] = mapped_column(Integer, default=0)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudentPlanningCommand(Base):
    __tablename__ = "student_planning_commands"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "request_id", name="uq_student_planning_request"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    undo_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
