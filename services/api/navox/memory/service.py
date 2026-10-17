"""Save only direct owner input. No automatic extraction or provider context injection."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.agent.audit import add_audit_event
from navox.db.memory import OwnerMemory
from navox.db.models import User
from navox.memory.schemas import (
    MAX_MEMORIES,
    MEMORY_LABELS,
    MemoryCreate,
    MemoryKind,
    MemoryUpdate,
    validate_content,
)


class MemoryError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


async def lock_owner(database: AsyncSession, user_id: UUID) -> None:
    # All writes for an owner take the same lock before reading the quota.
    # The unique preference key is a second database-level concurrency guard.
    owner = await database.scalar(select(User).where(User.id == user_id).with_for_update())
    if owner is None:
        raise MemoryError(404, "Memory not found")


async def owned_memory(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, memory_id: UUID
) -> OwnerMemory:
    memory = await database.scalar(
        select(OwnerMemory).where(
            OwnerMemory.id == memory_id,
            OwnerMemory.workspace_id == workspace_id,
            OwnerMemory.user_id == user_id,
        )
    )
    if memory is None:
        raise MemoryError(404, "Memory not found")
    return memory


async def save_memory(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, payload: MemoryCreate
) -> OwnerMemory:
    await lock_owner(database, user_id)
    count = await database.scalar(
        select(func.count())
        .select_from(OwnerMemory)
        .where(OwnerMemory.workspace_id == workspace_id, OwnerMemory.user_id == user_id)
    )
    if count is not None and count >= MAX_MEMORIES:
        raise MemoryError(409, "Memory is full. Delete an entry before saving another.")
    if payload.kind != "project_fact":
        existing = await database.scalar(
            select(OwnerMemory.id).where(
                OwnerMemory.workspace_id == workspace_id,
                OwnerMemory.user_id == user_id,
                OwnerMemory.preference_key == payload.kind,
            )
        )
        if existing is not None:
            raise MemoryError(409, "This preference is already saved. Edit the existing entry.")
    now = datetime.now(UTC)
    memory = OwnerMemory(
        id=uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        kind=payload.kind,
        preference_key=None if payload.kind == "project_fact" else payload.kind,
        label=payload.label or MEMORY_LABELS[payload.kind],
        content=payload.content,
        enabled=payload.enabled,
        provenance="user_saved",
        created_at=now,
        updated_at=now,
    )
    database.add(memory)
    audit_memory(database, memory, "memory.saved")
    await database.commit()
    return memory


async def update_memory(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    memory_id: UUID,
    payload: MemoryUpdate,
) -> OwnerMemory:
    await lock_owner(database, user_id)
    memory = await owned_memory(
        database, user_id=user_id, workspace_id=workspace_id, memory_id=memory_id
    )
    if payload.content is not None:
        try:
            memory.content = validate_content(cast(MemoryKind, memory.kind), payload.content)
        except ValueError as error:
            raise MemoryError(422, str(error)) from error
    if payload.label is not None:
        memory.label = payload.label
    if payload.enabled is not None:
        memory.enabled = payload.enabled
    memory.updated_at = datetime.now(UTC)
    audit_memory(database, memory, "memory.updated")
    await database.commit()
    return memory


async def delete_memory(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID, memory_id: UUID
) -> None:
    await lock_owner(database, user_id)
    memory = await owned_memory(
        database, user_id=user_id, workspace_id=workspace_id, memory_id=memory_id
    )
    audit_memory(database, memory, "memory.deleted")
    await database.delete(memory)
    await database.commit()


def audit_memory(database: AsyncSession, memory: OwnerMemory, event_type: str) -> None:
    # Retain the operation and provenance without retaining forgotten personal text.
    add_audit_event(
        database,
        user_id=memory.user_id,
        workspace_id=memory.workspace_id,
        event_type=event_type,
        entity_type="owner_memory",
        entity_id=memory.id,
        actor_type="user",
        actor_id=str(memory.user_id),
        metadata={"kind": memory.kind, "provenance": "user_saved"},
    )
