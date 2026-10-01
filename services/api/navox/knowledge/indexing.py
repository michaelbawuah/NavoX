"""Project already-retained canonical connector content into the knowledge index.

Derived text is built only from canonical rows the connector runtime chose to
retain. ``content_persisted=False`` rows are recorded honestly as ``NO_CONTENT``
and never reconstructed. Source authority stays with SPEC-003: the exact read
capability is mapped by shipped/approved configuration, never by content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.context import context_capabilities
from navox.ai.foundation.contracts import Sensitivity
from navox.connectors.contracts import CanonicalResource
from navox.connectors.normalization import canonical_resource_to_source_document
from navox.core.settings import Settings
from navox.db.knowledge import (
    KnowledgeChunk,
    KnowledgeResource,
    KnowledgeResourceIndex,
    KnowledgeResourcePermission,
)
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
    WorkspaceMembership,
)
from navox.knowledge.contracts import (
    Permission,
    PrincipalType,
    ResourceType,
    aware_utc,
    stored_utc,
)
from navox.knowledge.freshness import fresh_until
from navox.knowledge.permissions import (
    ACTIVE_CONNECTION_STATUSES,
    effective_read_capabilities,
    legacy_anchor_active,
)

PROJECTION_VERSION = "knowledge-index.v1"
CHUNK_CHAR_LIMIT = 1_200
MAX_CHUNKS = 200
MAX_TEXT_CHARS = 200_000
MAX_INDEX_BATCH = 200

INDEXED = "INDEXED"
NO_CONTENT = "NO_CONTENT"
STALE = "STALE"

_TYPE_BY_PROVIDER_RESOURCE: tuple[tuple[str, ResourceType], ...] = (
    ("communication.thread", ResourceType.EMAIL_THREAD),
    ("communication.message", ResourceType.EMAIL),
    ("calendar.event", ResourceType.CALENDAR_EVENT),
    ("academic.assignment", ResourceType.CANVAS_ASSIGNMENT),
    ("academic.announcement", ResourceType.CANVAS_ANNOUNCEMENT),
    ("meeting", ResourceType.MEETING),
    ("document", ResourceType.DOCUMENT),
    ("file", ResourceType.FILE),
    ("task", ResourceType.TASK),
)

STRUCTURED_KIND_BY_TYPE: dict[ResourceType, str] = {
    ResourceType.CALENDAR_EVENT: "calendar.event",
    ResourceType.CANVAS_ASSIGNMENT: "canvas.assignment",
    ResourceType.TASK: "task.due",
}


@dataclass(frozen=True)
class IndexOutcome:
    resource_id: UUID
    provider_resource_id: UUID
    source_type: ResourceType
    state: str
    chunks: int
    grant_created: bool
    reason: str | None = None


@dataclass(frozen=True)
class BackfillReport:
    outcomes: tuple[IndexOutcome, ...]
    examined: int
    truncated: bool
    bound: int


def knowledge_type_for(provider_resource_type: str) -> ResourceType:
    """Deterministic provider resource-type mapping; unknown types stay OTHER."""
    normalized = provider_resource_type.strip().casefold()
    for needle, kind in _TYPE_BY_PROVIDER_RESOURCE:
        if needle in normalized:
            return kind
    return ResourceType.OTHER


def trusted_read_capability(
    connector_key: str, provider_resource_type: str, settings: Settings | None = None
) -> str | None:
    """The single trusted read capability for one (connector, resource type).

    Unmapped or ambiguous mappings return ``None`` and deny indexing. Metadata
    and content never choose the capability.
    """
    granted = context_capabilities(settings).get((connector_key, provider_resource_type))
    if not granted or len(granted) != 1:
        return None
    return next(iter(granted))


def chunk_spans(text: str, *, limit: int = CHUNK_CHAR_LIMIT) -> list[str]:
    """Bounded, ordered, paragraph-aware spans with stable boundaries."""
    if limit < 1:
        raise ValueError("Chunk limit must be positive")
    spans: list[str] = []
    buffer = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            spans.append(buffer)
            buffer = ""
        while len(paragraph) > limit:
            spans.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        buffer = paragraph
    if buffer:
        spans.append(buffer)
    return [span.strip() for span in spans if span.strip()][:MAX_CHUNKS]


def _parse_moment(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return aware_utc(value) if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def structured_window(
    resource_type: ResourceType, canonical: dict[str, object]
) -> tuple[str | None, datetime | None, datetime | None]:
    """Typed date facts for structured sources; never a prose guess."""
    nested = canonical.get("source_document")
    if isinstance(nested, dict) and isinstance(nested.get("metadata"), dict):
        metadata = nested["metadata"]
        canonical = {
            **canonical,
            "starts_at": metadata.get("start_at"),
            "ends_at": metadata.get("end_at"),
            "due_at": metadata.get("due_at"),
        }
    kind = STRUCTURED_KIND_BY_TYPE.get(resource_type)
    if kind is None:
        return (None, None, None)
    if resource_type is ResourceType.CALENDAR_EVENT:
        start = _parse_moment(canonical.get("starts_at")) or _parse_moment(
            canonical.get("occurred_at")
        )
        end = _parse_moment(canonical.get("ends_at"))
        return (kind, start, end)
    due = _parse_moment(canonical.get("due_at")) or _parse_moment(canonical.get("due"))
    return (kind, due, None)


def _title_from(canonical: dict[str, object]) -> str | None:
    nested = canonical.get("source_document")
    if isinstance(nested, dict):
        subject = nested.get("subject")
        if isinstance(subject, str) and subject.strip():
            return subject.strip()[:500]
    for key in ("subject", "title", "name", "summary", "headline"):
        value = canonical.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return None


def _sensitivity_from(canonical: dict[str, object]) -> str:
    """Stored content can never lower sensitivity below the NavoX default.

    No operator configuration surface currently grants a lower bound, so a
    canonical claim of PUBLIC/INTERNAL is clamped to PERSONAL.
    """
    value = canonical.get("sensitivity")
    floor = Sensitivity.PERSONAL
    if not isinstance(value, str):
        return floor.value
    try:
        claimed = Sensitivity(value)
    except ValueError:
        return floor.value
    return (claimed if claimed.rank >= floor.rank else floor).value


async def _definition_for(
    database: AsyncSession, connection: ConnectorConnection
) -> ConnectorDefinition | None:
    definition = await database.scalar(
        select(ConnectorDefinition)
        .where(ConnectorDefinition.id == connection.connector_definition_id)
        .execution_options(populate_existing=True)
    )
    return definition


async def _existing_resource(
    database: AsyncSession, *, workspace_id: UUID, connection_id: UUID, external_id: str
) -> KnowledgeResource | None:
    existing = await database.scalar(
        select(KnowledgeResource)
        .where(
            KnowledgeResource.workspace_id == workspace_id,
            KnowledgeResource.source_connection_id == connection_id,
            KnowledgeResource.external_resource_id == external_id,
        )
        .execution_options(populate_existing=True)
    )
    return existing


async def _owner_view_grant(
    database: AsyncSession, resource: KnowledgeResource, *, owner_user_id: UUID
) -> KnowledgeResourcePermission | None:
    """Return the owner's existing VIEW grant row, if any.

    A revoked row is returned unchanged: a rebuild never resurrects authority
    that was withdrawn, and it never upgrades COMMENT/EDIT/OWNER to VIEW.
    """
    existing = await database.scalar(
        select(KnowledgeResourcePermission)
        .where(
            KnowledgeResourcePermission.resource_id == resource.id,
            KnowledgeResourcePermission.principal_type == PrincipalType.USER.value,
            KnowledgeResourcePermission.principal_id == owner_user_id,
            KnowledgeResourcePermission.permission == Permission.VIEW.value,
        )
        .order_by(KnowledgeResourcePermission.created_at, KnowledgeResourcePermission.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return existing


async def _retire_projection(
    database: AsyncSession,
    *,
    connection: ConnectorConnection,
    external_id: str,
    now: datetime,
) -> None:
    """Tombstone a projection whose canonical source is gone.

    Text, title, URL and chunks are removed so nothing is served from a deleted
    or disconnected source, and the row is never silently rebuilt later.
    """
    knowledge = await _existing_resource(
        database,
        workspace_id=connection.workspace_id,
        connection_id=connection.id,
        external_id=external_id,
    )
    if knowledge is None:
        return
    knowledge.deleted_at = now
    knowledge.normalized_text = None
    knowledge.title = None
    knowledge.canonical_url = None
    await database.execute(delete(KnowledgeChunk).where(KnowledgeChunk.resource_id == knowledge.id))
    index_row = await database.scalar(
        select(KnowledgeResourceIndex)
        .where(KnowledgeResourceIndex.resource_id == knowledge.id)
        .execution_options(populate_existing=True)
    )
    if index_row is not None:
        index_row.index_state = STALE
        index_row.chunk_count = 0
        index_row.text_length = 0
    await database.flush()


def canonical_contract(resource: ConnectorResource) -> CanonicalResource:
    """Rebuild the SPEC-003 contract for a stored canonical row.

    Identity was already established by the stored row's scope and external id;
    ``model_construct`` reuses the contract type without re-deriving the stable
    id, which the runtime computed at ingestion time.
    """
    return CanonicalResource.model_construct(
        resource_id=resource.id,
        workspace_id=resource.workspace_id,
        connector_connection_id=resource.connector_connection_id,
        provider=resource.provider,
        resource_type=resource.resource_type,
        external_id=resource.external_id,
        external_parent_id=resource.external_parent_id,
        version=resource.version,
        canonical=cast(dict[str, Any], dict(resource.canonical or {})),
        provider_metadata=cast(dict[str, Any], dict(resource.provider_metadata or {})),
        source_url=resource.source_url,
        # Stored values are normalized here: SQLite drops timezone information
        # on write, and the contract requires unambiguous instants.
        created_at=(
            stored_utc(resource.source_created_at)
            if resource.source_created_at is not None
            else None
        ),
        updated_at=(
            stored_utc(resource.source_updated_at)
            if resource.source_updated_at is not None
            else None
        ),
        retrieved_at=stored_utc(resource.retrieved_at),
    )


async def index_connector_resource(
    database: AsyncSession,
    *,
    connection: ConnectorConnection,
    resource: ConnectorResource,
    now: datetime,
    settings: Settings | None = None,
    force: bool = False,
) -> IndexOutcome:
    """Index one authorized canonical resource. Idempotent by M1 identity."""
    moment = aware_utc(now)
    if connection.workspace_id != resource.workspace_id:
        raise ValueError("Cross-workspace resource rejected")
    if connection.id != resource.connector_connection_id:
        raise ValueError("Cross-connection resource rejected")

    # Never trust a caller-supplied snapshot: reload the connection and the
    # canonical row, then require current owner membership, an unpaused owner and
    # current legacy authority before any projection work.
    fresh_connection = await database.get(
        ConnectorConnection, connection.id, populate_existing=True
    )
    if fresh_connection is None:
        raise ValueError("A stored connector connection is required to index")
    if (
        fresh_connection.workspace_id != connection.workspace_id
        or fresh_connection.user_id != connection.user_id
        or fresh_connection.connector_definition_id != connection.connector_definition_id
    ):
        raise ValueError("Connector connection authority changed")
    connection = fresh_connection

    stored = await database.get(ConnectorResource, resource.id, populate_existing=True)
    if stored is None:
        raise ValueError("A stored canonical resource is required to index")
    if (
        stored.workspace_id != connection.workspace_id
        or stored.connector_connection_id != connection.id
    ):
        raise ValueError("Cross-workspace resource rejected")
    if stored.deleted:
        await _retire_projection(
            database,
            connection=connection,
            external_id=stored.external_id,
            now=moment,
        )
        raise ValueError("Canonical source is deleted")
    membership = await database.get(
        WorkspaceMembership,
        (connection.workspace_id, connection.user_id),
        populate_existing=True,
    )
    if membership is None:
        raise ValueError("The source owner is not a current workspace member")
    owner = await database.get(User, connection.user_id, populate_existing=True)
    if owner is None or owner.agent_paused:
        raise ValueError("The source owner is paused or unavailable")
    if not await legacy_anchor_active(database, connection, connection.workspace_id):
        raise ValueError("The bound legacy connection is not active")
    resource = stored

    definition = await _definition_for(database, connection)
    if definition is None or not definition.active:
        raise ValueError("An active connector definition is required to index")
    capability = trusted_read_capability(definition.connector_key, resource.resource_type, settings)
    if capability is None:
        raise ValueError("No trusted read capability mapping for this resource type")
    if capability not in effective_read_capabilities(definition, connection):
        raise ValueError("The connection no longer grants the mapped read capability")

    source_type = knowledge_type_for(resource.resource_type)
    canonical = dict(resource.canonical or {})
    retained = canonical.get("content_persisted") is not False
    declared = canonical.get("source_type")
    if retained and isinstance(declared, str):
        mapped = knowledge_type_for(declared)
        if mapped is not ResourceType.OTHER:
            source_type = mapped

    knowledge = await _existing_resource(
        database,
        workspace_id=resource.workspace_id,
        connection_id=connection.id,
        external_id=resource.external_id,
    )
    if knowledge is None:
        knowledge = KnowledgeResource(
            workspace_id=resource.workspace_id,
            owner_user_id=connection.user_id,
            source_type=source_type.value,
            source_connection_id=connection.id,
            external_resource_id=resource.external_id,
            source_read_capability=capability,
            sensitivity=_sensitivity_from(canonical),
        )
        database.add(knowledge)
        await database.flush()
    elif knowledge.deleted_at is not None:
        # A retired projection is never rebuilt, not even by an explicit force.
        raise ValueError("A retired projection is never rebuilt")

    knowledge.source_type = source_type.value
    knowledge.source_resource_id = resource.id
    knowledge.source_read_capability = capability
    knowledge.sensitivity = _sensitivity_from(canonical)
    # Removed source fields are cleared so an older revision never lingers.
    knowledge.title = _title_from(canonical) if retained else None
    knowledge.canonical_url = resource.source_url
    knowledge.source_created_at = resource.source_created_at
    knowledge.source_updated_at = resource.source_updated_at
    knowledge.source_version = resource.version
    knowledge.deleted_at = None

    state = INDEXED
    text: str | None = None
    if retained:
        try:
            document = canonical_resource_to_source_document(
                canonical_contract(resource), provenance_connection_id=connection.id
            )
        except (ValueError, ValidationError):
            document = None
        if document is not None:
            body = document.content or ""
            text = "\n\n".join(part for part in (document.subject, body) if part)
            text = text[:MAX_TEXT_CHARS] if text else None
    if not text:
        state = NO_CONTENT

    kind, structured_at, structured_until = structured_window(source_type, canonical)
    knowledge.normalized_text = text
    if state == NO_CONTENT:
        knowledge.title = None
        knowledge.canonical_url = None
    knowledge.fresh_until = fresh_until(capability, resource.retrieved_at)
    knowledge.indexed_at = moment
    await database.flush()

    index_row = await database.scalar(
        select(KnowledgeResourceIndex)
        .where(KnowledgeResourceIndex.resource_id == knowledge.id)
        .execution_options(populate_existing=True)
    )
    if index_row is None:
        index_row = KnowledgeResourceIndex(
            workspace_id=knowledge.workspace_id,
            resource_id=knowledge.id,
            index_state=state,
            text_length=0,
            chunk_count=0,
        )
        database.add(index_row)
        await database.flush()

    unchanged = (
        not force
        and index_row.source_content_hash == resource.content_hash
        and index_row.index_state == state
    )
    chunks_written = index_row.chunk_count
    if not unchanged:
        await database.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.resource_id == knowledge.id)
        )
        spans = chunk_spans(text) if text else []
        for position, span in enumerate(spans):
            database.add(
                KnowledgeChunk(
                    workspace_id=knowledge.workspace_id,
                    resource_id=knowledge.id,
                    chunk_index=position,
                    text_content=span,
                    token_count=len(span.split()),
                    embedding_version=None,
                )
            )
        index_row.source_content_hash = resource.content_hash
        index_row.index_state = state
        index_row.structured_kind = kind
        index_row.structured_at = structured_at
        index_row.structured_until = structured_until
        index_row.text_length = len(text or "")
        index_row.chunk_count = len(spans)
        index_row.indexed_at = moment
        chunks_written = len(spans)
        await database.flush()

    grant_created = False
    existing_grant = await _owner_view_grant(database, knowledge, owner_user_id=connection.user_id)
    if existing_grant is None:
        database.add(
            KnowledgeResourcePermission(
                resource_id=knowledge.id,
                workspace_id=knowledge.workspace_id,
                principal_type=PrincipalType.USER.value,
                principal_id=connection.user_id,
                permission=Permission.VIEW.value,
                inherited=False,
                source_permission_id=f"connector:{connection.id}:{capability}",
                valid_from=moment,
            )
        )
        await database.flush()
        grant_created = True

    return IndexOutcome(
        resource_id=knowledge.id,
        provider_resource_id=resource.id,
        source_type=source_type,
        state=state,
        chunks=chunks_written,
        grant_created=grant_created,
        reason=None if state == INDEXED else "canonical content was not retained",
    )


async def backfill_workspace(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    limit: int = MAX_INDEX_BATCH,
    settings: Settings | None = None,
) -> BackfillReport:
    """Index a bounded, deterministic batch of one owner's retained resources.

    Only resources whose connection is currently authorized are considered; the
    bound is reported so callers never present a partial batch as a full index.
    """
    if limit < 1 or limit > MAX_INDEX_BATCH:
        raise ValueError("Index batch is out of bounds")
    connections = list(
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.status.in_(tuple(sorted(ACTIVE_CONNECTION_STATUSES))),
            )
            .order_by(ConnectorConnection.id)
        )
    )
    by_id = {connection.id: connection for connection in connections}
    if not by_id:
        return BackfillReport(outcomes=(), examined=0, truncated=False, bound=limit)
    candidates = list(
        await database.scalars(
            select(ConnectorResource)
            .where(
                ConnectorResource.workspace_id == workspace_id,
                ConnectorResource.connector_connection_id.in_(tuple(by_id)),
                ConnectorResource.deleted.is_(False),
            )
            .order_by(ConnectorResource.id)
            .limit(limit + 1)
        )
    )
    truncated = len(candidates) > limit
    outcomes: list[IndexOutcome] = []
    for resource in candidates[:limit]:
        connection = by_id[resource.connector_connection_id]
        try:
            outcomes.append(
                await index_connector_resource(
                    database,
                    connection=connection,
                    resource=resource,
                    now=now,
                    settings=settings,
                )
            )
        except ValueError:
            continue
    return BackfillReport(
        outcomes=tuple(outcomes),
        examined=len(candidates[:limit]),
        truncated=truncated,
        bound=limit,
    )
