from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_student_planning import (
    MONDAY,
    ROOT,
    accept,
    create_task,
    linked_source_task,
    routine_candidate,
    routine_headers,
)
from test_student_planning import planning_env as planning_env

from navox.db.lms_feeds import LmsFeedItem
from navox.db.models import UserSession
from navox.db.student_planning import StudentStudyBlock
from navox.student_planning import calendar_busy


async def setup(env, monkeypatch, **routine):
    provider = SimpleNamespace(connected=True, version="first", intervals=())

    async def collect(*_args, **_kwargs):
        return calendar_busy.CalendarBusy(
            provider.intervals, provider.version, provider.connected, ()
        )

    monkeypatch.setattr(calendar_busy, "collect", collect)
    candidate = await routine_candidate(env, **routine)
    response = await env.client.put(ROOT + "/preferences", json=candidate)
    assert response.status_code == 200, response.text
    return provider


def payload(task, **changes):
    return {
        "request_id": str(uuid4()),
        "task_id": task["id"],
        "task_revision": task["revision"],
        "timezone": "America/New_York",
        "start_at": (MONDAY + timedelta(hours=2)).isoformat(),
        "window_end_at": (MONDAY + timedelta(hours=4)).isoformat(),
        "minutes": 30,
        **changes,
    }


async def review(env, body):
    response = await env.client.post(
        ROOT + "/placements/preview", json=body, headers=routine_headers(env)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def confirm(env, body, checked, *, recovery_acknowledged=False):
    return await env.client.post(
        ROOT + "/placements/confirm",
        headers=routine_headers(env),
        json={
            **body,
            "preview_hash": checked["preview_hash"],
            "confirmed": True,
            "recovery_acknowledged": recovery_acknowledged,
        },
    )


async def reviewed_new_task(env, **changes):
    candidate = {
        "request_id": str(uuid4()),
        "title": "Work for the selected window",
        "estimated_minutes": 60,
        "due_at": (MONDAY + timedelta(days=1)).isoformat(),
        **changes,
    }
    response = await env.client.post(
        ROOT + "/tasks/review", headers=routine_headers(env), json=candidate
    )
    assert response.status_code == 200, response.text
    checked = response.json()
    response = await env.client.post(
        ROOT + "/tasks/confirm",
        headers=routine_headers(env),
        json={**candidate, "preview_hash": checked["preview_hash"]},
    )
    assert response.status_code == 200, response.text
    return response.json(), checked


@pytest.mark.asyncio
async def test_unplanned_work_creates_one_real_block_only_after_exact_owner_approval(
    planning_env, monkeypatch
):
    env = planning_env
    await setup(env, monkeypatch)
    task = await create_task(env, estimated_minutes=60)
    body = payload(task)
    assert (await env.client.post(ROOT + "/placements/preview", json=body)).status_code == 409
    checked = await review(env, body)
    async with env.factory() as database:
        assert await database.scalar(select(StudentStudyBlock.id)) is None
    rejected = await env.client.post(
        ROOT + "/placements/confirm",
        headers=routine_headers(env),
        json={**body, "preview_hash": checked["preview_hash"], "confirmed": False},
    )
    assert rejected.status_code == 422
    for _ in range(2):
        saved = await confirm(env, body, checked)
        assert saved.status_code == 200, saved.text
        assert saved.json()["after"]["id"] == checked["after"]["id"]
    plan = (await env.client.get(ROOT + "/plan")).json()
    assert len(plan["blocks"]) == 1 and not plan["needs_review"]
    assert plan["blocks"][0]["minutes"] == 30
    current = (await env.client.get(ROOT + "/tasks")).json()["tasks"][0]
    assert current["remaining_minutes"] == 60 and current["actual_minutes"] == 0
    assert (await confirm(env, {**body, "minutes": 20}, checked)).status_code == 409


@pytest.mark.asyncio
async def test_placement_preserves_other_blocks_and_cannot_double_allocate_effort(
    planning_env, monkeypatch
):
    env = planning_env
    await setup(env, monkeypatch)
    first = await create_task(env, estimated_minutes=60)
    second = await create_task(env, title="Independent essay", estimated_minutes=90)
    one = payload(first)
    result = await confirm(env, one, await review(env, one))
    assert result.status_code == 200, result.text
    original = result.json()["after"]
    two = payload(
        second,
        start_at=(MONDAY + timedelta(hours=5)).isoformat(),
        window_end_at=(MONDAY + timedelta(hours=7)).isoformat(),
    )
    result = await confirm(env, two, await review(env, two))
    assert result.status_code == 200, result.text
    async with env.factory() as database:
        rows = list(await database.scalars(select(StudentStudyBlock)))
        assert len(rows) == 2
        preserved = await database.get(StudentStudyBlock, UUID(original["id"]))
        assert preserved.revision == 1 and preserved.minutes == 30
    excessive = payload(
        first,
        minutes=45,
        start_at=(MONDAY + timedelta(hours=8)).isoformat(),
        window_end_at=(MONDAY + timedelta(hours=10)).isoformat(),
    )
    assert (
        await env.client.post(
            ROOT + "/placements/preview", json=excessive, headers=routine_headers(env)
        )
    ).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["calendar", "revoked", "task", "timezone"])
async def test_fresh_confirmation_rejects_source_or_permission_changes(
    planning_env, monkeypatch, change
):
    env = planning_env
    provider = await setup(env, monkeypatch)
    task = await create_task(env)
    body = payload(task)
    checked = await review(env, body)
    if change == "calendar":
        provider.version = "new-provider-snapshot"
    elif change == "revoked":
        provider.connected = False
    elif change == "timezone":
        body = {**body, "timezone": "America/Los_Angeles"}
    else:
        from navox.db.student_planning import StudentTask

        async with env.factory() as database:
            row = await database.get(StudentTask, UUID(task["id"]))
            row.revision += 1
            await database.commit()
    response = await confirm(env, body, checked)
    assert response.status_code == 409, response.text
    async with env.factory() as database:
        assert await database.scalar(select(StudentStudyBlock.id)) is None


@pytest.mark.asyncio
async def test_placement_preserves_buffer_before_deadline_and_the_saved_block_cap(
    planning_env, monkeypatch
):
    env = planning_env
    await setup(env, monkeypatch, wake_time="10:00", sleep_time="11:00", max_study_minutes=60)
    task = await create_task(env, due_at=(MONDAY + timedelta(hours=3)).isoformat())
    body = payload(task, minutes=60, window_end_at=(MONDAY + timedelta(hours=3)).isoformat())
    response = await env.client.post(
        ROOT + "/placements/preview", json=body, headers=routine_headers(env)
    )
    assert response.status_code == 409 and "buffer" in response.text
    allowed = {**body, "minutes": 51}
    assert (await confirm(env, allowed, await review(env, allowed))).status_code == 200
    too_long = payload(task, minutes=91)
    assert (
        await env.client.post(
            ROOT + "/placements/preview", json=too_long, headers=routine_headers(env)
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_busy_calendar_and_existing_study_break_block_placement(planning_env, monkeypatch):
    env = planning_env
    provider = await setup(env, monkeypatch)
    task = await create_task(env)
    body = payload(task)
    provider.intervals = ((MONDAY + timedelta(hours=2), MONDAY + timedelta(hours=3)),)
    assert (
        await env.client.post(ROOT + "/placements/preview", json=body, headers=routine_headers(env))
    ).status_code == 409
    provider.intervals = ()
    assert (await confirm(env, body, await review(env, body))).status_code == 200
    adjacent = payload(task, start_at=(MONDAY + timedelta(hours=2, minutes=35)).isoformat())
    response = await env.client.post(
        ROOT + "/placements/preview", json=adjacent, headers=routine_headers(env)
    )
    assert response.status_code == 409 and "break" in response.text


@pytest.mark.asyncio
async def test_real_class_and_sleep_windows_are_not_available_for_placement(
    planning_env, monkeypatch
):
    env = planning_env
    await setup(env, monkeypatch)
    course = await env.client.post(
        "/api/v1/student/courses",
        json={
            "code": "CS 100",
            "name": "Programming",
            "term_start": "2026-08-20",
            "term_end": "2026-12-20",
        },
    )
    assert course.status_code == 201, course.text
    meeting = await env.client.post(
        f"/api/v1/student/courses/{course.json()['id']}/meetings",
        json={
            "kind": "lecture",
            "weekdays": [0],
            "start_time": "10:00",
            "end_time": "11:00",
            "walk_minutes": 10,
            "timezone": "America/New_York",
        },
    )
    assert meeting.status_code == 201, meeting.text
    task = await create_task(env)
    for body in (
        payload(task),
        payload(
            task,
            start_at=(MONDAY + timedelta(hours=19)).isoformat(),
            window_end_at=(MONDAY + timedelta(hours=21)).isoformat(),
        ),
    ):
        response = await env.client.post(
            ROOT + "/placements/preview", json=body, headers=routine_headers(env)
        )
        assert response.status_code == 409, response.text
    async with env.factory() as database:
        assert await database.scalar(select(StudentStudyBlock.id)) is None


@pytest.mark.asyncio
async def test_suggested_move_requires_calendar_and_selected_window(planning_env, monkeypatch):
    env = planning_env
    provider = await setup(env, monkeypatch)
    task = await create_task(env)
    body = payload(task)
    placed = await confirm(env, body, await review(env, body))
    block = placed.json()["after"]
    move = {
        "request_id": str(uuid4()),
        "revision": 1,
        "start_at": (MONDAY + timedelta(hours=6)).isoformat(),
        "window_end_at": (MONDAY + timedelta(hours=6, minutes=15)).isoformat(),
        "require_calendar": True,
    }
    path = ROOT + f"/blocks/{block['id']}/move/preview"
    assert (await env.client.post(path, json=move, headers=routine_headers(env))).status_code == 422
    move["window_end_at"] = (MONDAY + timedelta(hours=8)).isoformat()
    provider.connected = False
    assert (await env.client.post(path, json=move, headers=routine_headers(env))).status_code == 409
    async with env.factory() as database:
        saved = await database.get(StudentStudyBlock, UUID(block["id"]))
        assert saved.revision == 1


@pytest.mark.asyncio
async def test_new_task_keeps_selected_window_with_explicit_partial_plan_recovery(
    planning_env, monkeypatch
):
    env = planning_env
    await setup(env, monkeypatch)
    original = await create_task(env, title="Original planned essay", estimated_minutes=60)
    accepted, _ = await accept(env)
    retired_ids = {row["id"] for row in accepted["blocks"]}
    assert retired_ids
    new_task, task_review = await reviewed_new_task(env)
    assert {row["id"] for row in task_review["replaced_blocks"]} == retired_ids
    stale = (await env.client.get(ROOT + "/plan")).json()
    assert stale["needs_review"] is True
    body = payload(new_task)
    checked = await review(env, body)
    assert checked["recovery_required"] is True
    assert checked["preserved_blocks"] == []
    assert {row["task_id"]: row["minutes"] for row in checked["unplanned_tasks"]} == {
        original["id"]: 60,
        new_task["id"]: 30,
    }
    assert (await confirm(env, body, checked)).status_code == 422
    async with env.factory() as database:
        rows = list(await database.scalars(select(StudentStudyBlock)))
        assert {str(row.id) for row in rows} == retired_ids
        assert all(row.status == "superseded" for row in rows)
    for _ in range(2):
        result = await confirm(env, body, checked, recovery_acknowledged=True)
        assert result.status_code == 200, result.text
        assert result.json()["unplanned_tasks"] == checked["unplanned_tasks"]
    plan = (await env.client.get(ROOT + "/plan")).json()
    assert plan["needs_review"] is False
    active = [row for row in plan["blocks"] if row["status"] == "planned"]
    assert len(active) == 1 and active[0]["task_id"] == new_task["id"]
    assert active[0]["start_at"] == checked["after"]["start_at"]
    async with env.factory() as database:
        for identifier in retired_ids:
            assert (await database.get(StudentStudyBlock, UUID(identifier))).status == "superseded"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["another_task", "full_replan"])
async def test_recovery_approval_cannot_outlive_a_changed_task_list_or_plan(
    planning_env, monkeypatch, change
):
    env = planning_env
    await setup(env, monkeypatch)
    await create_task(env, estimated_minutes=60)
    await accept(env)
    task, _ = await reviewed_new_task(env)
    body = payload(task)
    checked = await review(env, body)
    if change == "another_task":
        await reviewed_new_task(env, title="Another newly added task")
    else:
        await accept(env)
    result = await confirm(env, body, checked, recovery_acknowledged=True)
    assert result.status_code == 409, result.text
    async with env.factory() as database:
        assert await database.get(StudentStudyBlock, UUID(checked["after"]["id"])) is None


@pytest.mark.asyncio
async def test_recovery_does_not_reapprove_a_stale_ongoing_block(planning_env, monkeypatch):
    env = planning_env
    await setup(env, monkeypatch)
    await create_task(env, estimated_minutes=60)
    accepted, _ = await accept(env)
    ongoing = accepted["blocks"][0]
    env.clock.now = datetime.fromisoformat(ongoing["start_at"]) + timedelta(minutes=1)
    task, _ = await reviewed_new_task(env)
    result = await env.client.post(
        ROOT + "/placements/preview", headers=routine_headers(env), json=payload(task)
    )
    assert result.status_code == 409, result.text
    assert (await env.client.get(ROOT + "/plan")).json()["needs_review"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["changed", "cancelled", "revoked"])
@pytest.mark.parametrize("selected_source", [True, False])
async def test_placement_cannot_approve_stale_school_sources_even_without_future_blocks(
    planning_env,
    monkeypatch,
    change,
    selected_source,
):
    env = planning_env
    await setup(env, monkeypatch)
    source_task, source_id, access = await linked_source_task(env, monkeypatch)
    target = source_task if selected_source else await create_task(env, title="Independent work")
    if change == "revoked":
        access.allowed = False
    else:
        async with env.factory() as database:
            source = await database.get(LmsFeedItem, source_id)
            source.revision += 1
            if change == "cancelled":
                source.cancelled = True
            else:
                source.payload = {"title": "Changed assignment", "due_at": MONDAY.isoformat()}
            await database.commit()
    result = await env.client.post(
        ROOT + "/placements/preview", headers=routine_headers(env), json=payload(target)
    )
    assert result.status_code == 409 and "source" in result.text
    async with env.factory() as database:
        assert await database.scalar(select(StudentStudyBlock.id)) is None


@pytest.mark.asyncio
async def test_move_cannot_borrow_study_buffer_from_after_the_deadline(planning_env, monkeypatch):
    env = planning_env
    await setup(env, monkeypatch)
    task = await create_task(env, due_at=(MONDAY + timedelta(hours=3)).isoformat())
    body = payload(task)
    placed = await confirm(env, body, await review(env, body))
    assert placed.status_code == 200, placed.text
    block = placed.json()["after"]
    move = {
        "request_id": str(uuid4()),
        "revision": 1,
        "start_at": (MONDAY + timedelta(hours=2, minutes=30)).isoformat(),
    }
    path = ROOT + f"/blocks/{block['id']}/move/preview"
    denied = await env.client.post(path, json=move, headers=routine_headers(env))
    assert denied.status_code == 409 and "buffer before the deadline" in denied.text
    move["start_at"] = (MONDAY + timedelta(hours=2, minutes=10)).isoformat()
    allowed = await env.client.post(path, json=move, headers=routine_headers(env))
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_move_rejects_routine_clock_change_warnings(planning_env, monkeypatch):
    env = planning_env
    await setup(env, monkeypatch, focus_windows=[{"start_time": "01:15", "end_time": "01:45"}])
    task = await create_task(env, due_at=(MONDAY + timedelta(days=14)).isoformat())
    body = payload(task)
    placed = await confirm(env, body, await review(env, body))
    assert placed.status_code == 200, placed.text
    block = placed.json()["after"]
    # Fall-back day is within the allowed 14-day move horizon; the saved
    # 01:15 focus window is ambiguous even though this proposed start is not.
    move = {"request_id": str(uuid4()), "revision": 1, "start_at": "2026-11-01T15:00:00Z"}
    result = await env.client.post(
        ROOT + f"/blocks/{block['id']}/move/preview", json=move, headers=routine_headers(env)
    )
    assert result.status_code == 409 and "clock-change" in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["place", "move"])
@pytest.mark.parametrize("session_change", ["revoked_before_lock", "expired_during_calendar"])
async def test_placement_and_move_recheck_initiating_session_before_persistence(
    planning_env,
    monkeypatch,
    operation,
    session_change,
):
    from navox.student import service as schedule
    from navox.student_planning import authority

    env = planning_env
    await setup(env, monkeypatch)
    task = await create_task(env)
    body = payload(task)
    checked = await review(env, body)
    path = ROOT + "/placements/confirm"
    submitted = {**body, "preview_hash": checked["preview_hash"], "confirmed": True}
    block_id = None
    if operation == "move":
        saved = await confirm(env, body, checked)
        assert saved.status_code == 200, saved.text
        block_id = UUID(saved.json()["after"]["id"])
        move = {
            "request_id": str(uuid4()),
            "revision": 1,
            "start_at": (MONDAY + timedelta(hours=5)).isoformat(),
        }
        path = ROOT + f"/blocks/{block_id}/move/confirm"
        proposed = await env.client.post(
            path.replace("/confirm", "/preview"), json=move, headers=routine_headers(env)
        )
        assert proposed.status_code == 200, proposed.text
        submitted = {**move, "preview_hash": proposed.json()["preview_hash"]}

    clock = SimpleNamespace(now=datetime.now(UTC))
    async with env.factory() as database:
        session = await database.scalar(
            select(UserSession).where(UserSession.user_id == env.user_id)
        )
        session.expires_at = clock.now + timedelta(hours=1)
        await database.commit()

    async def now(_database):
        return clock.now

    monkeypatch.setattr(authority, "database_now", now)

    if session_change == "revoked_before_lock":
        original = schedule.owner
        intercepted = False

        async def owner(database, user_id, workspace_id, *, lock=False):
            nonlocal intercepted
            if lock and not intercepted:
                intercepted = True
                # Authentication has completed, but this operation has not
                # acquired the User lock yet. Model a logout that won that lock.
                async with env.factory() as other:
                    session = await other.scalar(
                        select(UserSession).where(UserSession.user_id == env.user_id)
                    )
                    session.revoked_at = clock.now
                    await other.commit()
            return await original(database, user_id, workspace_id, lock=lock)

        monkeypatch.setattr(schedule, "owner", owner)
    else:
        original_collect = calendar_busy.collect

        async def collect(*args, **kwargs):
            result = await original_collect(*args, **kwargs)
            clock.now += timedelta(hours=2)
            return result

        monkeypatch.setattr(calendar_busy, "collect", collect)

    result = await env.client.post(path, json=submitted, headers=routine_headers(env))
    assert result.status_code == 401, result.text
    async with env.factory() as database:
        rows = list(await database.scalars(select(StudentStudyBlock)))
        if block_id is None:
            assert rows == []
        else:
            assert len(rows) == 1 and rows[0].id == block_id and rows[0].revision == 1
