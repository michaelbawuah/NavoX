"""Perigon metadata through owner-bound credentials and the existing News rights pipeline."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.generic_api import GenericEndpointConfig, _contains_credential
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.generic_registration import ApprovedGenericConfig, approved_generic_connectors
from navox.connectors.outbound import ApprovedHTTPSTransport
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.models import ConnectorConnection, ConnectorDefinition
from navox.news.contracts import (
    Category,
    FeedType,
    NewsError,
    NewsItemInput,
    SourceDefinition,
    aware_utc,
)
from navox.news.feeds import MAX_FEED_BYTES, MAX_FEED_ITEMS, plain_text
from navox.news.images import feed_image


async def approved_api_connection(
    database: AsyncSession,
    settings: Settings,
    definition: SourceDefinition,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> tuple[ConnectorConnection, ApprovedGenericConfig, GenericEndpointConfig]:
    if definition.feed_type != FeedType.API or definition.api_connection_id is None:
        raise NewsError("invalid_source")
    try:
        connection = await owned_connector(
            database,
            connection_id=definition.api_connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=True,
        )
        registered = await database.get(
            ConnectorDefinition, connection.connector_definition_id, populate_existing=True
        )
        approved = next(
            (
                item
                for item in approved_generic_connectors(settings)
                if item.usage == "NEWS"
                and registered is not None
                and item.connector_key == registered.connector_key
            ),
            None,
        )
        if (
            approved is None
            or registered is None
            or registered.manifest != approved.manifest.model_dump(mode="json", by_alias=True)
            or connection.config != approved.config.model_dump(mode="json")
            or connection.provider != approved.config.provider
        ):
            raise NewsError("source_changed")
        endpoint = next(
            (
                item
                for item in approved.config.endpoints
                if item.name == definition.api_endpoint_name
            ),
            None,
        )
        if (
            endpoint is None
            or endpoint.capability not in connection.authorized_capabilities
            or endpoint.capability not in connection.provider_capabilities
            or definition.endpoint != approved.config.base_url + endpoint.path
            or endpoint.static_params.get("source") not in definition.article_domains
        ):
            raise NewsError("source_changed")
        return connection, approved, endpoint
    except (ConnectorAccessDenied, ValueError) as error:
        if isinstance(error, NewsError):
            raise
        raise NewsError("source_unavailable") from None


def parse_perigon(data: bytes, definition: SourceDefinition) -> tuple[list[NewsItemInput], int]:
    if len(data) > MAX_FEED_BYTES:
        raise NewsError("feed_too_large")
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise NewsError("invalid_feed") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise NewsError("invalid_feed")
    articles = payload["articles"]
    if len(articles) > MAX_FEED_ITEMS:
        raise NewsError("feed_too_large")
    items, rejected = [], 0
    category_names = {
        "world": Category.WORLD,
        "us": Category.US,
        "u.s.": Category.US,
        "business": Category.BUSINESS,
        "tech": Category.TECHNOLOGY,
        "technology": Category.TECHNOLOGY,
        "science": Category.SCIENCE,
    }
    for article in articles:
        try:
            if not isinstance(article, dict):
                raise ValueError
            identifier, title, url, published = (
                article.get(field) for field in ("articleId", "title", "url", "pubDate")
            )
            if not all(
                isinstance(value, str) and value for value in (identifier, title, url, published)
            ):
                raise ValueError
            assert isinstance(identifier, str) and isinstance(title, str)
            assert isinstance(url, str) and isinstance(published, str)
            if urlsplit(url).hostname not in definition.article_domains:
                raise ValueError
            source = article.get("source")
            if (
                not isinstance(source, dict)
                or source.get("domain") not in definition.article_domains
            ):
                raise ValueError
            # Provider classifications can select a display category, never source
            # authority, copy independence, verification, rights or retention.
            categories = []
            for category in article.get("categories", []):
                if isinstance(category, dict) and isinstance(category.get("name"), str):
                    mapped = category_names.get(category["name"].casefold())
                    if mapped is not None and mapped not in categories:
                        categories.append(mapped)
            authors = article.get("authorsByline")
            byline = (
                ", ".join(value for value in authors if isinstance(value, str))
                if isinstance(authors, list)
                else ""
            )
            description = article.get("description")
            items.append(
                NewsItemInput(
                    external_id=identifier,
                    headline=plain_text(title, limit=500),
                    canonical_url=url,
                    author=plain_text(byline, limit=200) or None,
                    published_at=aware_utc(
                        datetime.fromisoformat(published.replace("Z", "+00:00"))
                    ),
                    description=plain_text(description, limit=4000)
                    if definition.rights.snippet_storage_allowed and isinstance(description, str)
                    else None,
                    image=feed_image(
                        article.get("imageUrl"),
                        article.get("imageCaption"),
                        article.get("imageAttribution"),
                        definition,
                    )
                    if definition.rights.image_display_allowed
                    else None,
                    categories=tuple(categories) or (definition.category,),
                    language=definition.language,
                    region=definition.region,
                )
            )
        except (ValueError, TypeError, OverflowError):
            rejected += 1
    return items, rejected


async def fetch_api_items(
    database: AsyncSession,
    settings: Settings,
    definition: SourceDefinition,
    *,
    workspace_id: UUID,
    user_id: UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[NewsItemInput], int]:
    connection, approved, endpoint = await approved_api_connection(
        database, settings, definition, workspace_id=workspace_id, user_id=user_id
    )
    try:
        lease = await SecretBroker(settings).lease(
            database,
            connection_id=connection.id,
            workspace_id=workspace_id,
            user_id=user_id,
            purpose="sync.read",
            names={approved.config.token_secret_name},
        )
        with lease:
            credential = lease.get(approved.config.token_secret_name)
            params = {**endpoint.static_params, "size": str(endpoint.page_size)}
            async with (
                asyncio.timeout(20),
                httpx.AsyncClient(
                    transport=transport or ApprovedHTTPSTransport(approved.config.base_url),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=15,
                ) as client,
                client.stream(
                    "GET",
                    definition.endpoint,
                    params=params,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {credential}"},
                ) as response,
            ):
                if response.status_code == 429:
                    raise NewsError("rate_limited")
                if response.status_code != 200:
                    # Failure bodies can echo the API key. Never read or log them.
                    raise NewsError("feed_unavailable")
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != "application/json"
                ):
                    raise NewsError("invalid_feed")
                data = bytearray()
                async for part in response.aiter_bytes():
                    if len(data) + len(part) > MAX_FEED_BYTES:
                        raise NewsError("feed_too_large")
                    data.extend(part)
                try:
                    payload = json.loads(data)
                except (ValueError, UnicodeError, RecursionError):
                    raise NewsError("invalid_feed") from None
                if _contains_credential(payload, credential):
                    raise NewsError("invalid_feed")
        # A pause, permission change or disconnection during retrieval prevents
        # publication. Credentials never enter source rows or workflow payloads.
        await approved_api_connection(
            database, settings, definition, workspace_id=workspace_id, user_id=user_id
        )
        return parse_perigon(bytes(data), definition)
    except (SecretBrokerError, ConnectorRuntimeError, httpx.HTTPError, TimeoutError):
        raise NewsError("feed_unavailable") from None
