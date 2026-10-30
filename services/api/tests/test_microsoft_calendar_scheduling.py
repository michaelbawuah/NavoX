"""Microsoft native purpose/account proof and safe recovery without unqualified Undo."""

from copy import deepcopy
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_microsoft_calendar_consent import begin, finish
from test_microsoft_calendar_consent import microsoft_env as shared_microsoft_env
from test_student_calendar_consent import calendar_env as shared_calendar_env
from test_student_calendar_events import act

from navox.connectors.sync_state import database_now
from navox.db.models import OAuthAuthorizationAttempt
from navox.db.student_calendars import (
    StudentCalendarEvent,
    StudentCalendarGrant,
    StudentCalendarWriteGrant,
)
from navox.student_calendars import microsoft, scheduling
from navox.student_calendars.provider import CalendarTarget, CalendarWriteUncertain
from navox.student_calendars.schemas import CalendarEventPreview

calendar_env, microsoft_env = shared_calendar_env, shared_microsoft_env
BASE = "/api/v1/student/calendars"


async def status(env):
    response = await env.client.get(BASE + "/scheduling", headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def busy(env):
    _, state = await begin(env)
    assert (await finish(env, state)).status_code == 303
    connection = (await env.client.get(BASE, headers=env.headers)).json()["grants"][0]
    env.connection_id = connection["connection_id"]
    return connection


async def start_write(env, **changes):
    env.settings.student_microsoft_calendar_writes_enabled = True
    env.token_scopes = frozenset(microsoft.WRITE_SCOPES)
    current = await status(env)
    response = await env.client.post(
        BASE + "/outlook/scheduling/start",
        headers=env.headers,
        json={
            "generation": current["generation"],
            "revision": current["revision"],
            "request_id": str(uuid4()),
            "consent_version": "calendar-scheduling.v1",
            "confirmed": True,
            "connection_id": env.connection_id,
            **changes,
        },
    )
    if response.status_code != 200:
        return response, None
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    env.grant_id = response.json()["grant_id"]
    return response, state


async def connected(env):
    await busy(env)
    response, state = await start_write(env)
    assert response.status_code == 200, response.text
    done = await finish(env, state)
    assert done.status_code == 303, done.text
    return env


async def draft(env):
    current = await status(env)
    async with env.factory() as database:
        now = await database_now(database)
    start = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return {
        "generation": current["generation"],
        "revision": current["revision"],
        "request_id": str(uuid4()),
        "grant_id": env.grant_id,
        "calendar_id": "owned-ms",
        "title": "Reviewed Microsoft event",
        "location": "Study room",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(minutes=30)).isoformat(),
        "timezone": "UTC",
        "availability": {
            "start_at": start.replace(hour=7).isoformat(),
            "end_at": start.replace(hour=23).isoformat(),
            "duration_minutes": 30,
            "preferences_revision": 0,
            "expected_timezone": "UTC",
            "routine_confirmed": True,
            "travel_assumption": "zero",
        },
    }


@pytest.mark.asyncio
async def test_microsoft_write_oauth_is_separate_exact_seven_scope_and_preserves_busy_grant(
    microsoft_env,
):
    env = microsoft_env
    old = await busy(env)
    assert (await status(env))["grants"] == []
    response, state = await start_write(env)
    assert response.json()["requested_scopes"] == [
        "openid",
        "profile",
        "email",
        "offline_access",
        "User.Read",
        "Calendars.Read",
        "Calendars.ReadWrite",
    ]
    assert not any("Mail." in value for value in response.json()["requested_scopes"])
    assert (await finish(env, state)).status_code == 303
    view = await status(env)
    assert view["grants"][0]["provider"] == "outlook" and view["grants"][0]["status"] == "connected"
    assert (
        next(p for p in view["providers"] if p["id"] == "outlook")["availability"] == "unavailable"
    )
    async with env.factory() as database:
        grant = await database.get(StudentCalendarWriteGrant, UUID(env.grant_id))
        assert grant.provider == "microsoft" and grant.connection_id == UUID(old["connection_id"])
        assert (await database.get(StudentCalendarGrant, UUID(old["id"]))).status == "active"
        attempt = await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
        assert attempt.purpose == microsoft.SCHEDULING_PURPOSE
        assert attempt.requested_scopes == list(microsoft.WRITE_SCOPES)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["scopes", "connection"])
async def test_microsoft_pending_record_cannot_change_exact_scopes_or_bound_connection(
    microsoft_env, mutation
):
    env = microsoft_env
    await busy(env)
    _, state = await start_write(env)
    async with env.factory() as database:
        grant = await database.get(StudentCalendarWriteGrant, UUID(env.grant_id))
        attempt = await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
        if mutation == "scopes":
            attempt.requested_scopes = [*microsoft.WRITE_SCOPES, "Mail.Send"]
        else:
            grant.connection_id = None
        await database.commit()
    count = len(env.exchanges)
    assert (await finish(env, state)).status_code == 409
    assert len(env.exchanges) == count


@pytest.mark.asyncio
async def test_microsoft_narrow_write_response_never_promotes_scheduling_consent(microsoft_env):
    env = microsoft_env
    await busy(env)
    _, state = await start_write(env)
    env.token_scopes = frozenset(microsoft.SCOPES)
    assert (await finish(env, state)).status_code == 403
    assert (await status(env))["grants"][0]["status"] != "connected"


@pytest.mark.asyncio
async def test_microsoft_flag_and_readwrite_scope_cannot_enable_unqualified_event_creation(
    microsoft_env, monkeypatch
):
    env = await connected(microsoft_env)

    def never(**kwargs):
        raise AssertionError("Unqualified mutation must stop before any provider transport")

    monkeypatch.setattr(scheduling, "MicrosoftCalendarWriteProvider", never)
    count = len(env.exchanges)
    response = await env.client.post(
        BASE + "/events/preview", json=await draft(env), headers=env.headers
    )
    assert response.status_code == 409 and "not available" in response.text
    assert len(env.exchanges) == count
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentCalendarEvent)) == 0


async def seed_receipt(env):
    body = await draft(env)
    reviewed = CalendarEventPreview.model_validate(body)
    async with env.factory() as database:
        grant = await database.get(StudentCalendarWriteGrant, UUID(env.grant_id))
        now = await database_now(database)
        row = StudentCalendarEvent(
            id=uuid4(),
            owner_scope_id=grant.owner_scope_id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            grant_id=grant.id,
            auth_session_id=grant.auth_session_id,
            request_id=uuid4(),
            generation=grant.generation,
            revision=1,
            grant_revision=grant.revision,
            status="uncertain",
            provider="microsoft",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            encrypted_payload="",
            digest="",
        )
        payload = {
            "provider": "microsoft",
            "draft": reviewed.model_dump(mode="json"),
            "connection_id": env.connection_id,
            "calendar_label": "Owned Microsoft calendar",
            "etag": None,
            "creation_etag": None,
            "provenance": "Added by NavoX",
        }
        row.digest = scheduling._digest(
            {"id": str(row.id), "generation": row.generation, "payload": payload}
        )
        row.encrypted_payload = scheduling._seal(row, payload, env.settings)
        data = {
            **scheduling._body(row, reviewed, description="Added by NavoX"),
            "status": "confirmed",
            "etag": 'W/"observed-v1"',
        }
        database.add(row)
        await database.commit()
    response = await env.client.get(BASE + "/events/" + str(row.id), headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json(), data


class Recovery:
    def __init__(self, event):
        self.data, self.reads, self.missing = event, 0, False

    async def event(self, *, access_token, calendar_id, event_id, check):
        assert access_token == "synthetic-calendar-access" and calendar_id == "owned-ms"
        await check()
        self.reads += 1
        if self.missing == "none":
            return None
        if self.missing:
            raise CalendarWriteUncertain("Marker lookup cannot establish absence")
        return deepcopy(self.data)

    async def owned_calendars(self, *, access_token, check):
        await check()
        return (CalendarTarget("owned-ms", "Owned Microsoft calendar", "UTC"),)

    async def insert(self, **kwargs):
        raise AssertionError("Recovery cannot create")

    async def delete(self, **kwargs):
        raise AssertionError("Unqualified Microsoft Undo cannot delete")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, "none"])
async def test_microsoft_positive_recovery_never_guesses_absence_or_qualifies_undo(
    microsoft_env, monkeypatch, missing
):
    env = await connected(microsoft_env)
    env.settings.connector_secret_encryption_key = env.settings.google_token_encryption_key
    view, event = await seed_receipt(env)
    provider = Recovery(event)
    monkeypatch.setattr(scheduling, "MicrosoftCalendarWriteProvider", lambda **kwargs: provider)
    env.settings.student_microsoft_calendar_writes_enabled = (
        False  # Read-only recovery remains useful.
    )
    found = await act(env, view, "reconcile")
    assert found.status_code == 200 and found.json()["status"] == "created", found.text
    assert found.json()["provider"] == "outlook" and found.json()["can_undo"] is False
    assert "Undo is not available" in found.json()["message"]
    provider.missing = missing
    unknown = await act(env, found.json(), "reconcile")
    assert unknown.status_code == 200 and unknown.json()["status"] != "undone", unknown.text
    assert unknown.json()["can_undo"] is False
    assert (await act(env, unknown.json(), "undo")).status_code == 409
    assert provider.reads == 2
    async with env.factory() as database:
        assert (
            await database.get(StudentCalendarEvent, UUID(view["id"]))
        ).status in scheduling.CLAIMED_STATUSES
