"""Persistent, versioned AI configuration and content-free runtime evidence."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class AIRegistryRevision(Base):
    __tablename__ = "ai_registry_revisions"

    revision: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    snapshot: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIRegistryState(Base):
    __tablename__ = "ai_registry_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(Integer)


class AIProvider(Base):
    __tablename__ = "ai_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class AIModel(Base):
    __tablename__ = "ai_models"

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    provider: Mapped[str] = mapped_column(ForeignKey("ai_providers.id"))
    definition: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class AIModelCapability(Base):
    __tablename__ = "ai_model_capabilities"

    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_models.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(64), primary_key=True)


class AIProfile(Base):
    __tablename__ = "ai_profiles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    definition: Mapped[str] = mapped_column(Text)
    weights: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)


class AIProfileAssignment(Base):
    __tablename__ = "ai_profile_assignments"

    profile: Mapped[str] = mapped_column(
        ForeignKey("ai_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_models.id", ondelete="CASCADE"), primary_key=True
    )
    rollout_percent: Mapped[int] = mapped_column(Integer, default=0)
    shadow_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class AIPrompt(Base):
    __tablename__ = "ai_prompts"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    definition: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))


class AISchema(Base):
    __tablename__ = "ai_schemas"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    definition: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))


class AIRoutingPolicy(Base):
    __tablename__ = "ai_routing_policies"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIProviderHealth(Base):
    __tablename__ = "ai_provider_health"

    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_models.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), default="HEALTHY")
    failures: Mapped[int] = mapped_column(Integer, default=0)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    probe_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AIEvaluationRun(Base):
    __tablename__ = "ai_evaluation_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_models.id", ondelete="CASCADE"), index=True
    )
    model_digest: Mapped[str] = mapped_column(String(64))
    registry_revision: Mapped[int] = mapped_column(ForeignKey("ai_registry_revisions.revision"))
    task_type: Mapped[str] = mapped_column(String(64))
    prompt: Mapped[str] = mapped_column(String(180))
    schema: Mapped[str] = mapped_column(String(180))
    profile: Mapped[str] = mapped_column(
        ForeignKey("ai_profiles.id", ondelete="CASCADE"), index=True
    )
    evidence: Mapped[str] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AITaskRun(Base):
    __tablename__ = "ai_task_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    registry_revision: Mapped[int | None] = mapped_column(Integer)
    task_type: Mapped[str] = mapped_column(String(64))
    profile: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt: Mapped[str] = mapped_column(String(170))
    schema: Mapped[str] = mapped_column(String(170))
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict[str, int | None]] = mapped_column(JSON, default=dict)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    reserved_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
