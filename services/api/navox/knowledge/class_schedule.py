"""Bounded, read-only academic schedule projection through SPEC-007 authority.

Canonical provider records are read only after the existing resource-detail VIEW
check, and are rechecked before publication. No course implies a meeting time.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.canvas import (
    CANVAS_MANIFEST,
    CanvasConnector,
    validate_canvas_base_url,
)
from navox.connectors.builtin.google_calendar import (
    CALENDAR_MANIFEST,
    GoogleCalendarConnector,
    calendar_source_url,
)
from navox.connectors.catalog import build_connector_registry
from navox.connectors.contracts import CanonicalResource, ConnectorRuntimeError
from navox.connectors.google_calendar_sync import CalendarAuthority, GoogleCalendarTokenBroker
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.knowledge import KnowledgeResource, KnowledgeResourceIndex
from navox.db.models import Connection, ConnectorConnection, ConnectorDefinition, ConnectorResource
from navox.knowledge.contracts import stored_utc
from navox.knowledge.permissions import can_view_resource
from navox.knowledge.service import load_resource_detail

MAX_SOURCES = 400
CLASS_WORD = re.compile(r"\b(class|lecture|seminar|tutorial|lab|section)\b", re.I)
COURSE_CODE = re.compile(r"\b[A-Z]{2,8}[ -]?\d{3,5}\b", re.I)
NON_CLASS = re.compile(r"\b(assignment|quiz|exam|midterm|final|deadline|due|office hours)\b", re.I)


def transient_class_source_snapshot(
    resources: tuple[CanonicalResource, ...], *, now: datetime
) -> dict[str, object]:
    """Project one authorized provider read without retaining its canonical body."""
    courses: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    incomplete = False
    for resource in resources:
        canonical = resource.canonical
        nested = canonical.get("source_document")
        if isinstance(nested, dict):
            metadata = nested.get("metadata")
            title = _string(nested.get("subject"), 300)
        else:
            metadata = canonical.get("metadata")
            title = _string(canonical.get("subject"), 300)
        if not isinstance(metadata, dict):
            incomplete = True
            continue
        source = {
            "resource_id": str(resource.resource_id),
            "connection_id": str(resource.connector_connection_id),
            "external_resource_id": resource.external_id,
            "updated_at": _timestamp(metadata.get("scheduling_updated_at")),
            "navigation_available": resource.resource_type == "calendar.event"
            and resource.source_url is not None,
        }
        if resource.provider == "canvas" and resource.resource_type == "academic.course":
            course_id = _string(metadata.get("course_id"), 128)
            if not course_id or not title:
                incomplete = True
                continue
            courses.append(
                {
                    "course_id": course_id,
                    "course_name": title,
                    "course_code": _string(metadata.get("course_code"), 128),
                    "section": _string(metadata.get("section"), 128),
                    "source": source,
                }
            )
            continue
        if resource.resource_type != "calendar.event":
            continue
        start = _timestamp(metadata.get("start_at"))
        if not title or not start:
            if metadata.get("status") == "cancelled":
                incomplete = True  # An unidentified cancellation may supersede a class.
            continue
        parsed_start = datetime.fromisoformat(start)
        if not now - timedelta(days=1) <= parsed_start <= now + timedelta(days=90):
            continue
        if metadata.get("all_day") is True:
            continue
        if NON_CLASS.search(title) or not (CLASS_WORD.search(title) or COURSE_CODE.search(title)):
            continue
        parent = resource.external_parent_id
        course_id = (
            parent.removeprefix("course_") if parent and parent.startswith("course_") else None
        )
        events.append(
            {
                "provider": resource.provider,
                "course_id": course_id,
                "title": title,
                "start_at": start,
                "end_at": _timestamp(metadata.get("end_at")),
                "location": _string(metadata.get("location_name"), 256)
                or _string(metadata.get("location"), 256),
                "meeting_url": _string(metadata.get("meeting_url"), 2048),
                "status": "CANCELLED"
                if metadata.get("status") in {"cancelled", "deleted"}
                else "SCHEDULED",
                "explicit_class_meeting": True,
                "all_day": False,
                "fresh_until": (now + timedelta(minutes=2)).isoformat(),
                "source": source,
            }
        )
    return {"complete": not incomplete, "courses": courses, "events": events}


async def _live_class_source_resources(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    settings: Settings,
) -> tuple[CanonicalResource, ...] | None:
    """Read current class records through the existing scoped connector adapters."""
    rows = list(
        await database.execute(
            select(
                ConnectorConnection, ConnectorDefinition.connector_key, ConnectorDefinition.version
            )
            .join(
                ConnectorDefinition,
                ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
            )
            .where(
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.status.in_(("CONNECTED", "DEGRADED")),
                ConnectorDefinition.connector_key.in_(("canvas-lms", "google-calendar")),
            )
        )
    )
    if len(rows) > 4:
        return None
    resources: list[CanonicalResource] = []
    calendar_authorities: list[CalendarAuthority] = []
    for connection, connector_key, connector_version in rows:
        try:
            if connector_key == "google-calendar":
                if connection.legacy_connection_id is None:
                    return None
                legacy = await database.get(Connection, connection.legacy_connection_id)
                if (
                    legacy is None
                    or legacy.user_id != user_id
                    or legacy.workspace_id != workspace_id
                ):
                    return None
                authority = CalendarAuthority(
                    legacy.id,
                    user_id,
                    workspace_id,
                    legacy.credential_reference,
                    legacy.external_account_id,
                    frozenset(legacy.granted_scopes),
                )
                calendar_authorities.append(authority)
                registry = ConnectorRegistry()
                registry.register(CALENDAR_MANIFEST, GoogleCalendarConnector)
                runtime = ConnectorRuntime(
                    registry,
                    secret_broker=GoogleCalendarTokenBroker(settings, authority, connection.id),
                    authority_check=authority.check,
                )
                policy = frozenset({"calendar.events.read"})
            else:
                if connector_version == CANVAS_MANIFEST.version:
                    registry = ConnectorRegistry()
                    registry.register(CANVAS_MANIFEST, CanvasConnector)
                else:
                    registry = build_connector_registry(settings)
                runtime = ConnectorRuntime(
                    registry,
                    secret_broker=SecretBroker(settings),
                )
                policy = frozenset({"academic.courses.read", "calendar.events.read"})
            resources.extend(
                await runtime.read_preview(
                    database,
                    connection_id=connection.id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    policy_allowed=policy,
                )
            )
        except (ConnectorRuntimeError, SecretBrokerError, ValueError, TimeoutError):
            return None
        if len(resources) > MAX_SOURCES:
            return None
    try:
        for connection, _connector_key, _connector_version in rows:
            current = await owned_connector(
                database,
                connection_id=connection.id,
                workspace_id=workspace_id,
                user_id=user_id,
                require_active=True,
                lock_connection=False,
            )
            required = {
                "calendar.events.read"
                if resource.resource_type == "calendar.event"
                else "academic.courses.read"
                for resource in resources
                if resource.connector_connection_id == connection.id
            }
            if not required.issubset(
                set(current.authorized_capabilities) & set(current.provider_capabilities)
            ):
                return None
        for authority in calendar_authorities:
            await authority.check(database, False)
    except (ConnectorAccessDenied, ConnectorRuntimeError):
        return None
    return tuple(resources)


async def live_class_source_snapshot(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    settings: Settings,
) -> dict[str, object]:
    resources = await _live_class_source_resources(
        database, workspace_id=workspace_id, user_id=user_id, settings=settings
    )
    if resources is None:
        return {"complete": False, "courses": [], "events": []}
    return transient_class_source_snapshot(resources, now=datetime.now(UTC))


def class_navigation_url(
    resources: tuple[CanonicalResource, ...],
    *,
    connection_id: UUID,
    resource_id: UUID,
    canvas_base_url: str | None,
    now: datetime,
) -> str | None:
    """Resolve only an explicit upcoming class event from a complete fresh read."""
    snapshot = transient_class_source_snapshot(resources, now=now)
    if snapshot["complete"] is not True:
        return None
    events = snapshot["events"]
    if not isinstance(events, list):
        return None
    eligible = any(
        isinstance(event, dict)
        and event.get("status") == "SCHEDULED"
        and isinstance(event.get("source"), dict)
        and event["source"].get("connection_id") == str(connection_id)
        and event["source"].get("resource_id") == str(resource_id)
        and isinstance(event.get("start_at"), str)
        and datetime.fromisoformat(event["start_at"]) > now
        for event in events
    )
    if not eligible:
        return None
    resource = next(
        (
            item
            for item in resources
            if item.resource_id == resource_id
            and item.connector_connection_id == connection_id
            and item.resource_type == "calendar.event"
        ),
        None,
    )
    if resource is None:
        return None
    if resource.provider == "google":
        return calendar_source_url(resource.source_url)
    if resource.provider != "canvas" or canvas_base_url is None:
        return None
    try:
        expected = urlsplit(validate_canvas_base_url(canvas_base_url))
        candidate = urlsplit(resource.source_url or "")
    except ValueError:
        return None
    if (
        candidate.scheme != "https"
        or candidate.netloc != expected.netloc
        or candidate.username
        or candidate.password
        or not candidate.path.startswith(("/calendar", "/courses/"))
    ):
        return None
    return resource.source_url


async def live_class_navigation_target(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    settings: Settings,
    connection_id: UUID,
    resource_id: UUID,
) -> str | None:
    """Re-read scoped provider state and authority at the moment of navigation."""
    resources = await _live_class_source_resources(
        database, workspace_id=workspace_id, user_id=user_id, settings=settings
    )
    if resources is None:
        return None
    connection = await owned_connector(
        database,
        connection_id=connection_id,
        workspace_id=workspace_id,
        user_id=user_id,
        require_active=True,
        lock_authority=True,
    )
    base = connection.config.get("base_url")
    url = class_navigation_url(
        resources,
        connection_id=connection_id,
        resource_id=resource_id,
        canvas_base_url=base if isinstance(base, str) else None,
        now=datetime.now(UTC),
    )
    if url is None:
        return None
    required = "calendar.events.read"
    if (
        required not in connection.authorized_capabilities
        or required not in connection.provider_capabilities
    ):
        return None
    return url


def _string(value: object, limit: int) -> str | None:
    return value[:limit].strip() or None if isinstance(value, str) else None


def _timestamp(value: object) -> str | None:
    text = _string(value, 64)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def _parts(resource: ConnectorResource) -> tuple[str | None, dict[str, Any], str | None]:
    canonical = resource.canonical or {}
    source = canonical.get("source_document")
    if isinstance(source, dict):
        metadata = source.get("metadata")
        return (
            _string(source.get("subject"), 300),
            metadata if isinstance(metadata, dict) else {},
            _string(source.get("external_parent_id"), 128),
        )
    metadata = canonical.get("metadata")
    return (
        _string(canonical.get("subject"), 300),
        metadata if isinstance(metadata, dict) else {},
        _string(resource.external_parent_id, 128),
    )


async def class_source_snapshot(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, now: datetime | None = None
) -> dict[str, object]:
    moment = now or datetime.now(UTC)
    # Only this user's own connected academic/calendar records. The query selects
    # identifiers, never stored content; load_resource_detail is the VIEW gate.
    identifiers = list(
        await database.scalars(
            select(KnowledgeResource.id)
            .join(
                ConnectorResource,
                (ConnectorResource.id == KnowledgeResource.source_resource_id)
                & (ConnectorResource.workspace_id == KnowledgeResource.workspace_id),
            )
            .outerjoin(
                KnowledgeResourceIndex,
                (KnowledgeResourceIndex.resource_id == KnowledgeResource.id)
                & (KnowledgeResourceIndex.workspace_id == KnowledgeResource.workspace_id),
            )
            .where(
                KnowledgeResource.workspace_id == workspace_id,
                KnowledgeResource.owner_user_id == user_id,
                KnowledgeResource.deleted_at.is_(None),
                ConnectorResource.resource_type.in_(("academic.course", "calendar.event")),
                ConnectorResource.provider.in_(("canvas", "google")),
                or_(
                    ConnectorResource.resource_type == "academic.course",
                    and_(
                        KnowledgeResourceIndex.structured_at >= moment - timedelta(days=1),
                        KnowledgeResourceIndex.structured_at <= moment + timedelta(days=180),
                    ),
                ),
            )
            .order_by(ConnectorResource.retrieved_at.desc())
            .limit(MAX_SOURCES + 1)
        )
    )
    if len(identifiers) > MAX_SOURCES:
        return {"complete": False, "courses": [], "events": []}
    courses: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    incomplete = False
    for resource_id in identifiers:
        if not await can_view_resource(
            database,
            resource_id=resource_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=moment,
        ):
            continue
        detail = await load_resource_detail(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_id=resource_id,
            now=moment,
        )
        if detail is None:
            incomplete = True
            continue
        if detail.fresh_until is None or stored_utc(detail.fresh_until) <= moment:
            incomplete = True
            continue
        resource = await database.scalar(
            select(ConnectorResource)
            .join(KnowledgeResource, KnowledgeResource.source_resource_id == ConnectorResource.id)
            .where(
                KnowledgeResource.id == resource_id,
                KnowledgeResource.workspace_id == workspace_id,
                ConnectorResource.workspace_id == workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if resource is None:
            incomplete = True
            continue
        title, metadata, parent = _parts(resource)
        # A second authority/revision check fences a revocation or source update
        # while canonical metadata was being read.
        current_detail = await load_resource_detail(
            database,
            workspace_id=workspace_id,
            user_id=user_id,
            resource_id=resource_id,
            now=moment,
        )
        if current_detail is None or (
            current_detail.source_version != detail.source_version
            or current_detail.source_updated_at != detail.source_updated_at
            or current_detail.indexed_at != detail.indexed_at
            or current_detail.fresh_until != detail.fresh_until
        ):
            incomplete = True
            continue
        if title is None:
            incomplete = True
            continue
        source = {
            "resource_id": str(detail.resource_id),
            "connection_id": str(resource.connector_connection_id),
            "external_resource_id": resource.external_id,
            "updated_at": _timestamp(metadata.get("scheduling_updated_at")),
        }
        if resource.provider == "canvas" and resource.resource_type == "academic.course":
            course_id = _string(metadata.get("course_id"), 128)
            if course_id is None:
                incomplete = True
                continue
            courses.append(
                {
                    "course_id": course_id,
                    "course_name": title,
                    "course_code": _string(metadata.get("course_code"), 128),
                    "section": _string(metadata.get("section"), 128),
                    "source": source,
                }
            )
            continue
        if resource.resource_type != "calendar.event":
            continue
        start = _timestamp(metadata.get("start_at"))
        end = _timestamp(metadata.get("end_at"))
        if start is None or metadata.get("all_day") is True:
            continue  # An all-day/course listing never establishes a class time.
        start_moment = datetime.fromisoformat(start)
        if not (moment - timedelta(days=1) <= start_moment <= moment + timedelta(days=180)):
            continue
        course_id = (
            parent.removeprefix("course_") if parent and parent.startswith("course_") else None
        )
        # Calendar events must explicitly look like class meetings. Merely
        # appearing on the course calendar does not establish that fact.
        explicit = not NON_CLASS.search(title) and bool(
            CLASS_WORD.search(title) or COURSE_CODE.search(title)
        )
        if not explicit:
            continue
        status = "CANCELLED" if metadata.get("status") in ("cancelled", "deleted") else "SCHEDULED"
        events.append(
            {
                "provider": resource.provider,
                "course_id": course_id,
                "title": title,
                "start_at": start,
                "end_at": end,
                "location": _string(metadata.get("location_name"), 256)
                or _string(metadata.get("location"), 256),
                "meeting_url": _string(metadata.get("meeting_url"), 2048),
                "status": status,
                "explicit_class_meeting": True,
                "all_day": False,
                "fresh_until": stored_utc(detail.fresh_until).isoformat(),
                "source": source,
            }
        )
    return {"complete": not incomplete, "courses": courses, "events": events}
