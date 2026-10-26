"""Known native events remain busy when Google's free-busy projection lags."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_student_calendar_consent import BASE
from test_student_calendar_consent import calendar_env as calendar_env
from test_student_calendar_events import act, created, draft_body, preview
from test_student_calendar_events import event_env as event_env
from test_student_calendar_scheduling_consent import enable_scheduling

from navox.db.student_calendars import StudentCalendarEvent
from navox.db.student_planning import StudentPlanningState
from navox.providers.google_availability import GoogleAvailabilitySnapshot
from navox.student_calendars import scheduling
from navox.student_planning.schemas import Preferences


def lagged_busy(env, monkeypatch):
    async def collect(*, access_token, start_at, end_at, check):
        await check()
        result = GoogleAvailabilitySnapshot("complete", 1, (), datetime.now(UTC))
        await check()
        return result

    monkeypatch.setattr(env.busy, "collect", collect)


async def rejected_preview(env):
    response = await env.client.post(
        BASE + "/events/preview", json=await draft_body(env), headers=env.headers
    )
    assert response.status_code == 409, response.text
    assert "still claims this time" in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_known_created_event_blocks_a_new_preview_when_fresh_freebusy_omits_it(
    event_env, monkeypatch
):
    env = event_env
    first = await created(env)
    lagged_busy(env, monkeypatch)
    assert UUID(first["id"]).hex in env.writes.events
    await rejected_preview(env)
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []


@pytest.mark.asyncio
async def test_known_native_claim_is_rechecked_at_final_approval_after_fresh_authority_recovery(
    event_env, monkeypatch
):
    env = event_env
    first = await preview(env, title="First event")
    second = await preview(env, title="Second overlapping event")
    result = await act(env, first, "approve")
    assert result.status_code == 200 and result.json()["status"] == "created"
    lagged_busy(env, monkeypatch)
    recovered = await env.client.get(BASE + "/events/" + second["id"], headers=env.headers)
    assert recovered.status_code == 200
    assert recovered.json()["calendar_revision"] > second["calendar_revision"]
    response = await act(env, recovered.json(), "approve")
    assert response.status_code == 409 and "still claims this time" in response.text
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
async def test_adjacent_nonoverlapping_event_is_allowed_despite_the_retained_claim(
    event_env, monkeypatch
):
    env = event_env
    await created(env)
    lagged_busy(env, monkeypatch)
    env.start_at, env.end_at = (
        env.end_at + timedelta(minutes=20),
        env.end_at + timedelta(minutes=50),
    )
    second = await act(env, await preview(env), "approve")
    assert second.status_code == 200 and second.json()["status"] == "created", second.text
    assert len(env.writes.inserts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["undo", "exact_absence"])
async def test_verified_undo_or_exact_absence_releases_only_the_matching_native_claim(
    event_env, monkeypatch, release
):
    env = event_env
    first = await created(env)
    lagged_busy(env, monkeypatch)
    await rejected_preview(env)
    if release == "undo":
        response = await act(env, first, "undo")
    else:
        env.writes.events.pop(UUID(first["id"]).hex)
        response = await act(env, first, "reconcile")
    assert response.status_code == 200 and response.json()["status"] == "undone", response.text
    second = await act(env, await preview(env), "approve")
    assert second.status_code == 200 and second.json()["status"] == "created", second.text
    assert len(env.writes.inserts) == 2
    assert len(env.writes.deletes) == (1 if release == "undo" else 0)


@pytest.mark.asyncio
async def test_changed_remote_event_conservatively_claims_original_time_until_exact_absence(
    event_env, monkeypatch
):
    env = event_env
    first = await created(env)
    remote = env.writes.events[UUID(first["id"]).hex]
    remote["etag"] = '"moved-externally-v2"'
    remote["start"]["dateTime"] = (env.start_at + timedelta(hours=2)).isoformat()
    remote["end"]["dateTime"] = (env.end_at + timedelta(hours=2)).isoformat()
    changed = await act(env, first, "reconcile")
    assert changed.status_code == 200 and changed.json()["status"] == "changed"
    lagged_busy(env, monkeypatch)
    await rejected_preview(env)
    env.writes.events.pop(UUID(first["id"]).hex)
    absent = await act(env, changed.json(), "reconcile")
    assert absent.status_code == 200 and absent.json()["status"] == "undone", absent.text
    next_view = await preview(env)
    assert next_view["status"] == "pending"
    assert env.writes.deletes == []


@pytest.mark.asyncio
async def test_retained_verified_event_still_claims_time_after_reset_and_reconnection(
    event_env, monkeypatch
):
    env = event_env
    first = await created(env)
    reset = await env.client.post(
        "/api/v1/student/data/reset",
        json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
        headers=env.headers,
    )
    assert reset.status_code == 200, reset.text
    async with env.factory() as database:
        state = await database.scalar(
            select(StudentPlanningState).where(StudentPlanningState.user_id == env.user_id)
        )
        if state is None:
            state = StudentPlanningState(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                revision=1,
                preferences_revision=1,
                preferences=Preferences().model_dump(),
            )
            database.add(state)
        else:
            state.preferences_revision = 1
            state.preferences = Preferences().model_dump()
        await database.commit()
    _, connection_id, grant, _, _ = await enable_scheduling(env, monkeypatch)
    assert connection_id == env.connection_id
    env.grant_id = grant["grant_id"]
    lagged_busy(env, monkeypatch)
    listing = await env.client.get(BASE + "/events", headers=env.headers)
    old = next(row for row in listing.json()["events"] if row["id"] == first["id"])
    assert old["can_undo"] is False and "predates your reset" in old["message"]
    await rejected_preview(env)
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []


@pytest.mark.asyncio
async def test_claim_scan_bound_fails_closed_without_unbounded_decryption_or_provider_write(
    event_env, monkeypatch
):
    env = event_env
    await created(env)
    env.start_at, env.end_at = (
        env.end_at + timedelta(minutes=20),
        env.end_at + timedelta(minutes=50),
    )
    await created(env)
    monkeypatch.setattr(scheduling, "MAX_EVENT_CLAIMS", 1)
    env.start_at, env.end_at = (
        env.end_at + timedelta(minutes=10),
        env.end_at + timedelta(minutes=40),
    )
    calls = len(env.writes.inserts), len(env.writes.targets)
    response = await env.client.post(
        BASE + "/events/preview", json=await draft_body(env), headers=env.headers
    )
    assert response.status_code == 409 and "check safely" in response.text
    assert (len(env.writes.inserts), len(env.writes.targets)) == calls
    async with env.factory() as database:
        assert (
            len(
                list(
                    await database.scalars(
                        select(StudentCalendarEvent).where(StudentCalendarEvent.status == "created")
                    )
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_lagged_native_claim_preserves_existing_ten_minute_meeting_buffer(
    event_env, monkeypatch
):
    env = event_env
    await created(env)
    lagged_busy(env, monkeypatch)
    env.start_at, env.end_at = env.end_at + timedelta(minutes=5), env.end_at + timedelta(minutes=35)
    await rejected_preview(env)
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
async def test_lagged_native_claim_does_not_turn_unknown_travel_into_zero(event_env, monkeypatch):
    env = event_env
    await created(env)
    lagged_busy(env, monkeypatch)
    env.start_at, env.end_at = (
        env.end_at + timedelta(hours=3),
        env.end_at + timedelta(hours=3, minutes=30),
    )
    body = await draft_body(env)
    body["availability"]["travel_assumption"] = "unknown"
    uncertain = await env.client.post(BASE + "/events/preview", json=body, headers=env.headers)
    assert uncertain.status_code == 409 and "still claims this time" in uncertain.text
    body["request_id"] = str(uuid4())
    body["availability"]["travel_assumption"] = "zero"
    explicit = await env.client.post(BASE + "/events/preview", json=body, headers=env.headers)
    assert explicit.status_code == 200 and explicit.json()["status"] == "pending", explicit.text
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []
