"""Explicit owner-saved memory; never populated from source or model output."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class OwnerMemory(Base):
    __tablename__ = "owner_memories"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "preference_key", name="uq_owner_memory_preference"
        ),
        CheckConstraint(
            "kind IN ('reply_length', 'tone', 'signoff', 'study_hours', 'project_fact')",
            name="ck_owner_memory_kind",
        ),
        CheckConstraint(
            "(kind = 'project_fact' AND preference_key IS NULL) OR "
            "(kind <> 'project_fact' AND preference_key IS NOT NULL AND preference_key = kind)",
            name="ck_owner_memory_preference_key",
        ),
        CheckConstraint("length(label) BETWEEN 1 AND 80", name="ck_owner_memory_label"),
        CheckConstraint("length(content) BETWEEN 1 AND 500", name="ck_owner_memory_content"),
        CheckConstraint("provenance = 'user_saved'", name="ck_owner_memory_provenance"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    # NULL permits multiple project facts; every other kind has a single owner slot.
    preference_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    provenance: Mapped[str] = mapped_column(
        String(32), default="user_saved", server_default="user_saved"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
