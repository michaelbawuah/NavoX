"""Read-only, rights-checked timeline, coverage and change history fixtures."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, seed
from test_news_stories import item, span
from test_news_story_api import setup_story

from navox.news.contracts import NewsError
from navox.news.evidence import admit_claim
from navox.news.registry import revoke_rights
from navox.news.research import changes, coverage, timeline
from navox.news.stories import index_item, record_version


@pytest.mark.asyncio
async def test_timeline_never_invents_event_dates_and_claims_remain_attributed(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, row = await item(db, headline="An instrument was installed")
        definitions = {config.key: config}
        story = await index_item(db, row, definitions, now=NOW)
        result = await timeline(
            db, story.id, definitions, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        entry = result.entries[0]
        assert entry.event_started_at is None and entry.time_basis == "publication_time"
        assert entry.attribution_only and entry.source_url == row.canonical_url
        row.event_started_at = NOW - timedelta(days=2)
        await db.flush()
        result = await timeline(
            db, story.id, definitions, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert result.entries[0].event_started_at == NOW - timedelta(days=2)
        assert result.entries[0].published_at == NOW
        assert result.entries[0].time_basis == "event_time"
        claim = await admit_claim(db, story, span(row), definitions, now=NOW)
        compared = await coverage(
            db, story.id, definitions, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert compared.sources[0].claims[0].id == claim.id
        assert compared.sources[0].claims[0].status == "UNCONFIRMED"
        assert "no political" in compared.explanation
        assert "does not establish an omission" in compared.explanation


@pytest.mark.asyncio
async def test_changes_are_paginated_and_do_not_copy_removed_source_text(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, source, row = await item(db)
        definitions = {config.key: config}
        story = await index_item(db, row, definitions, now=NOW)
        for number in range(2, 5):
            story.version = number
            await record_version(db, story, "SOURCE_UPDATED", now=NOW + timedelta(seconds=number))
        result = await changes(
            db,
            story.id,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW + timedelta(minutes=1),
            limit=2,
        )
        assert [entry.version for entry in result.changes] == [1, 2]
        assert result.next_version == 2 and not result.history_complete
        page = await changes(
            db,
            story.id,
            definitions,
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW + timedelta(minutes=1),
            limit=2,
            since_version=2,
        )
        assert [entry.version for entry in page.changes] == [3, 4] and page.history_complete
        assert row.headline not in result.model_dump_json()
        with pytest.raises(ValueError):
            await changes(
                db,
                story.id,
                definitions,
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
                since_version=10**9,
            )
        await revoke_rights(db, source, now=NOW)
        with pytest.raises(NewsError):
            await changes(db, story.id, definitions, workspace_id=WORKSPACE, user_id=USER, now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", [timeline, coverage, changes])
async def test_research_cannot_read_another_workspace(ai_database, reader):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, row = await item(db)
        story = await index_item(db, row, {config.key: config}, now=NOW)
        with pytest.raises(NewsError, match="story_unavailable"):
            await reader(
                db,
                story.id,
                {config.key: config},
                workspace_id=OTHER_WORKSPACE,
                user_id=OTHER_USER,
                now=NOW,
            )


@pytest.mark.asyncio
async def test_research_endpoints_flags_bounds_and_unknown_stories(subscription_env, monkeypatch):
    from datetime import datetime

    from navox.api import news_stories

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(news_stories, "datetime", Clock)
    env = subscription_env
    story, _ = await setup_story(env)
    prefix = f"/api/v1/news/stories/{story.id}"
    assert (await env.client.get(prefix + "/timeline")).status_code == 503
    assert (await env.client.get(prefix + "/coverage")).status_code == 503
    env.settings.news_deep_research_enabled = True
    env.settings.news_coverage_comparison_enabled = True
    assert (await env.client.get(prefix + "/timeline")).status_code == 200
    assert (await env.client.get(prefix + "/coverage")).status_code == 200
    assert (await env.client.get(prefix + "/changes?limit=1")).json()["current_version"] == 1
    assert (await env.client.get(prefix + "/changes?since_version=9999")).status_code == 422
    assert (await env.client.get(prefix + "/timeline?limit=10000")).status_code == 422
    assert (await env.client.get(f"/api/v1/news/stories/{uuid4()}/timeline")).status_code == 404
    await env.client.post("/api/v1/auth/logout", headers=env.headers)
    for route in ("/timeline", "/coverage", "/changes"):
        assert (await env.client.get(prefix + route)).status_code == 401
