"""Reviewed importance never borrows popularity as significance or keeps stale evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_news_foundation import NOW, USER, WORKSPACE, seed
from test_news_stories import item, span

from navox.news.contracts import NewsError
from navox.news.importance import (
    ImportanceReview,
    ImportanceSignal,
    current_importance,
    review_importance,
)
from navox.news.registry import revoke_rights
from navox.news.stories import index_item


def reviewed(story, row, level=3):
    return ImportanceReview(
        story_id=story.id,
        story_version=story.version,
        reference="authored-fixture-review",
        reviewed_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        signals=tuple(
            ImportanceSignal(name=name, level=level, evidence=span(row))
            for name in (
                "geographic_scope",
                "people_affected",
                "public_safety",
                "significance",
                "source_breadth",
                "duration",
            )
        ),
    )


@pytest.mark.asyncio
async def test_reviewed_importance_has_cited_revision_and_policy(ai_database):
    await seed(ai_database)
    async with ai_database() as database:
        config, source, row = await item(database)
        definitions = {config.key: config}
        story = await index_item(database, row, definitions, now=NOW)
        assert await current_importance(database, story, definitions, now=NOW) is None
        review = reviewed(story, row)
        await review_importance(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            review=review,
            definitions=definitions,
            now=NOW,
        )
        assert await current_importance(database, story, definitions, now=NOW) == pytest.approx(
            0.75
        )
        stored = str(story.importance_review)
        assert row.headline not in stored and row.description not in stored
        assert config.fingerprint in stored
        assert (
            await current_importance(database, story, definitions, now=NOW + timedelta(hours=3))
            is None
        )
        changed_policy = config.model_copy(update={"name": "Changed registry definition"})
        assert (
            await current_importance(database, story, {config.key: changed_policy}, now=NOW) is None
        )
        story.version += 1
        assert await current_importance(database, story, definitions, now=NOW) is None


@pytest.mark.asyncio
async def test_importance_drops_when_rights_are_revoked(ai_database):
    await seed(ai_database)
    async with ai_database() as database:
        config, source, row = await item(database)
        definitions = {config.key: config}
        story = await index_item(database, row, definitions, now=NOW)
        await review_importance(
            database,
            workspace_id=WORKSPACE,
            user_id=USER,
            review=reviewed(story, row),
            definitions=definitions,
            now=NOW,
        )
        await revoke_rights(database, source, now=NOW)
        assert await current_importance(database, story, definitions, now=NOW) is None


@pytest.mark.asyncio
async def test_importance_rejects_foreign_story_and_unrelated_evidence(ai_database):
    await seed(ai_database)
    async with ai_database() as database:
        config, _, row = await item(database)
        other_config, _, other = await item(database, "other", headline="Unrelated evidence")
        definitions = {config.key: config, other_config.key: other_config}
        story = await index_item(database, row, definitions, now=NOW)
        await index_item(database, other, definitions, now=NOW)
        for owner, review in ((uuid4(), reviewed(story, row)), (USER, reviewed(story, other))):
            with pytest.raises(NewsError):
                await review_importance(
                    database,
                    workspace_id=WORKSPACE,
                    user_id=owner,
                    review=review,
                    definitions=definitions,
                    now=NOW,
                )


def test_importance_requires_complete_expiring_review_and_is_not_trend():
    with pytest.raises(ValueError):
        ImportanceReview(
            story_id=uuid4(),
            story_version=1,
            reference="fixture-review",
            reviewed_at=NOW,
            expires_at=NOW + timedelta(days=20),
            signals=(),
        )


@pytest.mark.asyncio
async def test_top_feed_uses_complete_reviews_and_falls_back_after_expiry(ai_database, monkeypatch):
    from datetime import datetime

    from navox.api import news_stories
    from navox.api.auth import CurrentAccount
    from navox.core.settings import Settings
    from navox.db.models import User, Workspace

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(news_stories, "datetime", Clock)
    await seed(ai_database)
    async with ai_database() as database:
        first_config, _, first = await item(database)
        second_config, _, second = await item(database, "second", headline="Another distinct event")
        definitions = {first_config.key: first_config, second_config.key: second_config}
        older = await index_item(database, first, definitions, now=NOW)
        newer = await index_item(database, second, definitions, now=NOW)
        newer.last_updated_at = NOW + timedelta(minutes=1)
        for story, row, level in ((older, first, 4), (newer, second, 1)):
            await review_importance(
                database,
                workspace_id=WORKSPACE,
                user_id=USER,
                review=reviewed(story, row, level),
                definitions=definitions,
                now=NOW,
            )
        await database.commit()
        account = CurrentAccount(
            user=await database.get(User, USER), workspace=await database.get(Workspace, WORKSPACE)
        )
        settings = Settings(
            news_feed_enabled=True,
            news_source_catalog=[config.model_dump(mode="json") for config in definitions.values()],
        )
        ranked = await news_stories.feed("top", account, database, settings)
        assert [row.id for row in ranked] == [older.id, newer.id]
        assert all(row.ranking_basis == "REVIEWED_IMPORTANCE" for row in ranked)
        newer.importance_review = None
        await database.commit()
        fallback = await news_stories.feed("top", account, database, settings)
        assert [row.id for row in fallback] == [newer.id, older.id]
        assert all(row.ranking_basis == "RECENCY" for row in fallback)
