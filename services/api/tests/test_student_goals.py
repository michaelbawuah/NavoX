"""Private manual reports, exact periods, revision fences and truthful projections."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from navox.api import student_goals
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.models import User
from navox.db.session import get_database_session
from navox.db.student_goals import StudentGoal, StudentGoalCheckIn, StudentGoalCommand
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.student import service as schedule
from navox.student_goals import service

NOW = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
ROOT = "/api/v1/student/goals"
PRIVATE_TITLE = "Call my family privately"
PRIVATE_NOTE = "A private explicit report"


@pytest_asyncio.fixture
async def goals_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    ).model_copy(update={"student_goals_enabled": True})
    clock = SimpleNamespace(now=NOW)

    async def database():
        async with ai_database() as session:
            yield session

    async def now(_database):
        return clock.now

    monkeypatch.setattr(service, "database_now", now)
    app = create_app()
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "goals@example.com",
                "password": "twelve-character-password",
                "display_name": "Student",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        user_id, workspace_id = UUID(data["id"]), UUID(data["workspace"]["id"])
        async with ai_database() as session:
            user = await session.get(User, user_id)
            user.timezone, user.agent_paused = "America/New_York", True
            await session.commit()
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            app=app,
            settings=settings,
            clock=clock,
            user_id=user_id,
            workspace_id=workspace_id,
        )


def goal_body(**changes):
    return {
        "request_id": str(uuid4()),
        "generation": 0,
        "title": PRIVATE_TITLE,
        "description": "My own goal",
        "category": "personal",
        "tracking": "qualitative",
        "cadence": "weekly",
        "starts_on": "2026-10-01",
        "ends_on": "2026-12-01",
        **changes,
    }


async def create(env, **changes):
    body = goal_body(**changes)
    response = await env.client.post(ROOT, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


async def current(env):
    response = await env.client.get(ROOT)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_goal_summary_rejects_a_switched_cookie_before_exposing_or_mutating_data(goals_env):
    env = goals_env
    expected = f"{env.workspace_id}:{env.user_id}"
    await create(env)
    assert (await env.client.get(ROOT, headers={"X-NavoX-Account": expected})).status_code == 200
    registration = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-goals@example.com",
            "password": "twelve-character-password",
            "display_name": "Another student",
        },
    )
    assert registration.status_code == 201
    read = await env.client.get(ROOT, headers={"X-NavoX-Account": expected})
    assert read.status_code == 409
    assert read.headers["cache-control"] == "private, no-store"
    assert PRIVATE_TITLE not in read.text
    write = await env.client.post(ROOT, headers={"X-NavoX-Account": expected}, json=goal_body())
    assert write.status_code == 409
    assert (await current(env))["goals"] == []


@pytest.mark.asyncio
async def test_goal_summary_rejects_duplicate_browser_account_headers(goals_env):
    env = goals_env
    expected = f"{env.workspace_id}:{env.user_id}"
    response = await env.client.get(
        ROOT, headers=[("X-NavoX-Account", expected), ("X-NavoX-Account", expected)]
    )
    assert response.status_code == 409


def report_body(goal, **changes):
    return {
        "request_id": str(uuid4()),
        "goal_revision": goal["revision"],
        "revision": 0,
        "expected_timezone": "America/New_York",
        "completed": True,
        "note": PRIVATE_NOTE + "\nA second private line",
        **changes,
    }


@pytest.mark.asyncio
async def test_empty_setup_is_valid_and_qualitative_report_remains_explicit(goals_env, monkeypatch):
    env = goals_env
    from navox.ai import configured

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Manual goals must never invoke a model")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    empty = await current(env)
    assert empty["goals"] == [] and empty["revision"] == 0
    row, body = await create(env)
    assert row["progress"]["qualitative_latest"] == "unknown"
    response = await env.client.post(
        f"{ROOT}/{row['id']}/checkins", json=report_body(row, completed=False)
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["on_date"] == "2026-11-01" and report["reported_timezone"] == "America/New_York"
    result = await current(env)
    goal = result["goals"][0]
    assert goal["progress"]["qualitative_latest"] == "no"
    assert goal["progress"]["latest_reported_on"] == "2026-11-01"
    assert (
        goal["progress"]["reported_count"] is None
        and goal["progress"]["target_met_from_reports"] is None
    )
    assert (
        goal["progress"]["period_start"] == "2026-10-26"
        and goal["progress"]["period_end"] == "2026-11-01"
    )
    assert goal["status"] == "active" and goal["status_source"] == "student_selected"
    assert result["planner_metrics"]["attendance_sleep_and_grades_observed"] is False
    assert "attendance" not in report and "percentage" not in json.dumps(goal)
    async with env.factory() as database:
        saved = await database.get(StudentGoal, UUID(row["id"]))
        check = await database.get(StudentGoalCheckIn, UUID(report["id"]))
        commands = list(await database.scalars(select(StudentGoalCommand)))
        assert (
            PRIVATE_TITLE not in saved.encrypted_payload
            and PRIVATE_NOTE not in check.encrypted_payload
        )
        assert PRIVATE_TITLE not in repr([c.__dict__ for c in commands])
        assert PRIVATE_NOTE not in repr([c.__dict__ for c in commands])
        assert commands[0].payload_hash != service.fingerprint(None, body)
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_maximum_unicode_goal_text_can_be_saved_retried_and_read(goals_env):
    env = goals_env
    body = goal_body(
        title="😀" * 160,
        description="🧠" * 1000,
        tracking="count",
        target=100000,
        unit="📚" * 40,
    )
    response = await env.client.post(ROOT, json=body)
    assert response.status_code == 201, response.text
    saved = response.json()
    for key in ("title", "description", "unit"):
        assert saved[key] == body[key]
    replay = await env.client.post(ROOT, json=body)
    assert replay.status_code == 201 and replay.json() == saved
    assert (await current(env))["goals"] == [saved]
    async with env.factory() as database:
        row = await database.get(StudentGoal, UUID(saved["id"]))
        assert len(row.encrypted_payload) > 16384
        assert body["description"] not in row.encrypted_payload


@pytest.mark.asyncio
async def test_maximum_unicode_checkin_note_can_be_saved_retried_and_read(goals_env):
    env = goals_env
    goal, _ = await create(env)
    body = report_body(goal, note="😀" * 1000)
    response = await env.client.post(f"{ROOT}/{goal['id']}/checkins", json=body)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["note"] == body["note"]
    replay = await env.client.post(f"{ROOT}/{goal['id']}/checkins", json=body)
    assert replay.status_code == 201 and replay.json() == saved
    exact = await env.client.get(
        f"{ROOT}/{goal['id']}/checkins", params={"on_date": saved["on_date"]}
    )
    assert exact.status_code == 200 and exact.json() == saved
    assert (await current(env))["goals"][0]["recent_checkins"] == [saved]
    async with env.factory() as database:
        row = await database.get(StudentGoalCheckIn, UUID(saved["id"]))
        assert len(row.encrypted_payload) > 16384
        assert body["note"] not in row.encrypted_payload


@pytest.mark.asyncio
async def test_count_is_absolute_for_day_retries_and_replacements_do_not_double_count(goals_env):
    env = goals_env
    row, _ = await create(env, tracking="count", target=3, unit="gym visits", category="habit")
    body = report_body(row, completed=None, count=2, on_date="2026-10-31")
    response = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=body)
    assert response.status_code == 201, response.text
    first = response.json()
    repeated = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=body)
    assert repeated.json() == first
    row = (await current(env))["goals"][0]
    assert row["progress"]["reported_count"] == 2
    response = await env.client.post(
        f"{ROOT}/{row['id']}/checkins", json=report_body(row, completed=None, count=1)
    )
    assert response.status_code == 201, response.text
    row = (await current(env))["goals"][0]
    assert (
        row["progress"]["reported_count"] == 3
        and row["progress"]["target_met_from_reports"] is True
    )
    replacement = report_body(
        row, completed=None, count=1, on_date="2026-10-31", revision=first["revision"]
    )
    response = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=replacement)
    assert response.status_code == 201, response.text
    row = (await current(env))["goals"][0]
    assert row["progress"]["reported_count"] == 2 and row["progress"]["days_reported"] == 2
    assert row["progress"]["remaining_count"] == 1
    exact = await env.client.get(f"{ROOT}/{row['id']}/checkins", params={"on_date": "2026-10-31"})
    assert exact.json()["revision"] == 2 and exact.json()["count"] == 1
    absent = await env.client.get(f"{ROOT}/{row['id']}/checkins", params={"on_date": "2026-10-30"})
    assert absent.status_code == 200 and absent.json() is None
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentGoalCheckIn)) == 2


@pytest.mark.asyncio
async def test_current_user_zone_dst_and_stale_zone_are_guarded(goals_env):
    env = goals_env
    row, _ = await create(env, cadence="daily")
    body = report_body(row)
    response = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=body)
    assert response.status_code == 201
    entry = response.json()
    env.clock.now += timedelta(hours=1)  # Both occurrences of 01:30 share the same local date.
    row = (await current(env))["goals"][0]
    duplicate = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report_body(row))
    assert duplicate.status_code == 409
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "Pacific/Auckland"
        await database.commit()
    changed = await current(env)
    assert changed["timezone"] == "Pacific/Auckland"
    stale = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report_body(row))
    assert stale.status_code == 409 and "zone" in stale.json()["detail"]
    exact = await env.client.get(
        f"{ROOT}/{row['id']}/checkins", params={"on_date": entry["on_date"]}
    )
    assert exact.json()["reported_timezone"] == "America/New_York"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"on_date": "2026-11-02"},
        {"on_date": "2026-09-01"},
        {"count": 1},
        {"completed": "yes"},
        {"expected_timezone": "made/up"},
        {"attendance": True},
    ],
)
async def test_invalid_reports_do_not_echo_private_values(goals_env, changes):
    env = goals_env
    row, _ = await create(env)
    response = await env.client.post(
        f"{ROOT}/{row['id']}/checkins", json=report_body(row, **changes)
    )
    assert response.status_code == 422
    assert (
        PRIVATE_NOTE not in response.text
        and response.headers["cache-control"] == "private, no-store"
    )
    assert (await current(env))["goals"][0]["progress"]["qualitative_latest"] == "unknown"


@pytest.mark.asyncio
async def test_active_quota_pause_restore_and_stale_revisions(goals_env):
    env = goals_env
    first, body = await create(env)
    await create(env, title="Second")
    await create(env, title="Third")
    rejected = await env.client.post(ROOT, json=goal_body(title="Fourth"))
    assert rejected.status_code == 409
    update = {
        **{key: value for key, value in body.items() if key != "generation"},
        "request_id": str(uuid4()),
        "revision": first["revision"],
        "status": "paused",
    }
    paused = await env.client.patch(f"{ROOT}/{first['id']}", json=update)
    assert paused.status_code == 200, paused.text
    await create(env, title="Fourth")
    stale = await env.client.patch(
        f"{ROOT}/{first['id']}", json={**update, "request_id": str(uuid4())}
    )
    assert stale.status_code == 409
    restore = await env.client.patch(
        f"{ROOT}/{first['id']}",
        json={
            **update,
            "request_id": str(uuid4()),
            "revision": paused.json()["revision"],
            "status": "active",
        },
    )
    assert restore.status_code == 409


@pytest.mark.asyncio
async def test_tracking_definition_cannot_reinterpret_existing_reports(goals_env):
    env = goals_env
    row, definition = await create(env, tracking="count", target=3, unit="visits")
    response = await env.client.post(
        f"{ROOT}/{row['id']}/checkins", json=report_body(row, completed=None, count=1)
    )
    assert response.status_code == 201
    fresh = (await current(env))["goals"][0]
    update = {
        **{key: value for key, value in definition.items() if key != "generation"},
        "request_id": str(uuid4()),
        "revision": fresh["revision"],
        "status": "active",
        "target": 8,
    }
    changed = await env.client.patch(f"{ROOT}/{row['id']}", json=update)
    assert changed.status_code == 409
    update.update(request_id=str(uuid4()), target=3, title="An updated private title")
    renamed = await env.client.patch(f"{ROOT}/{row['id']}", json=update)
    assert renamed.status_code == 200 and renamed.json()["progress"]["reported_count"] == 1


@pytest.mark.asyncio
async def test_checkin_erasure_works_flag_off_without_key_and_preserves_retry_fence(goals_env):
    env = goals_env
    row, _ = await create(env)
    body = report_body(row)
    response = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=body)
    assert response.status_code == 201
    report = response.json()
    env.settings.__dict__["student_goals_enabled"] = False
    env.settings.connector_secret_encryption_key = None
    deleted = await env.client.request(
        "DELETE",
        f"{ROOT}/{row['id']}/checkins/{report['id']}",
        json={"request_id": str(uuid4()), "revision": report["revision"]},
    )
    assert deleted.status_code == 204, deleted.text
    assert (await env.client.get(ROOT)).status_code == 404
    env.settings.__dict__["student_goals_enabled"] = True
    replay = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=body)
    assert replay.status_code == 410
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentGoalCheckIn)) == 0
        saved = await database.get(StudentGoal, UUID(row["id"]))
        assert saved.revision == 3


@pytest.mark.asyncio
async def test_goal_and_reset_erase_payloads_and_never_resurrect_old_requests(goals_env):
    env = goals_env
    row, create_body = await create(env)
    report = report_body(row)
    response = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report)
    assert response.status_code == 201
    stale_delete = await env.client.request(
        "DELETE",
        f"{ROOT}/{row['id']}",
        json={"request_id": str(uuid4()), "revision": row["revision"]},
    )
    assert stale_delete.status_code == 409
    fresh = (await current(env))["goals"][0]
    deletion = {"request_id": str(uuid4()), "revision": fresh["revision"]}
    for _ in range(2):
        assert (
            await env.client.request("DELETE", f"{ROOT}/{row['id']}", json=deletion)
        ).status_code == 204
    assert (await env.client.post(ROOT, json=create_body)).status_code == 410
    assert (await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report)).status_code == 410
    second, second_body = await create(env)
    board = await current(env)
    reset_body = {"request_id": str(uuid4()), "revision": board["revision"]}
    for _ in range(2):
        assert (await env.client.request("DELETE", ROOT, json=reset_body)).status_code == 204
    assert (await env.client.post(ROOT, json=second_body)).status_code == 410
    assert (await current(env))["goals"] == []
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentGoal)) == 0
        assert await database.scalar(select(func.count()).select_from(StudentGoalCheckIn)) == 0


@pytest.mark.asyncio
async def test_encrypted_payload_splicing_is_rejected_and_foreign_owner_cannot_read_or_erase(
    goals_env,
):
    env = goals_env
    first, _ = await create(env)
    second, _ = await create(env, title="Another private goal")
    async with env.factory() as database:
        a, b = (
            await database.get(StudentGoal, UUID(first["id"])),
            await database.get(StudentGoal, UUID(second["id"])),
        )
        b.encrypted_payload = a.encrypted_payload
        await database.commit()
    unreadable = await env.client.get(ROOT)
    assert unreadable.status_code == 503 and PRIVATE_TITLE not in unreadable.text
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as other:
        response = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "other-goals@example.com",
                "password": "twelve-character-password",
                "display_name": "Other student",
            },
        )
        assert response.status_code == 201
        absent = await other.get(f"{ROOT}/{first['id']}/checkins", params={"on_date": "2026-11-01"})
        deleted = await other.request(
            "DELETE", f"{ROOT}/{first['id']}", json={"request_id": str(uuid4()), "revision": 1}
        )
        assert absent.status_code == deleted.status_code == 404
        assert (await other.get(ROOT)).json()["goals"] == []


@pytest.mark.asyncio
async def test_retry_id_changed_payload_and_origin_are_rejected(goals_env):
    env = goals_env
    row, body = await create(env)
    assert (await env.client.post(ROOT, json=body)).json()["id"] == row["id"]
    changed = await env.client.post(ROOT, json={**body, "title": "Private different text"})
    assert changed.status_code == 409 and "Private different" not in changed.text
    blocked = await env.client.request(
        "DELETE",
        f"{ROOT}/{row['id']}",
        json={"request_id": str(uuid4()), "revision": 1},
        headers={"Origin": "https://other.example"},
    )
    assert blocked.status_code == 403
    invalid = await env.client.post(
        ROOT,
        content=b'{"title":"private-secret", "title":"hidden"}',
        headers={"Content-Type": "application/json"},
    )
    assert invalid.status_code == 422 and "private-secret" not in invalid.text


@pytest.mark.asyncio
async def test_planner_metrics_use_outcome_event_time_and_stay_separate_from_goal_reports(
    goals_env,
):
    env = goals_env
    async with env.factory() as database:
        task = StudentTask(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            title="Private planner task",
            estimated_minutes=90,
            remaining_minutes=0,
            actual_minutes=90,
            status="done",
        )
        database.add(task)
        await database.flush()
        for status, outcome_at, minutes in (
            ("done", NOW, 25),
            ("took_longer", NOW, 10),
            ("skipped", NOW, 0),
            ("done", NOW + timedelta(minutes=20), 45),
            ("took_longer", NOW - timedelta(days=10), 65),
            ("planned", None, 0),
        ):
            database.add(
                StudentStudyBlock(
                    id=uuid4(),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    task_id=task.id,
                    plan_id=uuid4(),
                    title=task.title,
                    start_at=NOW - timedelta(hours=2),
                    end_at=NOW - timedelta(hours=1),
                    minutes=60,
                    status=status,
                    outcome_at=outcome_at,
                    actual_minutes=minutes,
                )
            )
        await database.commit()
    result = await current(env)
    assert result["goals"] == []
    metrics = result["planner_metrics"]
    assert metrics["completed_blocks"] == 1 and metrics["skipped_blocks"] == 1
    assert metrics["recorded_minutes"] == 35 and metrics["source"] == "recorded_planner_outcomes"
    assert "Private planner task" not in json.dumps(result)


@pytest.mark.asyncio
async def test_shared_reset_helper_keeps_tombstones_without_decrypting(goals_env):
    env = goals_env
    row, body = await create(env)
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.forget_goals_data(database, env.user_id, env.workspace_id)
        await database.commit()
    assert (await env.client.post(ROOT, json=body)).status_code == 410
    assert (await current(env))["goals"] == []


@pytest.mark.asyncio
async def test_reset_generation_rejects_pre_reset_first_time_create_and_allows_fresh_form(
    goals_env,
):
    env = goals_env
    pending = goal_body()
    empty = await current(env)
    response = await env.client.request(
        "DELETE", ROOT, json={"request_id": str(uuid4()), "revision": empty["revision"]}
    )
    assert response.status_code == 204
    stale = await env.client.post(ROOT, json=pending)
    assert stale.status_code == 409 and "reset" in stale.json()["detail"]
    fresh = await current(env)
    assert fresh["generation"] == 1 and fresh["goals"] == []
    created = await env.client.post(ROOT, json=goal_body(generation=fresh["generation"]))
    assert created.status_code == 201


@pytest.mark.asyncio
async def test_full_student_reset_clears_goals_with_flag_off_and_retains_retry_fences(goals_env):
    env = goals_env
    row, body = await create(env)
    report = report_body(row)
    assert (await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report)).status_code == 201
    env.settings.student_goals_enabled = False
    env.settings.connector_secret_encryption_key = None
    request = {"request_id": str(uuid4()), "revision": 0, "confirmed": True}
    first = await env.client.post("/api/v1/student/data/reset", json=request)
    assert first.status_code == 200, first.text
    second = await env.client.post("/api/v1/student/data/reset", json=request)
    assert second.json()["reused"] is True
    env.settings.student_goals_enabled = True
    assert (await env.client.post(ROOT, json=body)).status_code == 410
    assert (await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report)).status_code == 410
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentGoal)) == 0
        assert await database.scalar(select(func.count()).select_from(StudentGoalCheckIn)) == 0


@pytest.mark.asyncio
async def test_registered_default_off_and_authentication_boundary(goals_env):
    env = goals_env
    assert Settings(_env_file=None, app_environment="test").student_goals_enabled is False
    assert (
        sum(
            getattr(route, "original_router", None) is student_goals.router
            for route in env.app.routes
        )
        == 1
    )
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as anonymous:
        assert (await anonymous.get(ROOT)).status_code == 401
    env.settings.student_goals_enabled = False
    assert (await env.client.get(ROOT)).status_code == 404
    assert (await env.client.post(ROOT, json=goal_body())).status_code == 404


@pytest.mark.asyncio
async def test_saved_goal_and_report_quotas_are_bounded_but_erasure_remains_available(
    goals_env, monkeypatch
):
    env = goals_env
    monkeypatch.setattr(service, "MAX_GOALS", 1)
    row, definition = await create(env)
    paused = await env.client.patch(
        f"{ROOT}/{row['id']}",
        json={
            **{key: value for key, value in definition.items() if key != "generation"},
            "request_id": str(uuid4()),
            "revision": row["revision"],
            "status": "paused",
        },
    )
    assert paused.status_code == 200
    assert (
        await env.client.post(ROOT, json=goal_body(title="Still counts toward saved quota"))
    ).status_code == 409
    monkeypatch.setattr(service, "MAX_COMMANDS", 1)
    blocked = await env.client.patch(
        f"{ROOT}/{row['id']}",
        json={
            **{key: value for key, value in definition.items() if key != "generation"},
            "request_id": str(uuid4()),
            "revision": paused.json()["revision"],
            "status": "active",
        },
    )
    assert blocked.status_code == 409
    env.settings.connector_secret_encryption_key = None
    env.settings.__dict__["student_goals_enabled"] = False
    deleted = await env.client.request(
        "DELETE",
        f"{ROOT}/{row['id']}",
        json={"request_id": str(uuid4()), "revision": paused.json()["revision"]},
    )
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_report_quota_and_body_limits_fail_before_writing(goals_env, monkeypatch):
    env = goals_env
    monkeypatch.setattr(service, "MAX_CHECKINS", 1)
    row, _ = await create(env)
    response = await env.client.post(f"{ROOT}/{row['id']}/checkins", json=report_body(row))
    assert response.status_code == 201
    fresh = (await current(env))["goals"][0]
    blocked = await env.client.post(
        f"{ROOT}/{row['id']}/checkins", json=report_body(fresh, on_date="2026-10-31")
    )
    assert blocked.status_code == 409
    large = await env.client.post(
        ROOT, content=b"x" * 16385, headers={"Content-Type": "application/json"}
    )
    assert large.status_code == 413
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentGoalCheckIn)) == 1


@pytest.mark.asyncio
async def test_postgres_goal_create_race_respects_shared_owner_quota(goals_env):
    env = goals_env
    async with env.factory() as database:
        if database.bind.dialect.name != "postgresql":
            pytest.skip("This quota race requires real PostgreSQL User row locks")
    await create(env, title="One")
    await create(env, title="Two")
    responses = await asyncio.gather(
        *(
            env.client.post(ROOT, json=goal_body(title=title))
            for title in ("Concurrent A", "Concurrent B")
        )
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len((await current(env))["goals"]) == 3
