"""SPEC-007 M1 knowledge contracts. None of these confer authorization.

Source systems stay authoritative: a stored representation is derived data, and
the values in it can never add permission, routing, tool or system authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from navox.ai.foundation.contracts import Sensitivity
from navox.connectors.contracts import CapabilityDefinition


class ResourceType(StrEnum):
    """Canonical knowledge resource types from the authoritative extract."""

    EMAIL = "EMAIL"
    EMAIL_THREAD = "EMAIL_THREAD"
    CALENDAR_EVENT = "CALENDAR_EVENT"
    DOCUMENT = "DOCUMENT"
    FILE = "FILE"
    CANVAS_ASSIGNMENT = "CANVAS_ASSIGNMENT"
    CANVAS_ANNOUNCEMENT = "CANVAS_ANNOUNCEMENT"
    MEETING = "MEETING"
    COMMITMENT = "COMMITMENT"
    SUBSCRIPTION = "SUBSCRIPTION"
    NEWS_STORY = "NEWS_STORY"
    TASK = "TASK"
    OTHER = "OTHER"


class PrincipalType(StrEnum):
    USER = "USER"
    WORKSPACE = "WORKSPACE"
    GROUP = "GROUP"
    PUBLIC = "PUBLIC"


class Permission(StrEnum):
    """Independent capabilities: only VIEW permits viewing a resource."""

    VIEW = "VIEW"
    COMMENT = "COMMENT"
    EDIT = "EDIT"
    OWNER = "OWNER"


# SPEC-002 commitments, SPEC-004 subscriptions and SPEC-006 News stay
# authoritative in their own domains. M1 has no live adapter for them, so their
# derived representations must never become retrieval-eligible.
STRUCTURED_RESOURCE_TYPES: frozenset[ResourceType] = frozenset(
    {ResourceType.COMMITMENT, ResourceType.SUBSCRIPTION, ResourceType.NEWS_STORY}
)


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
        allow_inf_nan=False,
    )


def aware_utc(value: datetime) -> datetime:
    """Domain timestamps must be unambiguous; naive external times are invalid."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone is required")
    return value.astimezone(UTC)


def stored_utc(value: datetime) -> datetime:
    """Normalize a persisted value. SQLite strips timezone information on write."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_source_read_capability(value: str) -> str:
    """Validate a capability name without granting it or any read authority."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("A source read capability is required")
    try:
        CapabilityDefinition(name=value, description="knowledge source read capability")
    except ValidationError as error:
        raise ValueError("Invalid source read capability") from error
    return value


def principal_matches(
    principal_type: PrincipalType | str,
    principal_id: UUID | None,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> bool:
    """Match one grant principal against the current caller scope.

    GROUP is fail-closed because NavoX has no group membership authority yet, and
    PUBLIC only ever means "anyone inside this authenticated workspace".
    This is grant matching only: it answers "does this principal name the
    caller", never "is the caller allowed to be here". Membership, resource
    scope and the grant's own workspace binding are separate checks that callers
    must make.
    """
    try:
        kind = PrincipalType(principal_type)
    except ValueError:
        return False
    if kind is PrincipalType.PUBLIC:
        return principal_id is None
    if principal_id is None:
        return False
    if kind is PrincipalType.USER:
        return principal_id == user_id
    if kind is PrincipalType.WORKSPACE:
        return principal_id == workspace_id
    return False


def permission_interval_active(
    *,
    valid_from: datetime | None,
    valid_until: datetime | None,
    revoked_at: datetime | None,
    now: datetime,
) -> bool:
    """Validity is ``[valid_from, valid_until)``; revoked or absent grants deny.

    A null ``valid_from`` is unbounded in the past, per the approved M1 contract.
    """
    moment = aware_utc(now)
    if revoked_at is not None:
        return False
    if valid_from is not None and stored_utc(valid_from) > moment:
        return False
    if valid_until is None:
        return True
    return moment < stored_utc(valid_until)


class KnowledgeResourceInput(Contract):
    """Canonical knowledge resource fields with their canonical source binding."""

    workspace_id: UUID
    owner_user_id: UUID
    source_type: ResourceType
    source_connection_id: UUID
    external_resource_id: str = Field(min_length=1, max_length=512)
    source_resource_id: UUID | None = None
    source_read_capability: str = Field(max_length=160)
    title: str | None = Field(default=None, max_length=500)
    normalized_text: str | None = None
    canonical_url: str | None = Field(default=None, max_length=2048)
    sensitivity: Sensitivity
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None
    indexed_at: datetime | None = None
    fresh_until: datetime | None = None
    source_version: str | None = Field(default=None, max_length=256)
    parser_version: str | None = Field(default=None, max_length=64)
    embedding_version: str | None = Field(default=None, max_length=64)
    entity_extraction_version: str | None = Field(default=None, max_length=64)
    ranking_version: str | None = Field(default=None, max_length=64)
    metadata: dict[str, object] = Field(default_factory=dict)
    deleted_at: datetime | None = None

    @field_validator("external_resource_id")
    @classmethod
    def validate_external_resource_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A source identity is required")
        return value

    @field_validator("source_read_capability")
    @classmethod
    def validate_capability(cls, value: str) -> str:
        return validate_source_read_capability(value)

    @field_validator(
        "source_created_at",
        "source_updated_at",
        "indexed_at",
        "fresh_until",
        "deleted_at",
    )
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else aware_utc(value)

    @property
    def identity(self) -> tuple[UUID, UUID, str]:
        """Exactly ``(workspace_id, source_connection_id, external_resource_id)``."""
        return (self.workspace_id, self.source_connection_id, self.external_resource_id)


class KnowledgeChunkInput(Contract):
    """A source-aware slice of one resource. Chunks never carry their own grant."""

    workspace_id: UUID
    resource_id: UUID
    chunk_index: int = Field(ge=0)
    section_title: str | None = Field(default=None, max_length=500)
    page_number: int | None = Field(default=None, ge=1)
    text_content: str | None = None
    token_count: int = Field(default=0, ge=0)
    embedding_version: str | None = Field(default=None, max_length=64)


class PermissionGrant(Contract):
    """One persisted permission row, validated as a domain contract.

    ``valid_from`` is optional: a null start is unbounded in the past and a null
    ``valid_until`` is unbounded in the future. The interval stays half-open.
    """

    resource_id: UUID
    workspace_id: UUID
    principal_type: PrincipalType
    principal_id: UUID | None = None
    permission: Permission
    inherited: bool = False
    source_permission_id: str | None = Field(default=None, max_length=512)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    revoked_at: datetime | None = None

    @field_validator("valid_from", "valid_until", "revoked_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else aware_utc(value)

    @model_validator(mode="after")
    def validate_grant(self) -> PermissionGrant:
        if self.principal_type is PrincipalType.PUBLIC:
            if self.principal_id is not None:
                raise ValueError("A public grant has no principal")
        elif self.principal_id is None:
            raise ValueError("A principal is required for this grant")
        if (
            self.principal_type is PrincipalType.WORKSPACE
            and self.principal_id != self.workspace_id
        ):
            raise ValueError("A workspace grant must name its own workspace")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("A grant interval must be non-empty")
        return self

    def permits_view(self, *, workspace_id: UUID, user_id: UUID, now: datetime) -> bool:
        """Whether this grant names a VIEW caller inside ``workspace_id``.

        This is grant matching, not complete authorization: callers must still
        establish current membership, resource scope and source authority.
        """
        return (
            self.permission is Permission.VIEW
            and self.workspace_id == workspace_id
            and principal_matches(
                self.principal_type,
                self.principal_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            and permission_interval_active(
                valid_from=self.valid_from,
                valid_until=self.valid_until,
                revoked_at=self.revoked_at,
                now=now,
            )
        )
