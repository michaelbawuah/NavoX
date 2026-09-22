from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import AuditEvent


def add_audit_event(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    event_type: str,
    entity_type: str,
    entity_id: UUID,
    metadata: dict[str, object] | None = None,
    actor_type: str = "navox",
    actor_id: str | None = None,
) -> None:
    database.add(
        AuditEvent(
            user_id=user_id,
            workspace_id=workspace_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=metadata or {},
        )
    )
