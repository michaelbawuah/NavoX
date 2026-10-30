"""Combined calendar HTTP flows use real Apple grants and synthetic busy reads.

No scope helper is replaced here: this exercises assembled source inventory,
provider dispatch and reset/disconnect fencing. SQLite is not PostgreSQL race
qualification and synthetic CalDAV is not live iCloud qualification.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_apple_calendar_connection import (
    ACCOUNT,
    PASSWORD,
    connect,
)
from test_apple_calendar_connection import (
    apple_env as shared_apple_fixture,
)

from navox.connectors.sync_state import database_now
from navox.db.apple_calendars import AppleCalendarGrant
from navox.db.models import ConnectionCredential, OAuthAuthorizationAttempt, User
from navox.db.student_calendars import StudentCalendarEvent
from navox.db.student_planning import StudentPlanningState
from navox.providers.apple_availability import AppleAvailabilitySnapshot
from navox.providers.google_availability import ProviderBusy
from navox.student_availability import service as availability
from navox.student_calendars import apple
from navox.student_calendars import service as calendars
from navox.student_planning.schemas import Preferences

apple_env = shared_apple_fixture
ROOT = "/api/v1/student/availability/preview"
APPLE = "/api/v1/student/calendars/apple"


@pytest_asyncio.fixture
async def combined_apple_env(apple_env, monkeypatch):
    env = apple_env
    # Connect through the real cookie/account/origin-bound API before providing
    # availability wire data. No manufactured grant or OAuth row is inserted.
    response, _ = await connect(env)
    assert response.status_code == 200, response.text
    env.apple_view = response.json()
    env.grant_id = env.apple_view["grants"][0]["id"]
    async with env.factory() as database:
        now = await database_now(database)
        user = await database.get(User, env.user_id)
        user.timezone = "UTC"
        database.add(
            StudentPlanningState(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                revision=1,
                preferences_revision=1,
                preferences=Preferences().model_dump(),
            )
        )
        await database.commit()
    tomorrow = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    env.payload = {
        "start_at": tomorrow.isoformat(),
        "end_at": tomorrow.replace(hour=23).isoformat(),
        "duration_minutes": 30,
        "preferences_revision": 1,
        "expected_timezone": "UTC",
        "routine_confirmed": True,
        "travel_assumption": "zero",
        "include_short_slots": False,
    }
    env.busy_start, env.busy_end = tomorrow.replace(hour=10), tomorrow.replace(hour=11)
    env.wire = {"calls": 0, "state": "complete", "during": None, "windows": []}

    class BusyReader:
        async def collect(self, *, apple_id, app_password, start_at, end_at, check):
            assert (apple_id, app_password) == (ACCOUNT, PASSWORD)
            await check()
            env.wire["calls"] += 1
            env.wire["windows"].append((start_at, end_at))
            if env.wire["during"] is not None:
                await env.wire["during"]()
            await check()
            return AppleAvailabilitySnapshot(
                env.wire["state"],
                2,
                (ProviderBusy(env.busy_start, env.busy_end),),
                datetime.now(UTC),
            )

    async def forbidden_google_token(*args, **kwargs):
        raise AssertionError("An Apple-only source requested a Google access token")

    async def forbidden_microsoft_token(*args, **kwargs):
        raise AssertionError("An Apple-only source requested a Microsoft access token")

    monkeypatch.setattr(apple, "AppleAvailabilityProvider", BusyReader)
    monkeypatch.setattr(availability, "_token", forbidden_google_token)
    monkeypatch.setattr(availability.microsoft, "access", forbidden_microsoft_token)
    return env


async def preview(env):
    return await env.client.post(ROOT, json=env.payload, headers=env.headers)


@pytest.mark.asyncio
async def test_apple_only_connection_confirms_source_coverage_and_blocks_busy_meeting(
    combined_apple_env,
):
    env = combined_apple_env
    response = await preview(env)
    assert response.status_code == 200, response.text
    body = response.json()
    source_id = "icloud-" + env.grant_id.replace("-", "")
    assert body["result"]["status"] == "complete"
    assert {row["source_id"] for row in body["source_summary"]} == {
        "student-timetable",
        "student-study-plan",
        source_id,
    }
    source = next(row for row in body["source_summary"] if row["source_id"] == source_id)
    assert source["state"] == "complete" and source["calendar_count"] == 2
    assert "google-calendar-availability" not in body["input"]["required_source_ids"]
    assert len(body["input"]["busy"]) == 1 and env.wire["calls"] == 1
    assert body["result"]["confirmed_openings"]
    for opening in body["result"]["confirmed_openings"]:
        first = datetime.fromisoformat(opening["start_at"].replace("Z", "+00:00"))
        last = datetime.fromisoformat(opening["end_at"].replace("Z", "+00:00"))
        assert last <= env.busy_start - timedelta(minutes=10) or first >= env.busy_end + timedelta(
            minutes=10
        )
    assert ACCOUNT not in response.text and PASSWORD not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    async with env.factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(OAuthAuthorizationAttempt)) == 0
        )
        assert await database.scalar(select(func.count()).select_from(StudentCalendarEvent)) == 0
        assert await database.scalar(select(func.count()).select_from(ConnectionCredential)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["partial", "unavailable"])
async def test_incomplete_apple_source_never_becomes_confirmed_free_time(combined_apple_env, state):
    env = combined_apple_env
    env.wire["state"] = state
    response = await preview(env)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"]["status"] == "partial"
    assert body["result"]["confirmed_openings"] == []
    assert len(body["input"]["busy"]) == 1
    assert (
        next(row for row in body["source_summary"] if row["source_id"].startswith("icloud-"))[
            "state"
        ]
        == state
    )


@pytest.mark.asyncio
async def test_common_calendar_view_reports_apple_without_google_oauth_configuration(
    combined_apple_env,
):
    env = combined_apple_env
    assert not env.settings.google_oauth_client_id
    response = await env.client.get("/api/v1/student/calendars", headers=env.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    apple_provider = next(row for row in body["providers"] if row["id"] == "icloud")
    assert apple_provider["availability"] == "available"
    grant = next(row for row in body["grants"] if row["id"] == env.grant_id)
    assert grant["provider"] == "icloud" and grant["status"] == "connected"


@pytest.mark.asyncio
async def test_disconnect_during_combined_preview_fences_the_entire_result(combined_apple_env):
    env = combined_apple_env

    async def disconnect():
        response = await env.client.post(
            APPLE + "/" + env.grant_id + "/disconnect",
            json={
                "generation": env.apple_view["generation"],
                "revision": env.apple_view["revision"],
                "request_id": str(uuid4()),
            },
            headers=env.headers,
        )
        assert response.status_code == 200, response.text

    env.wire["during"] = disconnect
    response = await preview(env)
    assert response.status_code == 409
    assert "confirmed_openings" not in response.text and PASSWORD not in response.text
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AppleCalendarGrant)) == 0
        assert await database.scalar(select(func.count()).select_from(ConnectionCredential)) == 0


@pytest.mark.asyncio
async def test_common_reset_during_combined_preview_erases_apple_and_preserves_unknown_coverage(
    combined_apple_env,
):
    env = combined_apple_env

    async def reset():
        async with env.factory() as database:
            await calendars.boundary(database, env.user_id, env.workspace_id, env.token_hash)
            await calendars.forget_calendar_data(database, env.user_id, env.workspace_id)
            await database.commit()

    env.wire["during"] = reset
    response = await preview(env)
    assert response.status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AppleCalendarGrant)) == 0
        assert await database.scalar(select(func.count()).select_from(ConnectionCredential)) == 0
    env.wire["during"] = None
    after = await preview(env)
    assert after.status_code == 200, after.text
    assert after.json()["result"]["confirmed_openings"] == []
    assert after.json()["result"]["status"] == "partial"
    assert not any(row["source_id"].startswith("icloud-") for row in after.json()["source_summary"])
    assert env.wire["calls"] == 1
