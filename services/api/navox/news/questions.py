"""Bounded plans from the user's question, never from source instructions."""

import re
from uuid import UUID

from pydantic import Field, model_validator

from navox.news.contracts import Contract
from navox.news.research_planning import research_phrase
from navox.news.retrieval import Depth, Freshness, NewsIntent, RetrievalPlan


def infer_intent(question: str) -> NewsIntent:
    """Recognize narrow request phrases; topic words alone do not choose a mode."""
    text = " ".join(question.casefold().replace("’", "'").split())
    research_intent, _ = research_phrase(question)
    if research_intent is not None:
        return NewsIntent(research_intent)
    trend_request = re.search(
        r"\b(?:what(?:'s| is| are)? (?:currently )?trending|"
        r"(?:show|tell|give) me (?:what(?:'s| is) |the )?trending|"
        r"trending (?:news|stories|topics)|what are people talking about)\b",
        text,
    )
    if trend_request:
        if re.search(r"\b(?:on|from) (?:x|twitter)\b", text):
            return NewsIntent.X_TRENDS
        return NewsIntent.TRENDING
    return NewsIntent.CURRENT_NEWS


class Question(Contract):
    request_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    intent: NewsIntent | None = None
    freshness: Freshness | None = None
    depth: Depth = Depth.STANDARD

    @property
    def plan(self) -> RetrievalPlan:
        intent = self.intent or infer_intent(self.question)
        default = (
            Freshness.RECENT
            if intent in {NewsIntent.TIMELINE, NewsIntent.BACKGROUND}
            else Freshness.FRESH
        )
        return RetrievalPlan(intent=intent, freshness=self.freshness or default, depth=self.depth)

    @model_validator(mode="after")
    def valid_plan(self) -> "Question":
        _ = self.plan
        return self
