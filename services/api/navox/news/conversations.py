"""Bounded, owned news conversations. Answers never confer action authority."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied, reject_credentials
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.features import configured_secrets
from navox.ai.runtime import GatewayUnavailable
from navox.core.settings import Settings
from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsClaim, NewsConversation, NewsConversationTurn, NewsItem, NewsStoryItem
from navox.news.ai_context import NewsContext, run_news_task
from navox.news.ai_contracts import CONVERSATION, NewsSelection
from navox.news.clustering import search_terms
from navox.news.contracts import Contract, NewsError, Verification, stored_utc
from navox.news.evidence import evaluate_claim, permitted_summary_item, quote_at
from navox.news.registry import catalog
from navox.news.stories import owned_story

Intent = Literal[
    "CURRENT_NEWS", "STORY_QUESTION", "VERIFY_CLAIM", "TIMELINE", "BACKGROUND", "WHATS_CHANGED"
]


class Question(Contract):
    request_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    intent: Intent = "CURRENT_NEWS"


class AnswerFact(Contract):
    text: str
    source_name: str
    source_url: str
    source_id: UUID
    status: Verification


class AnswerRead(Contract):
    id: UUID
    sequence: int
    question: str
    status: Literal["PROCESSING", "READY", "UNAVAILABLE", "SOURCES_CHANGED"]
    message: str
    facts: tuple[AnswerFact, ...] = ()
    as_of: datetime
    actions_executed: Literal[False] = False


async def owned_conversation(
    database: AsyncSession,
    identifier: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
    lock: bool = False,
) -> NewsConversation:
    query = select(NewsConversation).where(
        NewsConversation.id == identifier,
        NewsConversation.workspace_id == workspace_id,
        NewsConversation.user_id == user_id,
        NewsConversation.expires_at > now,
    )
    if lock:
        query = query.with_for_update()
    conversation = await database.scalar(query)
    if conversation is None:
        raise NewsError("conversation_unavailable")
    return conversation


async def new_conversation(
    database: AsyncSession,
    *,
    workspace_id: UUID,
    user_id: UUID,
    story_id: UUID | None,
    now: datetime,
) -> NewsConversation:
    if story_id is not None:
        await owned_story(database, story_id, workspace_id=workspace_id, user_id=user_id)
    row = NewsConversation(
        workspace_id=workspace_id,
        user_id=user_id,
        story_id=story_id,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    database.add(row)
    await database.flush()
    return row


async def begin_question(
    database: AsyncSession,
    settings: Settings,
    identifier: UUID,
    question: Question,
    *,
    workspace_id: UUID,
    user_id: UUID,
    now: datetime,
) -> tuple[NewsConversationTurn, bool]:
    if not settings.news_feed_enabled or not settings.news_chat_enabled:
        raise NewsError("news_disabled")
    reject_credentials(question.question, configured_secrets(settings))
    user = await database.scalar(select(User).where(User.id == user_id).with_for_update())
    if (
        user is None
        or user.agent_paused
        or await database.get(WorkspaceMembership, (workspace_id, user_id)) is None
    ):
        raise NewsError("conversation_unavailable")
    conversation = await owned_conversation(
        database, identifier, workspace_id=workspace_id, user_id=user_id, now=now, lock=True
    )
    existing = await database.scalar(
        select(NewsConversationTurn).where(
            NewsConversationTurn.conversation_id == identifier,
            NewsConversationTurn.request_id == question.request_id,
        )
    )
    if existing is not None:
        if existing.question != question.question or existing.intent != question.intent:
            raise NewsError("source_changed")
        return existing, False
    pending = await database.scalar(
        select(NewsConversationTurn).where(
            NewsConversationTurn.conversation_id == identifier,
            NewsConversationTurn.status == "PROCESSING",
        )
    )
    if pending is not None:
        raise NewsError("rate_limited")
    count = await database.scalar(
        select(func.count())
        .select_from(NewsConversationTurn)
        .join(NewsConversation)
        .where(
            NewsConversation.user_id == user_id,
            NewsConversation.workspace_id == workspace_id,
            NewsConversationTurn.created_at >= now - timedelta(hours=1),
        )
    )
    if (count or 0) >= 12:
        raise NewsError("rate_limited")
    sequence = await database.scalar(
        select(func.max(NewsConversationTurn.sequence)).where(
            NewsConversationTurn.conversation_id == identifier
        )
    )
    row = NewsConversationTurn(
        conversation_id=identifier,
        request_id=question.request_id,
        sequence=(sequence or 0) + 1,
        question=question.question,
        intent=question.intent,
        status="PROCESSING",
        source_snapshot={},
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    conversation.expires_at = row.expires_at
    database.add(row)
    await database.flush()
    return row, True


async def retrieve(
    database: AsyncSession,
    conversation: NewsConversation,
    turn: NewsConversationTurn,
    *,
    now: datetime,
) -> tuple[tuple[UUID, ...], tuple[str, ...]]:
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
    previous_ids = list(prior[0].source_snapshot) if prior else []
    query = select(NewsItem).where(
        NewsItem.workspace_id == conversation.workspace_id,
        NewsItem.user_id == conversation.user_id,
        NewsItem.expires_at > now,
    )
    if conversation.story_id is not None:
        query = query.join(NewsStoryItem, NewsStoryItem.news_item_id == NewsItem.id).where(
            NewsStoryItem.cluster_id == conversation.story_id
        )
    rows = list(
        await database.scalars(query.order_by(NewsItem.published_at.desc(), NewsItem.id).limit(200))
    )
    terms = search_terms(turn.question)
    rows.sort(
        key=lambda item: (
            str(item.id) in previous_ids,
            len(search_terms(item.headline + " " + (item.description or "")) & terms),
            stored_utc(item.published_at),
        ),
        reverse=True,
    )
    return tuple(item.id for item in rows[:12]), tuple(item.question for item in reversed(prior))


async def finish_question(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    turn_id: UUID,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> None:
    async with factory() as database:
        turn = await database.get(NewsConversationTurn, turn_id)
        if turn is None or turn.status != "PROCESSING":
            return
        conversation = await owned_conversation(
            database,
            turn.conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=datetime.now(UTC),
        )
        item_ids, previous = await retrieve(database, conversation, turn, now=datetime.now(UTC))
        question, intent = turn.question, turn.intent
    result = None
    selection = None
    snapshot_digest = None
    snapshot: dict[str, int] = {}
    failure = "ai_unavailable"
    try:
        if not item_ids:
            raise NewsError("item_unavailable")
        context = NewsContext(
            factory,
            settings,
            workspace_id=workspace_id,
            user_id=user_id,
            item_ids=item_ids,
            question=question,
            previous_questions=previous,
            freshness_seconds=86400 if intent in {"TIMELINE", "BACKGROUND"} else 1800,
        )
        runtime = await build_runtime(settings, factory)
        result, proposal, snapshot_digest = await run_news_task(runtime, context, CONVERSATION)
        if not isinstance(proposal, NewsSelection) or context.initial is None:
            raise NewsError("invalid_evidence")
        selection = proposal.model_dump(mode="json")
        snapshot = {str(item.id): item.revision for item in context.initial.items}
    except (
        ContextDenied,
        AIProviderNotConfigured,
        GatewayUnavailable,
        PermissionError,
        ValueError,
    ):
        # Detailed provider/source text never becomes a durable conversation error.
        pass
    async with factory() as database:
        turn = await database.get(NewsConversationTurn, turn_id)
        if turn is None or turn.status != "PROCESSING":
            return
        await owned_conversation(
            database,
            turn.conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=datetime.now(UTC),
            lock=True,
        )
        turn.status = "READY" if result is not None and selection is not None else "UNAVAILABLE"
        turn.selection, turn.source_snapshot, turn.context_digest = (
            selection,
            snapshot,
            snapshot_digest,
        )
        turn.trace_id, turn.failure_code = (
            result.trace_id if result else None,
            None if selection is not None else failure,
        )
        await database.commit()


async def answer_view(
    database: AsyncSession,
    conversation: NewsConversation,
    turn: NewsConversationTurn,
    settings: Settings,
    *,
    now: datetime,
) -> AnswerRead:
    base = dict(
        id=turn.id,
        sequence=turn.sequence,
        question=turn.question,
        as_of=stored_utc(turn.created_at),
    )
    if turn.conversation_id != conversation.id or stored_utc(turn.expires_at) <= now:
        raise NewsError("conversation_unavailable")
    if turn.status != "READY" or turn.selection is None:
        return AnswerRead.model_validate(
            base
            | dict(
                status="PROCESSING" if turn.status == "PROCESSING" else "UNAVAILABLE",
                message="Checking the available sources…"
                if turn.status == "PROCESSING"
                else "A supported answer is unavailable. Try again after your sources refresh.",
            )
        )
    try:
        age_limit = (
            timedelta(days=1)
            if turn.intent in {"TIMELINE", "BACKGROUND"}
            else timedelta(minutes=30)
        )
        if now - stored_utc(turn.created_at) > age_limit:
            raise NewsError("stale_evidence")
        definitions, items = catalog(settings), {}
        for identifier, revision in turn.source_snapshot.items():
            _, item, _ = await permitted_summary_item(
                database,
                UUID(identifier),
                definitions,
                workspace_id=conversation.workspace_id,
                user_id=conversation.user_id,
                now=now,
            )
            if item.revision != revision:
                raise NewsError("stale_evidence")
            items[item.id] = item
        selection = NewsSelection.model_validate(turn.selection)
        facts = []
        for claim_id in selection.claim_ids:
            claim = await database.scalar(
                select(NewsClaim).where(
                    NewsClaim.id == claim_id,
                    NewsClaim.workspace_id == conversation.workspace_id,
                    NewsClaim.user_id == conversation.user_id,
                )
            )
            if claim is None or claim.origin_item_id not in items:
                raise NewsError("stale_evidence")
            view = await evaluate_claim(database, claim, definitions, now=now)
            source = items[claim.origin_item_id]
            facts.append(
                AnswerFact(
                    text=view.text,
                    source_name=source.source_name,
                    source_url=source.canonical_url,
                    source_id=source.source_id,
                    status=view.status,
                )
            )
        for excerpt in selection.excerpts:
            source = items[excerpt.item_id]
            facts.append(
                AnswerFact(
                    text=quote_at(source, excerpt),
                    source_name=source.source_name,
                    source_url=source.canonical_url,
                    source_id=source.source_id,
                    status=Verification.ATTRIBUTED,
                )
            )
        message = (
            "The available sources do not fully answer this question."
            if selection.insufficient_context
            else "Here is what the sources report."
        )
        unique = {(fact.source_id, fact.text, fact.status): fact for fact in facts}
        return AnswerRead.model_validate(
            base | dict(status="READY", message=message, facts=tuple(unique.values()))
        )
    except (NewsError, ValueError, KeyError):
        return AnswerRead.model_validate(
            base
            | dict(
                status="SOURCES_CHANGED",
                message="Sources changed. Ask again for an updated answer.",
            )
        )
