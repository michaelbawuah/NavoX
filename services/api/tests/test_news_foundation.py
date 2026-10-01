"""Exercise rights before egress/storage/readback and real tenant foreign keys."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from navox.core.settings import Settings
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.db.news import NewsContentRights, NewsIngestionReceipt, NewsItem
from navox.news.contracts import ContentRights, NewsError, NewsItemInput, SourceDefinition
from navox.news.feeds import MAX_FEED_BYTES, parse_feed
from navox.news.ingestion import (
    ingest_source,
    item_view,
    purge_unavailable,
    store_item,
    visible_items,
)
from navox.news.registry import activate_source, catalog, current_rights, revoke_rights
from navox.news.rights import Operation, require_operation

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
USER, WORKSPACE = uuid4(), uuid4()
OTHER_USER, OTHER_WORKSPACE = uuid4(), uuid4()


def definition(**rights):
    policy = dict(
        metadata_storage_allowed=True,
        snippet_storage_allowed=True,
        retention_days=2,
        permission_reference="https://science.example.com/terms",
        reviewed_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=20),
    )
    return SourceDefinition(
        key="science-fixture",
        name="Science Fixture",
        domain="science.example.com",
        source_type="RESEARCH",
        feed_type="RSS",
        endpoint="https://science.example.com/feed",
        article_domains=("science.example.com",),
        category="science",
        independence_group="science-fixture",
        identity_verified=True,
        rights=ContentRights(**(policy | rights)),
    )


def rss(title="A measured observation", description="An attributed source statement."):
    return f"""<rss><channel><item><guid>item-one</guid><title>{title}</title>
    <link>https://science.example.com/observation</link>
    <pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate>
    <description>{description}</description>
    <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">
    PRIVATE FULL TEXT</content:encoded>
    </item></channel></rss>""".encode()


async def seed(factory):
    async with factory() as db:
        db.add_all(
            [
                User(id=USER, email="news@example.com"),
                Workspace(id=WORKSPACE, name="News"),
                User(id=OTHER_USER, email="other-news@example.com"),
                Workspace(id=OTHER_WORKSPACE, name="Other news"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                WorkspaceMembership(workspace_id=WORKSPACE, user_id=USER),
                WorkspaceMembership(workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER),
            ]
        )
        await db.commit()


async def activate(db, config):
    return await activate_source(db, config, workspace_id=WORKSPACE, user_id=USER, now=NOW)


def test_rights_are_explicit_and_no_full_text_or_images_leak_from_feed():
    config = definition()
    items, rejected = parse_feed(rss(), config)
    assert rejected == 0 and len(items) == 1
    assert items[0].description == "An attributed source statement."
    assert items[0].published_at == datetime(2026, 9, 28, 10, tzinfo=UTC)
    assert items[0].event_started_at is None
    assert "PRIVATE FULL TEXT" not in items[0].model_dump_json()
    for operation in (
        Operation.STORE_TEXT,
        Operation.PROCESS_TEXT,
        Operation.SUMMARY,
        Operation.IMAGE,
    ):
        with pytest.raises(NewsError, match="rights_denied"):
            require_operation(operation, config.rights)


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE rss [<!ENTITY x "secret">]><rss><channel/></rss>',
        "<!DOCTYPE rss><rss/>".encode("utf-16"),
        b"<not-feed/>",
        b"broken",
        b"x" * (MAX_FEED_BYTES + 1),
    ],
)
def test_unsafe_or_unbounded_feed_is_rejected(body):
    with pytest.raises(NewsError):
        parse_feed(body, definition())


@pytest.mark.parametrize(
    "link",
    [
        "http://science.example.com/a",
        "https://attacker.example/a",
        "file:///etc/passwd",
        "https://science.example.com@localhost/a",
    ],
)
def test_unregistered_article_links_are_rejected(link):
    data = rss().replace(b"https://science.example.com/observation", link.encode())
    items, rejected = parse_feed(data, definition())
    assert items == [] and rejected == 1


def test_missing_date_is_not_invented_and_markup_is_not_executed():
    items, rejected = parse_feed(rss().replace(b"Mon, 28 Sep 2026 10:00:00 GMT", b""), definition())
    assert items == [] and rejected == 1
    items, _ = parse_feed(rss(description="&lt;script&gt;unsafe&lt;/script&gt;Safe"), definition())
    assert items[0].description == "Safe"
    with pytest.raises(ValueError):
        NewsItemInput(
            external_id="a",
            headline="a",
            canonical_url="https://science.example.com/a",
            published_at=datetime(2026, 9, 28),
        )


@pytest.mark.asyncio
async def test_ingestion_is_scoped_idempotent_and_content_free_receipts(ai_database):
    await seed(ai_database)
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=rss(), headers={"content-type": "application/rss+xml"})

    config = definition()
    async with (
        ai_database() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client,
    ):
        source = await activate(db, config)
        request_id = uuid4()
        for _ in range(2):
            result = await ingest_source(
                db,
                client,
                {config.key: config},
                source_id=source.id,
                workspace_id=WORKSPACE,
                user_id=USER,
                request_id=request_id,
                now=NOW,
            )
            assert result.status == "COMPLETED" and result.stored_count == 1
        await db.commit()
        assert calls == [config.endpoint]
        assert await db.scalar(select(func.count()).select_from(NewsIngestionReceipt)) == 1
        items = await visible_items(
            db, {config.key: config}, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert len(items) == 1 and items[0].revision == 1
        assert (
            await visible_items(
                db, {config.key: config}, workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER, now=NOW
            )
            == []
        )
        with pytest.raises(NewsError, match="source_unavailable"):
            await ingest_source(
                db,
                client,
                {config.key: config},
                source_id=source.id,
                workspace_id=OTHER_WORKSPACE,
                user_id=OTHER_USER,
                request_id=uuid4(),
                now=NOW,
            )
        with pytest.raises(NewsError, match="refresh_too_soon"):
            await ingest_source(
                db,
                client,
                {config.key: config},
                source_id=source.id,
                workspace_id=WORKSPACE,
                user_id=USER,
                request_id=uuid4(),
                now=NOW,
            )
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_rights_expiry_and_config_change_deny_before_any_request(ai_database):
    await seed(ai_database)

    def forbidden(_):
        raise AssertionError("Network must not be called")

    config = definition()
    async with (
        ai_database() as db,
        httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client,
    ):
        source = await activate(db, config)
        for definitions, now, code in [
            ({config.key: config}, NOW + timedelta(days=21), "rights_expired"),
            (
                {config.key: config.model_copy(update={"name": "Unreviewed change"})},
                NOW,
                "source_changed",
            ),
            ({}, NOW, "source_unavailable"),
        ]:
            with pytest.raises(NewsError, match=code):
                await ingest_source(
                    db,
                    client,
                    definitions,
                    source_id=source.id,
                    workspace_id=WORKSPACE,
                    user_id=USER,
                    request_id=uuid4(),
                    now=now,
                )


@pytest.mark.asyncio
async def test_revoke_deletes_content_and_retention_is_not_extended_by_repoll(ai_database):
    await seed(ai_database)
    config = definition()
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        candidate = parse_feed(rss(), config)[0][0]
        first = await store_item(db, source, rights, config, candidate, now=NOW)
        second = await store_item(
            db, source, rights, config, candidate, now=NOW + timedelta(days=1)
        )
        assert second.id == first.id and second.retrieved_at == NOW and second.revision == 1
        assert (
            await purge_unavailable(
                db,
                {config.key: config},
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW + timedelta(days=3),
            )
            == 1
        )
        assert await db.scalar(select(func.count()).select_from(NewsItem)) == 0
        await store_item(db, source, rights, config, candidate, now=NOW)
        await revoke_rights(db, source, now=NOW)
        assert await db.scalar(select(func.count()).select_from(NewsItem)) == 0
        await db.commit()


@pytest.mark.asyncio
async def test_narrowed_rights_hide_and_remove_previously_stored_snippet(ai_database):
    await seed(ai_database)
    config = definition()
    narrowed = definition(snippet_storage_allowed=False)
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        row = await store_item(db, source, rights, config, parse_feed(rss(), config)[0][0], now=NOW)
        await activate(db, narrowed)
        view = await item_view(db, row, {narrowed.key: narrowed}, now=NOW)
        assert view.description is None
        await purge_unavailable(
            db, {narrowed.key: narrowed}, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert row.description is None
        assert await db.scalar(select(func.count()).select_from(NewsContentRights)) == 2


@pytest.mark.asyncio
async def test_item_foreign_keys_reject_cross_scope(ai_database):
    await seed(ai_database)
    config = definition()
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        row = await store_item(db, source, rights, config, parse_feed(rss(), config)[0][0], now=NOW)
        await db.commit()
        row.workspace_id = OTHER_WORKSPACE
        row.user_id = OTHER_USER
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


def test_settings_are_disabled_by_default_and_operator_catalog_is_strict():
    settings = Settings(_env_file=None)
    assert settings.news_feed_enabled is False and settings.news_chat_enabled is False
    assert catalog(settings) == {}
    settings.news_source_catalog = [{"key": "incomplete"}]
    with pytest.raises(NewsError, match="invalid_source"):
        catalog(settings)
