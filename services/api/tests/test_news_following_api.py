"""Following endpoints against an isolated authenticated ASGI fixture, not a live account."""

from datetime import datetime
from uuid import uuid4

import pytest
from test_news_following import version
from test_news_foundation import NOW
from test_news_story_api import setup_story

from navox.api import news, news_following, news_stories
from navox.db.news import NewsStoryPreference


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    for module in (news, news_following, news_stories):
        monkeypatch.setattr(module, "datetime", Clock)


@pytest.mark.asyncio
async def test_follow_updates_baseline_explicit_ack_and_repeat_follow(subscription_env):
    env = subscription_env
    story, _ = await setup_story(env)
    path = f"/api/v1/news/stories/{story.id}/follow"
    assert (
        await env.client.post(path, json={"enabled": True}, headers=env.headers)
    ).status_code == 200
    pref = await env.database.get(NewsStoryPreference, story.id, populate_existing=True)
    assert pref.last_read_version == 1
    await version(env.database, story, 2, {"a": "VERIFIED"})
    await env.database.commit()
    await env.client.post(path, json={"enabled": True}, headers=env.headers)
    response = await env.client.get("/api/v1/news/following/updates")
    data = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert data["entries"][0]["since_version"] == 1
    assert data["entries"][0]["events"][0]["version"] == 2
    assert not data["external_notifications_sent"]
    pref = await env.database.get(NewsStoryPreference, story.id, populate_existing=True)
    assert pref.last_read_version == 1
    seen_path = f"/api/v1/news/following/{story.id}/seen"
    result = await env.client.post(seen_path, json={"observed_version": 2}, headers=env.headers)
    assert result.status_code == 200 and result.json()["acknowledged_version"] == 2
    assert (await env.client.get("/api/v1/news/following/updates")).json()["entries"] == []
    await env.client.post(path, json={"enabled": False}, headers=env.headers)
    pref = await env.database.get(NewsStoryPreference, story.id, populate_existing=True)
    assert pref.last_read_version is None and not pref.followed
    result = await env.client.post(seen_path, json={"observed_version": 2}, headers=env.headers)
    assert result.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        {"observed_version": 0},
        {"observed_version": True},
        {"observed_version": "1"},
        {"observed_version": 1, "verification_status": "VERIFIED"},
    ],
)
async def test_invalid_seen_commands_are_rejected(subscription_env, command):
    env = subscription_env
    story, _ = await setup_story(env)
    result = await env.client.post(
        f"/api/v1/news/following/{story.id}/seen", json=command, headers=env.headers
    )
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_follow_updates_origin_flags_and_ownership(subscription_env):
    env = subscription_env
    story, _ = await setup_story(env)
    path = f"/api/v1/news/following/{story.id}/seen"
    assert (
        await env.client.post(
            path, json={"observed_version": 1}, headers={"Origin": "https://not-navox.example"}
        )
    ).status_code == 403
    env.settings.news_feed_enabled = False
    assert (await env.client.get("/api/v1/news/following/updates")).status_code == 503
    assert (
        await env.client.post(path, json={"observed_version": 1}, headers=env.headers)
    ).status_code == 503
    env.settings.news_feed_enabled = True
    assert (await env.client.get("/api/v1/news/following/updates?limit=26")).status_code == 422
    await env.client.post("/api/v1/auth/logout", headers=env.headers)
    assert (await env.client.get("/api/v1/news/following/updates")).status_code == 401
    response = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": f"follow-other-{uuid4().hex}@example.com",
            "password": "Different-password-long-123456",
            "display_name": "Other reader",
        },
    )
    assert response.status_code == 201
    assert (await env.client.get("/api/v1/news/following/updates")).json()["entries"] == []
    assert (
        await env.client.post(path, json={"observed_version": 1}, headers=env.headers)
    ).status_code == 404
