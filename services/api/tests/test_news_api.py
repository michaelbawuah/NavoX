"""Authenticated news source control and projections; no model/provider requests."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from test_news_foundation import NOW, definition, rss

from navox.db.news import NewsSource
from navox.news.feeds import parse_feed
from navox.news.ingestion import store_item
from navox.news.registry import current_rights


@pytest.fixture(autouse=True)
def news_clock(monkeypatch):
    from navox.api import news

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(news, "datetime", Clock)


@pytest.mark.asyncio
async def test_source_catalog_requires_auth_and_never_accepts_client_rights(subscription_env):
    env = subscription_env
    assert (await env.client.get("/api/v1/news/items")).status_code == 503
    config = definition()
    env.settings.news_feed_enabled = True
    env.settings.news_source_catalog = [config.model_dump(mode="json")]
    response = await env.client.get("/api/v1/news/sources")
    assert response.status_code == 200 and response.json()[0]["status"] == "disabled"
    url = f"/api/v1/news/sources/{config.key}/activate"
    assert (
        await env.client.post(
            url,
            json={"request_id": str(uuid4()), "rights": {"full_text_storage_allowed": True}},
            headers=env.headers,
        )
    ).status_code == 422
    assert (
        await env.client.post(
            url, json={"request_id": str(uuid4())}, headers={"Origin": "https://attacker.example"}
        )
    ).status_code == 403
    result = await env.client.post(url, json={"request_id": str(uuid4())}, headers=env.headers)
    assert result.status_code == 201
    identifier = UUID(result.json()["id"])
    row = await env.database.get(NewsSource, identifier)
    assert row.workspace_id == env.workspace_id and row.user_id == env.user_id
    rights = await current_rights(env.database, row)
    assert rights.policy["full_text_storage_allowed"] is False
    await env.client.post("/api/v1/auth/logout", headers=env.headers)
    assert (await env.client.get("/api/v1/news/sources")).status_code == 401


@pytest.mark.asyncio
async def test_items_and_revocation_use_real_owner_account(subscription_env):
    env = subscription_env
    config = definition()
    env.settings.news_feed_enabled = True
    env.settings.news_source_catalog = [config.model_dump(mode="json")]
    result = await env.client.post(
        f"/api/v1/news/sources/{config.key}/activate",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    identifier = UUID(result.json()["id"])
    row = await env.database.get(NewsSource, identifier)
    rights = await current_rights(env.database, row)
    await store_item(env.database, row, rights, config, parse_feed(rss(), config)[0][0], now=NOW)
    await env.database.commit()
    response = await env.client.get("/api/v1/news/items")
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]["source_name"] == "Science Fixture"
    assert "PRIVATE FULL TEXT" not in response.text
    env.settings.news_feed_enabled = False
    result = await env.client.post(
        f"/api/v1/news/sources/{identifier}/disable",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    assert result.status_code == 200
    env.settings.news_feed_enabled = True
    assert (await env.client.get("/api/v1/news/items")).json() == []
    result = await env.client.post(
        f"/api/v1/news/sources/{config.key}/activate",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    assert result.status_code == 201
    await env.database.refresh(row)
    assert row.rights_version == 2
    current = await current_rights(env.database, row)
    assert current.revoked_at is None and current.id != rights.id


@pytest.mark.asyncio
async def test_unknown_source_is_not_a_network_proxy(subscription_env):
    env = subscription_env
    env.settings.news_feed_enabled = True
    result = await env.client.post(
        "/api/v1/news/sources/arbitrary-domain/activate",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    assert result.status_code == 404
    result = await env.client.post(
        f"/api/v1/news/sources/{uuid4()}/refresh",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    assert result.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "feed,timeline,comparison",
    [
        (False, True, True),
        (True, False, False),
        (True, True, False),
        (True, False, True),
        (True, True, True),
    ],
)
async def test_availability_separates_bounded_views_from_reserved_research(
    subscription_env, feed, timeline, comparison
):
    env = subscription_env
    env.settings.news_feed_enabled = feed
    env.settings.news_deep_research_enabled = timeline
    env.settings.news_coverage_comparison_enabled = comparison
    response = await env.client.get("/api/v1/news/availability")
    assert response.status_code == 200
    result = response.json()
    assert result["timeline"] is (feed and timeline)
    assert result["source_comparison"] is (feed and comparison)
    assert result["deep_research"] is False
    assert result["coverage_comparison"] is False
    assert "x_trends" not in result
    await env.client.post("/api/v1/auth/logout", headers=env.headers)
    assert (await env.client.get("/api/v1/news/availability")).status_code == 401


@pytest.mark.asyncio
async def test_source_options_require_the_current_accounts_api_connection(subscription_env):
    from test_news_api_feeds import definition as api_definition
    from test_news_api_feeds import setup

    from navox.db.models import ConnectorConnection

    env = subscription_env
    approved_settings, private_config, _, _, _, private_connection_id = await setup(env.factory)
    env.settings.news_feed_enabled = True
    env.settings.generic_rest_connectors = approved_settings.generic_rest_connectors
    public_config = definition()
    env.settings.news_source_catalog = [
        private_config.model_dump(mode="json"),
        public_config.model_dump(mode="json"),
    ]

    # The foreign owner's catalog template is not an option for this signed-in account.
    response = await env.client.get("/api/v1/news/sources")
    assert response.status_code == 200
    assert [option["key"] for option in response.json()] == [public_config.key]
    denied = await env.client.post(
        f"/api/v1/news/sources/{private_config.key}/activate",
        json={"request_id": str(uuid4())},
        headers=env.headers,
    )
    assert denied.status_code == 404

    # An exact approved connector belonging to this account remains selectable.
    private_connection = await env.database.get(ConnectorConnection, private_connection_id)
    owned_connection = ConnectorConnection(
        connector_definition_id=private_connection.connector_definition_id,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        provider=private_connection.provider,
        external_account_id="owned-news-fixture",
        authorized_capabilities=list(private_connection.authorized_capabilities),
        provider_capabilities=list(private_connection.provider_capabilities),
        config=private_connection.config,
    )
    env.database.add(owned_connection)
    await env.database.commit()
    owned_config = api_definition(owned_connection.id)
    env.settings.news_source_catalog = [
        owned_config.model_dump(mode="json"),
        public_config.model_dump(mode="json"),
    ]
    response = await env.client.get("/api/v1/news/sources")
    assert response.status_code == 200
    assert [option["key"] for option in response.json()] == [owned_config.key, public_config.key]

    # Losing read consent removes the source option, without broadening its scope.
    owned_connection.authorized_capabilities = []
    await env.database.commit()
    response = await env.client.get("/api/v1/news/sources")
    assert [option["key"] for option in response.json()] == [public_config.key]
