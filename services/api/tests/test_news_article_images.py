"""Licensed photos fail closed at parsing, storage, readback and revocation."""

import hashlib
import importlib.util
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import select
from test_news_foundation import NOW, USER, WORKSPACE, activate, definition, rss, seed

from navox.db.news import NewsItem
from navox.news.api_feeds import parse_perigon
from navox.news.contracts import NewsError, NewsImage, SourceDefinition
from navox.news.feeds import parse_feed
from navox.news.ingestion import item_view, purge_unavailable, store_item
from navox.news.registry import current_rights
from navox.news.stories import index_item, story_view

PHOTO = {
    "url": "https://photos.example.com/observation.jpg?width=1200",
    "alt": "A scientist examining a sample.",
    "credit": "Photo: Fixture photographer / Science Fixture",
}


def licensed(*, allowed=True):
    value = definition(image_display_allowed=allowed).model_dump()
    value["image_domains"] = ("photos.example.com",)
    return SourceDefinition.model_validate(value)


def illustrated_rss():
    return rss().replace(
        b"</item>",
        b'<media:content xmlns:media="http://search.yahoo.com/mrss/" medium="image" '
        b'url="https://photos.example.com/observation.jpg?width=1200" />'
        b'<media:description xmlns:media="http://search.yahoo.com/mrss/">'
        b"A scientist examining a sample.</media:description>"
        b'<media:credit xmlns:media="http://search.yahoo.com/mrss/">'
        b"Photo: Fixture photographer / Science Fixture</media:credit></item>",
    )


def test_metadata_only_feed_does_not_extract_photo_caption_or_credit():
    item = parse_feed(illustrated_rss(), licensed(allowed=False))[0][0]
    assert item.image is None
    assert "Fixture photographer" not in item.model_dump_json()


def test_licensed_feed_requires_the_reviewed_host_caption_and_credit():
    item = parse_feed(illustrated_rss(), licensed())[0][0]
    assert item.image is not None and item.image.model_dump() == PHOTO
    for body in (
        illustrated_rss().replace(b"photos.example.com", b"unknown.example.com"),
        illustrated_rss().replace(b"Photo: Fixture photographer / Science Fixture", b""),
        illustrated_rss().replace(b"A scientist examining a sample.", b""),
    ):
        items, rejected = parse_feed(body, licensed())
        assert rejected == 0 and len(items) == 1 and items[0].image is None


@pytest.mark.parametrize(
    "url",
    [
        "http://photos.example.com/a.jpg",
        "https://localhost/a.jpg",
        "https://127.0.0.1/a.jpg",
        "https://169.254.169.254/a.jpg",
        "https://photos.example.com:8443/a.jpg",
        "https://user:password@photos.example.com/a.jpg",
        "https://photos.example.com/a.jpg#secret",
        "https://photos.example.com/a.jpg?apiKey=secret",
        "https://photos.example.com/a.jpg?signature=secret",
        "https://photos.example.com/a.jpg?access_token=secret",
        "https://photos.example.com/a.jpg?%2561uth=secret",
    ],
)
def test_image_contract_rejects_private_or_credential_bearing_urls(url):
    with pytest.raises(ValidationError):
        NewsImage.model_validate(PHOTO | {"url": url})


def test_perigon_photo_fields_do_not_grant_display_rights():
    article = {
        "articleId": "photo-one",
        "title": "A measured observation",
        "url": "https://science.example.com/observation",
        "pubDate": NOW.isoformat(),
        "source": {"domain": "science.example.com"},
        "imageUrl": PHOTO["url"],
        "imageCaption": PHOTO["alt"],
        "imageAttribution": PHOTO["credit"],
    }
    body = json.dumps({"articles": [article]}).encode()
    assert parse_perigon(body, licensed(allowed=False))[0][0].image is None
    assert parse_perigon(body, licensed())[0][0].image == NewsImage.model_validate(PHOTO)
    del article["imageAttribution"]
    assert (
        parse_perigon(json.dumps({"articles": [article]}).encode(), licensed())[0][0].image is None
    )


@pytest.mark.asyncio
async def test_allowed_image_projects_to_story_then_narrowed_rights_hide_and_purge(ai_database):
    await seed(ai_database)
    config = licensed()
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        item = parse_feed(illustrated_rss(), config)[0][0]
        row = await store_item(db, source, rights, config, item, now=NOW)
        story = await index_item(db, row, {config.key: config}, now=NOW)
        projected = await story_view(db, story, {config.key: config}, now=NOW)
        assert projected.image == item.image
        assert projected.sources[0].image == item.image
        original_deadline = row.expires_at
        await store_item(db, source, rights, config, item, now=NOW + timedelta(hours=1))
        assert row.revision == 1 and row.expires_at == original_deadline
        narrowed = licensed(allowed=False)
        await activate(db, narrowed)
        assert (await item_view(db, row, {narrowed.key: narrowed}, now=NOW)).image is None
        assert (await story_view(db, story, {narrowed.key: narrowed}, now=NOW)).image is None
        await purge_unavailable(
            db, {narrowed.key: narrowed}, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert row.image is None
        assert await db.scalar(select(NewsItem.id)) == row.id


@pytest.mark.asyncio
async def test_unlicensed_photo_is_not_stored_hashed_or_exposed_after_a_later_grant(ai_database):
    await seed(ai_database)
    config = licensed(allowed=False)
    async with ai_database() as db:
        source = await activate(db, config)
        rights = await current_rights(db, source)
        plain = parse_feed(rss(), config)[0][0]
        candidate = plain.model_copy(update={"image": NewsImage.model_validate(PHOTO)})
        row = await store_item(db, source, rights, config, candidate, now=NOW)
        assert row.image is None
        second = await store_item(db, source, rights, config, plain, now=NOW)
        assert second.content_digest == row.content_digest and second.revision == 1
        allowed = licensed()
        await activate(db, allowed)
        assert (await item_view(db, row, {allowed.key: allowed}, now=NOW)).image is None


@pytest.mark.asyncio
async def test_expired_image_rights_deny_readback(ai_database):
    await seed(ai_database)
    config = licensed()
    async with ai_database() as db:
        source = await activate(db, config)
        row = await store_item(
            db,
            source,
            await current_rights(db, source),
            config,
            parse_feed(illustrated_rss(), config)[0][0],
            now=NOW,
        )
        with pytest.raises(NewsError):
            await item_view(db, row, {config.key: config}, now=NOW + timedelta(days=21))


def test_empty_image_domains_preserve_existing_source_fingerprints():
    config = definition()
    payload = config.model_dump(mode="json")
    payload.pop("image_domains")
    payload.pop("api_connection_id")
    payload.pop("api_endpoint_name")
    payload.pop("evidence_policy")
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert config.fingerprint == expected


def test_image_migration_adds_one_nullable_column_and_guards_rollback():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0035_news_article_images.py"
    spec = importlib.util.spec_from_file_location("image_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0034_knowledge_email_drafts"
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.upgrade()
    sql = buffer.getvalue()
    assert "ALTER TABLE news_items ADD COLUMN image JSON" in sql
    assert "NOT NULL" not in sql and "DROP " not in sql and "UPDATE " not in sql
    assert NewsItem.__table__.c.image.nullable is True
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.downgrade()
    assert "article images prevent this downgrade" in buffer.getvalue()
    assert "DELETE FROM" not in buffer.getvalue()
