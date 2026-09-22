from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
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
