"""Encrypted owner goals, explicit daily reports and content-free retry fences."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class StudentGoalState(Base):
    __tablename__ = "student_goal_states"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_student_goal_state_owner"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)


class StudentGoal(Base):
    __tablename__ = "student_goals"
    __table_args__ = (
        UniqueConstraint("id", "user_id", "workspace_id", name="uq_student_goal_scope"),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed')", name="ck_student_goal_status"
        ),
        CheckConstraint("revision >= 1", name="ck_student_goal_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    definition_revision: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentGoalCheckIn(Base):
    __tablename__ = "student_goal_checkins"
    __table_args__ = (
        ForeignKeyConstraint(
            ["goal_id", "user_id", "workspace_id"],
            ["student_goals.id", "student_goals.user_id", "student_goals.workspace_id"],
            ondelete="CASCADE",
            name="fk_student_goal_checkin_scope",
        ),
        UniqueConstraint("goal_id", "on_date", name="uq_student_goal_checkin_day"),
        CheckConstraint("revision >= 1", name="ck_student_goal_checkin_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    goal_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    on_date: Mapped[date] = mapped_column(Date, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StudentGoalCommand(Base):
    __tablename__ = "student_goal_commands"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", "request_id", name="uq_student_goal_request"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(24))
    payload_hash: Mapped[str] = mapped_column(String(64))
    # No FK: erasure deliberately retains these opaque retry fences.
    goal_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid)
    applied_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="applied")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
