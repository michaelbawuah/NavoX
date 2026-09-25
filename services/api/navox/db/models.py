from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from navox.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    agent_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256))
    workspace_type: Mapped[str] = mapped_column(String(32), default="personal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_type: Mapped[str] = mapped_column(String(32), default="web", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectionCredential(Base):
    __tablename__ = "connection_credentials"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    key_version: Mapped[str] = mapped_column(String(64), default="local-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "external_account_id",
            name="uq_connections_workspace_provider_external_account",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    external_account_id: Mapped[str] = mapped_column(String(256))
    external_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    granted_scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    credential_reference: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("connection_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OAuthAuthorizationAttempt(Base):
    __tablename__ = "oauth_authorization_attempts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(64), default="identity")
    connection_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), nullable=True, index=True
    )
    requested_scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderEventSubscription(Base):
    """A server-created provider notification channel bound to one connection."""

    __tablename__ = "provider_event_subscriptions"
    __table_args__ = (
        UniqueConstraint("channel_id", name="uq_provider_event_subscriptions_channel_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    channel_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    channel_token_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Provisional registration deadlines are not evidence of provider expiration.
    expiration_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IncomingEvent(Base):
    """Canonical, content-minimized provider notification for later NavoX processing."""

    __tablename__ = "incoming_events"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "provider",
            "external_event_id",
            name="uq_incoming_events_connection_provider_external_event",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(128))
    external_event_id: Mapped[str] = mapped_column(String(512))
    external_resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, str]] = mapped_column("metadata", JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    intelligence_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)


class Person(Base):
    """Canonical workspace-scoped person resolved from provider identities."""

    __tablename__ = "people"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    canonical_name: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PersonIdentity(Base):
    """Provider-neutral identity mapped deterministically to one canonical person."""

    __tablename__ = "person_identities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "identity_type",
            "identity_value",
            name="uq_person_identities_workspace_type_value",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("people.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    identity_type: Mapped[str] = mapped_column(String(64), index=True)
    identity_value: Mapped[str] = mapped_column(String(512), index=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("1.000"))
    # Existing rows cannot be assigned to a connection from their provider alone.
    source_attributed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PersonIdentitySource(Base):
    """A connection that supplied an identity, without retaining source content."""

    __tablename__ = "person_identity_sources"

    identity_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("person_identities.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class OperationalObservation(Base):
    """Evidence-backed operational fact proposed by the intelligence layer."""

    __tablename__ = "operational_observations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    observation_type: Mapped[str] = mapped_column(String(64), index=True)
    subject_person_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("people.id", ondelete="SET NULL"), nullable=True, index=True
    )
    object_person_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("people.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    extractor_version: Mapped[str] = mapped_column(String(64))
    model_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ObservationEvidence(Base):
    """Bounded provenance linking an observation to one authorized provider resource."""

    __tablename__ = "observation_evidence"
    __table_args__ = (
        UniqueConstraint(
            "observation_id",
            "connection_id",
            "provider",
            "source_type",
            "external_resource_id",
            "source_hash",
            name="uq_observation_evidence_observation_source_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    observation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("operational_observations.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), index=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    external_resource_id: Mapped[str] = mapped_column(String(512), index=True)
    evidence_locator: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntelligenceFeedback(Base):
    """Bounded user feedback about intelligence outputs; never an authority grant."""

    __tablename__ = "intelligence_feedback"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", "request_id", name="uq_feedback_request"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    feedback_type: Mapped[str] = mapped_column(String(64), index=True)
    feedback_metadata: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Objective(Base):
    __tablename__ = "objectives"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    priority: Mapped[int] = mapped_column(default=3)
    target_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default="user")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    waiting_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Commitment(Base):
    __tablename__ = "commitments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_key", name="uq_commitments_workspace_dedupe_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    objective_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("objectives.id", ondelete="SET NULL"), nullable=True, index=True
    )
    commitment_type: Mapped[str] = mapped_column("type", String(32))
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    priority: Mapped[int] = mapped_column(default=3)
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(default=1.0)
    created_by: Mapped[str] = mapped_column(String(32), default="ai")
    dedupe_key: Mapped[str] = mapped_column(String(64))
    intelligence_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    attention_score: Mapped[int] = mapped_column(default=0)
    attention_factors: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    attention_band: Mapped[str] = mapped_column(String(32), default="SUPPRESS")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    waiting_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CommitmentSource(Base):
    __tablename__ = "commitment_sources"
    __table_args__ = (
        UniqueConstraint(
            "commitment_id",
            "incoming_event_id",
            name="uq_commitment_sources_commitment_incoming_event",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    commitment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    incoming_event_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("incoming_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(64))
    external_resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_metadata: Mapped[dict[str, str]] = mapped_column("metadata", JSON, default=dict)


class GmailRecheck(Base):
    """A resumable cleanup preview; no email text or model response is retained."""

    __tablename__ = "gmail_rechecks"

    commitment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    preview_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommitmentRelation(Base):
    __tablename__ = "commitment_relations"
    __table_args__ = (
        UniqueConstraint(
            "from_commitment_id",
            "to_commitment_id",
            "relation_type",
            name="uq_commitment_relations_from_to_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    from_commitment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="CASCADE"), index=True
    )
    to_commitment_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_id", name="uq_plans_workspace_request_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    objective_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("objectives.id", ondelete="SET NULL"), nullable=True, index=True
    )
    commitment_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    goal: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    planner_version: Mapped[str] = mapped_column(String(64), default="deterministic-v1")
    context_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    context_hash: Mapped[str] = mapped_column(String(64))
    source_attributed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    max_steps: Mapped[int] = mapped_column(default=8)
    replan_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlanSource(Base):
    __tablename__ = "plan_sources"

    plan_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class PlanStep(Base):
    __tablename__ = "plan_steps"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence_number", name="uq_plan_steps_plan_sequence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("plans.id", ondelete="CASCADE"), index=True
    )
    sequence_number: Mapped[int] = mapped_column()
    action_type: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    risk_level: Mapped[str] = mapped_column(String(8))
    input_payload: Mapped[dict[str, object]] = mapped_column("input", JSON, default=dict)
    output_payload: Mapped[dict[str, object]] = mapped_column("output", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("plan_step_id", name="uq_actions_plan_step_id"),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_actions_workspace_idempotency",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    plan_step_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("plan_steps.id", ondelete="CASCADE"), index=True
    )
    commitment_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    action_type: Mapped[str] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(8))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    result: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    policy_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("action_id", "version", name="uq_approvals_action_version"),
        UniqueConstraint(
            "workspace_id",
            "decision_request_id",
            name="uq_approvals_workspace_decision_request",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    action_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("actions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)
    action_payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decision_request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowRef(Base):
    __tablename__ = "workflow_refs"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "workflow_type",
            name="uq_workflow_refs_entity_workflow_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    workflow_type: Mapped[str] = mapped_column(String(64))
    temporal_workflow_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    temporal_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_metadata: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ProactivePreference(Base):
    __tablename__ = "proactive_preferences"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_proactive_preferences_workspace_user",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), default="22:00")
    quiet_hours_end: Mapped[str] = mapped_column(String(5), default="07:00")
    daily_briefing_hour: Mapped[int] = mapped_column(default=8)
    notify_threshold: Mapped[int] = mapped_column(default=85)
    briefing_threshold: Mapped[int] = mapped_column(default=65)
    dashboard_threshold: Mapped[int] = mapped_column(default=40)
    max_interruptions_per_day: Mapped[int] = mapped_column(default=3)
    cooldown_minutes: Mapped[int] = mapped_column(default=240)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProactiveSignal(Base):
    __tablename__ = "proactive_signals"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="uq_proactive_signals_workspace_fingerprint",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    commitment_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("commitments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    tier: Mapped[str] = mapped_column(String(32), default="dashboard", index=True)
    attention_score: Mapped[int] = mapped_column(default=0, index=True)
    score_components: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    what_happening: Mapped[str] = mapped_column(Text)
    why_matters: Mapped[str] = mapped_column(Text)
    suggested_capability: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_surfaced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    surface_count: Mapped[int] = mapped_column(default=0)
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BriefingSnapshot(Base):
    __tablename__ = "briefing_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "request_id",
            name="uq_briefing_snapshots_workspace_request",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    local_date: Mapped[str] = mapped_column(String(10), index=True)
    timezone: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    signal_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    item_count: Mapped[int] = mapped_column(default=0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class IntelligenceCursor(Base):
    __tablename__ = "intelligence_cursors"
    __table_args__ = (UniqueConstraint("connection_id", "source", name="uq_intelligence_cursor"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IntelligenceSourceReceipt(Base):
    """Durable per-revision progress, without storing raw source/model content."""

    __tablename__ = "intelligence_source_receipts"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "source",
            "external_id",
            "source_hash",
            "extractor_version",
            name="uq_intelligence_source_receipt",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(512))
    source_hash: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    source_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    commitment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GmailSyncPlan(Base):
    """A resumable read plan containing identifiers and chronology, never mail bodies."""

    __tablename__ = "gmail_sync_plans"
    __table_args__ = (UniqueConstraint("connection_id", name="uq_gmail_sync_plan_connection"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="CASCADE"), index=True
    )
    initial_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[str] = mapped_column(String(16), default="list")
    page_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages: Mapped[int] = mapped_column(default=0)
    reset: Mapped[bool] = mapped_column(Boolean, default=False)
    entries: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(default=0)
    commitment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    skipped: Mapped[int] = mapped_column(default=0)
    rejected: Mapped[int] = mapped_column(default=0)
    next_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IntelligencePreference(Base):
    __tablename__ = "intelligence_preferences"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    weights: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceDisplayPreference(Base):
    __tablename__ = "workspace_display_preferences"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    clock_format: Mapped[str] = mapped_column(String(8), default="12h")
    temperature_unit: Mapped[str] = mapped_column(String(16), default="celsius")
    weather_visible: Mapped[bool] = mapped_column(Boolean, default=False)
    weather_city: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ConnectorDefinition(Base):
    """Versioned connector manifest registered with NavoX core."""

    __tablename__ = "connector_definitions"
    __table_args__ = (
        UniqueConstraint(
            "connector_key",
            "version",
            name="uq_connector_definitions_key_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connector_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(120))
    connector_class: Mapped[str] = mapped_column(String(32), index=True)
    trust_level: Mapped[str] = mapped_column(String(32), index=True)
    manifest: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorConnection(Base):
    """Workspace-scoped universal connector connection."""

    __tablename__ = "connector_connections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "connector_definition_id",
            "external_account_id",
            name="uq_connector_connections_workspace_definition_account",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connector_definition_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_definitions.id", ondelete="RESTRICT"), index=True
    )
    legacy_connection_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), index=True)
    external_account_id: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CONNECTED", index=True)
    health_state: Mapped[str] = mapped_column(String(32), default="CONNECTED", index=True)
    authorized_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    config: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    credential_reference: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("connection_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    sync_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_generation: Mapped[int] = mapped_column(default=0, server_default="0")
    sync_run_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sync_lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    sync_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_healthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorResource(Base):
    """Canonical provider resource stored under one connector connection."""

    __tablename__ = "connector_resources"
    __table_args__ = (
        UniqueConstraint(
            "connector_connection_id",
            "resource_type",
            "external_id",
            name="uq_connector_resources_connection_type_external",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connector_connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_connections.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(128), index=True)
    external_id: Mapped[str] = mapped_column(String(512), index=True)
    external_parent_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    canonical: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorSubscription(Base):
    __tablename__ = "connector_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "connector_connection_id",
            "subscription_key",
            name="uq_connector_subscriptions_connection_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connector_connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_connections.id", ondelete="CASCADE"), index=True
    )
    subscription_key: Mapped[str] = mapped_column(String(256))
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CONNECTED", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation: Mapped[int] = mapped_column(default=0, server_default="0")
    lease_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    authorization_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_metadata: Mapped[dict[str, object]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorEventReceipt(Base):
    """Authenticated event locator and durable targeted-sync dispatch outbox."""

    __tablename__ = "connector_event_receipts"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "external_event_id", name="uq_connector_event_delivery"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    subscription_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_subscriptions.id", ondelete="CASCADE"), index=True
    )
    connector_connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_connections.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(160))
    external_event_id: Mapped[str] = mapped_column(String(512))
    external_resource_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConnectorSyncRun(Base):
    __tablename__ = "connector_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "connector_connection_id",
            "request_id",
            name="uq_connector_sync_runs_connection_request",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connector_connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_connections.id", ondelete="CASCADE"), index=True
    )
    consumer_version: Mapped[str] = mapped_column(
        String(128), default="canonical-consumer.v1", server_default="canonical-consumer.v1"
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    cursor_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_count: Mapped[int] = mapped_column(default=0)
    processed_count: Mapped[int] = mapped_column(default=0)
    duplicate_count: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation: Mapped[int] = mapped_column(default=0, server_default="0")
    authorization_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages_completed: Mapped[int] = mapped_column(default=0, server_default="0")
    fetch_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
    stale_count: Mapped[int] = mapped_column(default=0, server_default="0")
    result_ids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    cursor_hashes: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConnectorSyncReceipt(Base):
    """A revision and downstream acceptance commit together; no raw body here."""

    __tablename__ = "connector_sync_receipts"
    __table_args__ = (
        UniqueConstraint(
            "sync_run_id", "resource_id", "content_hash", name="uq_connector_sync_receipt"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    sync_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_sync_runs.id", ondelete="CASCADE"), index=True
    )
    consumer_version: Mapped[str] = mapped_column(
        String(128), default="canonical-consumer.v1", server_default="canonical-consumer.v1"
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    resource_id: Mapped[UUID] = mapped_column(Uuid)
    content_hash: Mapped[str] = mapped_column(String(64))
    result_ids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    outcome: Mapped[str] = mapped_column(String(16))
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConnectorImportSnapshot(Base):
    """Encrypted source data, stored separately from runtime history and credentials."""

    __tablename__ = "connector_import_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", "digest", name="uq_import_snapshot_owner_digest"
        ),
    )
    connection_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("connector_connections.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    digest: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(8))
    record_count: Mapped[int] = mapped_column()
    encrypted_payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
