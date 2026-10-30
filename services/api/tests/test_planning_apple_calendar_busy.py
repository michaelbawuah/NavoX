"""Real Apple purpose grants constrain Planner previews and exact acceptance.

Synthetic CalDAV replies exercise the native transaction; no Apple account or
external calendar is accessed.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_apple_calendar_connection import ACCOUNT, PASSWORD, connect
from test_apple_calendar_connection import apple_env as shared_apple_fixture

from navox.connectors.sync_state import database_now
from navox.db.apple_calendars import AppleCalendarGrant
from navox.db.models import User
from navox.db.student_planning import StudentStudyBlock
from navox.providers.apple_availability import AppleAvailabilitySnapshot
from navox.providers.google_availability import ProviderBusy
from navox.student import service as schedule
from navox.student_calendars import apple
from navox.student_planning import calendar_busy

apple_env = shared_apple_fixture
ROOT = "/api/v1/student/planning"


@pytest_asyncio.fixture
async def apple_planner_env(apple_env, monkeypatch):
    env = apple_env
    env.settings.student_planning_enabled = True
    connected, _ = await connect(env)
    assert connected.status_code == 200
    env.apple_view = connected.json()
    async with env.factory() as database:
        now = await database_now(database)
        user = await database.get(User, env.user_id)
        user.timezone = "UTC"
        await database.commit()
    env.first = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=125_000)
    env.busy_start = env.first.replace(hour=10, microsecond=0)
    env.busy_end = env.first.replace(hour=11, microsecond=0)
    env.provider = SimpleNamespace(state="complete", calls=0, during=None, queries=[])

    class Reader:
        async def collect(self, *, apple_id, app_password, start_at, end_at, check):
            assert (apple_id, app_password) == (ACCOUNT, PASSWORD)
            assert not start_at.microsecond and not end_at.microsecond
            await check()
            env.provider.calls += 1
            env.provider.queries.append((start_at, end_at))
            if env.provider.during:
                await env.provider.during()
            await check()
            return AppleAvailabilitySnapshot(
                env.provider.state,
                2,
                (ProviderBusy(env.busy_start, env.busy_end),),
                datetime.now(UTC),
            )

    async def forbidden(*args, **kwargs):
        raise AssertionError("Apple planning may not use Google or a nested Apple transaction")

    monkeypatch.setattr(calendar_busy, "AppleAvailabilityProvider", Reader)
    monkeypatch.setattr(calendar_busy, "access_token_for_connection", forbidden)
    monkeypatch.setattr(apple, "read", forbidden)
    monkeypatch.setattr(calendar_busy, "get_settings", lambda: env.settings)
    return env


async def collect(env, database):
    return await calendar_busy.collect(
        database,
        env.user_id,
        env.workspace_id,
        start_at=env.first,
        end_at=env.first + timedelta(hours=16),
        travel_minutes=15,
    )


@pytest.mark.asyncio
async def test_native_apple_inventory_and_busy_read_keep_parent_transaction(
    apple_planner_env, monkeypatch
):
    env = apple_planner_env
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        rows = await calendar_busy.inventory(database, env.user_id, env.workspace_id)
        assert len(rows) == 1 and rows[0]["provider"] == "icloud"
        assert PASSWORD not in repr(rows) and ACCOUNT not in repr(rows)

        async def forbidden():
            raise AssertionError("Collector must not commit or roll back the parent")

        monkeypatch.setattr(database, "commit", forbidden)
        monkeypatch.setattr(database, "rollback", forbidden)
        result = await collect(env, database)
        assert result.connected and result.digest != "unconnected"
        assert result.intervals == (
            (env.busy_start - timedelta(minutes=25), env.busy_end + timedelta(minutes=25)),
        )
        first, last = env.provider.queries[0]
        assert first <= env.first - timedelta(minutes=25)
        assert last >= env.first + timedelta(hours=16, minutes=25)


@pytest.mark.asyncio
@pytest.mark.parametrize("coverage", ["partial", "unavailable"])
async def test_apple_incomplete_coverage_blocks_planning_instead_of_ignoring_connection(
    apple_planner_env, coverage
):
    env = apple_planner_env
    env.provider.state = coverage
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        with pytest.raises(schedule.StudentError, match="fully checked"):
            await collect(env, database)
    assert env.provider.calls == 1


@pytest.mark.asyncio
async def test_changed_apple_grant_during_failed_read_rejects_stale_result(apple_planner_env):
    env = apple_planner_env
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)

        async def changed():
            row = await database.get(AppleCalendarGrant, UUID(env.apple_view["grants"][0]["id"]))
            row.revision += 1
            raise TimeoutError("synthetic private transport detail")

        env.provider.during = changed
        with pytest.raises(schedule.StudentError, match="access changed") as raised:
            await collect(env, database)
        assert raised.value.status_code == 409
        assert PASSWORD not in str(raised.value)


async def preview_task(env):
    task = await env.client.post(
        ROOT + "/tasks",
        headers=env.headers,
        json={
            "title": "Synthetic study task",
            "estimated_minutes": 180,
            "due_at": env.first.replace(hour=22).isoformat(),
            "request_id": str(uuid4()),
        },
    )
    assert task.status_code == 201, task.text
    response = await env.client.post(
        ROOT + "/preview",
        headers=env.headers,
        json={"day": env.first.date().isoformat(), "days": 1},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_planner_http_preview_places_study_outside_apple_busy_time(apple_planner_env):
    env = apple_planner_env
    result = await preview_task(env)
    assert result["result"]["blocks"]
    for block in result["result"]["blocks"]:
        first = datetime.fromisoformat(block["start_at"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(block["end_at"].replace("Z", "+00:00"))
        assert last <= env.busy_start - timedelta(minutes=25) or first >= env.busy_end + timedelta(
            minutes=25
        )
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == 0


@pytest.mark.asyncio
async def test_apple_disconnect_changes_planning_source_and_fences_old_approval(apple_planner_env):
    env = apple_planner_env
    result = await preview_task(env)
    current = (await env.client.get("/api/v1/student/calendars/apple", headers=env.headers)).json()
    disconnected = await env.client.post(
        "/api/v1/student/calendars/apple/" + current["grants"][0]["id"] + "/disconnect",
        headers=env.headers,
        json={
            "generation": current["generation"],
            "revision": current["revision"],
            "request_id": str(uuid4()),
        },
    )
    assert disconnected.status_code == 200
    accepted = await env.client.post(
        ROOT + "/accept",
        headers=env.headers,
        json={
            "preview_id": result["preview_id"],
            "preview_hash": result["preview_hash"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert accepted.status_code == 409, accepted.text
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == 0
