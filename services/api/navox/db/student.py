"""Owner-entered academic records, independent of provider credentials."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class StudentProfile(Base):
    __tablename__ = "student_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_student_profile_owner"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    school_name: Mapped[str | None] = mapped_column(String(120))
    lms: Mapped[str] = mapped_column(String(32), default="none")
    display_name: Mapped[str | None] = mapped_column(String(120))
    term_start: Mapped[date | None] = mapped_column(Date)
    term_end: Mapped[date | None] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class StudentCourse(Base):
    __tablename__ = "student_courses"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40), default="")
    name: Mapped[str] = mapped_column(String(200))
    professor: Mapped[str | None] = mapped_column(String(120))
    term_start: Mapped[date] = mapped_column(Date)
    term_end: Mapped[date] = mapped_column(Date)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentClassMeeting(Base):
    __tablename__ = "student_class_meetings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "import_key", name="uq_student_meeting_import"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    course_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_courses.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    weekdays: Mapped[list[int]] = mapped_column(JSON)
    dates: Mapped[list[str] | None] = mapped_column(JSON)
    import_key: Mapped[str | None] = mapped_column(String(128))
    import_series_key: Mapped[str | None] = mapped_column(String(64), index=True)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    timezone: Mapped[str] = mapped_column(String(64))
    building: Mapped[str | None] = mapped_column(String(160))
    room: Mapped[str | None] = mapped_column(String(80))
    instructor: Mapped[str | None] = mapped_column(String(120))
    walk_minutes: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class StudentMeetingException(Base):
    __tablename__ = "student_meeting_exceptions"
    __table_args__ = (
        UniqueConstraint("meeting_id", "date", name="uq_student_meeting_exception_date"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("student_class_meetings.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    start_time: Mapped[str | None] = mapped_column(String(5))
    end_time: Mapped[str | None] = mapped_column(String(5))
    building: Mapped[str | None] = mapped_column(String(160))
    room: Mapped[str | None] = mapped_column(String(80))


class StudentFixedBlock(Base):
    __tablename__ = "student_fixed_blocks"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    weekdays: Mapped[list[int]] = mapped_column(JSON)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    timezone: Mapped[str] = mapped_column(String(64))
    term_start: Mapped[date] = mapped_column(Date)
    term_end: Mapped[date] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class StudentTimetableBreak(Base):
    __tablename__ = "student_timetable_breaks"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class StudentImportPreview(Base):
    __tablename__ = "student_import_previews"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "request_id", name="uq_student_import_request"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    preview_hash: Mapped[str] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64))
    term_start: Mapped[date] = mapped_column(Date)
    term_end: Mapped[date] = mapped_column(Date)
    candidates: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[UUID | None] = mapped_column(Uuid)
    confirm_hash: Mapped[str | None] = mapped_column(String(64))
    course_ids: Mapped[list[str] | None] = mapped_column(JSON)
