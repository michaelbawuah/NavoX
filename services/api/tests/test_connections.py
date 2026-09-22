from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api import connections
from navox.api.main import app
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.session import get_database_session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        google_oauth_client_id="google-client-id",
        google_oauth_client_secret="google-client-secret",
        google_token_encryption_key=Fernet.generate_key().decode("utf-8"),
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    await engine.dispose()


async def register(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "twelve-character-password",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_google_start_uses_pkce_identity_scopes_and_server_state(client: AsyncClient) -> None:
    await register(client)

    response = await client.get("/api/v1/connections/google/start")

    assert response.status_code == 200
    query = parse_qs(urlparse(response.json()["authorization_url"]).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 32
    assert "include_granted_scopes" not in query
    assert "gmail" not in query["scope"][0]
    assert "calendar" not in query["scope"][0]
    assert "drive" not in query["scope"][0]


@pytest.mark.asyncio
async def test_callback_creates_scoped_connection_without_exposing_tokens(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register(client)
    start = await client.get("/api/v1/connections/google/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

    async def exchange(_: str, __: str, ___: Settings) -> dict[str, object]:
        return {
            "access_token": "access-token-that-must-not-persist",
            "refresh_token": "refresh-token-that-must-not-return",
            "expires_in": 3600,
            "scope": "openid https://www.googleapis.com/auth/userinfo.email",
        }

    async def profile(_: str) -> dict[str, object]:
        return {
            "sub": "google-account-123",
            "email": "connected@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(connections, "exchange_authorization_code", exchange)
    monkeypatch.setattr(connections, "fetch_google_profile", profile)

    callback = await client.get(
        f"/api/v1/connections/google/callback?code=google-code&state={state}",
        follow_redirects=False,
    )
    listed = await client.get("/api/v1/connections/google")

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("?google_connection=connected")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "active"
    assert "refresh-token-that-must-not-return" not in listed.text
    assert "access-token-that-must-not-persist" not in listed.text


@pytest.mark.asyncio
async def test_connection_health_refreshes_without_returning_a_token(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register(client)
    start = await client.get("/api/v1/connections/google/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

    async def exchange(_: str, __: str, ___: Settings) -> dict[str, object]:
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }

    async def profile(_: str) -> dict[str, object]:
        return {
            "sub": "google-account-123",
            "email": "connected@example.com",
            "email_verified": True,
        }

    async def refresh(_: str, __: Settings) -> dict[str, object]:
        return {"access_token": "new-access-token", "expires_in": 3600}

    monkeypatch.setattr(connections, "exchange_authorization_code", exchange)
    monkeypatch.setattr(connections, "fetch_google_profile", profile)
    monkeypatch.setattr(connections, "refresh_google_access_token", refresh)

    assert (
        await client.get(
            f"/api/v1/connections/google/callback?code=google-code&state={state}",
            follow_redirects=False,
        )
    ).status_code == 303
    connection_id = (await client.get("/api/v1/connections/google")).json()[0]["id"]

    health = await client.post(f"/api/v1/connections/google/{connection_id}/health")

    assert health.status_code == 200
    assert health.json()["healthy"] is True
    assert "new-access-token" not in health.text


@pytest.mark.asyncio
async def test_callback_rejects_an_unrequested_scope(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await register(client)
    start = await client.get("/api/v1/connections/google/start")
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

    async def exchange(_: str, __: str, ___: Settings) -> dict[str, object]:
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "scope": "openid https://www.googleapis.com/auth/gmail.readonly",
        }

    async def profile(_: str) -> dict[str, object]:
        return {
            "sub": "google-account-123",
            "email": "connected@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(connections, "exchange_authorization_code", exchange)
    monkeypatch.setattr(connections, "fetch_google_profile", profile)

    callback = await client.get(
        f"/api/v1/connections/google/callback?code=google-code&state={state}",
        follow_redirects=False,
    )

    assert callback.status_code == 303
    assert callback.headers["location"].endswith("?google_connection=scope_mismatch")
    assert (await client.get("/api/v1/connections/google")).json() == []
