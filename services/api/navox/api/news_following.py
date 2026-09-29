"""Owned follow-update inbox; acknowledgement is explicit and never an external action."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.api.news import news_failure, require_feed
from navox.news.contracts import Contract, NewsError
from navox.news.following import (
    FollowingPage,
    SeenReceipt,
    acknowledge_updates,
    following_updates,
)
from navox.news.registry import catalog

router = APIRouter(prefix="/news/following", tags=["news"])


class SeenCommand(Contract):
    observed_version: int = Field(strict=True, ge=1, le=2147483647)


@router.get("/updates")
async def updates(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
    after_story_id: UUID | None = None,
    limit: int = Query(default=10, ge=1, le=25),
) -> FollowingPage:
    require_feed(settings)
    response.headers["Cache-Control"] = "no-store"
    try:
        return await following_updates(
            database,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
            after_story_id=after_story_id,
            limit=limit,
        )
    except NewsError as error:
        raise news_failure(error) from None


@router.post("/{story_id}/seen")
async def seen(
    story_id: UUID,
    command: SeenCommand,
    request: Request,
    response: Response,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SeenReceipt:
    require_origin(request, settings.web_origin)
    require_feed(settings)
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await acknowledge_updates(
            database,
            story_id,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            observed_version=command.observed_version,
            now=datetime.now(UTC),
        )
        await database.commit()
        return result
    except NewsError as error:
        raise news_failure(error) from None
