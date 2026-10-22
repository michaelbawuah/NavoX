"""Real owner/session/timetable fences around mocked read-only Google wire data."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_vox_calls import vox_env as shared_vox_fixture

from navox.connectors.sync_state import database_now
from navox.db.models import (
    Connection,
    ConnectionCredential,
    OAuthAuthorizationAttempt,
    User,
    UserSession,
)
from navox.db.student import StudentClassMeeting, StudentCourse, StudentFixedBlock
from navox.db.student_calendars import StudentCalendarGrant, StudentCalendarState
from navox.db.student_planning import StudentPlanningState
from navox.providers.google_availability import GoogleAvailabilitySnapshot, ProviderBusy
from navox.student import service as schedule
from navox.student_availability import service
from navox.student_availability.schemas import AvailabilityPreview
from navox.student_calendars.schemas import AVAILABILITY_SCOPES, CONSENT_VERSION
from navox.student_planning.schemas import Preferences

vox_env = shared_vox_fixture
ROOT = "/api/v1/student/availability/preview"


class Provider:
    def __init__(self, *, state="complete", during=None, busy=True, day=None):
        self.state, self.during, self.with_busy = state, during, busy
        self.day = day
        self.calls = 0

    async def collect(self, *, access_token, start_at, end_at, check):
        assert access_token == "private-synthetic-access-token"
        self.calls += 1
        await check()
        if self.during:
            await self.during()
        await check()
        day = self.day or start_at
        busy = (ProviderBusy(day.replace(hour=10), day.replace(hour=11)),) if self.with_busy else ()
        return GoogleAvailabilitySnapshot(self.state, 2, busy, datetime.now(UTC))


@pytest_asyncio.fixture
async def availability_env(vox_env, monkeypatch):
    env = vox_env
    env.settings.student_calendar_availability_enabled = True
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
    env.payload = AvailabilityPreview(
        start_at=tomorrow,
        end_at=tomorrow.replace(hour=23),
        duration_minutes=30,
        preferences_revision=1,
        expected_timezone="UTC",
        routine_confirmed=True,
        travel_assumption="zero",
    )
    env.provider = Provider(day=tomorrow)

    async def refresh(_database, *, connection, settings):
        assert connection not in _database
        connection.access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        connection.last_checked_at = datetime.now(UTC)
        return "private-synthetic-access-token"

    monkeypatch.setattr(service, "access_token_for_connection", refresh)
    monkeypatch.setattr(service, "GoogleAvailabilityProvider", lambda: env.provider)
    return env


async def grant(env, *, status="active", scopes=AVAILABILITY_SCOPES):
    async with env.factory() as database:
        now = await database_now(database)
        state = await database.scalar(
            select(StudentCalendarState).where(
                StudentCalendarState.user_id == env.user_id,
                StudentCalendarState.workspace_id == env.workspace_id,
            )
        )
        if state is None:
            state = StudentCalendarState(
                user_id=env.user_id, workspace_id=env.workspace_id, generation=0, revision=1
            )
            database.add(state)
            await database.flush()
        credential = ConnectionCredential(
            encrypted_refresh_token="private-sealed-refresh-placeholder"
        )
        database.add(credential)
        await database.flush()
        connection = Connection(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            status="active",
            external_account_id="private-account-" + uuid4().hex,
            external_email="private-google@example.test",
            credential_reference=credential.id,
            granted_scopes=list(scopes),
        )
        database.add(connection)
        await database.flush()
        attempt = OAuthAuthorizationAttempt(
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            purpose="calendar_availability",
            connection_id=connection.id,
            requested_scopes=list(AVAILABILITY_SCOPES),
            state_hash=uuid4().hex,
            code_verifier="synthetic-no-network-verifier",
            expires_at=now + timedelta(minutes=10),
        )
        database.add(attempt)
        await database.flush()
        item = StudentCalendarGrant(
            owner_scope_id=state.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            generation=state.generation,
            revision=1,
            status=status,
            connection_id=connection.id if status == "active" else None,
            oauth_attempt_id=attempt.id,
            auth_session_id=env.session_id,
            request_id=uuid4(),
            consent_version=CONSENT_VERSION,
            accepted_at=now,
        )
        database.add(item)
        await database.commit()
        return item.id, connection.id


async def preview(env, payload=None):
    return await service.preview(
        env.factory,
        env.settings,
        env.user_id,
        env.workspace_id,
        env.token_hash,
        payload or env.payload,
        provider=env.provider,
    )


@pytest.mark.asyncio
async def test_fresh_complete_snapshot_uses_owned_sources_without_exposing_provider_identity(
    availability_env,
):
    env = availability_env
    await grant(env)
    result = await preview(env)
    assert result.result.status == "complete" and result.result.confirmed_openings
    assert env.provider.calls == 1
    assert result.input.routine_confirmed and len(result.input.busy) == 1
    assert result.checked_at == result.input.now == result.result.checked_at
    assert result.checked_at < result.expires_at <= result.checked_at + timedelta(seconds=60)
    assert all(item.fresh_until == result.expires_at for item in result.input.coverage)
    text = result.model_dump_json()
    assert all(
        value not in text
        for value in (
            "private-google",
            "private-account",
            "private-synthetic",
            "refresh-placeholder",
        )
    )
    assert result.changes_applied is False
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentCalendarGrant)) == 1
        connection = await database.scalar(select(Connection))
        assert connection.status == "active" and set(connection.granted_scopes) == set(
            AVAILABILITY_SCOPES
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["missing", "pending", "scope-missing", "partial", "unavailable"]
)
async def test_incomplete_calendar_inventory_cannot_produce_confirmed_openings(
    availability_env, source
):
    env = availability_env
    if source == "pending":
        await grant(env, status="pending")
    elif source != "missing":
        await grant(env, scopes=() if source == "scope-missing" else AVAILABILITY_SCOPES)
    if source in {"partial", "unavailable"}:
        env.provider.state = source
    result = await preview(env)
    assert result.result.confirmed_openings == ()
    assert result.result.provisional_openings
    assert any(item.state != "complete" for item in result.source_summary)
    assert env.provider.calls == (1 if source in {"partial", "unavailable"} else 0)


@pytest.mark.asyncio
async def test_default_revision_zero_is_not_a_saved_confirmed_routine(availability_env):
    env = availability_env
    await grant(env)
    async with env.factory() as database:
        row = await database.scalar(select(StudentPlanningState))
        row.preferences_revision = 0
        await database.commit()
    result = await preview(env, env.payload.model_copy(update={"preferences_revision": 0}))
    assert result.input.routine_confirmed is False
    assert result.result.status == "needs_routine" and result.result.confirmed_openings == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["class", "fixed"])
async def test_unknown_return_travel_remains_provisional_even_with_saved_one_way_walk(
    availability_env, kind
):
    env = availability_env
    await grant(env)
    env.provider.with_busy = False
    day = env.payload.start_at.date()
    async with env.factory() as database:
        if kind == "class":
            course = StudentCourse(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code="private-course-code",
                name="Private course",
                term_start=day,
                term_end=day,
            )
            database.add(course)
            await database.flush()
            database.add(
                StudentClassMeeting(
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    course_id=course.id,
                    kind="lecture",
                    weekdays=[day.weekday()],
                    start_time="12:00",
                    end_time="13:00",
                    timezone="UTC",
                    walk_minutes=5,
                )
            )
        else:
            database.add(
                StudentFixedBlock(
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    title="Private shift",
                    weekdays=[day.weekday()],
                    start_time="12:00",
                    end_time="13:00",
                    timezone="UTC",
                    term_start=day,
                    term_end=day,
                )
            )
        await database.commit()
    result = await preview(env, env.payload.model_copy(update={"travel_assumption": "unknown"}))
    assert result.result.confirmed_openings == ()
    assert result.result.provisional_openings
    assert any(warning.code == "travel_unknown" for warning in result.result.warnings)
    fact = result.input.busy[0]
    assert fact.travel_after_minutes is None
    assert fact.travel_before_minutes == (5 if kind == "class" else None)
    assert (
        "Private" not in result.model_dump_json()
        and "private-course-code" not in result.model_dump_json()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["grant", "reset", "session", "pause", "routine", "timezone", "schedule", "plan"]
)
async def test_late_provider_result_is_discarded_when_authority_or_internal_revision_changes(
    availability_env, change
):
    env = availability_env
    grant_id, _ = await grant(env)

    async def mutate():
        async with env.factory() as database:
            if change == "grant":
                item = await database.get(StudentCalendarGrant, grant_id)
                item.status, item.revoked_at = "revoked", await database_now(database)
                (await database.scalar(select(StudentCalendarState))).revision += 1
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
                        title="New private shift",
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

    env.provider.during = mutate
    with pytest.raises(schedule.StudentError) as raised:
        await preview(env)
    assert raised.value.status_code in {401, 409}


@pytest.mark.asyncio
async def test_detached_refresh_cannot_restore_scope_or_metadata_after_revocation(
    availability_env, monkeypatch
):
    env = availability_env
    grant_id, connection_id = await grant(env)

    async def refresh(_database, *, connection, settings):
        async with env.factory() as database:
            row = await database.get(StudentCalendarGrant, grant_id)
            row.status, row.revoked_at = "revoked", await database_now(database)
            (await database.scalar(select(StudentCalendarState))).revision += 1
            await database.commit()
        connection.access_token_expires_at = datetime.now(UTC) + timedelta(days=1)
        return "private-synthetic-access-token"

    monkeypatch.setattr(service, "access_token_for_connection", refresh)
    with pytest.raises(schedule.StudentError):
        await preview(env)
    assert env.provider.calls == 0
    async with env.factory() as database:
        assert (await database.get(Connection, connection_id)).access_token_expires_at is None
        assert (await database.get(StudentCalendarGrant, grant_id)).status == "revoked"


@pytest.mark.asyncio
async def test_refresh_scope_narrowing_is_retained_but_never_sent_to_provider(
    availability_env, monkeypatch
):
    env = availability_env
    _, connection_id = await grant(env)

    async def refresh(_database, *, connection, settings):
        connection.granted_scopes = []
        return "private-synthetic-access-token"

    monkeypatch.setattr(service, "access_token_for_connection", refresh)
    with pytest.raises(schedule.StudentError):
        await preview(env)
    assert env.provider.calls == 0
    async with env.factory() as database:
        assert (await database.get(Connection, connection_id)).granted_scopes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["routine", "timezone", "window"])
async def test_invalid_search_context_is_rejected_before_token_or_provider(
    availability_env, monkeypatch, change
):
    env = availability_env
    await grant(env)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Invalid preview must not refresh credentials")

    monkeypatch.setattr(service, "access_token_for_connection", forbidden)
    updates = (
        {"preferences_revision": 0}
        if change == "routine"
        else {"expected_timezone": "Europe/London"}
    )
    if change == "window":
        updates = {"end_at": env.payload.start_at + timedelta(days=16)}
    with pytest.raises(schedule.StudentError):
        await preview(env, env.payload.model_copy(update=updates))
    assert env.provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,status", [("disabled", 404), ("bearer", 403), ("origin", 403), ("account", 409)]
)
async def test_private_route_checks_flag_cookie_origin_and_account_before_provider(
    availability_env, mode, status
):
    env = availability_env
    await grant(env)
    headers = {}
    if mode == "disabled":
        env.settings.student_calendar_availability_enabled = False
    elif mode == "bearer":
        headers["Authorization"] = "Bearer untrusted"
    elif mode == "origin":
        headers["Origin"] = "https://unrelated.example"
    else:
        headers["X-NavoX-Account"] = "wrong-account"
    response = await env.client.post(
        ROOT, json=env.payload.model_dump(mode="json"), headers=headers
    )
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert env.provider.calls == 0


@pytest.mark.asyncio
async def test_actual_private_route_returns_fresh_owned_snapshot(availability_env):
    env = availability_env
    await grant(env)
    response = await env.client.post(ROOT, json=env.payload.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["input"]["user_id"] == str(env.user_id)
    assert response.json()["changes_applied"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["class", "google"])
@pytest.mark.parametrize("edge", ["before", "after"])
async def test_source_halo_preserves_buffers_from_events_outside_the_visible_window(
    availability_env, kind, edge
):
    env = availability_env
    await grant(env)
    start = env.payload.start_at.replace(hour=8)
    end = start.replace(hour=9)
    payload = env.payload.model_copy(update={"start_at": start, "end_at": end})
    event_start = start.replace(hour=7) if edge == "before" else end + timedelta(minutes=5)
    event_end = start - timedelta(minutes=5) if edge == "before" else end.replace(hour=10)
    if kind == "class":
        async with env.factory() as database:
            course = StudentCourse(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code="private-edge-course",
                name="Private edge course",
                term_start=start.date(),
                term_end=start.date(),
            )
            database.add(course)
            await database.flush()
            database.add(
                StudentClassMeeting(
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    course_id=course.id,
                    kind="lecture",
                    weekdays=[start.weekday()],
                    start_time=event_start.strftime("%H:%M"),
                    end_time=event_end.strftime("%H:%M"),
                    timezone="UTC",
                    walk_minutes=5,
                )
            )
            await database.commit()

    async def collect(*, access_token, start_at, end_at, check):
        assert start_at < start - service.SOURCE_PADDING
        assert end_at > end + service.SOURCE_PADDING
        assert start_at < event_start < event_end < end_at
        await check()
        await check()
        return GoogleAvailabilitySnapshot(
            "complete",
            1,
            (ProviderBusy(event_start, event_end),) if kind == "google" else (),
            datetime.now(UTC),
        )

    env.provider.collect = collect
    result = await preview(env, payload)
    assert result.input.start_at == start and result.input.end_at == end
    assert all(item.start_at == start and item.end_at == end for item in result.input.coverage)
    assert result.input.busy[0].start_at == event_start
    assert len(result.result.confirmed_openings) == 1
    opening = result.result.confirmed_openings[0]
    if edge == "before":
        assert opening.start_at == start + timedelta(minutes=5)
    else:
        assert opening.end_at == end - timedelta(minutes=10 if kind == "class" else 5)


@pytest.mark.asyncio
async def test_saved_routine_route_is_account_bound_and_does_not_read_the_provider(
    availability_env,
):
    env = availability_env
    route = ROOT.replace("/preview", "/routine")
    response = await env.client.get(route)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        **Preferences().model_dump(mode="json"),
        "revision": 1,
        "timezone": "UTC",
    }
    for headers, status in (
        ({"X-NavoX-Account": "wrong-account"}, 409),
        ({"Authorization": "Bearer untrusted"}, 403),
    ):
        denied = await env.client.get(route, headers=headers)
        assert denied.status_code == status
        assert denied.headers["cache-control"] == "private, no-store"
    async with env.factory() as database:
        (await database.get(UserSession, env.session_id)).revoked_at = await database_now(database)
        await database.commit()
    denied = await env.client.get(route)
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "private, no-store"
    assert env.provider.calls == 0


@pytest.mark.asyncio
async def test_unchecked_accounts_remain_required_unknown_sources_at_the_bounded_read_limit(
    availability_env,
):
    env = availability_env
    for _ in range(service.MAX_ACCOUNTS_PER_CHECK + 1):
        await grant(env)
    result = await preview(env)
    assert env.provider.calls == service.MAX_ACCOUNTS_PER_CHECK
    assert (
        len([source for source in result.source_summary if source.source_id.startswith("google-")])
        == service.MAX_ACCOUNTS_PER_CHECK + 1
    )
    assert any(source.state == "unavailable" for source in result.source_summary)
    assert result.result.confirmed_openings == ()


@pytest.mark.asyncio
async def test_cancelled_detached_refresh_cannot_publish_metadata_or_start_provider_reads(
    availability_env, monkeypatch
):
    env = availability_env
    _, connection_id = await grant(env)
    entered = asyncio.Event()

    async def refresh(_database, *, connection, settings):
        connection.access_token_expires_at = datetime.now(UTC) + timedelta(days=1)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "access_token_for_connection", refresh)
    task = asyncio.create_task(preview(env))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.provider.calls == 0
    async with env.factory() as database:
        assert (await database.get(Connection, connection_id)).access_token_expires_at is None


@pytest.mark.asyncio
async def test_provider_snapshot_that_outlives_freshness_is_discarded(
    availability_env, monkeypatch
):
    env = availability_env
    await grant(env)
    async with env.factory() as database:
        initial = await database_now(database)
    instants = iter((initial, initial + timedelta(seconds=61)))

    async def clock(_database):
        return next(instants)

    monkeypatch.setattr(service, "database_now", clock)
    with pytest.raises(schedule.StudentError) as raised:
        await preview(env)
    assert raised.value.status_code == 409 and env.provider.calls == 1
