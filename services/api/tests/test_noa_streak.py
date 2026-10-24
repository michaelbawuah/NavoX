from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from test_student_planning import create_task, routine_headers
from test_student_planning import planning_env as planning_env

from navox.api import noa
from navox.db.student_planning import StudentStudyBlock


async def seed_completion(env, task_id, minutes, *, days_ago=0, status="done"):
    completed_at = env.clock.now - timedelta(days=days_ago)
    async with env.factory() as database:
        database.add(
            StudentStudyBlock(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                task_id=task_id,
                plan_id=uuid4(),
                title="Synthetic recorded study",
                start_at=completed_at - timedelta(minutes=30),
                end_at=completed_at,
                minutes=30,
                revision=1,
                status=status,
                actual_minutes=minutes,
                outcome_at=completed_at,
            )
        )
        await database.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minutes,status,credited",
    [
        ([0], "done", False),
        ([14], "done", False),
        ([7, 8], "done", False),
        ([15], "done", True),
        ([15, 30], "done", True),
        ([30], "took_longer", False),
    ],
)
async def test_noa_credits_only_done_blocks_with_at_least_15_recorded_minutes(
    planning_env, monkeypatch, minutes, status, credited
):
    env = planning_env

    async def clock(_database):
        return env.clock.now

    monkeypatch.setattr(noa, "database_now", clock)
    task = await create_task(env)
    for recorded in minutes:
        await seed_completion(env, UUID(task["id"]), recorded, status=status)
    response = await env.client.get("/api/v1/student/noa", headers=routine_headers(env))
    assert response.status_code == 200, response.text
    assert response.json()["credited_today"] is credited
    assert response.json()["study_streak"] == int(credited)


@pytest.mark.asyncio
async def test_short_completion_today_does_not_break_yesterdays_qualified_streak(
    planning_env, monkeypatch
):
    env = planning_env

    async def clock(_database):
        return env.clock.now

    monkeypatch.setattr(noa, "database_now", clock)
    task = await create_task(env)
    for days_ago in (1, 2):
        await seed_completion(env, UUID(task["id"]), 15, days_ago=days_ago)
    await seed_completion(env, UUID(task["id"]), 14)
    response = await env.client.get("/api/v1/student/noa", headers=routine_headers(env))
    assert response.status_code == 200, response.text
    assert response.json()["study_streak"] == 2
    assert response.json()["credited_today"] is False
