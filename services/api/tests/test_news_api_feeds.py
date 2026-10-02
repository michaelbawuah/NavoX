"""Bounded API metadata: rights, tenant credentials, approval and failure redaction."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from navox.api.generic_rest import queue
from navox.connectors.catalog import build_connector_registry
from navox.connectors.generic_registration import ApprovedGenericConfig
from navox.connectors.secrets import SecretBroker
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.news import NewsItem
from navox.news.api_feeds import parse_perigon
from navox.news.contracts import NewsError, SourceDefinition
from navox.news.ingestion import ingest_source, visible_items
from navox.news.registry import activate_source

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
TOKEN = "synthetic-perigon-credential-never-persist-plain"


def approval():
    return ApprovedGenericConfig.model_validate(
        {
            "id": "perigon-trial",
            "usage": "NEWS",
            "config": {
                "display_name": "Perigon trial news",
                "provider": "perigon",
                "base_url": "https://api.perigon.io",
                "endpoints": [
                    {
                        "name": "bbc",
                        "path": "/v1/articles/all",
                        "capability": "news.bbc.read",
                        "resource_type": "news.bbc",
                        "items_field": "articles",
                        "id_field": "articleId",
                        "subject_field": "title",
                        "content_field": None,
                        "occurred_at_field": "pubDate",
                        "source_url_field": "url",
                        "page_size_param": "size",
                        "page_size": 10,
                        "static_params": {"source": "bbc.com", "language": "en", "sortBy": "date"},
                    }
                ],
            },
        }
    )


def definition(connection_id):
    return SourceDefinition.model_validate(
        {
            "key": "bbc-perigon",
            "name": "BBC via Perigon",
            "domain": "bbc.com",
            "source_type": "SYNDICATION",
            "feed_type": "API",
            "endpoint": "https://api.perigon.io/v1/articles/all",
            "api_connection_id": str(connection_id),
            "api_endpoint_name": "bbc",
            "article_domains": ["bbc.com", "www.bbc.com"],
            "category": "world",
            "independence_group": "perigon-trial",
            "rights": {
                "metadata_storage_allowed": True,
                "retention_days": 1,
                "permission_reference": "https://perigon.io/CSA.pdf",
                "reviewed_at": (NOW - timedelta(days=1)).isoformat(),
                "expires_at": (NOW + timedelta(days=7)).isoformat(),
            },
        }
    )


def article(**changes):
    return {
        "articleId": "bbc-article",
        "title": "Source headline",
        "url": "https://www.bbc.com/news/fixture",
        "pubDate": "2026-10-02T10:00:00Z",
        "source": {"domain": "bbc.com"},
        "categories": [{"name": "Science"}],
        "description": "Unlicensed snippet",
        "content": "UNLICENSED FULL ARTICLE",
        "summary": "Unqualified provider-generated summary",
        "imageUrl": "https://www.bbc.com/image.jpg",
        "verdict": "VERIFIED",
        **changes,
    }


async def setup(factory):
    approved = approval()
    settings = Settings(
        _env_file=None,
        connector_secret_encryption_key=Fernet.generate_key().decode(),
        generic_rest_connectors=[approved.model_dump(mode="json")],
    )
    async with factory() as db:
        user, workspace = User(email=f"{uuid4()}@example.com"), Workspace(name="News trial")
        db.add_all([user, workspace])
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace.id, user_id=user.id))
        registered = ConnectorDefinition(
            connector_key=approved.connector_key,
            version=approved.manifest.version,
            display_name=approved.manifest.display_name,
            connector_class="GENERIC_API",
            trust_level="WORKSPACE_PRIVATE",
            manifest=approved.manifest.model_dump(mode="json", by_alias=True),
        )
        db.add(registered)
        await db.flush()
        connection = ConnectorConnection(
            connector_definition_id=registered.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="perigon",
            external_account_id="synthetic",
            authorized_capabilities=["news.bbc.read"],
            provider_capabilities=["news.bbc.read"],
            config=approved.config.model_dump(mode="json"),
        )
        db.add(connection)
        await db.flush()
        await SecretBroker(settings).store(
            db,
            {"API_TOKEN": TOKEN},
            connection_id=connection.id,
            workspace_id=workspace.id,
            user_id=user.id,
        )
        config = definition(connection.id)
        source = await activate_source(
            db, config, workspace_id=workspace.id, user_id=user.id, now=NOW, settings=settings
        )
        await db.commit()
        return settings, config, source.id, workspace.id, user.id, connection.id


def test_api_metadata_does_not_copy_content_images_or_provider_verdicts():
    config = definition(uuid4())
    items, rejected = parse_perigon(json.dumps({"articles": [article()]}).encode(), config)
    assert rejected == 0 and len(items) == 1
    result = items[0].model_dump_json()
    assert items[0].description is None
    assert items[0].categories == ("science",)
    assert items[0].published_at == datetime(2026, 10, 2, 10, tzinfo=UTC)
    for forbidden in ("UNLICENSED", "Unqualified", "image.jpg", "VERIFIED"):
        assert forbidden not in result


@pytest.mark.parametrize(
    "changes",
    [
        {"pubDate": None},
        {"pubDate": "not-a-date"},
        {"pubDate": "2026-10-02T10:00:00"},
        {"url": "https://attacker.example/article"},
        {"source": {"domain": "attacker.example"}},
        {"title": ""},
        {"articleId": None},
    ],
)
def test_unregistered_links_and_missing_dates_are_never_invented(changes):
    items, rejected = parse_perigon(
        json.dumps({"articles": [article(**changes)]}).encode(), definition(uuid4())
    )
    assert not items and rejected == 1


def test_operational_approvals_keep_identity_and_news_is_not_generic_polling():
    approved = approval()
    operational = approved.model_copy(update={"usage": "OPERATIONAL"})
    assert operational.connector_key != approved.connector_key
    assert (
        operational._canonical_config()
        == json.dumps(
            operational.config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    )
    settings = Settings(_env_file=None, generic_rest_connectors=[approved.model_dump(mode="json")])
    assert approved.connector_key not in {
        m.id for m in build_connector_registry(settings).manifests()
    }
    changed = approved.model_dump(mode="json")
    changed["config"]["endpoints"][0]["content_field"] = "content"
    with pytest.raises(ValueError):
        ApprovedGenericConfig.model_validate(changed)


@pytest.mark.asyncio
async def test_encrypted_owner_bound_ingestion_and_replay(ai_database):
    settings, config, source_id, workspace_id, user_id, connection_id = await setup(ai_database)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)
        assert request.url.params["source"] == "bbc.com"
        return httpx.Response(200, json={"articles": [article()]})

    async with ai_database() as db, httpx.AsyncClient() as client:
        request_id = uuid4()
        for _ in range(2):
            result = await ingest_source(
                db,
                client,
                {config.key: config},
                source_id=source_id,
                workspace_id=workspace_id,
                user_id=user_id,
                request_id=request_id,
                now=NOW,
                settings=settings,
                api_transport=httpx.MockTransport(handler),
            )
            assert result.status == "COMPLETED" and result.stored_count == 1
        await db.commit()
        assert len(calls) == 1
        items = await visible_items(
            db, {config.key: config}, workspace_id=workspace_id, user_id=user_id, now=NOW
        )
        assert len(items) == 1 and items[0].source_name == "BBC via Perigon"
        assert items[0].description is None
        row = await db.scalar(select(NewsItem).where(NewsItem.source_id == source_id))
        assert row.description is None
        audits = list(await db.scalars(select(AuditEvent)))
        assert TOKEN not in json.dumps([event.event_metadata for event in audits])
        connection = await db.get(ConnectorConnection, connection_id)
        # The normal setup flow persists the credential but buys no generic sync.
        settings.ai_provider = "automatic"
        assert await queue(db, connection, uuid4(), settings) == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "revoke", ["connection", "capability", "approval", "rights", "owner", "membership"]
)
async def test_revocations_block_before_egress(ai_database, revoke):
    settings, config, source_id, workspace_id, user_id, connection_id = await setup(ai_database)
    calls = []

    def forbidden(request):
        calls.append(request)
        raise AssertionError("No request is authorized")

    async with ai_database() as db, httpx.AsyncClient() as client:
        connection = await db.get(ConnectorConnection, connection_id)
        if revoke == "connection":
            connection.status = "DISCONNECTED"
        elif revoke == "capability":
            connection.authorized_capabilities = []
        elif revoke == "approval":
            settings.generic_rest_connectors = []
        elif revoke == "owner":
            (await db.get(User, user_id)).agent_paused = True
        elif revoke == "membership":
            await db.delete(await db.get(WorkspaceMembership, (workspace_id, user_id)))
        await db.commit()
        now = NOW + timedelta(days=8) if revoke == "rights" else NOW
        try:
            result = await ingest_source(
                db,
                client,
                {config.key: config},
                source_id=source_id,
                workspace_id=workspace_id,
                user_id=user_id,
                request_id=uuid4(),
                now=now,
                settings=settings,
                api_transport=httpx.MockTransport(forbidden),
            )
            assert result.status == "FAILED"
        except NewsError:
            pass
        assert calls == []
        assert await db.scalar(select(NewsItem).where(NewsItem.source_id == source_id)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 500, 302, 200])
async def test_provider_failure_or_credential_reflection_never_stores_body(ai_database, status):
    settings, config, source_id, workspace_id, user_id, _ = await setup(ai_database)

    def handler(_):
        return httpx.Response(status, json={"articles": [article(title=TOKEN)]})

    async with ai_database() as db, httpx.AsyncClient() as client:
        result = await ingest_source(
            db,
            client,
            {config.key: config},
            source_id=source_id,
            workspace_id=workspace_id,
            user_id=user_id,
            request_id=uuid4(),
            now=NOW,
            settings=settings,
            api_transport=httpx.MockTransport(handler),
        )
        assert result.status == "FAILED"
        assert result.error_code in {"feed_unavailable", "rate_limited", "invalid_feed"}
        assert TOKEN not in result.error_code
        assert await db.scalar(select(NewsItem).where(NewsItem.source_id == source_id)) is None


@pytest.mark.asyncio
async def test_another_owner_cannot_activate_shared_credential_reference(ai_database):
    settings, config, _, workspace_id, _, _ = await setup(ai_database)
    async with ai_database() as db:
        other = User(email=f"{uuid4()}@example.com")
        db.add(other)
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=workspace_id, user_id=other.id))
        await db.commit()
        with pytest.raises(NewsError, match="source_unavailable"):
            await activate_source(
                db, config, workspace_id=workspace_id, user_id=other.id, now=NOW, settings=settings
            )
