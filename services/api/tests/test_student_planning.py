import asyncio
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from navox.api import student_planning
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.lms_feeds import LmsFeedItem
from navox.db.models import User
from navox.db.session import get_database_session
from navox.db.student import StudentClassMeeting, StudentCourse, StudentTimetableBreak
from navox.db.student_planning import (
    StudentPlanningCommand,
    StudentPlanningPreview,
    StudentPlanningState,
    StudentStudyBlock,
    StudentTask,
)
from navox.student import service as schedule
from navox.student_planning import service
from navox.student_planning.planner import build_plan
from navox.student_planning.schemas import DailyWindow, OutcomeInput, Preferences, TaskView

MONDAY = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # 08:00 New York
ROOT = "/api/v1/student/planning"


def task(**changes):
    return TaskView.model_validate(
        {
            "id": uuid4(),
            "title": "Study task",
            "remaining_minutes": 120,
            "estimated_minutes": 120,
            "actual_minutes": 0,
            "revision": 1,
            "source_item_id": None,
            "source_revision": None,
            **changes,
        }
    )


def test_plan_obeys_fixed_travel_never_sleep_focus_and_exact_deadline():
    start = datetime(2026, 10, 19, 7, tzinfo=UTC)
    due = start.replace(hour=16)
    work = task(due_at=due, difficulty=5)
    result = build_plan(
        [work],
        Preferences(
            wake_time="07:00",
            sleep_time="23:00",
            focus_windows=[DailyWindow(start_time="10:00", end_time="12:00")],
            never_schedule=[DailyWindow(start_time="12:00", end_time="13:00")],
        ),
        "UTC",
        start_at=start,
        end_at=start.replace(hour=23),
        busy=[
            (start.replace(hour=8, minute=45), start.replace(hour=10)),
            (start.replace(hour=17), start.replace(hour=21)),
        ],
    )
    focused = [block for block in result.blocks if block.focus_window]
    assert focused[0].start_at == start.replace(hour=10)
    assert sum(block.minutes for block in focused) == 105
    for block in result.blocks:
        assert block.start_at.hour >= 7 and block.end_at.hour <= 23
        assert block.end_at <= due and block.minutes <= 90
        assert not (
            block.start_at < start.replace(hour=10)
            and block.end_at > start.replace(hour=8, minute=45)
        )
        assert not (
            block.start_at < start.replace(hour=13) and block.end_at > start.replace(hour=12)
        )
    assert sum(block.minutes for block in result.blocks) == 120


def test_due_work_cannot_be_fitted_by_extending_past_deadline_or_erasing_buffer():
    start = datetime(2026, 10, 19, 9, tzinfo=UTC)
    result = build_plan(
        [task(due_at=start + timedelta(hours=1))],
        Preferences(
            wake_time="09:00",
            sleep_time="10:00",
        ),
        "UTC",
        start_at=start,
        end_at=start + timedelta(hours=1),
        busy=[],
    )
    assert sum(block.minutes for block in result.blocks) == 51
    assert result.capacities[0].buffer_minutes == 9
    assert result.unfitted_work[0].minutes == 69
    assert result.unfitted_work[0].reason == "cannot_meet_deadline"
    assert max(block.end_at for block in result.blocks) <= start + timedelta(hours=1)


def test_large_work_splits_across_days_with_breaks_and_real_buffer():
    start = datetime(2026, 10, 19, 0, tzinfo=UTC)
    result = build_plan(
        [task(remaining_minutes=600)],
        Preferences(
            wake_time="09:00",
            sleep_time="13:00",
            max_study_minutes=90,
            break_minutes=15,
        ),
        "UTC",
        start_at=start,
        end_at=start + timedelta(days=4),
        busy=[],
    )
    assert len({block.start_at.date() for block in result.blocks}) >= 3
    assert sum(block.minutes for block in result.blocks) == 600
    for capacity in result.capacities:
        assert capacity.planned_minutes <= capacity.free_minutes - capacity.buffer_minutes
    for previous, current in zip(result.blocks, result.blocks[1:], strict=False):
        assert current.start_at >= previous.end_at + timedelta(minutes=15)


@pytest.mark.parametrize(
    "day,expected",
    [(datetime(2027, 3, 14, 5, tzinfo=UTC), 120), (datetime(2026, 11, 1, 4, tzinfo=UTC), 240)],
)
def test_dst_windows_use_elapsed_minutes_and_do_not_guess_ambiguous_focus(day, expected):
    prefs = Preferences(
        wake_time="01:00" if expected == 120 else "00:00",
        sleep_time="04:00" if expected == 120 else "03:00",
        focus_windows=[
            DailyWindow(
                start_time="02:15" if expected == 120 else "01:15",
                end_time="02:45" if expected == 120 else "01:45",
            )
        ],
    )
    result = build_plan(
        [task(remaining_minutes=600)],
        prefs,
        "America/New_York",
        start_at=day,
        end_at=day + timedelta(hours=12),
        busy=[],
    )
    assert result.capacities[0].free_minutes == expected
    assert any("clock change" in warning for warning in result.warnings)
    assert all(block.end_at > block.start_at for block in result.blocks)


def test_completed_and_overdue_tasks_are_truthful_and_planner_is_deterministic():
    now = datetime(2026, 10, 19, 9, tzinfo=UTC)
    rows = [task(status="completed"), task(due_at=now - timedelta(minutes=1))]
    args = dict(start_at=now, end_at=now + timedelta(hours=8), busy=[])
    result = build_plan(rows, Preferences(), "UTC", **args)
    assert result.blocks == []
    assert len(result.unfitted_work) == 1
    assert result.unfitted_work[0].reason == "deadline_passed"
    assert result == build_plan(rows, Preferences(), "UTC", **args)


def test_took_longer_requires_remaining_estimate_and_skips_cannot_claim_work():
    base = dict(request_id=uuid4(), revision=1, task_revision=1)
    with pytest.raises(ValidationError):
        OutcomeInput(**base, outcome="took_longer", actual_minutes=50)
    with pytest.raises(ValidationError):
        OutcomeInput(**base, outcome="skipped", actual_minutes=20)


def test_a_deadline_in_ten_minutes_keeps_priority_over_larger_work_due_in_fifty():
    start = datetime(2026, 10, 19, 9, tzinfo=UTC)
    urgent = task(remaining_minutes=10, priority=1, due_at=start + timedelta(minutes=10))
    larger = task(remaining_minutes=80, priority=5, due_at=start + timedelta(minutes=50))
    result = build_plan(
        [larger, urgent],
        Preferences(wake_time="09:00", sleep_time="10:00"),
        "UTC",
        start_at=start,
        end_at=start + timedelta(hours=1),
        busy=[],
    )
    assert sum(block.minutes for block in result.blocks if block.task_id == urgent.id) == 10
    assert all(
        block.end_at <= urgent.due_at for block in result.blocks if block.task_id == urgent.id
    )


def test_varied_plans_conserve_work_and_respect_every_busy_interval_and_deadline():
    rng = random.Random(20261017)
    start = datetime(2026, 10, 19, 7, tzinfo=UTC)
    end = start + timedelta(days=3)
    for _ in range(25):
        rows = [
            task(
                remaining_minutes=rng.randint(1, 600),
                difficulty=rng.randint(1, 5),
                priority=rng.randint(1, 5),
                due_at=start + timedelta(hours=rng.randint(1, 72)),
            )
            for _ in range(rng.randint(1, 10))
        ]
        busy = [(start + timedelta(hours=rng.randint(1, 65)), start + timedelta(hours=66))]
        result = build_plan(
            rows,
            Preferences(
                focus_windows=[DailyWindow(start_time="09:00", end_time="12:00")],
            ),
            "UTC",
            start_at=start,
            end_at=end,
            busy=busy,
        )
        for row in rows:
            scheduled = [block for block in result.blocks if block.task_id == row.id]
            left = [work.minutes for work in result.unfitted_work if work.task_id == row.id]
            assert sum(block.minutes for block in scheduled) + sum(left) == row.remaining_minutes
            assert all(block.end_at <= row.due_at for block in scheduled)
        for block in result.blocks:
            assert all(not (block.start_at < right and block.end_at > left) for left, right in busy)
        for left, right in zip(result.blocks, result.blocks[1:], strict=False):
            assert right.start_at >= left.end_at + timedelta(minutes=15)
        for day in result.capacities:
            assert day.planned_minutes <= day.free_minutes - day.buffer_minutes


@pytest_asyncio.fixture
async def planning_env(ai_database, monkeypatch):
    settings = Settings(_env_file=None, app_environment="test", ai_provider="disabled")
    clock = SimpleNamespace(now=MONDAY)

    async def database():
        async with ai_database() as session:
            yield session

    async def now(_database):
        return clock.now

    monkeypatch.setattr(service, "database_now", now)
    app = create_app()
    if not any(getattr(route, "path", "") == ROOT + "/tasks" for route in app.routes):
        app.include_router(student_planning.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[student_planning.enabled] = lambda: None
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        result = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "planning@example.com",
                "password": "twelve-character-password",
                "display_name": "Planner owner",
            },
        )
        assert result.status_code == 201, result.text
        account = result.json()
        async with ai_database() as session:
            user = await session.get(User, UUID(account["id"]))
            user.timezone, user.agent_paused = "America/New_York", True
            await session.commit()
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            app=app,
            clock=clock,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
        )


async def create_task(env, **changes):
    result = await env.client.post(
        ROOT + "/tasks",
        json={
            "title": "Private math task",
            "request_id": str(uuid4()),
            "estimated_minutes": 180,
            "due_at": (MONDAY + timedelta(days=2)).isoformat(),
            **changes,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


async def preview(env, **changes):
    response = await env.client.post(ROOT + "/preview", json={"days": 7, **changes})
    assert response.status_code == 200, response.text
    return response.json()


async def accept(env, proposed=None, **changes):
    proposed = proposed or await preview(env)
    payload = {
        "preview_id": proposed["preview_id"],
        "preview_hash": proposed["preview_hash"],
        "request_id": str(uuid4()),
        "confirmed": True,
        **changes,
    }
    result = await env.client.post(ROOT + "/accept", json=payload)
    assert result.status_code == 200, result.text
    return result.json(), payload


@pytest.mark.asyncio
async def test_disabled_flag_is_real_and_reads_are_empty_without_writes(planning_env):
    env = planning_env
    for path in ("/preferences", "/tasks", "/plan"):
        assert (await env.client.get(ROOT + path)).status_code == 200
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentPlanningState)) == 0
    del env.app.dependency_overrides[student_planning.enabled]
    assert (await env.client.get(ROOT + "/tasks")).status_code == 404


@pytest.mark.asyncio
async def test_flag_rollback_keeps_only_authenticated_owned_task_erasure_available(planning_env):
    env = planning_env
    row = await create_task(env)
    await accept(env)
    del env.app.dependency_overrides[student_planning.enabled]
    assert (await env.client.get(ROOT + "/tasks")).status_code == 404
    assert (
        await env.client.post(
            ROOT + "/tasks", json={"title": "Blocked", "request_id": str(uuid4())}
        )
    ).status_code == 404
    path = ROOT + f"/tasks/{row['id']}?revision={row['revision']}&request_id={uuid4()}"
    assert (
        await env.client.delete(path, headers={"Origin": "https://foreign.example"})
    ).status_code == 403
    stale = ROOT + f"/tasks/{row['id']}?revision=999&request_id={uuid4()}"
    assert (await env.client.delete(stale)).status_code == 409
    assert (await env.client.delete(path)).status_code == 204
    async with env.factory() as database:
        assert await database.get(StudentTask, UUID(row["id"])) is None
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == 0
        assert await database.scalar(select(func.count()).select_from(StudentPlanningPreview)) == 0


@pytest.mark.asyncio
async def test_task_idempotency_revision_and_no_provider_call_while_paused(
    planning_env, monkeypatch
):
    from navox.ai import configured

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Manual study planning must not invoke an AI provider")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    env = planning_env
    identifier = str(uuid4())
    first = await create_task(env, request_id=identifier)
    again = await create_task(env, request_id=identifier)
    assert again == first
    response = await env.client.post(
        ROOT + "/tasks",
        json={
            "title": "Different",
            "request_id": identifier,
        },
    )
    assert response.status_code == 409
    response = await env.client.patch(
        ROOT + "/tasks/" + first["id"],
        json={
            "title": first["title"],
            "request_id": str(uuid4()),
            "revision": 99,
            "estimated_minutes": 180,
            "remaining_minutes": 180,
        },
    )
    assert response.status_code == 409
    assert (await preview(env))["result"]["blocks"]
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentTask)) == 1


@pytest.mark.asyncio
async def test_initial_plan_requires_exact_preview_and_accept_is_idempotent(planning_env):
    env = planning_env
    await create_task(env)
    proposed = await preview(env)
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == 0
    invalid = await env.client.post(
        ROOT + "/accept",
        json={
            "preview_id": proposed["preview_id"],
            "preview_hash": "0" * 64,
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert invalid.status_code == 409
    plan, payload = await accept(env, proposed)
    assert plan["blocks"]
    repeat = await env.client.post(ROOT + "/accept", json=payload)
    assert repeat.json() == plan
    assert len((await env.client.get(ROOT + "/plan")).json()["blocks"]) == len(plan["blocks"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["task", "course", "break", "timezone"])
async def test_source_changes_fence_old_preview_even_without_a_profile_revision(
    planning_env, mutation
):
    env = planning_env
    row = await create_task(env)
    proposed = await preview(env)
    async with env.factory() as database:
        if mutation == "task":
            task_row = await database.get(StudentTask, UUID(row["id"]))
            task_row.remaining_minutes += 20
        elif mutation == "course":
            database.add(
                StudentCourse(
                    id=uuid4(),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    code="NEW 100",
                    name="New class",
                    term_start=MONDAY.date(),
                    term_end=(MONDAY + timedelta(days=90)).date(),
                    archived=False,
                    revision=1,
                )
            )
        elif mutation == "break":
            database.add(
                StudentTimetableBreak(
                    id=uuid4(),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    name="Changed break",
                    start_date=MONDAY.date(),
                    end_date=MONDAY.date(),
                    revision=1,
                )
            )
        else:
            user = await database.get(User, env.user_id)
            user.timezone = "Europe/London"
        await database.commit()
    result = await env.client.post(
        ROOT + "/accept",
        json={
            "preview_id": proposed["preview_id"],
            "preview_hash": proposed["preview_hash"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_accept_rejects_expired_or_already_started_proposed_block(planning_env):
    env = planning_env
    await create_task(env)
    proposed = await preview(env)
    env.clock.now += timedelta(minutes=21)
    result = await env.client.post(
        ROOT + "/accept",
        json={
            "preview_id": proposed["preview_id"],
            "preview_hash": proposed["preview_hash"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_progress_replans_future_only_preserves_history_and_retry_does_not_double_count(
    planning_env,
):
    env = planning_env
    current_task = await create_task(env)
    plan, _ = await accept(env)
    block = next(row for row in plan["blocks"] if row["status"] == "planned")
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=30)
    payload = {
        "request_id": str(uuid4()),
        "revision": block["revision"],
        "task_revision": current_task["revision"],
        "outcome": "took_longer",
        "actual_minutes": 45,
        "remaining_minutes": 150,
    }
    result = await env.client.post(ROOT + f"/blocks/{block['id']}/outcome", json=payload)
    assert result.status_code == 200, result.text
    response = result.json()
    assert response["task"]["actual_minutes"] == 45
    assert response["task"]["remaining_minutes"] == 150
    assert response["block"]["status"] == "took_longer"
    future = [row for row in response["plan"]["blocks"] if row["status"] == "planned"]
    assert future and all(datetime.fromisoformat(row["start_at"]) > env.clock.now for row in future)
    assert any(
        row["id"] == block["id"] and row["status"] == "took_longer"
        for row in response["plan"]["blocks"]
    )
    repeated = await env.client.post(ROOT + f"/blocks/{block['id']}/outcome", json=payload)
    assert repeated.json() == response
    task_rows = (await env.client.get(ROOT + "/tasks")).json()["tasks"]
    assert task_rows[0]["actual_minutes"] == 45
    assert task_rows[0]["revision"] == 2


@pytest.mark.asyncio
async def test_skip_undo_and_forgetting_remove_derived_private_text(
    planning_env,
):
    env = planning_env
    current_task = await create_task(env)
    plan, _ = await accept(env)
    block = next(row for row in plan["blocks"] if row["status"] == "planned")
    request_id = str(uuid4())
    response = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": request_id,
            "revision": block["revision"],
            "task_revision": current_task["revision"],
            "outcome": "skipped",
            "actual_minutes": 0,
        },
    )
    assert response.status_code == 200, response.text
    changed = response.json()
    skipped_start, skipped_end = (
        datetime.fromisoformat(block["start_at"]),
        datetime.fromisoformat(block["end_at"]),
    )
    for replacement in changed["result"]["blocks"]:
        assert not (
            datetime.fromisoformat(replacement["start_at"]) < skipped_end
            and datetime.fromisoformat(replacement["end_at"]) > skipped_start
        )
    undone = await env.client.post(
        ROOT + "/undo",
        json={
            "request_id": str(uuid4()),
            "outcome_request_id": request_id,
            "revision": changed["plan"]["revision"],
        },
    )
    assert undone.status_code == 200, undone.text
    rows = (await env.client.get(ROOT + "/tasks")).json()["tasks"]
    assert rows[0]["remaining_minutes"] == 180 and rows[0]["actual_minutes"] == 0
    assert rows[0]["revision"] == 3
    forgotten = await env.client.delete(
        ROOT + f"/tasks/{current_task['id']}?revision=3&request_id={uuid4()}"
    )
    assert forgotten.status_code == 204, forgotten.text
    async with env.factory() as database:
        for model in (StudentTask, StudentStudyBlock, StudentPlanningPreview):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
        commands = list(await database.scalars(select(StudentPlanningCommand)))
        assert commands
        assert all("Private math task" not in str(command.result) for command in commands)
        assert all(command.undo_snapshot is None for command in commands)


@pytest.mark.asyncio
async def test_cross_owner_task_preview_and_outcome_are_not_accessible(planning_env):
    env = planning_env
    original_task = await create_task(env)
    proposed = await preview(env)
    plan, _ = await accept(env, proposed)
    registered = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-planning@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert registered.status_code == 201
    assert (await env.client.get(ROOT + "/tasks")).json()["tasks"] == []
    result = await env.client.post(
        ROOT + "/accept",
        json={
            "preview_id": proposed["preview_id"],
            "preview_hash": proposed["preview_hash"],
            "request_id": str(uuid4()),
            "confirmed": True,
        },
    )
    assert result.status_code == 404
    result = await env.client.post(
        ROOT + f"/blocks/{plan['blocks'][0]['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": 1,
            "task_revision": 1,
            "outcome": "skipped",
            "actual_minutes": 0,
        },
    )
    assert result.status_code == 404
    result = await env.client.delete(
        ROOT + f"/tasks/{original_task['id']}?revision=1&request_id={uuid4()}"
    )
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_authoritative_classes_manual_walk_shifts_and_never_windows_constrain_preview(
    planning_env,
):
    env = planning_env
    course = await env.client.post(
        "/api/v1/student/courses",
        json={
            "code": "CS 2110",
            "name": "Programming",
            "term_start": "2026-08-20",
            "term_end": "2026-12-10",
        },
    )
    assert course.status_code == 201, course.text
    meeting = await env.client.post(
        f"/api/v1/student/courses/{course.json()['id']}/meetings",
        json={
            "weekdays": [0],
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "America/New_York",
            "kind": "lecture",
            "walk_minutes": 15,
        },
    )
    assert meeting.status_code == 201, meeting.text
    shift = await env.client.post(
        "/api/v1/student/fixed-blocks",
        json={
            "title": "Campus shift",
            "weekdays": [0],
            "start_time": "10:00",
            "end_time": "11:00",
            "timezone": "America/New_York",
            "term_start": "2026-08-20",
            "term_end": "2026-12-10",
        },
    )
    assert shift.status_code == 201, shift.text
    preferences = await env.client.put(
        ROOT + "/preferences",
        json={
            "request_id": str(uuid4()),
            "revision": 0,
            "wake_time": "08:00",
            "sleep_time": "14:00",
            "never_schedule": [{"weekdays": [0], "start_time": "12:00", "end_time": "13:00"}],
        },
    )
    assert preferences.status_code == 200, preferences.text
    await create_task(env, due_at="2026-10-19T14:00:00-04:00", estimated_minutes=240)
    result = (await preview(env, days=1))["result"]
    for block in result["blocks"]:
        start, end = (
            datetime.fromisoformat(block["start_at"]),
            datetime.fromisoformat(block["end_at"]),
        )
        assert not (start < MONDAY.replace(hour=15) and end > MONDAY.replace(hour=12, minute=45))
        assert not (start < MONDAY.replace(hour=17) and end > MONDAY.replace(hour=16))
    assert result["unfitted_work"][0]["reason"] == "cannot_meet_deadline"


@pytest.mark.asyncio
async def test_source_forget_removes_planner_copies_atomically_and_retains_manual_tasks(
    planning_env,
    monkeypatch,
):
    from navox.lms_feeds import service as feeds

    async def authorized(*_args, **_kwargs):
        return True

    monkeypatch.setattr(feeds, "item_sources_authorized", authorized)
    env = planning_env
    derived = await create_task(env, title="Feed-private assignment")
    manual_request = str(uuid4())
    independent = await create_task(env, title="Independent manual task", request_id=manual_request)
    item_id = uuid4()
    async with env.factory() as database:
        database.add(
            LmsFeedItem(
                id=item_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                identity_key="a" * 64,
                payload={},
                revision=1,
                cancelled=False,
                conflicted=False,
            )
        )
        await database.flush()
        row = await database.get(StudentTask, UUID(derived["id"]))
        row.source_item_id, row.source_identity, row.source_revision = item_id, "b" * 64, 1
        await database.commit()
    await accept(env)
    await preview(env)
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.forget_source_tasks(database, env.user_id, env.workspace_id, [item_id])
        item = await database.get(LmsFeedItem, item_id)
        await database.delete(item)
        await database.commit()
    response = (await env.client.get(ROOT + "/tasks")).json()["tasks"]
    assert [row["id"] for row in response] == [independent["id"]]
    delayed_retry = await env.client.post(
        ROOT + "/tasks",
        json={
            "title": "Independent manual task",
            "request_id": manual_request,
            "estimated_minutes": 180,
            "due_at": (MONDAY + timedelta(days=2)).isoformat(),
        },
    )
    assert delayed_retry.status_code == 409
    assert "Feed-private" not in str((await env.client.get(ROOT + "/plan")).json())
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentPlanningPreview)) == 0
        receipts = list(await database.scalars(select(StudentPlanningCommand)))
        assert receipts and all(row.result == {"invalidated": True} for row in receipts)


@pytest.mark.asyncio
async def test_postgres_concurrent_progress_uses_fresh_owner_lock(planning_env):
    env = planning_env
    if env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("This concurrency assertion requires real PostgreSQL row locks")
    current_task = await create_task(env)
    plan, _ = await accept(env)
    block = next(row for row in plan["blocks"] if row["status"] == "planned")

    async def write():
        async with env.factory() as database:
            try:
                await service.outcome(
                    database,
                    env.user_id,
                    env.workspace_id,
                    UUID(block["id"]),
                    OutcomeInput(
                        request_id=uuid4(),
                        revision=block["revision"],
                        task_revision=current_task["revision"],
                        outcome="skipped",
                        actual_minutes=0,
                    ),
                    now=env.clock.now,
                )
                return 200
            except schedule.StudentError as error:
                await database.rollback()
                return error.status_code

    assert sorted(await asyncio.gather(write(), write())) == [200, 409]
    rows = (await env.client.get(ROOT + "/tasks")).json()["tasks"]
    assert rows[0]["revision"] == 2


@pytest.mark.asyncio
async def test_undo_refuses_a_changed_schedule_without_rewriting_completed_history(planning_env):
    env = planning_env
    row = await create_task(env)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    saved = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": row["revision"],
            "outcome": "skipped",
            "actual_minutes": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "Europe/London"
        await database.commit()
    response = await env.client.post(
        ROOT + "/undo",
        json={
            "request_id": str(uuid4()),
            "outcome_request_id": saved.json()["undo_request_id"],
            "revision": saved.json()["plan"]["revision"],
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_explicit_assignment_copy_checks_revision_and_stable_source_dedup(
    planning_env, monkeypatch
):
    from navox.lms_feeds import service as feeds

    env = planning_env
    identifier = uuid4()
    async with env.factory() as database:
        database.add(
            LmsFeedItem(
                id=identifier,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                identity_key="c" * 64,
                payload={"title": "Source title"},
                revision=1,
                cancelled=False,
                conflicted=False,
            )
        )
        await database.commit()

    async def source(database, **scope):
        assert scope == {
            "user_id": env.user_id,
            "workspace_id": env.workspace_id,
            "item_id": identifier,
            "require_active": False,
        }
        return await database.get(LmsFeedItem, identifier)

    monkeypatch.setattr(feeds, "owned_item", source)
    body = {
        "request_id": str(uuid4()),
        "title": "Reviewed assignment",
        "source_item_id": str(identifier),
        "source_revision": 2,
        "confirmed": True,
    }
    stale = await env.client.post(ROOT + "/tasks/from-assignment", json=body)
    assert stale.status_code == 409
    body["source_revision"] = 1
    response = await env.client.post(ROOT + "/tasks/from-assignment", json=body)
    assert response.status_code == 201, response.text
    duplicate = await env.client.post(
        ROOT + "/tasks/from-assignment", json={**body, "request_id": str(uuid4())}
    )
    assert duplicate.status_code == 409
    async with env.factory() as database:
        item = await database.get(LmsFeedItem, identifier)
        item.revision, item.payload = 2, {"title": "Changed source title"}
        await database.commit()
    copied = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert copied["title"] == "Reviewed assignment" and copied["source_revision"] == 1


@pytest.mark.asyncio
async def test_postgres_reset_racing_progress_cannot_resurrect_private_history(planning_env):
    env = planning_env
    if env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("This privacy race assertion requires real PostgreSQL row locks")
    current_task = await create_task(env)
    plan, _ = await accept(env)
    block = next(row for row in plan["blocks"] if row["status"] == "planned")

    async def progress():
        async with env.factory() as database:
            try:
                await service.outcome(
                    database,
                    env.user_id,
                    env.workspace_id,
                    UUID(block["id"]),
                    OutcomeInput(
                        request_id=uuid4(),
                        revision=block["revision"],
                        task_revision=current_task["revision"],
                        outcome="skipped",
                        actual_minutes=0,
                    ),
                    now=env.clock.now,
                )
            except schedule.StudentError as error:
                assert error.status_code == 404
                await database.rollback()

    async def reset():
        async with env.factory() as database:
            await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
            await service.forget_planning_data(database, env.user_id, env.workspace_id)
            await database.commit()

    await asyncio.gather(progress(), reset())
    async with env.factory() as database:
        for model in (
            StudentTask,
            StudentStudyBlock,
            StudentPlanningCommand,
            StudentPlanningPreview,
            StudentPlanningState,
        ):
            assert await database.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_done_records_actual_time_completes_task_and_preserves_completed_block(planning_env):
    env = planning_env
    current_task = await create_task(env)
    plan, _ = await accept(env)
    block = next(row for row in plan["blocks"] if row["status"] == "planned")
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=30)
    result = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": current_task["revision"],
            "outcome": "done",
            "actual_minutes": 30,
            "complete_task": True,
        },
    )
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["task"]["remaining_minutes"] == 0
    assert saved["task"]["actual_minutes"] == 30
    assert saved["task"]["status"] == "completed"
    assert saved["block"]["status"] == "done"
    assert saved["result"]["blocks"] == []
    assert any(
        row["id"] == block["id"] and row["status"] == "done" for row in saved["plan"]["blocks"]
    )


def task_update(row, **changes):
    fields = {
        key: row[key]
        for key in (
            "title",
            "course_id",
            "due_at",
            "estimated_minutes",
            "remaining_minutes",
            "difficulty",
            "priority",
            "course_weight",
            "status",
            "revision",
        )
    }
    return {**fields, "request_id": str(uuid4()), **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["archived", "completed"])
async def test_inactive_task_invalidates_future_and_old_block_cannot_reactivate_it(
    planning_env, status
):
    env = planning_env
    row = await create_task(env)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    changed = await env.client.patch(
        ROOT + "/tasks/" + row["id"],
        json=task_update(
            row,
            status=status,
            remaining_minutes=0 if status == "completed" else 180,
        ),
    )
    assert changed.status_code == 200, changed.text
    reloaded = (await env.client.get(ROOT + "/plan")).json()
    assert reloaded["needs_review"] is True and reloaded["review_notice"]
    assert all(item["status"] != "planned" for item in reloaded["blocks"])
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=5)
    progress = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": changed.json()["revision"],
            "outcome": "done",
            "actual_minutes": 5,
        },
    )
    assert progress.status_code == 409
    saved = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert saved["status"] == status and saved["actual_minutes"] == 0


@pytest.mark.asyncio
async def test_deadline_and_routine_changes_require_review_until_fresh_acceptance(planning_env):
    env = planning_env
    row = await create_task(env)
    await accept(env)
    changed = await env.client.patch(
        ROOT + "/tasks/" + row["id"],
        json=task_update(
            row,
            due_at=(MONDAY + timedelta(minutes=1)).isoformat(),
        ),
    )
    assert changed.status_code == 200, changed.text
    assert (await env.client.get(ROOT + "/plan")).json()["needs_review"] is True
    proposed = await preview(env)
    assert proposed["result"]["blocks"] == [] and proposed["result"]["unfitted_work"]
    accepted, _ = await accept(env, proposed)
    assert accepted["needs_review"] is False
    preferences = await env.client.put(
        ROOT + "/preferences",
        json={
            "request_id": str(uuid4()),
            "revision": 0,
            "wake_time": "08:00",
            "sleep_time": "22:00",
        },
    )
    assert preferences.status_code == 200, preferences.text
    assert (await env.client.get(ROOT + "/plan")).json()["needs_review"] is True


@pytest.mark.asyncio
async def test_external_timetable_change_marks_future_virtual_review_without_get_writes(
    planning_env,
):
    env = planning_env
    row = await create_task(env)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    course = await env.client.post(
        "/api/v1/student/courses",
        json={
            "code": "NEW 100",
            "name": "New class",
            "term_start": "2026-10-19",
            "term_end": "2026-12-10",
        },
    )
    assert course.status_code == 201, course.text
    meeting = await env.client.post(
        f"/api/v1/student/courses/{course.json()['id']}/meetings",
        json={
            "weekdays": [0],
            "start_time": "08:00",
            "end_time": "11:00",
            "timezone": "America/New_York",
        },
    )
    assert meeting.status_code == 201, meeting.text
    for _ in range(2):
        reloaded = (await env.client.get(ROOT + "/plan")).json()
        assert reloaded["needs_review"] is True
        assert reloaded["revision"] == plan["revision"]
        assert all(item["status"] == "needs_review" for item in reloaded["blocks"])
    async with env.factory() as database:
        stored = await database.get(StudentStudyBlock, UUID(block["id"]))
        assert stored.status == "planned"  # GET only labels an obsolete reference.
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=5)
    rejected = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": row["revision"],
            "outcome": "done",
            "actual_minutes": 5,
        },
    )
    assert rejected.status_code == 409
    assert (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]["actual_minutes"] == 0


@pytest.mark.asyncio
async def test_clock_passage_does_not_make_an_unchanged_accepted_plan_stale(planning_env):
    env = planning_env
    await create_task(env)
    plan, _ = await accept(env)
    assert plan["needs_review"] is False
    env.clock.now += timedelta(minutes=10)
    current = (await env.client.get(ROOT + "/plan")).json()
    assert current["needs_review"] is False
    assert current["revision"] == plan["revision"]


@pytest.mark.asyncio
async def test_archive_after_done_prevents_undo_reactivation_and_preserves_actual_history(
    planning_env,
):
    env = planning_env
    row = await create_task(env)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=30)
    completed = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": row["revision"],
            "outcome": "done",
            "actual_minutes": 30,
            "complete_task": True,
        },
    )
    assert completed.status_code == 200, completed.text
    done = completed.json()
    archived = await env.client.patch(
        ROOT + "/tasks/" + row["id"], json=task_update(done["task"], status="archived")
    )
    assert archived.status_code == 200, archived.text
    denied = await env.client.post(
        ROOT + "/undo",
        json={
            "request_id": str(uuid4()),
            "outcome_request_id": done["undo_request_id"],
            "revision": done["plan"]["revision"],
        },
    )
    assert denied.status_code == 409
    saved = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert saved["status"] == "archived" and saved["actual_minutes"] == 30
    history = (await env.client.get(ROOT + "/plan")).json()["blocks"]
    assert any(
        item["id"] == block["id"] and item["status"] == "done" and item["actual_minutes"] == 30
        for item in history
    )


@pytest.mark.asyncio
async def test_dense_but_known_class_overlaps_do_not_become_false_clock_change_errors(planning_env):
    env = planning_env
    course = await env.client.post(
        "/api/v1/student/courses",
        json={
            "name": "Dense schedule",
            "code": "TEST",
            "term_start": "2026-10-19",
            "term_end": "2026-12-10",
        },
    )
    assert course.status_code == 201, course.text
    async with env.factory() as database:
        for _ in range(21):
            database.add(
                StudentClassMeeting(
                    id=uuid4(),
                    course_id=UUID(course.json()["id"]),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    kind="lecture",
                    weekdays=[0],
                    start_time="09:00",
                    end_time="10:00",
                    timezone="America/New_York",
                    revision=1,
                )
            )
        await database.commit()
    await create_task(env)
    result = (await preview(env))["result"]
    assert result["blocks"]
    for block in result["blocks"]:
        start, end = (
            datetime.fromisoformat(block["start_at"]),
            datetime.fromisoformat(block["end_at"]),
        )
        assert not (start < MONDAY.replace(hour=14) and end > MONDAY.replace(hour=13))


async def linked_source_task(env, monkeypatch):
    from navox.lms_feeds import service as feeds

    row = await create_task(env)
    identifier = uuid4()
    access = SimpleNamespace(allowed=True)
    async with env.factory() as database:
        database.add(
            LmsFeedItem(
                id=identifier,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                identity_key="d" * 64,
                payload={"title": "School assignment", "due_at": row["due_at"]},
                revision=1,
                cancelled=False,
                conflicted=False,
            )
        )
        await database.flush()
        saved = await database.get(StudentTask, UUID(row["id"]))
        saved.source_item_id, saved.source_identity, saved.source_revision = identifier, "e" * 64, 1
        await database.commit()

    async def authorized(_database, item, **_kwargs):
        assert item.id == identifier
        return access.allowed

    async def owned(database, **scope):
        assert scope == {
            "user_id": env.user_id,
            "workspace_id": env.workspace_id,
            "item_id": identifier,
            "require_active": False,
        }
        if not access.allowed:
            raise feeds.FeedAccessError(403, "Source unavailable")
        return await database.get(LmsFeedItem, identifier)

    monkeypatch.setattr(feeds, "item_sources_authorized", authorized)
    monkeypatch.setattr(feeds, "owned_item", owned)
    return (await env.client.get(ROOT + "/tasks")).json()["tasks"][0], identifier, access


@pytest.mark.asyncio
@pytest.mark.parametrize("imported", [False, True])
async def test_archived_course_work_and_its_stale_sources_stay_out_of_active_planning(
    planning_env, monkeypatch, imported
):
    env = planning_env
    course_data = {
        "name": "Former course",
        "code": "OLD 100",
        "term_start": "2026-10-19",
        "term_end": "2026-12-10",
    }
    course_response = await env.client.post("/api/v1/student/courses", json=course_data)
    assert course_response.status_code == 201, course_response.text
    course = course_response.json()
    if imported:
        row, _, access = await linked_source_task(env, monkeypatch)
        async with env.factory() as database:
            saved = await database.get(StudentTask, UUID(row["id"]))
            saved.course_id = UUID(course["id"])
            await database.commit()
    else:
        row = await create_task(env, course_id=course["id"])
    unrelated = await create_task(env, title="Current independent work")
    accepted, _ = await accept(env)
    block = next(item for item in accepted["blocks"] if item["task_id"] == row["id"])
    archived = await env.client.patch(
        "/api/v1/student/courses/" + course["id"],
        json={**course_data, "revision": course["revision"], "archived": True},
    )
    assert archived.status_code == 200, archived.text
    if imported:
        access.allowed = False
    assert (await env.client.get(ROOT + "/plan")).json()["needs_review"] is True
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=1)
    denied = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": row["revision"],
            "outcome": "done",
            "actual_minutes": 1,
        },
    )
    assert denied.status_code == 409
    proposed = await preview(env)
    assert {item["task_id"] for item in proposed["result"]["blocks"]} == {unrelated["id"]}
    assert not any(item["task_id"] == row["id"] for item in proposed["result"]["unfitted_work"])
    fresh, _ = await accept(env, proposed)
    assert fresh["needs_review"] is False
    tasks = (await env.client.get(ROOT + "/tasks")).json()["tasks"]
    preserved = next(item for item in tasks if item["id"] == row["id"])
    assert preserved["status"] == "active" and preserved["actual_minutes"] == 0
    assert preserved["course_id"] == course["id"]
    async with env.factory() as database:
        assert await database.get(StudentStudyBlock, UUID(block["id"])) is not None


@pytest.mark.asyncio
async def test_changed_school_deadline_requires_explicit_source_review_without_overwriting_edits(
    planning_env, monkeypatch
):
    env = planning_env
    row, source_id, _ = await linked_source_task(env, monkeypatch)
    await accept(env)
    async with env.factory() as database:
        source = await database.get(LmsFeedItem, source_id)
        source.revision = 2
        source.payload = {
            "title": "Updated school title",
            "due_at": (MONDAY + timedelta(hours=1)).isoformat(),
        }
        await database.commit()
    stale = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert stale["source_revision"] == 1 and stale["source_current_revision"] == 2
    assert stale["source_needs_review"] is True and stale["source_reviewable"] is True
    assert stale["source_current_title"] == "Updated school title"
    assert stale["due_at"] == row["due_at"]  # School updates never replace an owner's task edits.
    assert (await env.client.get(ROOT + "/plan")).json()["needs_review"] is True
    denied = await env.client.post(ROOT + "/preview", json={"days": 7})
    assert denied.status_code == 409
    reviewed = await env.client.patch(
        ROOT + "/tasks/" + row["id"],
        json=task_update(
            stale,
            title="My deliberately kept title",
            reviewed_source_revision=2,
            confirmed_source=True,
        ),
    )
    assert reviewed.status_code == 200, reviewed.text
    latest = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert latest["source_revision"] == 2 and latest["source_needs_review"] is False
    assert latest["title"] == "My deliberately kept title" and latest["due_at"] == row["due_at"]
    fresh, _ = await accept(env)
    assert fresh["needs_review"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["revoked", "cancelled", "conflicted"])
async def test_unavailable_school_source_cannot_be_acknowledged_active_but_can_be_archived(
    planning_env, monkeypatch, unavailable
):
    env = planning_env
    row, source_id, access = await linked_source_task(env, monkeypatch)
    await accept(env)
    if unavailable == "revoked":
        access.allowed = False
    else:
        async with env.factory() as database:
            item = await database.get(LmsFeedItem, source_id)
            setattr(item, unavailable, True)
            item.revision += 1
            await database.commit()
    current = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert current["source_needs_review"] is True and current["source_reviewable"] is False
    if unavailable == "revoked":
        assert current["source_current_title"] is None and current["source_current_due_at"] is None
    assert (await env.client.get(ROOT + "/plan")).json()["needs_review"] is True
    rejected = await env.client.patch(
        ROOT + "/tasks/" + row["id"],
        json=task_update(
            current,
            reviewed_source_revision=2,
            confirmed_source=True,
        ),
    )
    assert rejected.status_code in {403, 409}
    archived = await env.client.patch(
        ROOT + "/tasks/" + row["id"], json=task_update(current, status="archived")
    )
    assert archived.status_code == 200, archived.text
    fresh, _ = await accept(env)
    assert fresh["needs_review"] is False


@pytest.mark.asyncio
async def test_undo_cannot_reactivate_completed_work_after_its_school_source_changes(
    planning_env, monkeypatch
):
    env = planning_env
    row, source_id, _ = await linked_source_task(env, monkeypatch)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    env.clock.now = datetime.fromisoformat(block["start_at"]) + timedelta(minutes=30)
    done = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": row["revision"],
            "outcome": "done",
            "actual_minutes": 30,
            "complete_task": True,
        },
    )
    assert done.status_code == 200, done.text
    async with env.factory() as database:
        source = await database.get(LmsFeedItem, source_id)
        source.revision += 1
        await database.commit()
    denied = await env.client.post(
        ROOT + "/undo",
        json={
            "request_id": str(uuid4()),
            "outcome_request_id": done.json()["undo_request_id"],
            "revision": done.json()["plan"]["revision"],
        },
    )
    assert denied.status_code == 409
    saved = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert saved["status"] == "completed" and saved["actual_minutes"] == 30


@pytest.mark.asyncio
async def test_replayed_accept_returns_current_staleness_instead_of_an_old_valid_plan(planning_env):
    env = planning_env
    await create_task(env)
    initial, payload = await accept(env)
    async with env.factory() as database:
        database.add(
            StudentCourse(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                name="New course",
                code="NEW",
                term_start=MONDAY.date(),
                term_end=(MONDAY + timedelta(days=20)).date(),
                archived=False,
                revision=1,
            )
        )
        await database.commit()
    replayed = await env.client.post(ROOT + "/accept", json=payload)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["needs_review"] is True
    assert all(item["status"] == "needs_review" for item in replayed.json()["blocks"])
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == len(
            initial["blocks"]
        )


@pytest.mark.asyncio
async def test_replayed_progress_after_archive_reports_already_recorded_and_does_not_reactivate(
    planning_env,
):
    env = planning_env
    create_id = str(uuid4())
    row = await create_task(env, request_id=create_id)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    payload = {
        "request_id": str(uuid4()),
        "revision": block["revision"],
        "task_revision": row["revision"],
        "outcome": "skipped",
        "actual_minutes": 0,
    }
    changed = await env.client.post(ROOT + f"/blocks/{block['id']}/outcome", json=payload)
    assert changed.status_code == 200, changed.text
    archived = await env.client.patch(
        ROOT + "/tasks/" + row["id"], json=task_update(changed.json()["task"], status="archived")
    )
    assert archived.status_code == 200, archived.text
    replay = await env.client.post(ROOT + f"/blocks/{block['id']}/outcome", json=payload)
    assert replay.status_code == 409 and "already recorded" in replay.json()["detail"]
    created_again = await env.client.post(
        ROOT + "/tasks",
        json={
            "title": "Private math task",
            "request_id": create_id,
            "estimated_minutes": 180,
            "due_at": (MONDAY + timedelta(days=2)).isoformat(),
        },
    )
    assert created_again.status_code == 201
    assert created_again.json()["status"] == "archived"
    assert created_again.json()["actual_minutes"] == 0


@pytest.mark.asyncio
async def test_replayed_undo_refreshes_source_validity_without_restoring_anything_twice(
    planning_env,
):
    env = planning_env
    row = await create_task(env)
    plan, _ = await accept(env)
    block = next(item for item in plan["blocks"] if item["status"] == "planned")
    changed = await env.client.post(
        ROOT + f"/blocks/{block['id']}/outcome",
        json={
            "request_id": str(uuid4()),
            "revision": block["revision"],
            "task_revision": row["revision"],
            "outcome": "skipped",
            "actual_minutes": 0,
        },
    )
    assert changed.status_code == 200, changed.text
    payload = {
        "request_id": str(uuid4()),
        "outcome_request_id": changed.json()["undo_request_id"],
        "revision": changed.json()["plan"]["revision"],
    }
    first = await env.client.post(ROOT + "/undo", json=payload)
    assert first.status_code == 200, first.text
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "Europe/London"
        await database.commit()
    second = await env.client.post(ROOT + "/undo", json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["needs_review"] is True
    assert second.json()["revision"] == first.json()["revision"]
