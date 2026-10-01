"""User-scoped exclusions applied before any retriever reads content.

Exclusions are a preference, never a grant: they only ever remove material.
``FOLDER`` uses the source-controlled ``external_parent_id`` supplied by the
canonical connector row, never a metadata claim from stored content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.knowledge import KnowledgeExclusion
from navox.db.models import ConnectorConnection
from navox.knowledge.permissions import can_view_resource
from navox.knowledge.search_contracts import (
    ExclusionCreate,
    ExclusionScope,
    ExclusionView,
)


@dataclass(frozen=True)
class ExclusionFilters:
    """Immutable snapshot of one user's exclusions for one request."""

    source_ids: frozenset[UUID] = field(default_factory=frozenset)
    folders: frozenset[tuple[UUID, str]] = field(default_factory=frozenset)
    resource_ids: frozenset[UUID] = field(default_factory=frozenset)
    types: frozenset[str] = field(default_factory=frozenset)

    @property
    def count(self) -> int:
        return len(self.source_ids) + len(self.folders) + len(self.resource_ids) + len(self.types)

    @property
    def empty(self) -> bool:
        return self.count == 0

    def excludes(
        self,
        *,
        source_type: str,
        resource_id: UUID | None = None,
        source_connection_id: UUID | None = None,
        external_parent_id: str | None = None,
    ) -> bool:
        if source_type in self.types:
            return True
        if resource_id is not None and resource_id in self.resource_ids:
            return True
        if source_connection_id is not None and source_connection_id in self.source_ids:
            return True
        if source_connection_id is not None and external_parent_id:
            return (source_connection_id, external_parent_id) in self.folders
        return False


def exclusion_view(row: KnowledgeExclusion) -> ExclusionView:
    return ExclusionView(
        id=row.id,
        scope=ExclusionScope(row.scope),
        source_connection_id=row.source_connection_id,
        external_id=row.external_id,
        resource_id=row.resource_id,
        resource_type=row.resource_type,
        created_at=row.created_at,
    )


async def load_exclusions(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> ExclusionFilters:
    rows = await database.scalars(
        select(KnowledgeExclusion)
        .where(
            KnowledgeExclusion.workspace_id == workspace_id,
            KnowledgeExclusion.user_id == user_id,
        )
        .order_by(KnowledgeExclusion.id)
        .execution_options(populate_existing=True)
    )
    source_ids: set[UUID] = set()
    folders: set[tuple[UUID, str]] = set()
    resource_ids: set[UUID] = set()
    types: set[str] = set()
    for row in rows:
        if row.scope == ExclusionScope.SOURCE.value and row.source_connection_id is not None:
            source_ids.add(row.source_connection_id)
        elif (
            row.scope == ExclusionScope.FOLDER.value
            and row.source_connection_id is not None
            and row.external_id
        ):
            folders.add((row.source_connection_id, row.external_id))
        elif row.scope == ExclusionScope.RESOURCE.value and row.resource_id is not None:
            resource_ids.add(row.resource_id)
        elif row.scope == ExclusionScope.TYPE.value and row.resource_type:
            types.add(row.resource_type)
    return ExclusionFilters(
        source_ids=frozenset(source_ids),
        folders=frozenset(folders),
        resource_ids=frozenset(resource_ids),
        types=frozenset(types),
    )


async def list_exclusions(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> list[KnowledgeExclusion]:
    return list(
        await database.scalars(
            select(KnowledgeExclusion)
            .where(
                KnowledgeExclusion.workspace_id == workspace_id,
                KnowledgeExclusion.user_id == user_id,
            )
            .order_by(KnowledgeExclusion.created_at, KnowledgeExclusion.id)
        )
    )


async def create_exclusion(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    payload: ExclusionCreate,
) -> KnowledgeExclusion:
    """Persist one exclusion after validating an owned, visible target.

    Unsupported or foreign targets raise the same generic error as unknown ones,
    so this endpoint is never an existence oracle, and a rejected write never
    leaves an unusable transaction behind.
    """
    if payload.scope in {ExclusionScope.SOURCE, ExclusionScope.FOLDER}:
        if payload.source_connection_id is None:
            raise ValueError("Unknown or unsupported exclusion target")
        owned = await database.scalar(
            select(ConnectorConnection.id).where(
                ConnectorConnection.id == payload.source_connection_id,
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
            )
        )
        if owned is None:
            raise ValueError("Unknown or unsupported exclusion target")
    elif payload.scope is ExclusionScope.RESOURCE:
        if payload.resource_id is None:
            raise ValueError("Unknown or unsupported exclusion target")
        visible = await can_view_resource(
            database,
            resource_id=payload.resource_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=datetime.now(UTC),
        )
        if not visible:
            raise ValueError("Unknown or unsupported exclusion target")

    row = KnowledgeExclusion(
        workspace_id=workspace_id,
        user_id=user_id,
        scope=payload.scope.value,
        source_connection_id=payload.source_connection_id,
        external_id=payload.external_id,
        resource_id=payload.resource_id,
        resource_type=(payload.resource_type.value if payload.resource_type is not None else None),
    )
    database.add(row)
    try:
        await database.flush()
    except IntegrityError as error:
        raise ValueError("Unknown or unsupported exclusion target") from error
    return row


async def delete_exclusion(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, exclusion_id: UUID
) -> bool:
    result = cast(
        CursorResult[Any],
        await database.execute(
            delete(KnowledgeExclusion).where(
                KnowledgeExclusion.id == exclusion_id,
                KnowledgeExclusion.workspace_id == workspace_id,
                KnowledgeExclusion.user_id == user_id,
            )
        ),
    )
    return bool(result.rowcount)
