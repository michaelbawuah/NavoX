"""Content-free server usage, quota reservations, and persistent operator limits."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class VoxUsagePolicy(Base):
    __tablename__ = "vox_usage_policy"
    __table_args__ = (
        CheckConstraint("id = 1 AND revision >= 1", name="ck_vox_usage_policy_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, object]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoxUsageStream(Base):
    __tablename__ = "vox_usage_streams"
    __table_args__ = (
        CheckConstraint("kind IN ('transport','provider')", name="ck_vox_usage_stream_kind"),
        CheckConstraint(
            "status IN ('open','complete','uncertain')", name="ck_vox_usage_stream_status"
        ),
        CheckConstraint(
            "sequence >= 0 AND reserved_tokens >= 0", name="ck_vox_usage_stream_nonnegative"
        ),
        CheckConstraint(
            "reconciled_cost_usd IS NULL OR reconciled_cost_usd >= 0",
            name="ck_vox_usage_reconciled_cost",
        ),
        Index("ix_vox_usage_stream_owner_created", "user_id", "created_at"),
        Index("ix_vox_usage_stream_call", "call_id"),
        Index(
            "uq_vox_usage_call_transport",
            "call_id",
            unique=True,
            postgresql_where=text("kind = 'transport'"),
            sqlite_where=text("kind = 'transport'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    # No FK to the 24-hour reservation: quota cannot disappear with call cleanup.
    call_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="open")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    cumulative: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    pricing: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reconciled_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    reconciliation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


class VoxUsageEvent(Base):
    __tablename__ = "vox_usage_events"
    __table_args__ = (
        UniqueConstraint("stream_id", "event_id", name="uq_vox_usage_event"),
        UniqueConstraint("stream_id", "sequence", name="uq_vox_usage_sequence"),
        CheckConstraint("sequence >= 1", name="ck_vox_usage_event_sequence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    stream_id: Mapped[UUID] = mapped_column(
        ForeignKey("vox_usage_streams.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))


class VoxUsageDelta(Base):
    __tablename__ = "vox_usage_deltas"
    __table_args__ = (
        CheckConstraint(
            "speaking_ms >= 0 AND idle_ms >= 0 AND connected_ms >= 0 AND tokens >= 0",
            name="ck_vox_usage_delta_nonnegative",
        ),
        CheckConstraint("speaking_ms + idle_ms = connected_ms", name="ck_vox_usage_delta_clock"),
        CheckConstraint(
            "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",
            name="ck_vox_usage_delta_estimate",
        ),
        Index("ix_vox_usage_delta_owner_time", "user_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    stream_id: Mapped[UUID] = mapped_column(
        ForeignKey("vox_usage_streams.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    speaking_ms: Mapped[int] = mapped_column(BigInteger)
    idle_ms: Mapped[int] = mapped_column(BigInteger)
    connected_ms: Mapped[int] = mapped_column(BigInteger)
    tokens: Mapped[int] = mapped_column(BigInteger)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
