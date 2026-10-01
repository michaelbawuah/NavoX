"""Current permission-checked retrieval and explicit prior-answer reference resolution."""

from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.news import NewsConversation, NewsConversationTurn
from navox.news.contracts import NewsError, stored_utc
from navox.news.jobs import NewsSourceWork
from navox.news.registry import catalog
from navox.news.retrieval import (
    Freshness,
    NewsIntent,
    RetrievalPlan,
    RetrievalResult,
    numbered_reference,
    select_evidence,
)


def turn_plan(turn: NewsConversationTurn) -> RetrievalPlan:
    if turn.retrieval_plan is not None:
        return RetrievalPlan.model_validate(turn.retrieval_plan)
    return RetrievalPlan(
        intent=NewsIntent(turn.intent),
        freshness=(
            Freshness.RECENT if turn.intent in {"TIMELINE", "BACKGROUND"} else Freshness.FRESH
        ),
    )


async def retrieve_for_turn(
    database: AsyncSession,
    conversation: NewsConversation,
    turn: NewsConversationTurn,
    settings: Settings,
    *,
    now: datetime,
) -> tuple[RetrievalResult, tuple[str, ...]]:
    from navox.news.conversations import answer_view

    if turn.conversation_id != conversation.id:
        raise NewsError("conversation_unavailable")
    if not settings.news_feed_enabled or not settings.news_chat_enabled:
        raise NewsError("news_disabled")
    plan = turn_plan(turn)
    if plan.intent == NewsIntent.X_TRENDS:
        raise NewsError("news_unavailable")
    if plan.intent == NewsIntent.DEEP_RESEARCH and not settings.news_deep_research_enabled:
        raise NewsError("news_disabled")
    if (
        plan.intent == NewsIntent.COVERAGE_COMPARISON
        and not settings.news_coverage_comparison_enabled
    ):
        raise NewsError("news_disabled")
    prior = list(
        await database.scalars(
            select(NewsConversationTurn)
            .where(
                NewsConversationTurn.conversation_id == conversation.id,
                NewsConversationTurn.sequence < turn.sequence,
                NewsConversationTurn.expires_at > now,
            )
            .order_by(NewsConversationTurn.sequence.desc())
            .limit(4)
        )
    )
    reference = numbered_reference(turn.question)
    previous_ids: tuple[UUID, ...] = ()
    target = None
    if prior:
        previous = await answer_view(database, conversation, prior[0], settings, now=now)
        if previous.status == "READY":
            # Follow the displayed fact order, never a database JSON object's key order.
            previous_ids = tuple(fact.item_id for fact in previous.facts)
    if reference is not None:
        if reference > len(previous_ids):
            raise NewsError("invalid_reference")
        target = previous_ids[reference - 1]
    selected = await select_evidence(
        database,
        catalog(settings),
        workspace_id=conversation.workspace_id,
        user_id=conversation.user_id,
        question=turn.question,
        plan=plan,
        now=now,
        story_id=conversation.story_id,
        reference_item_id=target,
        previous_item_ids=tuple(dict.fromkeys(previous_ids)),
    )
    return selected, tuple(row.question for row in reversed(prior))


async def refresh_work_for_turn(
    database: AsyncSession,
    settings: Settings,
    turn_id: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> list[NewsSourceWork]:
    from navox.news.conversations import owned_conversation

    turn = await database.get(NewsConversationTurn, turn_id)
    if turn is None or turn.status != "PROCESSING" or turn.processing_started_at is not None:
        return []
    if stored_utc(turn.expires_at) <= now:
        return []
    conversation = await owned_conversation(
        database, turn.conversation_id, workspace_id=workspace_id, user_id=user_id, now=now
    )
    selected, _ = await retrieve_for_turn(database, conversation, turn, settings, now=now)
    return [
        NewsSourceWork(
            str(source_id),
            str(workspace_id),
            str(user_id),
            str(uuid5(turn_id, "news-refresh:" + str(source_id))),
        )
        for source_id in selected.refresh_source_ids
    ]
