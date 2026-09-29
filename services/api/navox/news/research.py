"""Evidence-backed timeline and coverage views, without guessed events or political scores."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import NewsClaim, NewsStoryVersion
from navox.news.contracts import Contract, SourceDefinition, stored_utc
from navox.news.evidence import ClaimRead, claim_views
from navox.news.stories import owned_story, story_view


class TimelineEntry(Contract):
    item_id: UUID
    reported_headline: str
    source_name: str
    source_url: str
    published_at: datetime
    event_started_at: datetime | None
    event_ended_at: datetime | None
    time_basis: Literal["event_time", "publication_time"]
    attribution_only: Literal[True] = True


class TimelineRead(Contract):
    story_id: UUID
    as_of: datetime
    entries: tuple[TimelineEntry, ...] = Field(max_length=100)
    limited: bool = True
    explanation: str = (
        "Source reports are attributed, not independently verified events. "
        "Publication time is shown when the source provides no event time."
    )


class CoverageSource(Contract):
    item_id: UUID
    source_id: UUID
    source_name: str
    source_url: str
    headline: str
    description: str | None
    language: str
    published_at: datetime
    claims: tuple[ClaimRead, ...] = Field(max_length=50)


class CoverageRead(Contract):
    story_id: UUID
    as_of: datetime
    sources: tuple[CoverageSource, ...] = Field(max_length=100)
    limited: bool = True
    explanation: str = (
        "Compare only the currently permitted source wording and stored claims below. "
        "A missing claim does not establish an omission by a publisher. "
        "This view assigns no political, bias, trust or quality scores."
    )


class ChangeEntry(Contract):
    version: int
    change_kind: str
    generated_at: datetime


class ChangesRead(Contract):
    story_id: UUID
    since_version: int
    current_version: int
    changes: tuple[ChangeEntry, ...] = Field(max_length=100)
    next_version: int | None
    history_complete: bool


async def timeline(
    database: AsyncSession,
    story_id: UUID,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    limit: int = 50,
) -> TimelineRead:
    if not 1 <= limit <= 100:
        raise ValueError("Invalid timeline limit")
    story = await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    view = await story_view(database, story, definitions, now=now)
    sources = sorted(
        view.sources,
        key=lambda item: (
            item.event_started_at or item.published_at,
            item.published_at,
            item.id.hex,
        ),
    )
    return TimelineRead(
        story_id=story_id,
        as_of=now,
        entries=tuple(
            TimelineEntry(
                item_id=item.id,
                reported_headline=item.headline,
                source_name=item.source_name,
                source_url=item.canonical_url,
                published_at=item.published_at,
                event_started_at=item.event_started_at,
                event_ended_at=item.event_ended_at,
                time_basis="event_time"
                if item.event_started_at is not None
                else "publication_time",
            )
            for item in sources[:limit]
        ),
    )


async def coverage(
    database: AsyncSession,
    story_id: UUID,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    limit: int = 50,
) -> CoverageRead:
    if not 1 <= limit <= 100:
        raise ValueError("Invalid coverage limit")
    story = await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    view = await story_view(database, story, definitions, now=now)
    claims = await claim_views(database, story, definitions, now=now)
    origins = dict(
        (
            await database.execute(
                select(NewsClaim.id, NewsClaim.origin_item_id).where(
                    NewsClaim.cluster_id == story.id,
                    NewsClaim.workspace_id == workspace_id,
                    NewsClaim.user_id == user_id,
                )
            )
        )
        .tuples()
        .all()
    )
    return CoverageRead(
        story_id=story_id,
        as_of=now,
        sources=tuple(
            CoverageSource(
                item_id=item.id,
                source_id=item.source_id,
                source_name=item.source_name,
                source_url=item.canonical_url,
                headline=item.headline,
                description=item.description,
                language=item.language,
                published_at=item.published_at,
                claims=tuple(claim for claim in claims if origins.get(claim.id) == item.id),
            )
            for item in view.sources[:limit]
        ),
    )


async def changes(
    database: AsyncSession,
    story_id: UUID,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    since_version: int = 0,
    limit: int = 50,
) -> ChangesRead:
    if not 0 <= since_version or not 1 <= limit <= 100:
        raise ValueError("Invalid change window")
    story = await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    await story_view(database, story, definitions, now=now)
    if since_version > story.version:
        raise ValueError("Requested version is newer than this story")
    rows = list(
        await database.scalars(
            select(NewsStoryVersion)
            .where(
                NewsStoryVersion.cluster_id == story.id,
                NewsStoryVersion.version > since_version,
                NewsStoryVersion.version <= story.version,
            )
            .order_by(NewsStoryVersion.version)
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    shown = rows[:limit]
    # Missing historical versions stay visible as incomplete history, not invented entries.
    complete = (
        not more
        and len(shown) == story.version - since_version
        and all(row.version == since_version + offset + 1 for offset, row in enumerate(shown))
    )
    return ChangesRead(
        story_id=story.id,
        since_version=since_version,
        current_version=story.version,
        changes=tuple(
            ChangeEntry(
                version=row.version,
                change_kind=row.change_kind,
                generated_at=stored_utc(row.generated_at),
            )
            for row in shown
        ),
        next_version=shown[-1].version if more and shown else None,
        history_complete=complete,
    )
