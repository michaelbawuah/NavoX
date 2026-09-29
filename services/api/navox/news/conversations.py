"""Bounded, owned news conversations. Answers never confer action authority."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied, reject_credentials
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.features import configured_secrets
from navox.ai.runtime import GatewayUnavailable
from navox.core.settings import Settings
from navox.db.models import User, WorkspaceMembership
from navox.db.news import NewsClaim, NewsConversation, NewsConversationTurn, NewsStoryItem
from navox.news.ai_context import NewsContext, run_news_task
from navox.news.ai_contracts import CONVERSATION, NewsSelection
from navox.news.contracts import Contract, NewsError, Verification, stored_utc
from navox.news.conversation_retrieval import retrieve_for_turn, turn_plan
from navox.news.evidence import evaluate_claim, permitted_summary_item, quote_at
from navox.news.questions import Question as Question
from navox.news.registry import catalog
from navox.news.retrieval import Freshness
from navox.news.stories import owned_story


class AnswerFact(Contract):
    item_id: UUID
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
    retrieval_limited: bool = True
    source_scope: Literal["owned_permitted_items"] = "owned_permitted_items"
    freshness: Freshness = Freshness.FRESH


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
        if existing.question != question.question or turn_plan(existing) != question.plan:
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
        retrieval_plan=question.plan.model_dump(mode="json"),
        retrieval_metadata={},
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    conversation.expires_at = row.expires_at
    database.add(row)
    await database.flush()
    return row, True


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
        if (
            turn is None
            or turn.status != "PROCESSING"
            or stored_utc(turn.expires_at) <= datetime.now(UTC)
        ):
            return
        conversation = await owned_conversation(
            database,
            turn.conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            now=datetime.now(UTC),
            lock=True,
        )
        await database.refresh(turn)
        if turn.status != "PROCESSING" or turn.processing_started_at is not None:
            return
        # Reserve exactly one model attempt even when a workflow is delivered twice.
        reserved = await database.scalar(
            update(NewsConversationTurn)
            .where(
                NewsConversationTurn.id == turn.id,
                NewsConversationTurn.processing_started_at.is_(None),
                NewsConversationTurn.status == "PROCESSING",
            )
            .values(processing_started_at=datetime.now(UTC))
            .returning(NewsConversationTurn.id)
        )
        if reserved is None:
            return
        await database.commit()
    result = None
    selection = None
    snapshot_digest = None
    context: NewsContext | None = None
    snapshot: dict[str, int] = {}
    metadata: dict[str, object] = {}
    failure = "ai_unavailable"
    try:
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
            retrieved, previous = await retrieve_for_turn(
                database, conversation, turn, settings, now=datetime.now(UTC)
            )
            question, plan, story_id = turn.question, turn_plan(turn), conversation.story_id
        metadata = {
            "examined": retrieved.examined,
            "limited": retrieved.limited,
            "scope": retrieved.scope,
            "resolved_reference": retrieved.resolved_reference,
            "ordered_item_ids": [str(item.id) for item in retrieved.items],
        }
        if not retrieved.items:
            raise NewsError(
                "stale_evidence" if retrieved.refresh_source_ids else "item_unavailable"
            )
        context = NewsContext(
            factory,
            settings,
            workspace_id=workspace_id,
            user_id=user_id,
            item_ids=tuple(item.id for item in retrieved.items),
            question=question,
            previous_questions=previous,
            freshness_seconds=plan.freshness_seconds,
            story_id=story_id,
        )
        runtime = await build_runtime(settings, factory)
        result, proposal, snapshot_digest = await run_news_task(runtime, context, CONVERSATION)
        if not isinstance(proposal, NewsSelection) or context.initial is None:
            raise NewsError("invalid_evidence")
        selection = proposal.model_dump(mode="json")
        snapshot = {str(item.id): item.revision for item in context.initial.items}
    except NewsError as error:
        failure = error.code
        selection = None
    except (
        ContextDenied,
        AIProviderNotConfigured,
        GatewayUnavailable,
        PermissionError,
        ValueError,
    ):
        selection = None
    async with factory() as database:
        if context is not None and selection is not None:
            try:
                current = await context.read_in_session(database, lock=True)
                if context.initial is None or current != context.initial:
                    raise NewsError("source_changed")
            except (ContextDenied, NewsError, ValueError):
                selection, failure = None, "source_changed"
        turn = await database.get(NewsConversationTurn, turn_id, populate_existing=True)
        if (
            turn is None
            or turn.status != "PROCESSING"
            or stored_utc(turn.expires_at) <= datetime.now(UTC)
        ):
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
        turn.selection = selection
        turn.source_snapshot = snapshot if selection is not None else {}
        turn.context_digest = snapshot_digest if selection is not None else None
        turn.retrieval_metadata = metadata
        turn.trace_id = result.trace_id if result else None
        turn.failure_code = None if selection is not None else failure
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
        retrieval_limited=bool((turn.retrieval_metadata or {}).get("limited", True)),
        freshness=turn_plan(turn).freshness,
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
        age_limit = timedelta(seconds=turn_plan(turn).freshness_seconds)
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
            if item.revision != revision or item.last_observed_at < now - age_limit:
                raise NewsError("stale_evidence")
            membership = await database.get(NewsStoryItem, item.id)
            if membership is None or membership.item_revision != item.revision:
                raise NewsError("stale_evidence")
            await owned_story(
                database,
                membership.cluster_id,
                workspace_id=conversation.workspace_id,
                user_id=conversation.user_id,
            )
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
            view = await evaluate_claim(
                database,
                claim,
                definitions,
                now=now,
                freshness_seconds=turn_plan(turn).freshness_seconds,
            )
            source = items[claim.origin_item_id]
            facts.append(
                AnswerFact(
                    item_id=source.id,
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
                    item_id=source.id,
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
        if bool((turn.retrieval_metadata or {}).get("limited", True)):
            message += " This is a bounded selection from your connected sources."
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
