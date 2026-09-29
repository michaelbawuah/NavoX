"""Explicit in-app follow updates. Reads never record reading history or send messages."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.news import NewsStory, NewsStoryPreference, NewsStoryVersion
from navox.news.contracts import Contract, NewsError, SourceDefinition, Verification, stored_utc
from navox.news.stories import StoryRead, owned_story, story_view

HISTORY_LIMIT = 50
SIGNIFICANT_STATES = frozenset(
    {
        Verification.VERIFIED,
        Verification.CORROBORATED,
        Verification.DISPUTED,
        Verification.CONTRADICTED,
        Verification.RETRACTED,
    }
)


class FollowEvent(Contract):
    version: int
    generated_at: datetime
    kind: Literal["EVIDENCE_ASSESSMENT_CHANGED"] = "EVIDENCE_ASSESSMENT_CHANGED"
    description: str = "A material claim's evidence assessment changed."


class FollowedUpdate(Contract):
    story: StoryRead
    since_version: int | None
    through_version: int
    events: tuple[FollowEvent, ...] = Field(default=(), max_length=50)
    history_complete: bool
    baseline_required: bool = False


class FollowingPage(Contract):
    entries: tuple[FollowedUpdate, ...] = Field(max_length=25)
    next_story_id: UUID | None
    as_of: datetime
    scope: Literal["followed_stories"] = "followed_stories"
    external_notifications_sent: Literal[False] = False


class SeenReceipt(Contract):
    story_id: UUID
    acknowledged_version: int
    current_version: int


def material_change(before: dict[str, str], after: dict[str, str]) -> bool:
    """Count evidence transitions, not copies, title edits, popularity or source volume."""
    if any(value not in Verification for value in (*before.values(), *after.values())):
        raise NewsError("invalid_evidence")
    return any(
        before.get(key) != after.get(key)
        and (before.get(key) in SIGNIFICANT_STATES or after.get(key) in SIGNIFICANT_STATES)
        for key in before.keys() | after.keys()
    )


async def follow_update(
    database: AsyncSession,
    story: NewsStory,
    preference: NewsStoryPreference,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> FollowedUpdate:
    if (
        (preference.cluster_id, preference.workspace_id, preference.user_id)
        != (story.id, story.workspace_id, story.user_id)
        or not preference.followed
        or preference.dismissed
    ):
        raise NewsError("source_changed")
    view = await story_view(database, story, definitions, now=now)
    baseline = preference.last_read_version
    if baseline is None:
        # Legacy follows have no known starting point. Never fabricate an earlier visit.
        return FollowedUpdate(
            story=view,
            since_version=None,
            through_version=story.version,
            history_complete=False,
            baseline_required=True,
        )
    if baseline < 1 or baseline > story.version:
        raise NewsError("source_changed")
    before = await database.scalar(
        select(NewsStoryVersion).where(
            NewsStoryVersion.cluster_id == story.id,
            NewsStoryVersion.version == baseline,
        )
    )
    versions = list(
        await database.scalars(
            select(NewsStoryVersion)
            .where(
                NewsStoryVersion.cluster_id == story.id,
                NewsStoryVersion.version > baseline,
                NewsStoryVersion.version <= story.version,
            )
            .order_by(NewsStoryVersion.version)
            .limit(HISTORY_LIMIT + 1)
        )
    )
    events: list[FollowEvent] = []
    through = baseline
    contiguous = before is not None
    previous = before.claim_states if before is not None else {}
    for version in versions[:HISTORY_LIMIT]:
        if not contiguous or version.version != through + 1:
            contiguous = False
            break
        try:
            changed = material_change(previous, version.claim_states)
        except NewsError:
            contiguous = False
            break
        if changed:
            events.append(
                FollowEvent(
                    version=version.version,
                    generated_at=stored_utc(version.generated_at),
                )
            )
        through = version.version
        previous = version.claim_states
    complete = contiguous and through == story.version and len(versions) <= HISTORY_LIMIT
    return FollowedUpdate(
        story=view,
        since_version=baseline,
        through_version=through,
        events=tuple(events),
        history_complete=complete,
    )


async def following_updates(
    database: AsyncSession,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    after_story_id: UUID | None = None,
    limit: int = 10,
) -> FollowingPage:
    if not 1 <= limit <= 25:
        raise ValueError("Choose a bounded following page size")
    query = (
        select(NewsStory)
        .join(NewsStoryPreference, NewsStoryPreference.cluster_id == NewsStory.id)
        .where(
            NewsStory.workspace_id == workspace_id,
            NewsStory.user_id == user_id,
            NewsStory.suppressed.is_(False),
            NewsStoryPreference.workspace_id == workspace_id,
            NewsStoryPreference.user_id == user_id,
            NewsStoryPreference.followed.is_(True),
            NewsStoryPreference.dismissed.is_(False),
        )
    )
    if after_story_id is not None:
        query = query.where(NewsStory.id > after_story_id)
    rows = list(await database.scalars(query.order_by(NewsStory.id).limit(limit + 1)))
    entries: list[FollowedUpdate] = []
    for story in rows[:limit]:
        preference = await database.get(NewsStoryPreference, story.id, populate_existing=True)
        if preference is None:
            continue
        try:
            entry = await follow_update(database, story, preference, definitions, now=now)
        except NewsError:
            continue
        if entry.events or not entry.history_complete or entry.baseline_required:
            entries.append(entry)
    # Advance by scanned story, not last emitted alert: quiet rows cannot starve later ones.
    return FollowingPage(
        entries=tuple(entries),
        as_of=now,
        next_story_id=rows[limit - 1].id if len(rows) > limit else None,
    )


async def acknowledge_updates(
    database: AsyncSession,
    story_id: UUID,
    definitions: dict[str, SourceDefinition],
    *,
    workspace_id: UUID,
    user_id: UUID,
    observed_version: int,
    now: datetime,
) -> SeenReceipt:
    if isinstance(observed_version, bool) or not isinstance(observed_version, int):
        raise ValueError("A story version is required")
    story = await owned_story(
        database, story_id, workspace_id=workspace_id, user_id=user_id, lock=True
    )
    await story_view(database, story, definitions, now=now)
    preference = await database.scalar(
        select(NewsStoryPreference)
        .where(
            NewsStoryPreference.cluster_id == story_id,
            NewsStoryPreference.workspace_id == workspace_id,
            NewsStoryPreference.user_id == user_id,
            NewsStoryPreference.followed.is_(True),
            NewsStoryPreference.dismissed.is_(False),
        )
        .execution_options(populate_existing=True)
    )
    if preference is None or not 1 <= observed_version <= story.version:
        raise NewsError("source_changed")
    version = await database.scalar(
        select(NewsStoryVersion.id).where(
            NewsStoryVersion.cluster_id == story_id,
            NewsStoryVersion.version == observed_version,
        )
    )
    if version is None:
        raise NewsError("source_changed")
    baseline = preference.last_read_version
    if baseline is not None:
        if not 1 <= baseline <= story.version:
            raise NewsError("source_changed")
        if observed_version > baseline:
            shown = await follow_update(database, story, preference, definitions, now=now)
            if observed_version > shown.through_version:
                raise NewsError("source_changed")
    # A delayed acknowledgement never marks a newer, unseen version as read or goes backward.
    preference.last_read_version = max(preference.last_read_version or 0, observed_version)
    await database.flush()
    return SeenReceipt(
        story_id=story_id,
        acknowledged_version=preference.last_read_version,
        current_version=story.version,
    )
