"""Actual private route with fresh source reads and post-preview authority fences."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_student_availability_service import availability_env as shared_availability_env
from test_student_availability_service import grant
from test_vox_calls import vox_env as shared_vox_env

from navox.api import student_availability as api
from navox.connectors.sync_state import database_now
from navox.db.models import User, UserSession
from navox.db.student import StudentFixedBlock
from navox.db.student_calendars import (
    StudentCalendarEvent,
    StudentCalendarGrant,
    StudentCalendarState,
)
from navox.db.student_planning import StudentPlanningState

vox_env = shared_vox_env
availability_env = shared_availability_env
ROOT = "/api/v1/student/availability/suggestions"


@pytest.mark.asyncio
async def test_actual_route_returns_two_fresh_owned_choices_without_writing(availability_env):
    env = availability_env
    await grant(env)
    response = await env.client.post(ROOT, json=env.payload.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    snapshot, selection = body["snapshot"], body["selection"]
    assert snapshot["input"]["user_id"] == str(env.user_id)
    assert snapshot["input"]["workspace_id"] == str(env.workspace_id)
    assert len(selection["suggestions"]) == 2
    assert selection["duration_minutes"] == env.payload.duration_minutes
    assert selection["status"] == "ok"
    assert snapshot["revision"]["preferences_revision"] == 1
    assert snapshot["result"]["warnings"] == []
    assert snapshot["checked_at"] < snapshot["expires_at"]
    assert not body["changes_applied"] and not selection["changes_applied"]
    assert not snapshot["changes_applied"]
    assert env.provider.calls == 1
    assert all(
        value not in response.text
        for value in ("private-google", "private-account", "private-synthetic")
    )
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentCalendarEvent)) == 0


@pytest.mark.asyncio
async def test_explicit_purpose_filters_times_and_explicit_duration_is_preserved(availability_env):
    env = availability_env
    await grant(env)
    body = {**env.payload.model_dump(mode="json"), "purpose": "lunch", "duration_minutes": 15}
    response = await env.client.post(ROOT, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["selection"]["duration_minutes"] == 15
    assert value["snapshot"]["input"]["duration_minutes"] == 15
    for option in value["selection"]["suggestions"]:
        assert "lunch_time" in option["reasons"]
        assert (
            "T11:" in option["start_at"]
            or "T12:" in option["start_at"]
            or ("T13:" in option["start_at"])
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["snapshot", "other_windows", "crunch", "count", "now"])
async def test_client_cannot_supply_source_facts_observation_or_count(availability_env, field):
    env = availability_env
    await grant(env)
    response = await env.client.post(
        ROOT,
        json={**env.payload.model_dump(mode="json"), field: "private-untrusted-value"},
    )
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "private-untrusted-value" not in response.text
    assert env.provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update", [{"purpose": None}, {"purpose": "private-purpose"}, {"duration_minutes": 4}]
)
async def test_invalid_choices_are_rejected_without_private_echo(availability_env, update):
    env = availability_env
    await grant(env)
    response = await env.client.post(ROOT, json={**env.payload.model_dump(mode="json"), **update})
    assert response.status_code == 422, response.text
    assert "private-purpose" not in response.text
    assert env.provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,status",
    [("disabled", 404), ("bearer", 403), ("origin", 403), ("account", 409), ("cookie", 401)],
)
async def test_existing_private_guards_run_before_sources(availability_env, mode, status):
    env = availability_env
    await grant(env)
    headers = {}
    if mode == "disabled":
        env.settings.student_calendar_availability_enabled = False
    elif mode == "bearer":
        headers["Authorization"] = "Bearer untrusted"
    elif mode == "origin":
        headers["Origin"] = "https://unrelated.example"
    elif mode == "cookie":
        env.client.cookies.clear()
    else:
        headers["X-NavoX-Account"] = "wrong-account"
    response = await env.client.post(
        ROOT, json=env.payload.model_dump(mode="json"), headers=headers
    )
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert env.provider.calls == 0


@pytest.mark.asyncio
async def test_partial_sources_remain_unknown_and_have_no_confirmed_choices(availability_env):
    env = availability_env
    await grant(env)
    env.provider.state = "partial"
    response = await env.client.post(ROOT, json=env.payload.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selection"]["suggestions"] == []
    assert body["selection"]["status"] == "partial_information"
    assert body["snapshot"]["result"]["status"] == "partial"
    assert body["snapshot"]["result"]["warnings"]
    assert any(item["state"] == "partial" for item in body["snapshot"]["source_summary"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["grant", "reset", "session", "pause", "routine", "timezone", "schedule", "plan"]
)
async def test_post_preview_changes_invalidate_selection_before_publication(
    availability_env, monkeypatch, change
):
    env = availability_env
    grant_id, _ = await grant(env)
    original = api.service.preview

    async def changed(*args, **kwargs):
        snapshot = await original(*args, **kwargs)
        # The provider and preview service completed successfully. Change state
        # only afterward to exercise the endpoint's final publication boundary.
        async with env.factory() as database:
            if change == "grant":
                item = await database.get(StudentCalendarGrant, grant_id)
                item.status = "revoked"
                item.revoked_at = await database_now(database)
            elif change == "reset":
                (await database.scalar(select(StudentCalendarState))).generation += 1
            elif change == "session":
                (await database.get(UserSession, env.session_id)).revoked_at = await database_now(
                    database
                )
            elif change == "pause":
                (await database.get(User, env.user_id)).agent_paused = True
            elif change == "timezone":
                (await database.get(User, env.user_id)).timezone = "Europe/London"
            elif change == "schedule":
                day = env.payload.start_at.date()
                database.add(
                    StudentFixedBlock(
                        user_id=env.user_id,
                        workspace_id=env.workspace_id,
                        title="Private changed shift",
                        weekdays=[day.weekday()],
                        start_time="12:00",
                        end_time="13:00",
                        timezone="UTC",
                        term_start=day,
                        term_end=day,
                    )
                )
            elif change == "plan":
                (await database.scalar(select(StudentPlanningState))).revision += 1
            else:
                (await database.scalar(select(StudentPlanningState))).preferences_revision += 1
            await database.commit()
        return snapshot

    monkeypatch.setattr(api.service, "preview", changed)
    response = await env.client.post(ROOT, json=env.payload.model_dump(mode="json"))
    assert response.status_code in {401, 409}, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert env.provider.calls == 1
    assert "Private changed shift" not in response.text


@pytest.mark.asyncio
async def test_shorter_snapshot_expiry_is_rechecked_at_exact_boundary(
    availability_env, monkeypatch
):
    env = availability_env
    await grant(env)
    original = api.service.preview
    expires = None

    async def expiring(*args, **kwargs):
        nonlocal expires
        snapshot = await original(*args, **kwargs)
        expires = snapshot.checked_at + timedelta(seconds=1)
        return snapshot.model_copy(update={"expires_at": expires})

    async def clock(_database):
        assert expires is not None
        return expires

    monkeypatch.setattr(api.service, "preview", expiring)
    monkeypatch.setattr(api, "database_now", clock)
    response = await env.client.post(ROOT, json=env.payload.model_dump(mode="json"))
    assert response.status_code == 409, response.text
    assert "expired" in response.text


@pytest.mark.asyncio
async def test_unexpected_source_error_is_private_and_does_not_echo(availability_env, monkeypatch):
    env = availability_env

    async def failed(*_args, **_kwargs):
        raise ValueError("private-database-or-provider-value")

    monkeypatch.setattr(api.service, "preview", failed)
    response = await env.client.post(ROOT, json=env.payload.model_dump(mode="json"))
    assert response.status_code == 503, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "private-database-or-provider-value" not in response.text


@pytest.mark.asyncio
async def test_existing_preview_route_remains_available(availability_env):
    env = availability_env
    await grant(env)
    response = await env.client.post(
        "/api/v1/student/availability/preview", json=env.payload.model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["confirmed_openings"]
    assert "selection" not in response.json()
