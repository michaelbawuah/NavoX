from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from navox.api.main import app
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import Commitment
from navox.db.session import get_database_session


@pytest_asyncio.fixture
async def today_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, session_factory
    app.dependency_overrides.pop(get_database_session, None)
    app.dependency_overrides.pop(get_settings, None)
    await engine.dispose()


async def register(client: AsyncClient, email: str = "owner@example.com") -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "twelve-character-password",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201
    return response.json()


async def create_manual(
    client: AsyncClient,
    *,
    title: str,
    commitment_type: str = "task",
    priority: int = 3,
    due_at: datetime | None = None,
    request_id: UUID | None = None,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/commitments",
        json={
            "request_id": str(request_id or uuid4()),
            "type": commitment_type,
            "title": title,
            "priority": priority,
            "due_at": due_at.isoformat() if due_at is not None else None,
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_manual_create_is_idempotent_and_today_refreshes_after_completion(
    today_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = today_environment
    await register(client)
    request_id = uuid4()

    first = await create_manual(
        client,
        title="Submit systems report",
        priority=5,
        request_id=request_id,
    )
    second = await create_manual(
        client,
        title="Submit systems report",
        priority=5,
        request_id=request_id,
    )

    assert first["id"] == second["id"]

    today = await client.get("/api/v1/today", params={"timezone": "UTC"})
    assert today.status_code == 200
    body = today.json()
    assert body["total"] == 1
    assert [item["title"] for item in body["needs_attention"]] == ["Submit systems report"]

    completed = await client.post(f"/api/v1/commitments/{first['id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    repeated = await client.post(f"/api/v1/commitments/{first['id']}/complete")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "completed"

    refreshed = await client.get("/api/v1/today", params={"timezone": "UTC"})
    assert refreshed.status_code == 200
    assert refreshed.json()["total"] == 0


@pytest.mark.asyncio
async def test_today_projects_attention_upcoming_renewals_waiting_and_candidate_review(
    today_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = today_environment
    account = await register(client)
    workspace = account["workspace"]
    assert isinstance(workspace, dict)
    user_id = UUID(str(account["id"]))
    workspace_id = UUID(str(workspace["id"]))
    now = datetime.now(UTC)

    urgent = await create_manual(client, title="Finish lab", priority=5)
    renewal = await create_manual(
        client,
        title="Renew domain",
        commitment_type="renewal",
        due_at=now + timedelta(days=5),
    )
    waiting = await create_manual(
        client,
        title="Professor response",
        commitment_type="follow_up",
        due_at=now + timedelta(days=3),
    )
    waiting_response = await client.post(f"/api/v1/commitments/{waiting['id']}/waiting")
    assert waiting_response.status_code == 200

    async with session_factory() as session:
        session.add(
            Commitment(
                user_id=user_id,
                workspace_id=workspace_id,
                commitment_type="promise",
                title="Send project notes",
                description=None,
                status="candidate",
                priority=3,
                due_at=None,
                confidence=0.72,
                created_by="ai",
                dedupe_key="candidate-" + uuid4().hex,
            )
        )
        session.add(
            Commitment(
                user_id=user_id,
                workspace_id=workspace_id,
                commitment_type="task",
                title="Future hidden task",
                description=None,
                status="confirmed",
                priority=5,
                due_at=None,
                confidence=1.0,
                created_by="user",
                dedupe_key="future-" + uuid4().hex,
                valid_from=now + timedelta(days=2),
            )
        )
        await session.commit()

    response = await client.get("/api/v1/today", params={"timezone": "UTC"})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 4
    attention_titles = {item["title"] for item in body["needs_attention"]}
    assert {"Finish lab", "Send project notes"} <= attention_titles
    assert "Future hidden task" not in attention_titles

    coming_titles = {item["title"] for item in body["coming_up"]}
    assert {"Renew domain", "Professor response"} <= coming_titles
    assert {item["title"] for item in body["renewals"]} == {"Renew domain"}
    assert {item["title"] for item in body["waiting_on"]} == {"Professor response"}

    resumed = await client.post(f"/api/v1/commitments/{waiting['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "confirmed"

    invalid_resume = await client.post(f"/api/v1/commitments/{urgent['id']}/resume")
    assert invalid_resume.status_code == 409

    assert renewal["id"]


@pytest.mark.asyncio
async def test_today_queries_are_read_only_bounded_and_workspace_scoped(
    today_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = today_environment
    await register(client)
    await create_manual(
        client,
        title="Renew hosting",
        commitment_type="renewal",
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    await create_manual(
        client,
        title="Send mentor update",
        commitment_type="promise",
        priority=4,
    )

    renewals = await client.post(
        "/api/v1/today/query",
        json={"query": "What renewals are coming up?", "timezone": "UTC"},
    )
    assert renewals.status_code == 200
    assert renewals.json()["intent"] == "renewals"
    assert [item["title"] for item in renewals.json()["items"]] == ["Renew hosting"]

    promises = await client.post(
        "/api/v1/today/query",
        json={"query": "What promises have I made?", "timezone": "UTC"},
    )
    assert promises.status_code == 200
    assert promises.json()["intent"] == "promises"
    assert [item["title"] for item in promises.json()["items"]] == ["Send mentor update"]

    unsupported = await client.post(
        "/api/v1/today/query",
        json={"query": "Send an email for me", "timezone": "UTC"},
    )
    assert unsupported.status_code == 200
    assert unsupported.json()["intent"] == "unsupported"
    assert unsupported.json()["items"] == []

    invalid_timezone = await client.get(
        "/api/v1/today", params={"timezone": "Definitely/Not_A_Timezone"}
    )
    assert invalid_timezone.status_code == 422

    await register(client, email="other@example.com")
    isolated = await client.get("/api/v1/today", params={"timezone": "UTC"})
    assert isolated.status_code == 200
    assert isolated.json()["total"] == 0
