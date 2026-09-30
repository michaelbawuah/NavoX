"""Bounded per-user recent searches, cleared independently of the index."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.knowledge import KnowledgeRecentSearch
from navox.knowledge.search_contracts import RecentSearchView, SearchMode

MAX_RECENT_SEARCHES = 25


def _view(row: KnowledgeRecentSearch) -> RecentSearchView:
    return RecentSearchView(
        id=row.id,
        query=row.query,
        mode=SearchMode(row.mode),
        result_count=row.result_count,
        created_at=row.created_at,
    )


async def record_recent_search(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    mode: SearchMode,
    query: str,
    result_count: int,
    now: datetime | None = None,
    limit: int = MAX_RECENT_SEARCHES,
) -> None:
    """Upsert one query and prune the oldest rows beyond ``limit``."""
    moment = now or datetime.now(UTC)
    existing = await database.scalar(
        select(KnowledgeRecentSearch).where(
            KnowledgeRecentSearch.workspace_id == workspace_id,
            KnowledgeRecentSearch.user_id == user_id,
            KnowledgeRecentSearch.mode == mode.value,
            KnowledgeRecentSearch.query == query,
        )
    )
    if existing is None:
        existing = KnowledgeRecentSearch(
            workspace_id=workspace_id,
            user_id=user_id,
            mode=mode.value,
            query=query,
            result_count=result_count,
            created_at=moment,
            updated_at=moment,
        )
        database.add(existing)
    else:
        existing.result_count = result_count
        existing.updated_at = moment
    await database.flush()
    keep = [
        row.id
        for row in await database.scalars(
            select(KnowledgeRecentSearch)
            .where(
                KnowledgeRecentSearch.workspace_id == workspace_id,
                KnowledgeRecentSearch.user_id == user_id,
            )
            .order_by(KnowledgeRecentSearch.updated_at.desc(), KnowledgeRecentSearch.id)
            .limit(limit)
        )
    ]
    if len(keep) == limit:
        stale = select(KnowledgeRecentSearch.id).where(
            KnowledgeRecentSearch.workspace_id == workspace_id,
            KnowledgeRecentSearch.user_id == user_id,
            KnowledgeRecentSearch.id.notin_(keep),
        )
        await database.execute(
            delete(KnowledgeRecentSearch).where(KnowledgeRecentSearch.id.in_(stale))
        )
        await database.flush()


async def list_recent_searches(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID, limit: int = MAX_RECENT_SEARCHES
) -> list[RecentSearchView]:
    rows = await database.scalars(
        select(KnowledgeRecentSearch)
        .where(
            KnowledgeRecentSearch.workspace_id == workspace_id,
            KnowledgeRecentSearch.user_id == user_id,
        )
        .order_by(KnowledgeRecentSearch.updated_at.desc(), KnowledgeRecentSearch.id)
        .limit(limit)
    )
    return [_view(row) for row in rows]


async def clear_recent_searches(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> int:
    """Clear history only. Derived content and the canonical index are untouched."""
    result = cast(
        CursorResult[Any],
        await database.execute(
            delete(KnowledgeRecentSearch).where(
                KnowledgeRecentSearch.workspace_id == workspace_id,
                KnowledgeRecentSearch.user_id == user_id,
            )
        ),
    )
    await database.flush()
    return int(result.rowcount or 0)


async def count_recent_searches(
    database: AsyncSession, *, workspace_id: UUID, user_id: UUID
) -> int:
    return int(
        await database.scalar(
            select(func.count())
            .select_from(KnowledgeRecentSearch)
            .where(
                KnowledgeRecentSearch.workspace_id == workspace_id,
                KnowledgeRecentSearch.user_id == user_id,
            )
        )
        or 0
    )
