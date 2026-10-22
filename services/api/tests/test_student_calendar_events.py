"""Real API/database/availability boundaries with synthetic Google transport.

The interval engine and native authority checks run unchanged. These tests do
not qualify a real Google account or a supported physical device.
"""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from test_student_calendar_consent import BASE
from test_student_calendar_consent import calendar_env as calendar_env
from test_student_calendar_scheduling_consent import (
    busy_status,
    disconnect_scheduling,
    enable_scheduling,
)

from navox.connectors.sync_state import database_now
from navox.db.models import OAuthAuthorizationAttempt, User
from navox.db.student_calendars import StudentCalendarEvent
from navox.db.student_planning import StudentPlanningState
from navox.providers.google_availability import GoogleAvailabilitySnapshot, ProviderBusy
from navox.student_availability import service as availability
from navox.student_calendars import scheduling
from navox.student_calendars.provider import (
    CalendarTarget,
    CalendarWriteChanged,
    CalendarWriteRejected,
    CalendarWriteUncertain,
    GoogleCalendarWriteError,
)
from navox.student_planning.schemas import Preferences

CALENDAR = "owned-private-calendar@example.test"
TOKEN = "synthetic-calendar-events-access-token"
TITLE = "Private internship preparation meeting"
LOCATION = "Private study room 42"


class WriteProvider:
    def __init__(self):
        self.events = {}
        self.inserts = []
        self.deletes = []
        self.reads = []
        self.targets = []
        self.read_error = False
        self.insert_mode = "normal"
        self.delete_mode = "normal"
        self.during_insert = None
        self.target_label = "Private personal calendar"

    async def target(self, *, access_token, calendar_id, check):
        assert access_token == TOKEN
        await check()
        self.targets.append(calendar_id)
        if calendar_id != CALENDAR:
            raise CalendarWriteRejected("Not the selected calendar")
        await check()
        return CalendarTarget(CALENDAR, self.target_label, "UTC")

    async def owned_calendars(self, *, access_token, check):
        return (await self.target(access_token=access_token, calendar_id=CALENDAR, check=check),)

    async def insert(self, *, access_token, calendar_id, event, check):
        assert access_token == TOKEN and calendar_id == CALENDAR
        await check()
        self.inserts.append(deepcopy(event))
        if self.insert_mode == "rejected":
            raise CalendarWriteRejected("Synthetic rejected", status_code=403)
        if self.insert_mode == "timeout_before":
            raise CalendarWriteUncertain("Synthetic ambiguous insert")
        created = {**deepcopy(event), "etag": '"created-v1"', "status": "confirmed"}
        self.events[event["id"]] = created
        if self.during_insert:
            await self.during_insert()
        await check()
        if self.insert_mode == "timeout_after":
            raise CalendarWriteUncertain("Synthetic ambiguous insert")
        return deepcopy(created)

    async def event(self, *, access_token, calendar_id, event_id, check):
        assert access_token == TOKEN and calendar_id == CALENDAR
        await check()
        self.reads.append(event_id)
        if self.read_error:
            raise GoogleCalendarWriteError("Synthetic read unavailable")
        result = deepcopy(self.events.get(event_id))
        await check()
        return result

    async def delete(self, *, access_token, calendar_id, event_id, etag, check):
        assert access_token == TOKEN and calendar_id == CALENDAR
        await check()
        self.deletes.append((event_id, etag))
        if self.delete_mode == "timeout_before":
            raise CalendarWriteUncertain("Synthetic ambiguous delete")
        if self.events.get(event_id, {}).get("etag") != etag:
            raise CalendarWriteChanged("Synthetic changed")
        self.events.pop(event_id, None)
        await check()
        if self.delete_mode == "timeout_after":
            raise CalendarWriteUncertain("Synthetic ambiguous delete")


class BusyProvider:
    def __init__(self, writes):
        self.writes = writes
        self.extra = ()
        self.state = "complete"

    async def collect(self, *, access_token, start_at, end_at, check):
        assert access_token == TOKEN
        await check()
        busy = tuple(self.extra) + tuple(
            ProviderBusy(
                datetime.fromisoformat(event["start"]["dateTime"]),
                datetime.fromisoformat(event["end"]["dateTime"]),
            )
            for event in self.writes.events.values()
        )
        await check()
        return GoogleAvailabilitySnapshot(self.state, 1, busy, datetime.now(UTC))


@pytest_asyncio.fixture
async def event_env(calendar_env, monkeypatch):
    env = calendar_env
    env.settings.connector_secret_encryption_key = SecretStr(Fernet.generate_key().decode())
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
    _, env.connection_id, started, _, _ = await enable_scheduling(env, monkeypatch)
    env.grant_id = started["grant_id"]
    env.start_at = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    env.end_at = env.start_at + timedelta(minutes=30)
    env.writes = WriteProvider()
    env.busy = BusyProvider(env.writes)

    async def refresh(database, *, connection, settings):
        assert connection not in database
        connection.access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        connection.last_checked_at = datetime.now(UTC)
        return TOKEN

    monkeypatch.setattr(availability, "access_token_for_connection", refresh)
    monkeypatch.setattr(availability, "GoogleAvailabilityProvider", lambda: env.busy)
    monkeypatch.setattr(scheduling, "GoogleCalendarWriteProvider", lambda: env.writes)
    return env


async def draft_body(env, **changes):
    current = await busy_status(env)
    return {
        "generation": current["generation"],
        "revision": current["revision"],
        "request_id": str(uuid4()),
        "grant_id": env.grant_id,
        "calendar_id": CALENDAR,
        "title": TITLE,
        "location": LOCATION,
        "start_at": env.start_at.isoformat(),
        "end_at": env.end_at.isoformat(),
        "timezone": "UTC",
        "availability": {
            "start_at": env.start_at.replace(hour=7).isoformat(),
            "end_at": env.start_at.replace(hour=23).isoformat(),
            "duration_minutes": 30,
            "preferences_revision": 1,
            "expected_timezone": "UTC",
            "routine_confirmed": True,
            "travel_assumption": "zero",
        },
        **changes,
    }


async def preview(env, **changes):
    response = await env.client.post(
        BASE + "/events/preview", json=await draft_body(env, **changes), headers=env.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def command(view, *, confirmed=False, **changes):
    result = {
        "generation": view["generation"],
        "revision": view["calendar_revision"],
        "request_id": str(uuid4()),
        "event_revision": view["revision"],
        "digest": view["digest"],
        **changes,
    }
    if confirmed:
        result["confirmed"] = True
    return result


async def act(env, view, action, **changes):
    return await env.client.post(
        BASE + "/events/" + view["id"] + "/" + action,
        json=command(view, confirmed=action in {"approve", "undo"}, **changes),
        headers=env.headers,
    )


async def created(env):
    view = await preview(env)
    response = await act(env, view, "approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created", response.text
    return response.json()


@pytest.mark.asyncio
async def test_real_route_preview_is_encrypted_private_and_has_no_event_side_effect(event_env):
    env = event_env
    view = await preview(env)
    assert view["status"] == "pending" and view["changes_applied"] is False
    assert view["can_undo"] is False and view["title"] == TITLE
    assert env.writes.inserts == [] and env.writes.deletes == [] and env.writes.reads == []
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(view["id"]))
        assert TITLE not in str(row.__dict__) and LOCATION not in str(row.__dict__)
        assert CALENDAR not in str(row.__dict__) and TOKEN not in str(row.__dict__)
        assert row.status == "pending" and row.encrypted_payload
    listing = await env.client.get(BASE + "/events", headers=env.headers)
    assert listing.status_code == 200 and listing.json()["events"][0]["title"] == TITLE
    assert listing.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_real_route_review_approval_verification_and_exact_undo(event_env):
    env = event_env
    view = await created(env)
    assert view["changes_applied"] is True and view["can_undo"] is True
    assert len(env.writes.inserts) == 1
    body = env.writes.inserts[0]
    assert body["id"] == UUID(view["id"]).hex
    assert body["summary"] == TITLE and body["location"] == LOCATION
    assert body["visibility"] == "private" and body["reminders"] == {"useDefault": False}
    assert not {"attendees", "recurrence", "conferenceData"}.intersection(body)
    response = await act(env, view, "undo")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "undone" and response.json()["can_undo"] is False
    assert env.writes.events == {}
    assert env.writes.deletes == [(UUID(view["id"]).hex, '"created-v1"')]


@pytest.mark.asyncio
async def test_preview_request_replay_returns_same_draft_without_provider_write(event_env):
    env = event_env
    body = await draft_body(env)
    first = await env.client.post(BASE + "/events/preview", json=body, headers=env.headers)
    replay = await env.client.post(BASE + "/events/preview", json=body, headers=env.headers)
    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    changed = await env.client.post(
        BASE + "/events/preview",
        json={**body, "title": "Changed reviewed event"},
        headers=env.headers,
    )
    assert changed.status_code == 409 and env.writes.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"event_revision": 2}, {"digest": "0" * 64}, {"revision": 999}])
async def test_exact_revision_and_payload_hash_are_required_before_insert(event_env, change):
    env = event_env
    view = await preview(env)
    response = await act(env, view, "approve", **change)
    assert response.status_code == 409 and env.writes.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, 1, "true"])
async def test_approval_requires_explicit_true_without_any_side_effect(event_env, confirmed):
    env = event_env
    view = await preview(env)
    body = {**command(view), "confirmed": confirmed}
    response = await env.client.post(
        BASE + "/events/" + view["id"] + "/approve", json=body, headers=env.headers
    )
    assert response.status_code == 422 and env.writes.inserts == []


@pytest.mark.asyncio
async def test_expired_review_cannot_insert(event_env):
    env = event_env
    view = await preview(env)
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(view["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    response = await act(env, view, "approve")
    assert response.status_code == 409 and env.writes.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["busy", "partial", "routine_changed"])
async def test_approval_rechecks_real_availability_and_does_not_override_new_conflicts(
    event_env, source
):
    env = event_env
    view = await preview(env)
    if source == "busy":
        env.busy.extra = (ProviderBusy(env.start_at, env.end_at),)
    elif source == "partial":
        env.busy.state = "partial"
    else:
        async with env.factory() as database:
            planning = await database.scalar(select(StudentPlanningState))
            planning.preferences_revision += 1
            await database.commit()
    response = await act(env, view, "approve")
    assert response.status_code == 409 and env.writes.inserts == []


@pytest.mark.asyncio
async def test_successful_approval_cannot_be_replayed(event_env):
    env = event_env
    initial = await preview(env)
    response = await act(env, initial, "approve")
    assert response.status_code == 200 and response.json()["status"] == "created"
    assert (await act(env, initial, "approve")).status_code == 409
    assert (await act(env, response.json(), "approve")).status_code == 409
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
async def test_uncertain_insert_reconciliation_reads_exact_id_without_resubmitting(event_env):
    env = event_env
    env.writes.insert_mode = "timeout_after"
    env.writes.read_error = True
    pending = await preview(env)
    response = await act(env, pending, "approve")
    assert response.status_code == 200, response.text
    held = response.json()
    assert held["status"] == "uncertain" and held["can_undo"] is False
    assert len(env.writes.inserts) == 1
    assert (await act(env, held, "approve")).status_code == 409
    env.writes.read_error = False
    checked = await act(env, held, "reconcile")
    assert checked.status_code == 200 and checked.json()["status"] == "created"
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []
    assert set(env.writes.reads) == {UUID(held["id"]).hex}


@pytest.mark.asyncio
async def test_unresolved_insert_blocks_new_overlapping_draft_and_survives_activity_reload(
    event_env,
):
    env = event_env
    env.writes.insert_mode = "timeout_before"
    held = (await act(env, await preview(env), "approve")).json()
    assert held["status"] == "uncertain"
    listing = await env.client.get(BASE + "/events", headers=env.headers)
    assert listing.json()["events"][0]["id"] == held["id"]
    response = await env.client.post(
        BASE + "/events/preview", json=await draft_body(env), headers=env.headers
    )
    assert response.status_code == 409 and "unconfirmed" in response.text
    checked = await act(env, held, "reconcile")
    assert checked.status_code == 200 and checked.json()["status"] == "uncertain"
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["description", "etag", "reminders", "attachments"])
async def test_external_event_changes_become_sticky_and_never_authorize_delete(event_env, change):
    env = event_env
    view = await created(env)
    event_id = UUID(view["id"]).hex
    original = deepcopy(env.writes.events[event_id])
    if change == "description":
        env.writes.events[event_id]["description"] = "User's later important notes"
    elif change == "etag":
        env.writes.events[event_id]["etag"] = '"external-v2"'
    elif change == "reminders":
        env.writes.events[event_id]["reminders"] = {"useDefault": True}
    else:
        env.writes.events[event_id]["attachments"] = [{"fileUrl": "https://example.test/notes"}]
    response = await act(env, view, "reconcile")
    assert response.status_code == 200 and response.json()["status"] == "changed"
    changed = response.json()
    env.writes.events[event_id] = original
    checked = await act(env, changed, "reconcile")
    assert checked.status_code == 200 and checked.json()["status"] == "changed"
    assert (await act(env, checked.json(), "undo")).status_code == 409
    assert env.writes.deletes == []


@pytest.mark.asyncio
async def test_uncertain_delete_is_not_replayed_until_exact_absence_is_observed(event_env):
    env = event_env
    view = await created(env)
    env.writes.delete_mode = "timeout_before"
    response = await act(env, view, "undo")
    assert response.status_code == 200 and response.json()["status"] == "undoing"
    held = response.json()
    assert (await act(env, held, "undo")).status_code == 409
    still_held = await act(env, held, "reconcile")
    assert still_held.status_code == 200 and still_held.json()["status"] == "undoing"
    assert len(env.writes.deletes) == 1
    env.writes.events.clear()  # Simulate the original delayed provider DELETE completing.
    checked = await act(env, still_held.json(), "reconcile")
    assert checked.status_code == 200 and checked.json()["status"] == "undone"
    assert len(env.writes.deletes) == 1


@pytest.mark.asyncio
async def test_concurrent_approve_while_first_insert_is_in_flight_does_not_duplicate(event_env):
    env = event_env
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_insert():
        entered.set()
        await release.wait()

    env.writes.during_insert = blocked_insert
    view = await preview(env)
    first = asyncio.create_task(act(env, view, "approve"))
    await asyncio.wait_for(entered.wait(), 5)
    second = await asyncio.wait_for(act(env, view, "approve"), 5)
    assert second.status_code == 409
    release.set()
    completed = await asyncio.wait_for(first, 5)
    assert completed.status_code == 200 and completed.json()["status"] == "created"
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "disconnect", "new_login", "reset"])
async def test_native_authority_change_blocks_old_review_before_provider_write(event_env, change):
    env = event_env
    view = await preview(env)
    if change == "pause":
        async with env.factory() as database:
            user = await database.get(User, env.user_id)
            user.agent_paused = True
            await database.commit()
    elif change == "disconnect":
        assert (await disconnect_scheduling(env, env.grant_id)).status_code == 200
    elif change == "new_login":
        response = await env.client.post(
            "/api/v1/auth/login",
            json={"email": "calendar-native@example.com", "password": "twelve-character-password"},
        )
        assert response.status_code == 200
    else:
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
            headers=env.headers,
        )
        assert response.status_code == 200, response.text
    response = await act(env, view, "approve")
    assert response.status_code in (404, 409) and env.writes.inserts == []


@pytest.mark.asyncio
async def test_reset_after_insert_submission_preserves_uncertain_receipt_and_prevents_replay(
    event_env, monkeypatch
):
    env = event_env

    async def reset_during_insert():
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
            headers=env.headers,
        )
        assert response.status_code == 200, response.text

    env.writes.during_insert = reset_during_insert
    view = await preview(env)
    response = await act(env, view, "approve")
    assert response.status_code == 409
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(view["id"]))
        assert row is not None and row.status in {"executing", "uncertain"}
        assert row.generation == 0
    listing = await env.client.get(BASE + "/events", headers=env.headers)
    assert listing.status_code == 200 and listing.json()["generation"] == 1
    held = next(item for item in listing.json()["events"] if item["id"] == view["id"])
    assert held["can_undo"] is False and "predates your reset" in held["message"]
    assert (await act(env, held, "reconcile")).status_code == 409
    assert (await act(env, view, "approve")).status_code in (404, 409)
    _, connection_id, _, _, _ = await enable_scheduling(env, monkeypatch)
    assert connection_id == env.connection_id
    listing = await env.client.get(BASE + "/events", headers=env.headers)
    held = next(item for item in listing.json()["events"] if item["id"] == view["id"])
    env.settings.student_calendar_writes_enabled = False
    checked = await act(env, held, "reconcile")
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "created" and checked.json()["can_undo"] is False
    env.settings.student_calendar_writes_enabled = True
    assert (await act(env, checked.json(), "approve")).status_code == 409
    assert (await act(env, checked.json(), "undo")).status_code == 409
    assert len(env.writes.inserts) == 1
    assert env.writes.deletes == []


@pytest.mark.asyncio
async def test_account_header_and_malformed_private_input_are_rejected_without_effects(event_env):
    env = event_env
    body = await draft_body(env)
    response = await env.client.post(
        BASE + "/events/preview",
        json=body,
        headers={**env.headers, "X-NavoX-Account": "another:account"},
    )
    assert response.status_code == 409
    response = await env.client.post(
        BASE + "/events/preview",
        json={**body, "unreviewed_private_input": "must-not-escape"},
        headers=env.headers,
    )
    assert response.status_code == 422 and "must-not-escape" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert env.writes.inserts == [] and env.writes.deletes == []


@pytest.mark.asyncio
async def test_another_signed_in_owner_cannot_read_or_approve_private_event_receipt(event_env):
    env = event_env
    view = await preview(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as other:
        registered = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "another-calendar-owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Other student",
            },
        )
        assert registered.status_code == 201
        account = registered.json()
        headers = {
            "Origin": env.settings.web_origin,
            "X-NavoX-Account": f"{account['workspace']['id']}:{account['id']}",
        }
        listing = await other.get(BASE + "/events", headers=headers)
        assert listing.status_code == 200 and listing.json()["events"] == []
        assert TITLE not in listing.text and LOCATION not in listing.text
        detail = await other.get(BASE + "/events/" + view["id"], headers=headers)
        assert detail.status_code == 404
        assert TITLE not in detail.text and LOCATION not in detail.text
        response = await other.post(
            BASE + "/events/" + view["id"] + "/approve",
            json=command(view, confirmed=True, generation=0, revision=0),
            headers=headers,
        )
        assert response.status_code == 404
        assert TITLE not in response.text and LOCATION not in response.text
    assert env.writes.inserts == []


@pytest.mark.asyncio
async def test_write_flag_blocks_new_admission_but_keeps_read_only_recovery_and_withdrawal(
    event_env,
):
    env = event_env
    view = await created(env)
    current = await busy_status(env)
    async with env.factory() as database:
        attempts_before = await database.scalar(
            select(func.count()).select_from(OAuthAuthorizationAttempt)
        )
    env.settings.student_calendar_writes_enabled = False
    assert (await env.client.get(BASE, headers=env.headers)).status_code == 200
    assert (await env.client.get(BASE + "/scheduling", headers=env.headers)).status_code == 200
    assert (await env.client.get(BASE + "/events", headers=env.headers)).status_code == 200
    common = {
        "generation": current["generation"],
        "revision": current["revision"],
        "request_id": str(uuid4()),
    }
    blocked = [
        (
            "/google/scheduling/start",
            {
                **common,
                "consent_version": "calendar-scheduling.v1",
                "confirmed": True,
                "connection_id": env.connection_id,
            },
        ),
        ("/scheduling/targets", {**common, "grant_id": env.grant_id}),
        ("/events/preview", await draft_body(env)),
        ("/events/" + view["id"] + "/approve", command(view, confirmed=True)),
        ("/events/" + view["id"] + "/undo", command(view, confirmed=True)),
    ]
    targets_before = len(env.writes.targets)
    for path, body in blocked:
        response = await env.client.post(BASE + path, json=body, headers=env.headers)
        assert response.status_code == 404, response.text
    assert len(env.writes.targets) == targets_before
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []
    async with env.factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(OAuthAuthorizationAttempt))
            == attempts_before
        )
    response = await act(env, view, "reconcile")
    assert response.status_code == 200 and response.json()["status"] == "created"
    assert (await disconnect_scheduling(env, env.grant_id)).status_code == 200
    assert (await busy_status(env))["grants"][0]["status"] == "connected"
    assert env.writes.deletes == []
