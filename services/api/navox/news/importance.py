"""Source-cited, explicitly reviewed public-importance inputs, separate from trend.

Only trusted application review may write an assessment. No model/public request
can set importance. Missing, expired or changed evidence yields no score; top feeds
fall back to recency unless every candidate has a current assessment.
"""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsStory, NewsStoryItem
from navox.news.contracts import Contract, NewsError, SourceDefinition, aware_utc
from navox.news.evidence import ClaimSpan, permitted_summary_item, quote_at

IMPORTANCE_VERSION: Literal["news-importance.reviewed-v1"] = "news-importance.reviewed-v1"
SignalName = Literal[
    "geographic_scope",
    "people_affected",
    "public_safety",
    "significance",
    "source_breadth",
    "duration",
]
_WEIGHTS: dict[str, float] = {
    "geographic_scope": 0.15,
    "people_affected": 0.2,
    "public_safety": 0.25,
    "significance": 0.2,
    "source_breadth": 0.1,
    "duration": 0.1,
}


class ImportanceSignal(Contract):
    name: SignalName
    # Reviewed ordinal scale: absent, limited, substantial, broad, exceptional.
    level: int = Field(ge=0, le=4, strict=True)
    evidence: ClaimSpan


class ImportanceReview(Contract):
    policy_version: Literal["news-importance.reviewed-v1"] = IMPORTANCE_VERSION
    story_id: UUID
    story_version: int = Field(ge=1)
    reference: str = Field(min_length=8, max_length=128)
    reviewed_at: datetime
    expires_at: datetime
    signals: tuple[ImportanceSignal, ...] = Field(min_length=6, max_length=6)
    source_fingerprints: dict[UUID, str] = Field(default_factory=dict, max_length=6)

    _aware = field_validator("reviewed_at", "expires_at")(aware_utc)

    @model_validator(mode="after")
    def complete_review(self) -> "ImportanceReview":
        if {signal.name for signal in self.signals} != set(_WEIGHTS):
            raise ValueError("Each importance dimension requires one explicit reviewed input")
        if not self.reviewed_at < self.expires_at <= self.reviewed_at + timedelta(days=7):
            raise ValueError("Importance reviews expire within seven days")
        return self

    @property
    def score(self) -> float:
        return sum(_WEIGHTS[signal.name] * signal.level / 4 for signal in self.signals)


async def _check_review(
    database: AsyncSession,
    story: NewsStory,
    review: ImportanceReview,
    definitions: dict[str, SourceDefinition],
    now: datetime,
) -> dict[UUID, str]:
    if (
        review.story_id != story.id
        or review.story_version != story.version
        or not review.reviewed_at <= now < review.expires_at
    ):
        raise NewsError("importance_unavailable")
    fingerprints: dict[UUID, str] = {}
    for signal in review.signals:
        span = signal.evidence
        member = await database.get(NewsStoryItem, span.item_id, populate_existing=True)
        if (
            member is None
            or member.cluster_id != story.id
            or member.item_revision != span.item_revision
        ):
            raise NewsError("importance_unavailable")
        _, item, source = await permitted_summary_item(
            database,
            span.item_id,
            definitions,
            workspace_id=story.workspace_id,
            user_id=story.user_id,
            now=now,
        )
        quote_at(item, span)
        fingerprints[source.id] = definitions[source.source_key].fingerprint
    return fingerprints


async def review_importance(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    review: ImportanceReview,
    definitions: dict[str, SourceDefinition],
    now: datetime,
) -> None:
    """Internal trusted review boundary, deliberately absent from model/API schemas.

    A reviewer attests the ordinal inputs and their exact source spans. The server
    binds current source policy fingerprints; submitted fingerprints confer no grant.
    """
    user = await database.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    membership = await database.get(
        WorkspaceMembership, (workspace_id, user_id), populate_existing=True
    )
    story = await database.scalar(
        select(NewsStory)
        .where(
            NewsStory.id == review.story_id,
            NewsStory.workspace_id == workspace_id,
            NewsStory.user_id == user_id,
            NewsStory.suppressed.is_(False),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.agent_paused or membership is None or story is None:
        raise NewsError("importance_unavailable")
    fingerprints = await _check_review(database, story, review, definitions, now)
    story.importance_review = review.model_copy(
        update={"source_fingerprints": fingerprints}
    ).model_dump(mode="json")
    await database.flush()


async def current_importance(
    database: AsyncSession,
    story: NewsStory,
    definitions: dict[str, SourceDefinition],
    *,
    now: datetime,
) -> float | None:
    if not story.importance_review:
        return None
    try:
        review = ImportanceReview.model_validate(story.importance_review)
        fingerprints = await _check_review(database, story, review, definitions, now)
        if fingerprints != review.source_fingerprints:
            return None
    except (ValueError, NewsError):
        return None
    return review.score
