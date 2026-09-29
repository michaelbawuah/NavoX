"""Explicit, bounded question options; never inferred from source instructions."""

from uuid import UUID

from pydantic import Field, model_validator

from navox.news.contracts import Contract
from navox.news.retrieval import Depth, Freshness, NewsIntent, RetrievalPlan


class Question(Contract):
    request_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    intent: NewsIntent = NewsIntent.CURRENT_NEWS
    freshness: Freshness | None = None
    depth: Depth = Depth.STANDARD

    @property
    def plan(self) -> RetrievalPlan:
        default = (
            Freshness.RECENT
            if self.intent in {NewsIntent.TIMELINE, NewsIntent.BACKGROUND}
            else Freshness.FRESH
        )
        return RetrievalPlan(
            intent=self.intent, freshness=self.freshness or default, depth=self.depth
        )

    @model_validator(mode="after")
    def valid_plan(self) -> "Question":
        _ = self.plan
        return self
