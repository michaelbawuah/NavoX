"""Observed-activity ranking for News. It never assigns political or truth scores."""

from collections import defaultdict
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import NewsSource, NewsStory, NewsStoryItem, NewsStoryVersion
from navox.news.contracts import Contract, NewsItemRead, stored_utc

TREND_WINDOW = timedelta(hours=6)


class TrendSignals(Contract):
    story_id: UUID
    recent_changes: int = Field(ge=0)
    recent_source_additions: int = Field(ge=0)
    recent_reports: int = Field(ge=0)
    independent_source_groups: int = Field(ge=0)
    last_updated_at: datetime

    @property
    def sort_key(self) -> tuple[int, int, int, int, float, str]:
        # Smaller tuple sorts first. No opaque confidence/importance percentage is created.
        return (
            -self.recent_source_additions,
            -self.recent_changes,
            -self.recent_reports,
            -self.independent_source_groups,
            -self.last_updated_at.timestamp(),
            self.story_id.hex,
        )


async def observed_trend_signals(
    database: AsyncSession,
    story: NewsStory,
    items: tuple[NewsItemRead, ...],
    *,
    now: datetime,
) -> TrendSignals:
    cutoff = now - TREND_WINDOW
    changes = list(
        await database.scalars(
            select(NewsStoryVersion).where(
                NewsStoryVersion.cluster_id == story.id,
                NewsStoryVersion.generated_at >= cutoff,
                NewsStoryVersion.generated_at <= now,
            )
        )
    )
    source_ids = {item.source_id for item in items}
    groups: set[str] = set()
    if source_ids:
        groups = set(
            await database.scalars(
                select(NewsSource.independence_group).where(NewsSource.id.in_(source_ids))
            )
        )
    return TrendSignals(
        story_id=story.id,
        recent_changes=len(changes),
        recent_source_additions=sum(row.change_kind == "SOURCE_ADDED" for row in changes),
        recent_reports=sum(item.published_at >= cutoff for item in items),
        independent_source_groups=len(groups),
        last_updated_at=stored_utc(story.last_updated_at),
    )


def activity_tiebreak(item: NewsItemRead) -> tuple[float, str]:
    """Newest item first, then a stable identifier so equal timestamps never vary."""
    return (-item.published_at.timestamp(), item.id.hex)


async def order_trending_items(
    database: AsyncSession,
    items: tuple[NewsItemRead, ...],
    *,
    now: datetime,
) -> tuple[NewsItemRead, ...]:
    """Order authorized items by observed story activity, never truth or ideology.

    Callers pass items that already passed permission, ownership, suppression and
    freshness checks. Each input item is returned exactly once; a cluster with no
    owned activity record keeps its items in deterministic recency order at the end.
    """
    if not items:
        return ()
    memberships = dict(
        (
            await database.execute(
                select(NewsStoryItem.news_item_id, NewsStoryItem.cluster_id).where(
                    NewsStoryItem.news_item_id.in_(item.id for item in items)
                )
            )
        )
        .tuples()
        .all()
    )
    grouped: dict[UUID, list[NewsItemRead]] = defaultdict(list)
    ungrouped: list[NewsItemRead] = []
    for item in items:
        cluster_id = memberships.get(item.id)
        if cluster_id is None:
            ungrouped.append(item)
        else:
            grouped[cluster_id].append(item)
    ranked: list[tuple[tuple[int, int, int, int, float, str], UUID]] = []
    if grouped:
        stories = {
            story.id: story
            for story in await database.scalars(
                select(NewsStory).where(NewsStory.id.in_(tuple(grouped)))
            )
        }
        for story_id, group in grouped.items():
            story = stories.get(story_id)
            if story is None:
                ungrouped.extend(group)
                continue
            signals = await observed_trend_signals(database, story, tuple(group), now=now)
            ranked.append((signals.sort_key, story_id))
    ordered: list[NewsItemRead] = []
    for _, story_id in sorted(ranked):
        ordered.extend(sorted(grouped[story_id], key=activity_tiebreak))
    ordered.extend(sorted(ungrouped, key=activity_tiebreak))
    return tuple(ordered)
