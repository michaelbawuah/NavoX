"""Opaque media connection epochs and cleanup records that survive owner erasure."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class VoxMediaConnection(Base):
    __tablename__ = "vox_media_connections"
    __table_args__ = (
        UniqueConstraint("owner_scope_id", "request_id", name="uq_vox_media_request"),
        CheckConstraint("generation >= 0", name="ck_vox_media_generation"),
        CheckConstraint(
            "status IN ('preparing', 'confirmed', 'revoked')", name="ck_vox_media_status"
        ),
        CheckConstraint("expires_at >= created_at", name="ck_vox_media_expiry"),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND reason IS NOT NULL) OR "
            "(status != 'revoked' AND revoked_at IS NULL AND reason IS NULL)",
            name="ck_vox_media_revoked",
        ),
        CheckConstraint(
            "status != 'confirmed' OR confirmed_at IS NOT NULL", name="ck_vox_media_confirmed"
        ),
        Index(
            "uq_vox_media_current_owner",
            "owner_scope_id",
            unique=True,
            postgresql_where=text("status IN ('preparing', 'confirmed')"),
            sqlite_where=text("status IN ('preparing', 'confirmed')"),
        ),
    )

    # This server UUID is the connection epoch; callers cannot choose or reuse it.
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), index=True
    )
    call_id: Mapped[UUID] = mapped_column(
        ForeignKey("vox_calls.id", ondelete="CASCADE"), index=True
    )
    owner_scope_id: Mapped[UUID] = mapped_column(
        ForeignKey("vox_states.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    room_name: Mapped[str] = mapped_column(String(64), unique=True)
    client_identity: Mapped[str] = mapped_column(String(64), unique=True)
    agent_identity: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(24), nullable=True)


class VoxMediaCleanup(Base):
    __tablename__ = "vox_media_cleanup"
    __table_args__ = (
        CheckConstraint(
            "status IN ('armed', 'pending', 'claimed', 'done')", name="ck_vox_cleanup_status"
        ),
        CheckConstraint("attempts >= 0", name="ck_vox_cleanup_attempts"),
        CheckConstraint(
            "(status = 'claimed' AND claim_id IS NOT NULL AND claim_expires_at IS NOT NULL) OR "
            "(status != 'claimed' AND claim_id IS NULL AND claim_expires_at IS NULL)",
            name="ck_vox_cleanup_claim",
        ),
        CheckConstraint(
            "(status = 'armed' AND requested_at IS NULL AND next_attempt_at IS NULL) OR "
            "(status IN ('pending', 'claimed') AND requested_at IS NOT NULL "
            "AND next_attempt_at IS NOT NULL) OR "
            "(status = 'done' AND requested_at IS NOT NULL AND next_attempt_at IS NULL)",
            name="ck_vox_cleanup_schedule",
        ),
        CheckConstraint(
            "(status = 'done' AND completed_at IS NOT NULL) OR "
            "(status != 'done' AND completed_at IS NULL)",
            name="ck_vox_cleanup_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # Deliberately no FKs: logout, reset, session or account deletion cannot erase
    # the only durable instruction to retire a resource created by a late worker.
    connection_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    owner_scope_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    room_name: Mapped[str] = mapped_column(String(64), unique=True)
    client_identity: Mapped[str] = mapped_column(String(64), unique=True)
    agent_identity: Mapped[str] = mapped_column(String(64), unique=True)
    connection_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="armed", index=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    claim_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
