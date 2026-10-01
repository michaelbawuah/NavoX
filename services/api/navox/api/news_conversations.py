from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from navox.ai.context import ContextDenied
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.news import news_failure, require_feed
from navox.db.news import NewsConversationTurn
from navox.news.contracts import Contract, NewsError
from navox.news.conversations import (
    AnswerRead,
    Question,
    answer_view,
    begin_question,
    new_conversation,
    owned_conversation,
)
from navox.news.dispatcher import dispatch_news_conversation
from navox.news.jobs import NewsConversationWork

router = APIRouter(prefix="/news/conversations", tags=["news"])


class CreateConversation(Contract):
    story_id: UUID | None = None


def require_chat(settings: SettingsDependency) -> None:
    require_feed(settings)
    if not settings.news_chat_enabled:
        raise HTTPException(503, "News conversations are not available yet.")


@router.post("", status_code=201)
async def create(
    command: CreateConversation,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    require_origin(request, settings.web_origin)
    require_chat(settings)
    try:
        conversation = await new_conversation(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            story_id=command.story_id,
            now=datetime.now(UTC),
        )
        await database.commit()
        return {"id": str(conversation.id)}
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/{conversation_id}")
async def read(
    conversation_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[AnswerRead]:
    require_chat(settings)
    now = datetime.now(UTC)
    try:
        conversation = await owned_conversation(
            database,
            conversation_id,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=now,
        )
        turns = await database.scalars(
            select(NewsConversationTurn)
            .where(
                NewsConversationTurn.conversation_id == conversation.id,
                NewsConversationTurn.expires_at > now,
            )
            .order_by(NewsConversationTurn.sequence)
            .limit(100)
        )
        return [
            await answer_view(database, conversation, turn, settings, now=now) for turn in turns
        ]
    except NewsError as error:
        raise news_failure(error) from None


@router.post("/{conversation_id}/messages", status_code=202)
async def message(
    conversation_id: UUID,
    command: Question,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    require_origin(request, settings.web_origin)
    require_chat(settings)
    try:
        turn, created = await begin_question(
            database,
            settings,
            conversation_id,
            command,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
        )
        await database.commit()
        if created:
            try:
                await dispatch_news_conversation(
                    settings,
                    NewsConversationWork(
                        str(turn.id), str(account.workspace.id), str(account.user.id)
                    ),
                )
            except (OSError, RuntimeError, TimeoutError):
                turn.status, turn.failure_code = "UNAVAILABLE", "news_unavailable"
                await database.commit()
                raise HTTPException(
                    503, "News conversations are temporarily unavailable."
                ) from None
        return {"id": str(turn.id), "status": turn.status}
    except NewsError as error:
        raise news_failure(error) from None
    except ContextDenied:
        raise HTTPException(400, "Remove sensitive credentials before asking about news.") from None
