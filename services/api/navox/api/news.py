"""Authenticated, owner-scoped news. No endpoint accepts arbitrary fetch targets or rights."""

from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.db.news import NewsSource, NewsSourceFeed
from navox.news.api_feeds import approved_api_connection
from navox.news.contracts import FeedType, NewsError, NewsItemRead
from navox.news.ingestion import ingest_source, purge_unavailable, visible_items
from navox.news.registry import activate_source, catalog, owned_source, revoke_rights
from navox.news.stories import index_source

router = APIRouter(prefix="/news", tags=["news"])


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


def news_failure(error: NewsError) -> HTTPException:
    status = 404 if error.code.endswith("unavailable") else 409
    if error.code in {"refresh_too_soon", "rate_limited"}:
        status = 429
    return HTTPException(status, "News is temporarily unavailable. Please try again later.")


def require_feed(settings: SettingsDependency) -> None:
    if not settings.news_feed_enabled:
        raise HTTPException(503, "News is not available yet.")


@router.get("/availability")
async def availability(
    account: CurrentAccountDependency,
    settings: SettingsDependency,
) -> dict[str, bool]:
    del account
    return {
        "feed": settings.news_feed_enabled,
        "chat": settings.news_feed_enabled and settings.news_chat_enabled,
        # Reserved flags cannot advertise an adapter or workflow not yet delivered.
        "deep_research": False,
        "coverage_comparison": False,
        # These bounded read-only views are implemented; broader research remains reserved.
        "timeline": settings.news_feed_enabled and settings.news_deep_research_enabled,
        "source_comparison": settings.news_feed_enabled
        and settings.news_coverage_comparison_enabled,
    }


@router.get("/sources")
async def sources(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[dict[str, object]]:
    require_feed(settings)
    try:
        definitions = catalog(settings)
    except NewsError as error:
        raise news_failure(error) from None
    rows = list(
        await database.scalars(
            select(NewsSource).where(
                NewsSource.workspace_id == account.workspace.id,
                NewsSource.user_id == account.user.id,
            )
        )
    )
    by_key = {row.source_key: row for row in rows}
    result: list[dict[str, object]] = []
    for definition in definitions.values():
        if definition.feed_type == FeedType.API:
            try:
                await approved_api_connection(
                    database,
                    settings,
                    definition,
                    workspace_id=account.workspace.id,
                    user_id=account.user.id,
                )
            except NewsError:
                # An operator catalog template is not this account's read authority.
                # Keep unusable, owner-bound connections out of another user's UI.
                continue
        row = by_key.get(definition.key)
        feed = (
            await database.scalar(select(NewsSourceFeed).where(NewsSourceFeed.source_id == row.id))
            if row
            else None
        )
        result.append(
            {
                "key": definition.key,
                "name": definition.name,
                "domain": definition.domain,
                "category": definition.category,
                "id": str(row.id) if row else None,
                "status": row.status
                if row and row.config_digest == definition.fingerprint
                else "disabled",
                "health": feed.health_status if feed else "not_started",
                "last_success_at": feed.last_success_at.isoformat()
                if feed and feed.last_success_at
                else None,
            }
        )
    return result


@router.post("/sources/{key}/activate", status_code=201)
async def activate(
    key: str,
    command: Command,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    del command
    require_origin(request, settings.web_origin)
    require_feed(settings)
    try:
        definition = catalog(settings).get(key)
        if definition is None:
            raise NewsError("source_unavailable")
        source = await activate_source(
            database,
            definition,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
            settings=settings,
        )
        await purge_unavailable(
            database,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
        )
        await database.commit()
        return {"id": str(source.id), "status": source.status}
    except NewsError as error:
        raise news_failure(error) from None


@router.post("/sources/{source_id}/disable")
async def disable(
    source_id: UUID,
    command: Command,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    del command
    # Source revocation is available even if serving is disabled.
    require_origin(request, settings.web_origin)
    try:
        source = await owned_source(
            database,
            source_id,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            lock=True,
        )
        await revoke_rights(database, source, now=datetime.now(UTC))
        await database.commit()
        return {"status": "disabled"}
    except NewsError as error:
        raise news_failure(error) from None


@router.post("/sources/{source_id}/refresh")
async def refresh(
    source_id: UUID,
    command: Command,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    require_feed(settings)
    if account.user.agent_paused:
        raise HTTPException(409, "Resume NavoX before refreshing news.")
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            result = await ingest_source(
                database,
                client,
                catalog(settings),
                source_id=source_id,
                workspace_id=account.workspace.id,
                user_id=account.user.id,
                request_id=command.request_id,
                now=datetime.now(UTC),
                settings=settings,
            )
        if result.status == "COMPLETED":
            await index_source(
                database,
                source_id,
                catalog(settings),
                workspace_id=account.workspace.id,
                user_id=account.user.id,
                now=datetime.now(UTC),
            )
        await database.commit()
        return {
            "id": str(result.id),
            "status": result.status,
            "stored_count": result.stored_count,
            "rejected_count": result.rejected_count,
        }
    except NewsError as error:
        raise news_failure(error) from None


@router.get("/items", response_model=list[NewsItemRead])
async def items(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> list[NewsItemRead]:
    require_feed(settings)
    try:
        return await visible_items(
            database,
            catalog(settings),
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            now=datetime.now(UTC),
        )
    except NewsError as error:
        raise news_failure(error) from None
