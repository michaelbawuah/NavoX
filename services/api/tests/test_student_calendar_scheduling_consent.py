"""Separate scheduling-purpose consent under real native transactions.

The Google exchange/profile responses are synthetic; these are not live-account
qualification results.
"""

import asyncio
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_student_calendar_consent import BASE, IDENTITY, callback
from test_student_calendar_consent import calendar_env as calendar_env
from test_student_calendar_consent import disconnect as busy_disconnect
from test_student_calendar_consent import start as busy_start
from test_student_calendar_consent import status as busy_status

from navox.db.models import Connection, OAuthAuthorizationAttempt
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarWriteGrant
from navox.student_calendars import oauth, service
from navox.student_calendars.schemas import AVAILABILITY_SCOPES

WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
CONSENT = "calendar-scheduling.v1"


async def scheduling_status(env):
    response = await env.client.get(BASE + "/scheduling", headers=env.headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


async def busy_connection(env):
    started, query, _ = await busy_start(env)
    result = await callback(env, query)
    assert result.headers["location"].endswith("calendar_availability_enabled")
    busy = await busy_status(env)
    grant = next(item for item in busy["grants"] if item["id"] == started["grant_id"])
    assert grant["status"] == "connected"
    return started["grant_id"], grant["connection_id"]


async def start_body(env, connection_id_value, **changes):
    current = await busy_status(env)
    return {
        "generation": current["generation"],
        "revision": current["revision"],
        "request_id": str(uuid4()),
        "consent_version": CONSENT,
        "confirmed": True,
        "connection_id": connection_id_value,
        **changes,
    }


async def scheduling_start(env, connection_id, **changes):
    body = await start_body(env, connection_id, **changes)
    response = await env.client.post(
        BASE + "/google/scheduling/start", json=body, headers=env.headers
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return response.json(), query, body


def scheduling_exchange(monkeypatch, *, scopes=None):
    async def exchange(*_):
        return {
            "access_token": "synthetic-scheduling-token-never-retained",
            "refresh_token": "synthetic-scheduling-refresh",
            "scope": " ".join(
                scopes if scopes is not None else [*IDENTITY, *AVAILABILITY_SCOPES, WRITE_SCOPE]
            ),
            "expires_in": 3600,
        }

    monkeypatch.setattr(oauth, "exchange_authorization_code", exchange)


async def enable_scheduling(env, monkeypatch):
    busy_id, connection_id = await busy_connection(env)
    scheduling_exchange(monkeypatch)
    started, query, body = await scheduling_start(env, connection_id)
    result = await callback(env, query)
    assert result.headers["location"].endswith("calendar_scheduling_enabled"), result.headers
    return busy_id, connection_id, started, query, body


async def disconnect_scheduling(env, grant_id, *, current=None):
    if current is None:
        current = await busy_status(env)
    return await env.client.post(
        BASE + "/scheduling/" + grant_id + "/disconnect",
        headers=env.headers,
        json={
            "generation": current["generation"],
            "revision": current["revision"],
            "request_id": str(uuid4()),
        },
    )


@pytest.mark.asyncio
async def test_existing_busy_consent_does_not_request_or_create_scheduling_authority(calendar_env):
    env = calendar_env
    started, query, _ = await busy_start(env)
    assert WRITE_SCOPE not in started["requested_scopes"]
    assert query["scope"] == [" ".join([*IDENTITY, *AVAILABILITY_SCOPES])]
    assert (
        (await callback(env, query)).headers["location"].endswith("calendar_availability_enabled")
    )
    assert (await scheduling_status(env))["grants"] == []
    async with env.factory() as database:
        assert list(await database.scalars(select(StudentCalendarWriteGrant))) == []


@pytest.mark.asyncio
async def test_broad_provider_scopes_do_not_substitute_for_native_scheduling_consent(
    calendar_env, monkeypatch
):
    env = calendar_env
    scheduling_exchange(monkeypatch)
    await busy_connection(env)
    assert (await scheduling_status(env))["grants"] == []
    async with env.factory() as database:
        connection = (await database.scalars(select(Connection))).one()
        assert WRITE_SCOPE in connection.granted_scopes
        assert list(await database.scalars(select(StudentCalendarWriteGrant))) == []


@pytest.mark.asyncio
async def test_scheduling_start_requests_narrow_owned_event_scope_bound_to_existing_busy_account(
    calendar_env,
):
    env = calendar_env
    busy_id, connection_id = await busy_connection(env)
    started, query, body = await scheduling_start(env, connection_id)
    assert started["requested_scopes"] == [*IDENTITY, *AVAILABILITY_SCOPES, WRITE_SCOPE]
    assert query["scope"] == [" ".join(started["requested_scopes"])]
    assert query["code_challenge_method"] == ["S256"]
    assert not any(
        "gmail" in scope or scope.endswith("/calendar") for scope in started["requested_scopes"]
    )
    async with env.factory() as database:
        write = await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        attempt = await database.get(OAuthAuthorizationAttempt, write.oauth_attempt_id)
        busy = await database.get(StudentCalendarGrant, UUID(busy_id))
        assert write.status == "pending" and write.connection_id == UUID(connection_id)
        assert write.consent_version == CONSENT
        assert attempt.purpose == "calendar_scheduling"
        assert busy.status == "active"
    current = await busy_status(env)
    replay = await env.client.post(
        BASE + "/google/scheduling/start",
        json={**body, "revision": current["revision"]},
        headers=env.headers,
    )
    assert replay.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, 1, "true", "must-never-be-echoed", None])
async def test_scheduling_requires_explicit_true_and_sanitizes_rejected_input(
    calendar_env, confirmed
):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    body = await start_body(env, connection_id, confirmed=confirmed)
    response = await env.client.post(
        BASE + "/google/scheduling/start", json=body, headers=env.headers
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
    assert "must-never-be-echoed" not in response.text


@pytest.mark.asyncio
async def test_busy_disclosure_or_missing_existing_connection_cannot_start_scheduling(calendar_env):
    env = calendar_env
    busy_id, connection_id = await busy_connection(env)
    for changes in ({"consent_version": "calendar-availability.v1"}, {"connection_id": None}):
        response = await env.client.post(
            BASE + "/google/scheduling/start",
            json=await start_body(env, connection_id, **changes),
            headers=env.headers,
        )
        assert response.status_code == 422
    assert (await busy_disconnect(env, busy_id)).status_code == 200
    response = await env.client.post(
        BASE + "/google/scheduling/start",
        json=await start_body(env, connection_id),
        headers=env.headers,
    )
    assert response.status_code == 409
    async with env.factory() as database:
        assert (await database.get(Connection, UUID(connection_id))).status == "active"
        assert list(await database.scalars(select(StudentCalendarWriteGrant))) == []


@pytest.mark.asyncio
async def test_scheduling_has_exact_cookie_account_origin_and_release_guards(calendar_env):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    body = await start_body(env, connection_id)
    assert (await env.client.get(BASE + "/scheduling")).status_code == 409
    assert (
        await env.client.get(
            BASE + "/scheduling", headers={**env.headers, "Authorization": "Bearer value"}
        )
    ).status_code == 403
    for headers in (
        {"X-NavoX-Account": env.headers["X-NavoX-Account"]},
        {**env.headers, "X-NavoX-Account": "wrong:account"},
    ):
        response = await env.client.post(
            BASE + "/google/scheduling/start", json=body, headers=headers
        )
        assert response.status_code in (403, 409)
        assert response.headers["cache-control"] == "private, no-store"
    env.settings.student_calendar_availability_enabled = False
    # Viewing and withdrawing existing consent stays available when writes are off.
    assert (await env.client.get(BASE + "/scheduling", headers=env.headers)).status_code == 200
    assert (
        await env.client.post(BASE + "/google/scheduling/start", json=body, headers=env.headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_scheduling_callback_preserves_separate_busy_grant_and_proves_write_purpose(
    calendar_env, monkeypatch
):
    env = calendar_env
    busy_id, connection_id, started, query, _ = await enable_scheduling(env, monkeypatch)
    current = await scheduling_status(env)
    assert current["consent_version"] == CONSENT
    assert current["grants"][0]["status"] == "connected"
    assert current["grants"][0]["id"] == started["grant_id"]
    assert (await busy_status(env))["grants"][0]["id"] == busy_id
    assert (await busy_status(env))["grants"][0]["status"] == "connected"
    async with env.factory() as database:
        busy = await database.get(StudentCalendarGrant, UUID(busy_id))
        write = await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        connection = await database.get(Connection, UUID(connection_id))
        assert busy.status == "active" and busy.revision == 2
        assert write.status == "active" and write.revision == 2
        assert write.connection_id == busy.connection_id
        assert set(connection.granted_scopes) == {*IDENTITY, *AVAILABILITY_SCOPES, WRITE_SCOPE}
    assert (await callback(env, query)).status_code == 400


@pytest.mark.asyncio
async def test_scheduling_callback_denies_narrowed_scopes_without_touching_busy_connection(
    calendar_env, monkeypatch
):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    started, query, _ = await scheduling_start(env, connection_id)
    scheduling_exchange(monkeypatch, scopes=[*IDENTITY, *AVAILABILITY_SCOPES])
    response = await callback(env, query)
    assert response.headers["location"].endswith("scope_mismatch")
    assert (await busy_status(env))["grants"][0]["status"] == "connected"
    async with env.factory() as database:
        write = await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        connection = await database.get(Connection, UUID(connection_id))
        assert write.status != "active"
        assert WRITE_SCOPE not in connection.granted_scopes


@pytest.mark.asyncio
async def test_provider_permission_narrowing_removes_connected_scheduling_status(
    calendar_env, monkeypatch
):
    env = calendar_env
    _, connection_id, _, _, _ = await enable_scheduling(env, monkeypatch)
    async with env.factory() as database:
        connection = await database.get(Connection, UUID(connection_id))
        connection.granted_scopes = [*IDENTITY, *AVAILABILITY_SCOPES]
        await database.commit()
    assert (await scheduling_status(env))["grants"][0]["status"] == "unavailable"
    assert (await busy_status(env))["grants"][0]["status"] == "connected"


@pytest.mark.asyncio
async def test_new_login_cannot_complete_previous_cookie_scheduling_window(
    calendar_env, monkeypatch
):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    started, query, _ = await scheduling_start(env, connection_id)
    logged_in = await env.client.post(
        "/api/v1/auth/login",
        json={"email": "calendar-native@example.com", "password": "twelve-character-password"},
    )
    assert logged_in.status_code == 200

    async def forbidden(*_):
        raise AssertionError("Old scheduling cookie must be rejected before provider exchange")

    monkeypatch.setattr(oauth, "exchange_authorization_code", forbidden)
    assert (await callback(env, query)).headers["location"].endswith("failed")
    async with env.factory() as database:
        assert (
            await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        ).status != "active"


@pytest.mark.asyncio
async def test_scheduling_callback_rejects_wrong_google_account(calendar_env, monkeypatch):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    started, query, _ = await scheduling_start(env, connection_id)
    scheduling_exchange(monkeypatch)

    async def profile(*_):
        return {
            "sub": "different-google-account",
            "email": "different@example.test",
            "email_verified": True,
        }

    monkeypatch.setattr(oauth, "fetch_profile", profile)
    assert (await callback(env, query)).headers["location"].endswith("account_mismatch")
    async with env.factory() as database:
        assert (
            await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        ).status != "active"


@pytest.mark.asyncio
async def test_oauth_purpose_cannot_be_swapped_to_complete_scheduling(calendar_env, monkeypatch):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    started, query, _ = await scheduling_start(env, connection_id)
    async with env.factory() as database:
        grant = await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        attempt = await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
        attempt.purpose = "calendar_availability"
        await database.commit()

    async def forbidden(*_):
        raise AssertionError("A swapped OAuth purpose must fail before exchange")

    monkeypatch.setattr(oauth, "exchange_authorization_code", forbidden)
    assert (await callback(env, query)).headers["location"].endswith("failed")


@pytest.mark.asyncio
async def test_write_withdrawal_works_while_disabled_and_keeps_busy_consent(
    calendar_env, monkeypatch
):
    env = calendar_env
    busy_id, connection_id, started, _, _ = await enable_scheduling(env, monkeypatch)
    current = await busy_status(env)
    env.settings.student_calendar_availability_enabled = False
    response = await disconnect_scheduling(env, started["grant_id"], current=current)
    assert response.status_code == 200, response.text
    assert response.json()["grants"][0]["status"] == "revoked"
    async with env.factory() as database:
        busy = await database.get(StudentCalendarGrant, UUID(busy_id))
        write = await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        connection = await database.get(Connection, UUID(connection_id))
        assert busy.status == "active" and write.status == "revoked"
        assert connection.status == "active" and connection.credential_reference is not None


@pytest.mark.asyncio
async def test_write_flag_withdrawn_during_oauth_window_prevents_provider_exchange(
    calendar_env, monkeypatch
):
    env = calendar_env
    _, connection_id = await busy_connection(env)
    started, query, _ = await scheduling_start(env, connection_id)
    env.settings.student_calendar_writes_enabled = False

    async def forbidden(*_):
        raise AssertionError("Disabled writes must reject pending OAuth before exchange")

    monkeypatch.setattr(oauth, "exchange_authorization_code", forbidden)
    assert (await callback(env, query)).headers["location"].endswith("failed")
    async with env.factory() as database:
        assert (
            await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        ).status != "active"
    assert (await busy_status(env))["grants"][0]["status"] == "connected"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disconnect", "busy_disconnect", "reset", "logout"])
async def test_late_scheduling_profile_cannot_restore_withdrawn_authority(
    calendar_env, monkeypatch, change
):
    env = calendar_env
    busy_id, connection_id = await busy_connection(env)
    scheduling_exchange(monkeypatch)
    started, query, _ = await scheduling_start(env, connection_id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_profile(*_):
        entered.set()
        await release.wait()
        return {
            "sub": "synthetic-google-calendar",
            "email": "calendar@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(oauth, "fetch_profile", slow_profile)
    task = asyncio.create_task(callback(env, query))
    await asyncio.wait_for(entered.wait(), 5)
    if change == "disconnect":
        assert (await disconnect_scheduling(env, started["grant_id"])).status_code == 200
    elif change == "busy_disconnect":
        assert (await busy_disconnect(env, busy_id)).status_code == 200
    elif change == "reset":
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": str(uuid4())},
            headers=env.headers,
        )
        assert response.status_code == 200, response.text
    else:
        assert (await env.client.post("/api/v1/auth/logout")).status_code == 200
    release.set()
    response = await asyncio.wait_for(task, 5)
    assert response.headers["location"].endswith("failed")
    async with env.factory() as database:
        grant = await database.get(StudentCalendarWriteGrant, UUID(started["grant_id"]))
        assert grant is None or grant.status != "active"
        connection = await database.get(Connection, UUID(connection_id))
        assert WRITE_SCOPE not in connection.granted_scopes
        if change == "reset":
            assert list(await database.scalars(select(StudentCalendarWriteGrant))) == []
            assert list(await database.scalars(select(StudentCalendarGrant))) == []
            assert (await service.state(database, env.user_id, env.workspace_id)).generation == 1
