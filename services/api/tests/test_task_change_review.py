"""Task review binds all retired blocks and retries only the original approved write."""

from uuid import UUID, uuid4

import pytest
from test_student_planning import (
    ROOT,
    accept,
    create_task,
    routine_headers,
)
from test_student_planning import (
    planning_env as shared_planning_fixture,
)

from navox.db.student_planning import StudentStudyBlock

planning_env = shared_planning_fixture


@pytest.mark.asyncio
async def test_review_new_task_is_read_only_and_confirmation_retires_exact_blocks(planning_env):
    env = planning_env
    await create_task(env)
    before, _ = await accept(env)
    body = {
        "request_id": str(uuid4()),
        "title": "New lab report",
        "estimated_minutes": 90,
        "remaining_minutes": 90,
    }
    path, headers = ROOT + "/tasks", routine_headers(env)
    preview = await env.client.post(path + "/review", json=body, headers=headers)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["before"] is None
    assert {b["id"] for b in result["replaced_blocks"]} == {
        b["id"] for b in before["blocks"] if b["status"] == "planned"
    }
    assert (await env.client.get(ROOT + "/plan")).json() == before
    confirmed = {**body, "preview_hash": result["preview_hash"]}
    saved = await env.client.post(path + "/confirm", json=confirmed, headers=headers)
    assert saved.status_code == 200, saved.text
    repeated = await env.client.post(path + "/confirm", json=confirmed, headers=headers)
    assert repeated.json() == saved.json()
    assert saved.json()["actual_minutes"] == 0
    after = (await env.client.get(ROOT + "/plan")).json()
    assert {b["id"] for b in after["blocks"] if b["status"] == "superseded"} == {
        b["id"] for b in result["replaced_blocks"]
    }


@pytest.mark.asyncio
async def test_task_confirmation_rejects_changed_blocks_changed_body_or_wrong_owner(planning_env):
    env = planning_env
    await create_task(env)
    plan, _ = await accept(env)
    body = {"request_id": str(uuid4()), "title": "New task", "estimated_minutes": 30}
    path, headers = ROOT + "/tasks", routine_headers(env)
    assert (await env.client.post(path + "/review", json=body)).status_code == 409
    preview = (await env.client.post(path + "/review", json=body, headers=headers)).json()
    confirmation = {**body, "preview_hash": preview["preview_hash"]}
    assert (
        await env.client.post(
            path + "/confirm",
            json=confirmation,
            headers={
                "X-NavoX-Account": f"{uuid4()}:{uuid4()}",
            },
        )
    ).status_code == 409
    assert (
        await env.client.post(
            path + "/confirm",
            json={
                **confirmation,
                "title": "Changed after review",
            },
            headers=headers,
        )
    ).status_code == 409
    async with env.factory() as db:
        block = await db.get(StudentStudyBlock, UUID(plan["blocks"][0]["id"]))
        block.title = "Changed block"
        block.revision += 1
        await db.commit()
    assert (
        await env.client.post(path + "/confirm", json=confirmation, headers=headers)
    ).status_code == 409


@pytest.mark.asyncio
async def test_reviewed_remaining_work_update_binds_task_revision_and_credits_no_focus(
    planning_env,
):
    env = planning_env
    original = await create_task(env)
    body = {
        "request_id": str(uuid4()),
        "revision": original["revision"],
        "title": original["title"],
        "estimated_minutes": original["estimated_minutes"],
        "remaining_minutes": 95,
    }
    path = ROOT + f"/tasks/{original['id']}"
    headers = routine_headers(env)
    preview = await env.client.post(path + "/review", json=body, headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["before"]["remaining_minutes"] == original["remaining_minutes"]
    saved = await env.client.post(
        path + "/confirm",
        json={
            **body,
            "preview_hash": preview.json()["preview_hash"],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["remaining_minutes"] == 95 and saved.json()["actual_minutes"] == 0
    stale = await env.client.post(path + "/review", json=body, headers=headers)
    assert stale.status_code == 409
