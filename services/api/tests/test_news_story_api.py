from datetime import datetime
from uuid import UUID, uuid4

import pytest
from test_news_foundation import NOW, definition, rss

from navox.api import news, news_stories
from navox.db.news import NewsSource
from navox.news.feeds import parse_feed
from navox.news.ingestion import store_item
from navox.news.registry import current_rights
from navox.news.stories import index_item


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(news, "datetime", Clock)
    monkeypatch.setattr(news_stories, "datetime", Clock)


async def setup_story(env):
    config = definition()
    env.settings.news_feed_enabled = True
    env.settings.news_source_catalog = [config.model_dump(mode="json")]
    response = await env.client.post(
        f"/api/v1/news/sources/{config.key}/activate",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    source = await env.database.get(NewsSource, UUID(response.json()["id"]))
    row = await store_item(
        env.database,
        source,
        await current_rights(env.database, source),
        config,
        parse_feed(rss(), config)[0][0],
        now=NOW,
    )
    story = await index_item(env.database, row, {config.key: config}, now=NOW)
    await env.database.commit()
    return story, source


@pytest.mark.asyncio
async def test_feed_preferences_saves_and_revocation(subscription_env):
    env = subscription_env
    story, source = await setup_story(env)
    for path in ("", "/top", "/for-you", "/categories/science"):
        response = await env.client.get("/api/v1/news" + path)
        assert response.status_code == 200
        assert (
            len(response.json()) == 1 and response.json()[0]["verification_status"] == "UNCONFIRMED"
        )
    assert (await env.client.get("/api/v1/news/categories/business")).json() == []
    assert (await env.client.get("/api/v1/news/saved")).json() == []
    path = f"/api/v1/news/stories/{story.id}"
    assert (
        await env.client.post(path + "/save", json={"enabled": True}, headers=env.headers)
    ).status_code == 200
    assert len((await env.client.get("/api/v1/news/saved")).json()) == 1
    assert (await env.client.get(path + "/sources")).json()[0]["source_name"] == "Science Fixture"
    assert (await env.client.get(path + "/claims")).json() == []
    assert (await env.client.get(path + "/updates")).json()[0]["change_kind"] == "DISCOVERED"
    assert (await env.client.get(path + "/updates?since_version=1")).json() == []
    response = await env.client.patch(
        "/api/v1/news/preferences",
        json={"topics": ["space"], "categories": ["science"]},
        headers=env.headers,
    )
    assert response.json()["topics"] == ["space"]
    response = await env.client.patch(
        "/api/v1/news/preferences", json={"region": "us"}, headers=env.headers
    )
    assert response.json()["topics"] == ["space"] and response.json()["region"] == "us"
    assert (
        await env.client.patch(
            "/api/v1/news/preferences", json={"political_profile": "inferred"}, headers=env.headers
        )
    ).status_code == 422
    assert (
        await env.client.post(path + "/dismiss", json={"enabled": True}, headers=env.headers)
    ).status_code == 200
    assert (await env.client.get("/api/v1/news/top")).json() == []
    assert (await env.client.get(path)).status_code == 200
    await env.client.post(
        f"/api/v1/news/sources/{source.id}/disable",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    assert (await env.client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_news_cannot_set_verification_or_access_other_owner(subscription_env):
    env = subscription_env
    story, _ = await setup_story(env)
    path = f"/api/v1/news/stories/{story.id}"
    assert (
        await env.client.post(path + "/verify", json={"enabled": True}, headers=env.headers)
    ).status_code == 422
    assert (
        await env.client.post(
            path + "/save",
            json={"enabled": True, "verification_status": "VERIFIED"},
            headers=env.headers,
        )
    ).status_code == 422
    assert (
        await env.client.post(
            path + "/save", json={"enabled": True}, headers={"Origin": "https://attacker.example"}
        )
    ).status_code == 403
    await env.client.post("/api/v1/auth/logout", headers=env.headers)
    assert (await env.client.get(path)).status_code == 401
    response = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "news-second@example.com",
            "password": "Unrelated-strong-password-1287",
            "display_name": "Other reader",
        },
        headers=env.headers,
    )
    assert response.status_code == 201
    assert (await env.client.get(path)).status_code == 404
    assert (await env.client.get(path + "/claims")).status_code == 404
    assert (await env.client.get(path + "/updates")).status_code == 404
    assert (await env.client.get("/api/v1/news/preferences")).json()["topics"] == []
    assert (await env.client.get("/api/v1/news/top")).json() == []
