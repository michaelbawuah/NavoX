"""Reauthorize the exact owned news snapshot before and after every provider request."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.context import ContextDenied, MinimizedContext, reject_credentials
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import (
    AIResult,
    AITask,
    Capability,
    JSONDocument,
    LatencyClass,
    Profile,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.foundation.persistence import digest
from navox.ai.runtime import GatewayRuntime
from navox.core.settings import Settings
from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsClaim, NewsItem, NewsSource, NewsStoryItem
from navox.intelligence.contracts import SourceDocument
from navox.news.ai_contracts import (
    CONVERSATION,
    EXTRACTION,
    SYNTHESIS,
    ClaimExtraction,
    NewsContextInput,
    NewsSelection,
    NewsSynthesis,
    validate_news_output,
)
from navox.news.contracts import NewsError
from navox.news.evidence import evaluate_claim, permitted_summary_item
from navox.news.registry import catalog
from navox.news.stories import owned_story


class NewsContext:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        workspace_id: UUID,
        user_id: UUID,
        item_ids: tuple[UUID, ...],
        question: str,
        previous_questions: tuple[str, ...] = (),
        freshness_seconds: int = 1800,
        mode: Literal["conversation", "intelligence"] = "conversation",
        story_id: UUID | None = None,
    ) -> None:
        if not 1 <= len(item_ids) <= 12 or len(set(item_ids)) != len(item_ids):
            raise ContextDenied("News evidence selection is unavailable")
        if not 60 <= freshness_seconds <= 86400 * 365:
            raise ContextDenied("Invalid news freshness requirement")
        if mode not in {"conversation", "intelligence"} or (
            mode == "intelligence" and story_id is None
        ):
            raise ContextDenied("Invalid news task scope")
        self.mode, self.story_id = mode, story_id
        self.factory, self.settings = factory, settings
        self.workspace_id, self.user_id, self.item_ids = workspace_id, user_id, item_ids
        self.question, self.previous_questions = question, previous_questions
        self.freshness_seconds = freshness_seconds
        self.as_of = datetime.now(UTC)
        self.initial: NewsContextInput | None = None

    async def prepare(self) -> NewsContextInput:
        self.initial = await self._read()
        return self.initial

    async def _read(self) -> NewsContextInput:
        async with self.factory() as database:
            return await self.read_in_session(database)

    async def read_in_session(
        self, database: AsyncSession, *, lock: bool = False
    ) -> NewsContextInput:
        """Reauthorize publication in the same transaction as its writes."""
        enabled = (
            self.settings.news_chat_enabled
            if self.mode == "conversation"
            else self.settings.news_intelligence_enabled
        )
        if not self.settings.news_feed_enabled or not enabled:
            raise ContextDenied("News intelligence is unavailable")
        definitions, now = catalog(self.settings), datetime.now(UTC)
        if lock:
            source_ids = list(
                await database.scalars(
                    select(NewsItem.source_id).where(
                        NewsItem.id.in_(self.item_ids),
                        NewsItem.workspace_id == self.workspace_id,
                        NewsItem.user_id == self.user_id,
                    )
                )
            )
            await database.execute(
                select(NewsSource)
                .where(NewsSource.id.in_(source_ids))
                .order_by(NewsSource.id)
                .with_for_update()
            )
        user_query = (
            select(User).where(User.id == self.user_id).execution_options(populate_existing=True)
        )
        if lock:
            user_query = user_query.with_for_update()
        user = await database.scalar(user_query)
        member = await database.get(WorkspaceMembership, (self.workspace_id, self.user_id))
        if user is None or member is None or user.agent_paused:
            raise ContextDenied("News intelligence is paused")
        if self.story_id is not None:
            try:
                await owned_story(
                    database,
                    self.story_id,
                    workspace_id=self.workspace_id,
                    user_id=self.user_id,
                    lock=lock,
                )
            except NewsError:
                raise ContextDenied("News story changed") from None
        items, claims = [], []
        cluster_ids = set()
        for identifier in self.item_ids:
            try:
                _, view, _ = await permitted_summary_item(
                    database,
                    identifier,
                    definitions,
                    workspace_id=self.workspace_id,
                    user_id=self.user_id,
                    now=now,
                )
            except NewsError:
                raise ContextDenied("News evidence permissions changed") from None
            if view.last_observed_at < now - timedelta(seconds=self.freshness_seconds):
                raise ContextDenied("News evidence requires a fresh source check")
            items.append(view)
            membership = await database.get(NewsStoryItem, identifier)
            if self.story_id is not None and (
                membership is None
                or membership.cluster_id != self.story_id
                or membership.item_revision != view.revision
            ):
                raise ContextDenied("News story membership changed")
            if membership is not None:
                cluster_ids.add(membership.cluster_id)
        # Every selected claim must originate in the explicitly selected item set.
        rows = await database.scalars(
            select(NewsClaim)
            .where(
                NewsClaim.workspace_id == self.workspace_id,
                NewsClaim.user_id == self.user_id,
                NewsClaim.cluster_id.in_(cluster_ids),
                NewsClaim.origin_item_id.in_(self.item_ids),
            )
            .order_by(NewsClaim.first_seen_at, NewsClaim.id)
            .limit(40)
        )
        for row in rows:
            try:
                claims.append(
                    await evaluate_claim(
                        database,
                        row,
                        definitions,
                        now=now,
                        freshness_seconds=self.freshness_seconds,
                    )
                )
            except NewsError:
                continue
        context = NewsContextInput(
            question=self.question,
            previous_questions=self.previous_questions,
            items=tuple(items),
            claims=tuple(claims),
            as_of=self.as_of,
        )
        reject_credentials(context.model_dump_json(), configured_secrets(self.settings))
        return context

    async def build(
        self, task: AITask, documents: Mapping[UUID, SourceDocument], *, user_request: str = ""
    ) -> MinimizedContext:
        if (
            (task.workspace_id, task.user_id) != (self.workspace_id, self.user_id)
            or task.context_references
            or documents
            or user_request
            or task.sensitivity != Sensitivity.PERSONAL
        ):
            raise ContextDenied("News context scope mismatch")
        current = await self._read()
        if self.initial is None or self.initial != current:
            raise ContextDenied("News changed during this request; refresh and ask again")
        return MinimizedContext(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            source_ids=self.item_ids,
            sensitivity=Sensitivity.PERSONAL,
            content=JSONDocument(
                text=json.dumps({"news_context": current.model_dump(mode="json")})
            ),
        )


async def run_news_task(
    runtime: GatewayRuntime,
    context: NewsContext,
    reference: VersionedRef,
    *,
    max_cost: Decimal = Decimal("0.05"),
) -> tuple[AIResult, ClaimExtraction | NewsSelection | NewsSynthesis, str]:
    if reference not in {EXTRACTION, CONVERSATION, SYNTHESIS}:
        raise ValueError("Unknown news task")
    if (reference == CONVERSATION) != (context.mode == "conversation"):
        raise ContextDenied("News task mode mismatch")
    snapshot = await context.prepare()
    ceiling = runtime.store.operator_policy
    task = AITask.model_validate(
        dict(
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            profile=Profile.NEWS_SYNTHESIS,
            task_type=TaskType.SUMMARIZE if reference == SYNTHESIS else TaskType.REASON,
            prompt=reference,
            output_schema=reference,
            capability_requirements={Capability.TEXT, Capability.STRUCTURED_OUTPUT},
            sensitivity=Sensitivity.PERSONAL,
            latency_class=LatencyClass.BACKGROUND
            if context.mode == "intelligence"
            else LatencyClass.INTERACTIVE,
            quality_class=QualityClass.HIGH,
            max_cost=min(max_cost, ceiling.max_cost),
            max_output_tokens=2000,
            provider_policy=ProviderPolicy(
                workspace_id=context.workspace_id,
                user_id=context.user_id,
                revision=1,
                grants=ceiling.grants,
                allow_fallback=ceiling.allow_fallback,
                max_fallbacks=ceiling.max_fallbacks if ceiling.allow_fallback else 0,
            ),
        )
    )

    def semantic(value: Any) -> None:
        validate_news_output(reference, value, snapshot)

    result = await runtime.execute(
        task, context_builder=context, documents={}, semantic_validator=semantic
    )
    selection = validate_news_output(reference, json.loads(result.output.text), snapshot)
    return result, selection, digest(snapshot.model_dump_json())
