"""Explicit preview, course confirmation, refresh and forgetting of private feeds."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.secrets import SecretBrokerError
from navox.db.lms_feeds import LmsFeed, LmsFeedItem, LmsFeedSource
from navox.lms_feeds.parser import parse_bounded
from navox.lms_feeds.schemas import (
    ConfirmCommand,
    CourseMatches,
    FeedItem,
    FeedUpdate,
    PreviewCommand,
)
from navox.lms_feeds.service import (
    FeedAccessError,
    aware,
    confirm_preview,
    create_preview,
    feed_view,
    forget_feed,
    item_sources_authorized,
    locked_feed,
    require_owner,
    reset_epoch,
    update_course_matches,
)
from navox.lms_feeds.transport import FeedError, FeedFetcher

router = APIRouter(prefix="/student/lms-feeds", tags=["learning-system calendar feeds"])


def get_feed_fetcher() -> FeedFetcher:
    return FeedFetcher()


FetcherDependency = Annotated[FeedFetcher, Depends(get_feed_fetcher)]


async def command[T: BaseModel](request: Request, model: type[T]) -> T:
    if request.headers.get("content-type", "").partition(";")[0] != "application/json":
        raise HTTPException(415, "Use a JSON request")
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > 64 * 1024:
            raise HTTPException(413, "Calendar command is too large")
        content.extend(part)
    try:
        return model.model_validate_json(content)
    except (ValidationError, ValueError):
        # Framework validation normally echoes invalid input, including a URL.
        raise HTTPException(
            422, "Review the calendar link, timezone and confirmation fields"
        ) from None


def enabled(settings: object) -> None:
    if not getattr(settings, "student_lms_feeds_enabled", False):
        raise HTTPException(404, "Calendar feeds are not available")


def failure(error: Exception) -> HTTPException:
    if isinstance(error, FeedAccessError):
        return HTTPException(error.status, str(error))
    if isinstance(error, FeedError):
        if error.code in {"unsupported_recurrence_frequency", "invalid_or_complex_calendar"}:
            return HTTPException(
                422,
                "This calendar uses an unsupported or overly complex pattern. "
                "No events were saved. Try an exported schedule or another feed.",
            )
        if error.code == "feed_too_large":
            return HTTPException(
                422, "This calendar exceeds the 5 MB limit. Try a feed for fewer courses."
            )
    return HTTPException(
        422, "This calendar could not be read. Check the private feed link or try a new one."
    )


@router.get("")
async def list_feeds(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    enabled(settings)
    response.headers["Cache-Control"] = "no-store"
    try:
        await require_owner(
            database, user_id=account.user.id, workspace_id=account.workspace.id, active=False
        )
        rows = await database.scalars(
            select(LmsFeed)
            .where(
                LmsFeed.user_id == account.user.id,
                LmsFeed.workspace_id == account.workspace.id,
            )
            .order_by(LmsFeed.id)
        )
        return {"feeds": [feed_view(row) for row in rows]}
    except FeedAccessError as error:
        raise failure(error) from None


@router.post("/preview")
async def preview_feed(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    fetcher: FetcherDependency,
    response: Response,
) -> dict[str, object]:
    enabled(settings)
    require_origin(request, settings.web_origin)
    response.headers["Cache-Control"] = "no-store"
    body = await command(request, PreviewCommand)
    try:
        await require_owner(
            database, user_id=account.user.id, workspace_id=account.workspace.id, active=True
        )
        epoch = await reset_epoch(
            database, user_id=account.user.id, workspace_id=account.workspace.id
        )
        # No database locks are held while performing a bounded remote read.
        await database.commit()
        fetched = await fetcher.fetch(body.url)
        if fetched.status != 200:
            raise FeedError("invalid_preview_response")
        now = datetime.now(UTC)
        parsed = await parse_bounded(
            fetched.content,
            timezone=body.timezone,
            all_day_due_time=body.all_day_due_time,
            now=now,
            private_url=body.url,
            platform=body.platform,
        )
        feed, preview = await create_preview(
            database,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            command=body,
            parsed=parsed,
            fetched=fetched,
            settings=settings,
            now=now,
            expected_reset=epoch,
        )
        return {
            "preview_token": str(preview.id),
            "feed_id": str(feed.id),
            "preview_hash": preview.preview_hash,
            "expires_at": aware(preview.expires_at).isoformat(),
            "domain": feed.domain,
            "platform": parsed.platform,
            "count": len(parsed.items),
            "course_hints": parsed.course_hints,
            "all_day_count": parsed.all_day_count,
            "all_day_due_time": body.all_day_due_time,
            "items": [item.model_dump(mode="json") for item in parsed.items],
            "notice": (
                "Review course matches and the all-day due time before connecting. "
                "Feeds contain dates, not grades or announcements."
            ),
        }
    except (FeedError, FeedAccessError, SecretBrokerError) as error:
        await database.rollback()
        raise failure(error) from None


@router.post("", status_code=201)
async def connect_feed(
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    enabled(settings)
    require_origin(request, settings.web_origin)
    response.headers["Cache-Control"] = "no-store"
    body = await command(request, ConfirmCommand)
    try:
        feed = await confirm_preview(
            database,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            command=body,
            now=datetime.now(UTC),
        )
        return feed_view(feed)
    except FeedAccessError as error:
        await database.rollback()
        raise failure(error) from None


@router.post("/{feed_id}/refresh", status_code=202)
async def refresh_feed(
    feed_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    enabled(settings)
    require_origin(request, settings.web_origin)
    try:
        feed = await locked_feed(
            database,
            feed_id=feed_id,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            active=True,
        )
        now = datetime.now(UTC)
        if feed.last_manual_at and now - aware(feed.last_manual_at) < timedelta(minutes=1):
            raise FeedAccessError(429, "This feed was just queued. Refresh its status in a moment.")
        feed.last_manual_at, feed.next_sync_at = now, now
        await database.commit()
        return {"state": "queued", "feed_id": str(feed.id)}
    except FeedAccessError as error:
        await database.rollback()
        raise failure(error) from None


@router.put("/{feed_id}")
async def update_feed(
    feed_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    body = await command(request, FeedUpdate)
    if body.enabled:
        enabled(settings)
    try:
        feed = await locked_feed(
            database, feed_id=feed_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        if feed.revision != body.revision:
            raise FeedAccessError(409, "The calendar changed. Refresh before saving.")
        if feed.state == "preview":
            raise FeedAccessError(409, "Review and confirm this calendar before enabling it")
        if body.enabled:
            from navox.connectors.authorization import ConnectorAccessDenied, owned_connector

            if feed.last_error in {"feed_link_expired", "feed_history_limit"}:
                raise FeedAccessError(
                    409, "Copy a fresh calendar link and review it before reconnecting"
                )
            try:
                await owned_connector(
                    database,
                    connection_id=feed.connection_id,
                    user_id=account.user.id,
                    workspace_id=account.workspace.id,
                    require_active=True,
                )
            except ConnectorAccessDenied:
                raise FeedAccessError(
                    409, "Reconnect or resume the calendar source first"
                ) from None
        feed.state = "active" if body.enabled else "paused"
        feed.revision += 1
        feed.lease_token, feed.lease_until = None, None
        feed.next_sync_at = datetime.now(UTC) if body.enabled else None
        await database.commit()
        return feed_view(feed)
    except FeedAccessError as error:
        await database.rollback()
        raise failure(error) from None


@router.put("/{feed_id}/matches")
async def match_courses(
    feed_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    enabled(settings)
    require_origin(request, settings.web_origin)
    body = await command(request, CourseMatches)
    try:
        feed = await locked_feed(
            database, feed_id=feed_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        if feed.revision != body.revision or feed.state == "preview":
            raise FeedAccessError(409, "The calendar changed. Refresh before saving.")
        await update_course_matches(database, feed, body.course_matches, now=datetime.now(UTC))
        await database.commit()
        return feed_view(feed)
    except FeedAccessError as error:
        await database.rollback()
        raise failure(error) from None


@router.get("/{feed_id}/matches")
async def course_matches(
    feed_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
) -> dict[str, object]:
    enabled(settings)
    response.headers["Cache-Control"] = "no-store"
    try:
        feed = await locked_feed(
            database, feed_id=feed_id, user_id=account.user.id, workspace_id=account.workspace.id
        )
        sources = await database.scalars(
            select(LmsFeedSource).where(LmsFeedSource.feed_id == feed.id)
        )
        hints = sorted(
            {
                item.course_hint
                for source in sources
                if (item := FeedItem.model_validate(source.payload)).course_hint
            }
        )
        return {
            "revision": feed.revision,
            "course_hints": hints,
            "course_matches": feed.course_matches,
            "unconfirmed_hints": [hint for hint in hints if hint not in feed.course_matches],
        }
    except FeedAccessError as error:
        raise failure(error) from None


@router.get("/items")
async def list_items(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    response: Response,
    after: UUID | None = None,
) -> dict[str, object]:
    enabled(settings)
    response.headers["Cache-Control"] = "no-store"
    try:
        await require_owner(
            database, user_id=account.user.id, workspace_id=account.workspace.id, active=False
        )
    except FeedAccessError as error:
        raise failure(error) from None
    query = select(LmsFeedItem).where(
        LmsFeedItem.user_id == account.user.id, LmsFeedItem.workspace_id == account.workspace.id
    )
    if after:
        query = query.where(LmsFeedItem.id > after)
    rows = list(await database.scalars(query.order_by(LmsFeedItem.id).limit(101)))
    visible = [row for row in rows[:100] if await item_sources_authorized(database, row)]
    return {
        "items": [
            {
                "id": str(row.id),
                "revision": row.revision,
                "course_id": str(row.course_id) if row.course_id else None,
                "cancelled": row.cancelled,
                "conflicted": row.conflicted,
                "item": row.payload,
            }
            for row in visible
        ],
        "next_cursor": str(rows[99].id) if len(rows) > 100 else None,
    }


@router.delete("/{feed_id}", status_code=204)
async def remove_feed(
    feed_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> Response:
    # Removal stays available when rollout is disabled or assistance is paused.
    require_origin(request, settings.web_origin)
    try:
        await forget_feed(
            database,
            feed_id=feed_id,
            user_id=account.user.id,
            workspace_id=account.workspace.id,
            settings=settings,
            now=datetime.now(UTC),
        )
    except (FeedAccessError, SecretBrokerError) as error:
        await database.rollback()
        raise failure(error) from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
