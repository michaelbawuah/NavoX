"""Background safety gates, pagination and cancellation without live providers."""

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from temporalio.exceptions import CancelledError
from test_news_foundation import NOW, OTHER_USER, USER, WORKSPACE, activate, definition, rss, seed

from navox.core.settings import Settings
from navox.db.models import User
from navox.db.news import NewsItem
from navox.news import activities, ingestion
from navox.news.contracts import NewsError
from navox.news.feeds import fetch_feed, parse_feed
from navox.news.ingestion import ingest_source, purge_unavailable, store_item
from navox.news.jobs import NewsSourceWork
from navox.news.registry import current_rights
from navox.workflows import news


@pytest.fixture
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(activities, "datetime", Clock)


@pytest.mark.asyncio
@pytest.mark.parametrize("paused,enabled", [(True, True), (False, False)])
async def test_background_pause_blocks_egress_but_keeps_retention(
    ai_database, monkeypatch, fixed_clock, paused, enabled
):
    await seed(ai_database)
    config = definition()
    settings = Settings(
        news_feed_enabled=enabled, news_source_catalog=[config.model_dump(mode="json")]
    )
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Paused source must not fetch")

    monkeypatch.setattr(activities, "ingest_source", forbidden)
    async with ai_database() as db:
        source = await activate(db, config)
        row = await store_item(
            db,
            source,
            await current_rights(db, source),
            config,
            parse_feed(rss(), config)[0][0],
            now=NOW,
        )
        row.expires_at = NOW - timedelta(seconds=1)
        owner = await db.get(User, USER)
        owner.agent_paused = paused
        await db.commit()
        identifier = row.id
        work = NewsSourceWork(str(source.id), str(WORKSPACE), str(USER), str(uuid4()))
    result = await activities.ingest_news_source_activity(work)
    assert result.status == "paused" and result.purged_count == 1
    async with ai_database() as db:
        assert await db.get(NewsItem, identifier) is None
    assert (
        await activities.ingest_news_source_activity(replace(work, user_id=str(OTHER_USER)))
    ).status == "unavailable"


@pytest.mark.asyncio
async def test_retention_and_source_scan_do_not_starve_later_records(ai_database, monkeypatch):
    await seed(ai_database)
    config = definition()
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        for index in range(7):
            candidate = parse_feed(rss(), config)[0][0].model_copy(
                update={"external_id": str(index)}
            )
            row = await store_item(db, source, rights, config, candidate, now=NOW)
            row.id = UUID(int=index + 1)
            row.expires_at = NOW + timedelta(days=1) if index < 3 else NOW - timedelta(seconds=1)
        await db.flush()
        assert (
            await purge_unavailable(
                db,
                {config.key: config},
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
                batch_size=2,
            )
            == 4
        )
        assert await db.scalar(select(func.count()).select_from(NewsItem)) == 3
        await db.commit()
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(activities, "PAGE_SIZE", 1)
    page = await activities.news_sources_activity(None)
    assert len(page.sources) == 1 and page.after is not None
    assert (await activities.news_sources_activity(page.after)).sources == []


@pytest.mark.asyncio
async def test_expiry_during_request_cannot_store_content(ai_database, monkeypatch):
    await seed(ai_database)
    config = definition(expires_at=NOW + timedelta(seconds=1))
    ticks = iter([0, 2])
    monkeypatch.setattr(ingestion, "monotonic", lambda: next(ticks))
    async with (
        ai_database() as db,
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, content=rss(), headers={"content-type": "application/rss+xml"}
                )
            )
        ) as client,
    ):
        source = await activate(db, config)
        receipt = await ingest_source(
            db,
            client,
            {config.key: config},
            source_id=source.id,
            workspace_id=WORKSPACE,
            user_id=USER,
            request_id=uuid4(),
            now=NOW,
        )
        assert receipt.status == "FAILED" and receipt.error_code == "rights_expired"
        assert await db.scalar(select(func.count()).select_from(NewsItem)) == 0


@pytest.mark.asyncio
async def test_redirect_does_not_expand_registered_destination():
    calls = []

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect), follow_redirects=True
    ) as client:
        with pytest.raises(NewsError, match="feed_unavailable"):
            await fetch_feed(client, definition())
    assert calls == [definition().endpoint]


@pytest.mark.asyncio
async def test_stale_feed_cannot_undo_correction(ai_database):
    await seed(ai_database)
    config = definition()
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        item = parse_feed(rss(), config)[0][0]
        corrected = item.model_copy(update={"headline": "Corrected measurement", "updated_at": NOW})
        row = await store_item(db, source, rights, config, corrected, now=NOW)
        await store_item(db, source, rights, config, item, now=NOW + timedelta(hours=1))
        assert row.headline == "Corrected measurement" and row.revision == 1


@pytest.mark.asyncio
async def test_reconciliation_caps_concurrency_and_propagates_cancellation(monkeypatch):
    active = peak = 0

    async def child(fn, payload, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        if payload.request_id == "fail":
            raise RuntimeError("A source is unavailable")

    monkeypatch.setattr(news.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(news.workflow, "logger", logging.getLogger(__name__))
    work = [
        NewsSourceWork(str(uuid4()), str(WORKSPACE), str(USER), str(index)) for index in range(9)
    ]
    await news.reconcile_page(work + [replace(work[0], request_id="fail")])
    assert peak == 4 and active == 0

    async def cancelled(*args, **kwargs):
        raise CancelledError("cancelled")

    monkeypatch.setattr(news.workflow, "execute_child_workflow", cancelled)
    with pytest.raises(CancelledError):
        await news.reconcile_page(work[:1])
