"""Versioned communication drafts and provider-independent session references."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
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


class CommunicationDraft(Base):
    __tablename__ = "communication_drafts"
    __table_args__ = (
        CheckConstraint(
            "binding_kind IN ('COMMITMENT', 'KNOWLEDGE_EMAIL')",
            name="ck_communication_drafts_binding_kind",
        ),
        # A commitment draft always cites a real commitment; the knowledge-email
        # binding never does, and instead pins the immutable Gmail message id.
        CheckConstraint(
            "(binding_kind = 'COMMITMENT' AND commitment_id IS NOT NULL)"
            " OR (binding_kind = 'KNOWLEDGE_EMAIL' AND commitment_id IS NULL"
            " AND source_external_id IS NOT NULL)",
            name="ck_communication_drafts_binding_commitment",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    commitment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("commitments.id", ondelete="CASCADE"), nullable=True
    )
    # Tagged source binding. Existing rows stay COMMITMENT; a knowledge-email
    # draft carries the opaque SPEC-007 resource selector plus the exact Gmail
    # external message id it was authorized against.
    binding_kind: Mapped[str] = mapped_column(
        String(32), default="COMMITMENT", server_default="COMMITMENT"
    )
    source_external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    source_reference: Mapped[str | None] = mapped_column(String(512))
    action_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("actions.id", ondelete="SET NULL"), unique=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="review")
    approved_version: Mapped[int | None] = mapped_column(Integer)
    approved_payload_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommunicationDraftVersion(Base):
    __tablename__ = "communication_draft_versions"
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("communication_drafts.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    generated_provider: Mapped[str | None] = mapped_column(String(32))
    generated_model: Mapped[str | None] = mapped_column(String(128))
    to: Mapped[list[str]] = mapped_column(JSON)
    cc: Mapped[list[str]] = mapped_column(JSON, default=list)
    bcc: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    attachment_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(8))
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantSession(Base):
    __tablename__ = "assistant_sessions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(16), default="PERSONAL")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssistantTurn(Base):
    __tablename__ = "assistant_turns"
    __table_args__ = (UniqueConstraint("session_id", "task_id", name="uq_assistant_turn_task"),)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_input_reference: Mapped[str] = mapped_column(String(512))
    context_snapshot_reference: Mapped[str] = mapped_column(String(512))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    output_reference: Mapped[str] = mapped_column(String(512))
    action_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    task_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
