from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.main import app
from navox.db.base import Base
from navox.db.session import get_database_session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client
    app.dependency_overrides.pop(get_database_session, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_registration_creates_a_personal_workspace_and_session(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@example.com",
            "password": "twelve-character-password",
            "display_name": "Owner",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "email", "display_name", "workspace"}
    assert body["email"] == "owner@example.com"
    assert body["display_name"] == "Owner"
    assert body["workspace"]["name"] == "Owner's workspace"
    assert body["workspace"]["workspace_type"] == "personal"
    assert "password" not in response.text
    assert "httponly" in response.headers["set-cookie"].casefold()
    assert "samesite=lax" in response.headers["set-cookie"].casefold()

    current_account = await client.get("/api/v1/auth/me")

    assert current_account.status_code == 200
    assert current_account.json()["workspace"]["id"] == body["workspace"]["id"]


@pytest.mark.asyncio
async def test_registration_rejects_a_duplicate_email(client: AsyncClient) -> None:
    payload = {
        "email": "owner@example.com",
        "password": "twelve-character-password",
        "display_name": "Owner",
    }
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201

    duplicate = await client.post("/api/v1/auth/register", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "An account with that email already exists"


@pytest.mark.asyncio
async def test_login_and_logout_revoke_the_server_side_session(client: AsyncClient) -> None:
    payload = {
        "email": "owner@example.com",
        "password": "twelve-character-password",
        "display_name": "Owner",
    }
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    invalid_login = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": "incorrect-password"},
    )
    assert invalid_login.status_code == 401

    valid_login = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )

    assert valid_login.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 200



@pytest.mark.asyncio
async def test_extension_session_is_bearer_scoped_revocable_and_not_cookie_backed(
    client: AsyncClient,
) -> None:
    registration = {
        "email": "extension@example.com",
        "password": "twelve-character-password",
        "display_name": "Extension Owner",
    }
    assert (await client.post("/api/v1/auth/register", json=registration)).status_code == 201
    await client.post("/api/v1/auth/logout")

    login = await client.post(
        "/api/v1/auth/extension/login",
        json={"email": registration["email"], "password": registration["password"]},
    )

    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["account"]["email"] == registration["email"]
    assert "set-cookie" not in login.headers

    token = body["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    current_account = await client.get("/api/v1/auth/me", headers=headers)
    assert current_account.status_code == 200
    assert current_account.json()["email"] == registration["email"]

    logout = await client.post("/api/v1/auth/extension/logout", headers=headers)
    assert logout.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_extension_login_rejects_invalid_credentials_and_malformed_bearer(
    client: AsyncClient,
) -> None:
    registration = {
        "email": "extension-invalid@example.com",
        "password": "twelve-character-password",
        "display_name": "Owner",
    }
    assert (await client.post("/api/v1/auth/register", json=registration)).status_code == 201

    invalid = await client.post(
        "/api/v1/auth/extension/login",
        json={"email": registration["email"], "password": "wrong-password"},
    )
    assert invalid.status_code == 401

    malformed = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Basic not-a-bearer-token"},
    )
    assert malformed.status_code == 401
