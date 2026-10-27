"""Owned Apple write consent and native receipts; synthetic provider calls only."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select
from test_apple_calendar_connection import ACCOUNT, PASSWORD, connect
from test_apple_calendar_connection import apple_env as shared_apple_env
from test_student_calendar_events import act
from test_vox_media import begin, confirm

from navox.connectors.sync_state import database_now
from navox.db.models import OAuthAuthorizationAttempt, User
from navox.db.student_calendars import StudentCalendarEvent, StudentCalendarWriteGrant
from navox.db.student_planning import StudentPlanningState, StudentStudyBlock, StudentTask
from navox.student_calendars import apple, claims, displacement, scheduling
from navox.student_calendars.provider import (
    CalendarCreateReceipt,
    CalendarTarget,
    CalendarWriteChanged,
    CalendarWriteUncertain,
)
from navox.student_planning import calendar_busy
from navox.student_planning import service as planning
from navox.student_planning.schemas import Preferences
from navox.vox.calendar_approval import CalendarApprovalCoordinator
from navox.vox.calendar_tools import PREPARE_TOOL, UNDO_TOOL, CalendarTools, _when
from navox.vox.worker import DatabaseAuthority, WorkerAssignment

apple_env = shared_apple_env
BASE = "/api/v1/student/calendars"
TARGET = "https://p01-caldav.icloud.com/123/calendars/personal/"


class Writer:
    def __init__(self):
        self.events, self.inserts, self.deletes = {}, [], []
        self.creation_etag = '"created-v1"'
        self.observed_etag = '"created-v1"'
        self.insert_mode = self.delete_mode = "normal"
        self.during_target = None

    async def _check(self, apple_id, app_password, check):
        assert (apple_id, app_password) == (ACCOUNT, PASSWORD)
        await check()

    async def owned_calendars(self, *, apple_id, app_password, check):
        await self._check(apple_id, app_password, check)
        return (CalendarTarget(TARGET, "Personal Apple calendar", "UTC"),)

    async def target(self, *, apple_id, app_password, calendar_id, check):
        await self._check(apple_id, app_password, check)
        assert calendar_id == TARGET
        if self.during_target:
            await self.during_target()
        await check()
        return CalendarTarget(TARGET, "Personal Apple calendar", "UTC")

    async def event(self, *, apple_id, app_password, calendar_id, event_id, check):
        await self._check(apple_id, app_password, check)
        assert calendar_id == TARGET
        return deepcopy(self.events.get(event_id))

    async def insert(self, *, apple_id, app_password, calendar_id, event, check):
        await self._check(apple_id, app_password, check)
        assert calendar_id == TARGET and event["description"] == "Added by NavoX"
        self.inserts.append(deepcopy(event))
        if self.insert_mode == "unknown_absent":
            raise CalendarWriteUncertain("Synthetic PUT outcome is unknown")
        created = {**deepcopy(event), "status": "confirmed", "etag": self.observed_etag}
        if self.creation_etag is None:
            created["sequence"] = 2  # A later semantically matching version.
        self.events[event["id"]] = deepcopy(created)
        await check()
        return CalendarCreateReceipt(created, self.creation_etag)

    async def delete(self, *, apple_id, app_password, calendar_id, event_id, etag, check):
        await self._check(apple_id, app_password, check)
        assert calendar_id == TARGET
        self.deletes.append((event_id, etag))
        if self.delete_mode == "unknown_present":
            raise CalendarWriteUncertain("Synthetic conditional DELETE outcome is unknown")
        if self.events.get(event_id, {}).get("etag") != etag:
            raise CalendarWriteChanged("Synthetic stale version")
        self.events.pop(event_id, None)
        await check()


@pytest_asyncio.fixture
async def apple_write_env(apple_env, monkeypatch):
    env = apple_env
    env.settings.student_calendar_writes_enabled = True
    env.settings.student_apple_calendar_writes_enabled = True
    env.settings.connector_secret_encryption_key = SecretStr(Fernet.generate_key().decode())
    response, _ = await connect(env)
    assert response.status_code == 200, response.text
    env.connection_id = response.json()["grants"][0]["connection_id"]
    env.busy_grant_id = response.json()["grants"][0]["id"]
    async with env.factory() as database:
        (await database.get(User, env.user_id)).timezone = "UTC"
        database.add(
            StudentPlanningState(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                revision=1,
                preferences_revision=1,
                preferences=Preferences().model_dump(),
            )
        )
        now = await database_now(database)
        await database.commit()
    env.start_at = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    env.end_at = env.start_at + timedelta(minutes=30)
    env.writes = Writer()
    monkeypatch.setattr(scheduling, "AppleCalendarWriteProvider", lambda **kwargs: env.writes)
    monkeypatch.setattr(calendar_busy, "AppleAvailabilityProvider", apple.AppleAvailabilityProvider)
    monkeypatch.setattr(calendar_busy, "get_settings", lambda: env.settings)
    monkeypatch.setattr(claims, "get_settings", lambda: env.settings)
    monkeypatch.setattr(displacement, "get_settings", lambda: env.settings)
    return env


async def status(env):
    response = await env.client.get(BASE + "/scheduling", headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def consent(env, **changes):
    current = await status(env)
    response = await env.client.post(
        BASE + "/apple/scheduling/start",
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
    if response.status_code == 200:
        env.grant_id = response.json()["grants"][0]["id"]
    return response


async def draft(env, **changes):
    current = await status(env)
    return {
        "generation": current["generation"],
        "revision": current["revision"],
        "request_id": str(uuid4()),
        "grant_id": env.grant_id,
        "calendar_id": TARGET,
        "title": "Reviewed Apple meeting",
        "location": "Study room",
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
        BASE + "/events/preview", json=await draft(env, **changes), headers=env.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def add_study(env):
    env.settings.student_planning_enabled = True
    task_id, block_id = uuid4(), uuid4()
    async with env.factory() as database:
        database.add(
            StudentTask(
                id=task_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                title="Apple study plan",
                due_at=env.start_at.replace(hour=22),
                estimated_minutes=60,
                remaining_minutes=60,
                actual_minutes=0,
                difficulty=3,
                priority=3,
                course_weight=1,
                status="active",
                revision=1,
            )
        )
        await database.flush()
        database.add(
            StudentStudyBlock(
                id=block_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                task_id=task_id,
                plan_id=uuid4(),
                title="Apple study plan",
                start_at=env.start_at,
                end_at=env.start_at + timedelta(hours=1),
                minutes=60,
                focus_window=False,
                revision=1,
                status="planned",
                actual_minutes=0,
            )
        )
        await database.flush()
        state = await planning.state(database, env.user_id, env.workspace_id)
        state.window_start, state.window_end = (
            env.start_at.replace(hour=7),
            env.start_at.replace(hour=23),
        )
        state.accepted_source_hash = await planning.stable_source_hash(
            database, env.user_id, env.workspace_id, state
        )
        await database.commit()
    return block_id


@pytest.mark.asyncio
async def test_apple_busy_and_write_consents_are_distinct_and_no_oauth_is_fabricated(
    apple_write_env,
):
    env = apple_write_env
    assert (await status(env))["grants"] == []
    response = await consent(env)
    assert response.status_code == 200, response.text
    assert response.json()["grants"][0]["provider"] == "icloud"
    assert (
        PASSWORD not in response.text and response.headers["cache-control"] == "private, no-store"
    )
    async with env.factory() as database:
        grant = await database.scalar(select(StudentCalendarWriteGrant))
        assert grant.provider == "icloud" and grant.oauth_attempt_id is None
        assert (
            await database.scalar(select(func.count()).select_from(OAuthAuthorizationAttempt)) == 0
        )
    assert not env.writes.inserts


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, 1, "true", None])
async def test_apple_write_consent_requires_literal_current_confirmation(
    apple_write_env, confirmed
):
    response = await consent(apple_write_env, confirmed=confirmed)
    assert response.status_code == 422
    assert (await status(apple_write_env))["grants"] == []


@pytest.mark.asyncio
async def test_apple_exact_targets_create_readback_and_event_only_undo(apple_write_env):
    env = apple_write_env
    assert (await consent(env)).status_code == 200
    current = await status(env)
    targets = await env.client.post(
        BASE + "/scheduling/targets",
        headers=env.headers,
        json={
            "generation": current["generation"],
            "revision": current["revision"],
            "request_id": str(uuid4()),
            "grant_id": env.grant_id,
        },
    )
    assert targets.status_code == 200 and targets.json()["provider"] == "icloud", targets.text
    view = await preview(env)
    assert view["provider"] == "icloud" and view["status"] == "pending" and not env.writes.inserts
    created = await act(env, view, "approve")
    assert created.status_code == 200 and created.json()["status"] == "created", created.text
    assert created.json()["can_undo"] is True and "Apple" in created.json()["message"]
    removed = await act(env, created.json(), "undo")
    assert removed.status_code == 200 and removed.json()["status"] == "undone", removed.text
    assert len(env.writes.inserts) == len(env.writes.deletes) == 1


@pytest.mark.asyncio
async def test_matching_get_after_no_put_version_never_pins_original_creation_or_enables_undo(
    apple_write_env,
):
    env = apple_write_env
    await consent(env)
    env.writes.creation_etag, env.writes.observed_etag = None, '"later-v2"'
    created = await act(env, await preview(env), "approve")
    assert created.status_code == 200 and created.json()["status"] == "created", created.text
    assert created.json()["can_undo"] is False
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(created.json()["id"]))
        payload = scheduling._open(row, env.settings)
        assert payload["creation_etag"] is None and payload["etag"] == '"later-v2"'
    assert (await act(env, created.json(), "undo")).status_code == 409
    assert env.writes.deletes == []
    held = await env.client.post(
        BASE + "/events/preview", json=await draft(env), headers=env.headers
    )
    assert held.status_code == 409  # The observed event still reserves its native interval.


@pytest.mark.asyncio
async def test_unknown_apple_put_absence_stays_held_and_never_replays(apple_write_env):
    env = apple_write_env
    await consent(env)
    env.writes.insert_mode = "unknown_absent"
    view = await preview(env)
    first = await act(env, view, "approve")
    assert first.status_code == 200 and first.json()["status"] == "uncertain", first.text
    assert (await act(env, first.json(), "reconcile")).json()["status"] == "uncertain"
    assert (await act(env, first.json(), "approve")).status_code == 409
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
async def test_apple_unknown_conditional_undo_is_not_repeated(apple_write_env):
    env = apple_write_env
    await consent(env)
    created = await act(env, await preview(env), "approve")
    env.writes.delete_mode = "unknown_present"
    held = await act(env, created.json(), "undo")
    assert held.status_code == 200 and held.json()["status"] == "undoing", held.text
    assert (await act(env, held.json(), "undo")).status_code == 409
    assert (await act(env, held.json(), "reconcile")).json()["status"] == "undoing"
    assert len(env.writes.deletes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("microseconds", [1, 999999])
async def test_unrepresentable_apple_time_is_rejected_before_review_or_study_movement(
    apple_write_env, microseconds
):
    env = apple_write_env
    await consent(env)
    block_id = await add_study(env)
    response = await env.client.post(
        BASE + "/events/preview",
        headers=env.headers,
        json=await draft(
            env,
            start_at=env.start_at.replace(microsecond=microseconds).isoformat(),
            end_at=env.end_at.replace(microsecond=microseconds).isoformat(),
            allow_study_displacement=True,
        ),
    )
    assert response.status_code == 422, response.text
    async with env.factory() as database:
        block = await database.get(StudentStudyBlock, block_id)
        assert block.revision == 1 and planning.block_view(block).start_at == env.start_at
        assert await database.scalar(select(func.count()).select_from(StudentCalendarEvent)) == 0
    assert env.writes.inserts == []


@pytest.mark.asyncio
async def test_apple_study_moves_remain_saved_after_calendar_only_undo(apple_write_env):
    env = apple_write_env
    await consent(env)
    block_id = await add_study(env)
    view = await preview(env, allow_study_displacement=True)
    assert len(view["study_moves"]) == 1 and view["study_changes_applied"] is False
    created = await act(env, view, "approve")
    assert created.status_code == 200 and created.json()["study_changes_applied"] is True, (
        created.text
    )
    async with env.factory() as database:
        moved = planning.block_view(await database.get(StudentStudyBlock, block_id))
        assert moved.revision == 2 and moved.start_at != env.start_at
    removed = await act(env, created.json(), "undo")
    assert removed.status_code == 200 and removed.json()["status"] == "undone", removed.text
    async with env.factory() as database:
        assert planning.block_view(await database.get(StudentStudyBlock, block_id)) == moved


@pytest.mark.asyncio
async def test_native_vox_apple_review_names_provider_and_uses_host_only_approval(apple_write_env):
    env = apple_write_env
    await consent(env)
    env.settings.student_vox_enabled = True
    env.client.headers.update(env.headers)
    _, intent = await begin(env)
    await confirm(env, intent)
    check = DatabaseAuthority(
        env.factory,
        WorkerAssignment(
            intent.connection_id,
            intent.room_name,
            intent.client_identity,
            intent.agent_identity,
        ),
    )
    approval = CalendarApprovalCoordinator()
    tools = CalendarTools(check, env.settings, approval, voice_available=True)
    result = await tools.execute(
        PREPARE_TOOL,
        {
            "title": "Reviewed Apple meeting",
            "location": "Study room",
            "start_at": env.start_at.isoformat(),
            "duration_minutes": 30,
            "calendar_name": None,
            "account_name": None,
            "provider": "icloud",
            "allow_study_displacement": False,
        },
    )
    assert result["status"] == "review_required" and result["voice_approval_available"], result
    assert not env.writes.inserts
    readback = await approval.after_tool()
    assert "Apple calendar" in readback.answer and "Google calendar" not in readback.answer
    await readback.authorize()
    await approval.played(readback.answer)
    outcome = await approval.input("yes please")
    assert "Apple confirmed" in outcome.answer and len(env.writes.inserts) == 1
    assert (await tools.execute(UNDO_TOOL, {}))["status"] == "review_required"
    undo = await approval.after_tool()
    assert "from Apple calendar" in undo.answer
    await undo.authorize()
    await approval.played(undo.answer)
    assert "Apple confirmed" in (await approval.input("go ahead")).answer
    assert len(env.writes.deletes) == 1


def test_host_exact_fold_readback_includes_both_versioned_local_offsets():
    from datetime import UTC, datetime

    text = _when(
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
        "America/New_York",
    )
    assert "01:30 AM EDT, UTC-04:00" in text
    assert "01:15 AM EST, UTC-05:00" in text and text.endswith("America/New_York")


@pytest.mark.asyncio
async def test_apple_busy_credential_alone_cannot_authorize_event_writes(apple_write_env):
    env = apple_write_env
    env.grant_id = env.busy_grant_id
    response = await env.client.post(
        BASE + "/events/preview", json=await draft(env), headers=env.headers
    )
    assert response.status_code == 409
    assert env.writes.inserts == [] and (await status(env))["grants"] == []


@pytest.mark.asyncio
async def test_independent_apple_admission_flag_blocks_broad_current_credentials(apple_write_env):
    env = apple_write_env
    env.settings.student_apple_calendar_writes_enabled = False
    assert (await consent(env)).status_code == 404
    assert (await status(env))["grants"] == [] and env.writes.inserts == []


@pytest.mark.asyncio
async def test_apple_busy_revocation_during_target_read_prevents_event_and_study_admission(
    apple_write_env,
):
    env = apple_write_env
    await consent(env)
    block_id = await add_study(env)
    view = await preview(env, allow_study_displacement=True)
    current = await status(env)

    async def revoke():
        env.writes.during_target = None
        response = await env.client.post(
            BASE + f"/{env.busy_grant_id}/disconnect",
            headers=env.headers,
            json={
                "generation": current["generation"],
                "revision": current["revision"],
                "request_id": str(uuid4()),
            },
        )
        assert response.status_code == 200, response.text

    env.writes.during_target = revoke
    response = await act(env, view, "approve")
    assert response.status_code == 409, response.text
    assert env.writes.inserts == []
    async with env.factory() as database:
        assert (await database.get(StudentStudyBlock, block_id)).revision == 1
        assert (await database.get(StudentCalendarEvent, UUID(view["id"]))).status == "pending"
