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
from navox.db.models import Commitment, ProactiveSignal
from navox.db.session import get_database_session
from navox.proactive.activities import next_briefing_delay
from navox.proactive.engine import evaluate_workspace


@pytest_asyncio.fixture
async def proactive_environment() -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession]]
]:
    engine = create_async_engine("sqlite+aiosqlite://")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings()
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


async def create_commitment(
    client: AsyncClient,
    *,
    title: str,
    commitment_type: str,
    priority: int,
    due_at: datetime | None = None,
    description: str | None = None,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/commitments",
        json={
            "request_id": str(uuid4()),
            "type": commitment_type,
            "title": title,
            "description": description,
            "priority": priority,
            "due_at": due_at.isoformat() if due_at else None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_briefing_surfaces_deadlines_renewals_promises_and_waiting(
    proactive_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = proactive_environment
    await register(client)
    now = datetime.now(UTC)

    await create_commitment(
        client,
        title="Submit final report",
        commitment_type="deadline",
        priority=5,
        due_at=now + timedelta(hours=3),
    )
    await create_commitment(
        client,
        title="Renew domain",
        commitment_type="renewal",
        priority=4,
        due_at=now + timedelta(days=5),
    )
    await create_commitment(
        client,
        title="Send promised update",
        commitment_type="promise",
        priority=5,
        due_at=now + timedelta(days=1),
    )
    waiting = await create_commitment(
        client,
        title="Wait for advisor reply",
        commitment_type="follow_up",
        priority=4,
    )
    assert (await client.post(f"/api/v1/commitments/{waiting['id']}/waiting")).status_code == 200

    async with session_factory() as session:
        commitment = await session.get(Commitment, UUID(waiting["id"]))
        assert commitment is not None
        commitment.waiting_since = now - timedelta(days=8)
        await session.commit()

    response = await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    assert response.status_code == 200, response.text
    body = response.json()
    all_items = body["notify_now"] + body["briefing"] + body["dashboard"]
    types = {item["signal_type"] for item in all_items}
    assert {
        "deadline_warning",
        "renewal_warning",
        "promise_follow_up",
        "waiting_response",
    } <= types
    for item in all_items:
        assert 0 <= item["attention_score"] <= 100
        assert item["what_happening"]
        assert item["why_matters"]
        assert item["score_components"]


@pytest.mark.asyncio
async def test_quiet_hours_downgrade_interruptions_without_lowering_score(
    proactive_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = proactive_environment
    account = await register(client)
    workspace = account["workspace"]
    assert isinstance(workspace, dict)
    fixed_now = datetime(2026, 9, 22, 3, 0, tzinfo=UTC)

    await create_commitment(
        client,
        title="Critical deadline",
        commitment_type="deadline",
        priority=5,
        due_at=fixed_now + timedelta(hours=1),
    )
    preferences = await client.post(
        "/api/v1/proactive/preferences",
        json={
            "timezone": "UTC",
            "notifications_enabled": True,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "daily_briefing_hour": 8,
            "notify_threshold": 70,
            "briefing_threshold": 50,
            "dashboard_threshold": 30,
            "max_interruptions_per_day": 3,
            "cooldown_minutes": 240,
        },
    )
    assert preferences.status_code == 200

    async with session_factory() as session:
        signals = await evaluate_workspace(
            session,
            user_id=UUID(str(account["id"])),
            workspace_id=UUID(str(workspace["id"])),
            timezone_name="UTC",
            now=fixed_now,
        )
    signal = next(item for item in signals if item.signal_type == "deadline_warning")
    assert signal.attention_score >= 70
    assert signal.tier == "briefing"


@pytest.mark.asyncio
async def test_snooze_dismiss_completion_and_tenant_isolation(
    proactive_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, session_factory = proactive_environment
    await register(client)
    commitment = await create_commitment(
        client,
        title="Follow up today",
        commitment_type="follow_up",
        priority=5,
        due_at=datetime.now(UTC) + timedelta(hours=4),
    )
    briefing = await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    briefing_body = briefing.json()
    item = [
        *briefing_body["notify_now"],
        *briefing_body["briefing"],
        *briefing_body["dashboard"],
    ][0]

    snoozed = await client.post(
        f"/api/v1/proactive/signals/{item['id']}/snooze",
        json={"until": (datetime.now(UTC) + timedelta(hours=6)).isoformat()},
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["tier"] == "suppressed"

    async with session_factory() as session:
        signal = await session.get(ProactiveSignal, UUID(item["id"]))
        assert signal is not None
        signal.snoozed_until = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    refreshed = await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    visible = (
        refreshed.json()["notify_now"]
        + refreshed.json()["briefing"]
        + refreshed.json()["dashboard"]
    )
    assert any(row["id"] == item["id"] for row in visible)

    dismissed = await client.post(f"/api/v1/proactive/signals/{item['id']}/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"

    hidden = await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    hidden_ids = {
        row["id"]
        for row in hidden.json()["notify_now"]
        + hidden.json()["briefing"]
        + hidden.json()["dashboard"]
    }
    assert item["id"] not in hidden_ids

    completed = await client.post(f"/api/v1/commitments/{commitment['id']}/complete")
    assert completed.status_code == 200
    await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    async with session_factory() as session:
        signal = await session.get(ProactiveSignal, UUID(item["id"]))
        assert signal is not None
        assert signal.status == "resolved"

    await register(client, "other@example.com")
    isolated = await client.post(f"/api/v1/proactive/signals/{item['id']}/dismiss")
    assert isolated.status_code == 404


@pytest.mark.asyncio
async def test_meeting_prep_uses_saved_state_only(
    proactive_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = proactive_environment
    await register(client)
    meeting = await create_commitment(
        client,
        title="Project review",
        commitment_type="meeting",
        priority=4,
        due_at=datetime.now(UTC) + timedelta(hours=2),
        description="Review launch risks and decisions.",
    )

    response = await client.get("/api/v1/proactive/meeting-prep")
    assert response.status_code == 200
    body = response.json()
    assert body["commitment_id"] == meeting["id"]
    assert body["title"] == "Project review"
    assert any("Review launch risks" in point for point in body["prep_points"])


@pytest.mark.asyncio
async def test_briefing_refresh_resolves_stale_state(
    proactive_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = proactive_environment
    await register(client)
    commitment = await create_commitment(
        client,
        title="Short-lived deadline",
        commitment_type="deadline",
        priority=5,
        due_at=datetime.now(UTC) + timedelta(hours=1),
    )
    before = await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    before_body = before.json()
    before_items = [
        *before_body["notify_now"],
        *before_body["briefing"],
        *before_body["dashboard"],
    ]
    assert any(item["commitment_id"] == commitment["id"] for item in before_items)

    completed = await client.post(f"/api/v1/commitments/{commitment['id']}/complete")
    assert completed.status_code == 200
    after = await client.get("/api/v1/proactive/briefing", params={"timezone": "UTC"})
    after_body = after.json()
    after_items = [
        *after_body["notify_now"],
        *after_body["briefing"],
        *after_body["dashboard"],
    ]
    assert all(item["commitment_id"] != commitment["id"] for item in after_items)



@pytest.mark.asyncio
async def test_today_signature_queries_use_proactive_saved_state(
    proactive_environment: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = proactive_environment
    await register(client)
    await create_commitment(
        client,
        title="Prepare launch review",
        commitment_type="meeting",
        priority=5,
        due_at=datetime.now(UTC) + timedelta(hours=2),
        description="Review launch risks.",
    )
    await create_commitment(
        client,
        title="File grant deadline",
        commitment_type="deadline",
        priority=5,
        due_at=datetime.now(UTC) + timedelta(hours=3),
    )
    await create_commitment(
        client,
        title="Renew hosting",
        commitment_type="renewal",
        priority=4,
        due_at=datetime.now(UTC) + timedelta(days=5),
    )

    forgetting = await client.post(
        "/api/v1/today/query",
        json={"query": "What am I forgetting?", "timezone": "UTC"},
    )
    assert forgetting.status_code == 200
    assert forgetting.json()["intent"] == "forgetting"
    assert forgetting.json()["items"]

    meeting = await client.post(
        "/api/v1/today/query",
        json={"query": "Prepare me for my next meeting.", "timezone": "UTC"},
    )
    assert meeting.status_code == 200
    assert meeting.json()["intent"] == "meeting_prep"
    assert meeting.json()["items"][0]["title"] == "Prepare launch review"
    assert meeting.json()["details"]

    money = await client.post(
        "/api/v1/today/query",
        json={"query": "Anything costing me money soon?", "timezone": "UTC"},
    )
    assert money.status_code == 200
    assert money.json()["intent"] == "renewals"
    assert money.json()["items"][0]["title"] == "Renew hosting"

    handleable = await client.post(
        "/api/v1/today/query",
        json={"query": "What can you handle for me?", "timezone": "UTC"},
    )
    assert handleable.status_code == 200
    assert handleable.json()["intent"] == "handleable"
    assert handleable.json()["items"]



def test_daily_briefing_delay_respects_dst_offset_changes() -> None:
    before_fall_back = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    delay = next_briefing_delay(
        timezone_name="America/New_York",
        briefing_hour=8,
        now=before_fall_back,
    )
    assert delay == 7 * 60 * 60 + 30 * 60
