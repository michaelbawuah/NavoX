"""Owned connector outage labels, without resource counts or provider error text."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import ConnectorConnection, ConnectorDefinition
from navox.knowledge.exclusions import ExclusionFilters
from navox.knowledge.search_contracts import RetrievalPlan, SourceIssue

_LABELS = {
    "google-gmail": "Gmail",
    "google-calendar": "Calendar",
    "google-drive": "Drive",
    "canvas-lms": "Canvas",
    "generic-import": "Imported files",
}
_TYPES = {
    "communication.messages.read": {"EMAIL"},
    "calendar.events.read": {"CALENDAR_EVENT"},
    "documents.read": {"DOCUMENT"},
    "files.read": {"FILE", "DOCUMENT"},
    "academic.assignments.read": {"CANVAS_ASSIGNMENT"},
    "academic.courses.read": {"OTHER"},
    "academic.announcements.read": {"CANVAS_ANNOUNCEMENT"},
}
_ISSUES = {"DEGRADED", "AUTH_EXPIRED", "RATE_LIMITED", "SYNC_FAILED"}


async def source_issues(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    plan: RetrievalPlan,
    filters: ExclusionFilters,
) -> tuple[SourceIssue, ...]:
    query = (
        select(
            ConnectorConnection.id,
            ConnectorDefinition.connector_key,
            ConnectorConnection.health_state,
            ConnectorConnection.authorized_capabilities,
        )
        .join(
            ConnectorDefinition,
            ConnectorDefinition.id == ConnectorConnection.connector_definition_id,
        )
        .where(
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.user_id == user_id,
            ConnectorConnection.status != "DISCONNECTED",
            ConnectorConnection.health_state.in_(_ISSUES),
        )
    )
    if plan.source_ids:
        query = query.where(ConnectorConnection.id.in_(plan.source_ids))
    results = []
    for connection_id, key, state, capabilities in await database.execute(
        query.order_by(ConnectorConnection.id).limit(50)
    ):
        if connection_id in filters.source_ids:
            continue
        types = (
            set().union(*(_TYPES.get(capability, set()) for capability in capabilities))
            - filters.types
        )
        if not types or (plan.types and not types.intersection(plan.types)):
            continue
        results.append(
            SourceIssue(
                connection_id=connection_id,
                source_label=_LABELS.get(key, "Connected source"),
                state=state,
            )
        )
    return tuple(results)
