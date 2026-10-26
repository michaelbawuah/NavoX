"""Encrypted conversation memory; plaintext source content never belongs in metadata."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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


class MemoryPrivacy(Base):
    __tablename__ = "memory_privacy"
    __table_args__ = (
        CheckConstraint("generation >= 0", name="ck_memory_privacy_generation"),
        CheckConstraint(
            "retention_days IS NULL OR retention_days IN (30, 90)", name="ck_memory_retention"
        ),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    history_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    off_the_record: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disclosure_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryConversation(Base):
    __tablename__ = "memory_conversations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "source_kind",
            "source_id",
            name="uq_memory_conversation_source",
        ),
        CheckConstraint("source_kind IN ('vox', 'chat')", name="ck_memory_conversation_kind"),
        CheckConstraint(
            "message_count >= 0 AND message_count <= 2000", name="ck_memory_message_count"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    source_kind: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[UUID] = mapped_column(Uuid)
    generation: Mapped[int] = mapped_column(Integer)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    encrypted_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryFact(Base):
    __tablename__ = "memory_facts"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    source_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("memory_conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(16))
    encrypted_content: Mapped[str] = mapped_column(Text)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryThread(Base):
    __tablename__ = "memory_threads"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    source_conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("memory_conversations.id", ondelete="CASCADE"), index=True
    )
    encrypted_content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
