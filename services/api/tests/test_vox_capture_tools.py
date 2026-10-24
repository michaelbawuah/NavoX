"""Voice capture proposes owner-bound changes and never applies them."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_schedule_tools import MONDAY, course, study_block, tool

from navox.core.settings import Settings
from navox.db.models import User
from navox.db.student import StudentCourse
from navox.db.student_planning import (
    StudentPlanningCommand,
    StudentPlanningState,
    StudentStudyBlock,
    StudentTask,
)
from navox.student_planning import service as planning
from navox.vox.capture_tools import CaptureTools, deadline
from navox.vox.worker import WorkerAccessLost

vox_env = shared_vox_fixture


async def capture(env, monkeypatch):
    reader = await tool(env, monkeypatch)
    published = []

    async def publish(value):
        published.append(value)

    return CaptureTools(
        reader.check, Settings(_env_file=None, student_planning_enabled=True), publish
    ), published


def test_deadline_uses_saved_zone_and_rejects_skipped_and_repeated_times():
    assert deadline("2026-10-24T15:00", "America/New_York") == datetime(
        2026, 10, 24, 19, tzinfo=UTC
    )
    for local in ("2026-11-01T01:30", "2027-03-14T02:30", "2026-02-30T12:00"):
        with pytest.raises(ValueError):
            deadline(local, "America/New_York")


@pytest.mark.asyncio
async def test_new_task_capture_resolves_owned_course_without_writing(vox_env, monkeypatch):
    tools, published = await capture(vox_env, monkeypatch)
    await course(vox_env)
    result = await tools.execute(
        "prepare_student_task",
        {
            "title": "Write lab report",
            "course": "cs2110",
            "due_local": "2026-10-21T17:00",
            "estimated_minutes": 75,
        },
    )
    assert result["status"] == "review_required" and result["applied"] is False
    assert result["course"] == "CS 2110"
    assert result["replaced_count"] == 0
    assert result["full_readback_on_screen"] is False
    assert "replaced_blocks" not in result
    assert published[0]["task"]["due_at"] == "2026-10-21T21:00:00Z"
    assert published[0]["kind"] == "task"
    assert "task_id" not in published[0]
    async with vox_env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudentTask)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["prepare_student_task", "prepare_task_remaining"])
@pytest.mark.parametrize("block_count", [1, 9, 13])
async def test_task_handoff_keeps_complete_hashed_block_review_on_screen(
    vox_env, monkeypatch, name, block_count
):
    tools, published = await capture(vox_env, monkeypatch)
    vox_env.settings.student_planning_enabled = True
    task_id, plan_id = uuid4(), uuid4()
    async with vox_env.factory() as db:
        db.add(
            StudentTask(
                id=task_id,
                user_id=vox_env.user_id,
                workspace_id=vox_env.workspace_id,
                title="Revise calculus",
                due_at=MONDAY + timedelta(days=14),
                estimated_minutes=900,
                remaining_minutes=900,
            )
        )
        db.add(
            StudentPlanningState(
                user_id=vox_env.user_id,
                workspace_id=vox_env.workspace_id,
                revision=7,
                preferences={},
                accepted_source_hash="a" * 64,
                needs_review=False,
                window_start=MONDAY,
                window_end=MONDAY + timedelta(days=14),
            )
        )
        await db.flush()
        for index in range(block_count):
            db.add(
                StudentStudyBlock(
                    id=uuid4(),
                    user_id=vox_env.user_id,
                    workspace_id=vox_env.workspace_id,
                    task_id=task_id,
                    plan_id=plan_id,
                    title="Revise calculus",
                    start_at=MONDAY + timedelta(days=index, hours=1),
                    end_at=MONDAY + timedelta(days=index, hours=2),
                    minutes=60,
                )
            )
        await db.commit()

    async def saved_snapshot():
        async with vox_env.factory() as db:
            result = []
            for model in (
                StudentTask,
                StudentStudyBlock,
                StudentPlanningState,
                StudentPlanningCommand,
            ):
                rows = list(await db.scalars(select(model).order_by(model.id)))
                result.append(
                    [
                        {
                            column.name: getattr(row, column.name)
                            for column in model.__table__.columns
                        }
                        for row in rows
                    ]
                )
            return result

    before = await saved_snapshot()
    async with vox_env.factory() as db:
        expected_blocks = [
            planning.block_view(row).model_dump(mode="json")
            for row in await db.scalars(
                select(StudentStudyBlock).order_by(StudentStudyBlock.start_at, StudentStudyBlock.id)
            )
        ]
    arguments = (
        {
            "title": "Write lab report",
            "course": None,
            "due_local": "2026-10-21T17:00",
            "estimated_minutes": 75,
        }
        if name == "prepare_student_task"
        else {"title": "Revise calculus", "remaining_minutes": 95}
    )
    result = await tools.execute(name, arguments)
    assert result["status"] == "review_required" and result["applied"] is False
    assert result["replaced_count"] == block_count
    assert result["full_readback_on_screen"] is True
    assert "replaced_blocks" not in result
    assert len(json.dumps(result).encode()) < 2000
    assert all(row["id"] not in json.dumps(result) for row in expected_blocks)
    assert len(published) == 1
    proposal = published[0]
    assert result["title"] == proposal["task"]["title"]
    assert result["estimated_minutes"] == proposal["task"]["estimated_minutes"]
    assert result["remaining_minutes"] == proposal["task"]["remaining_minutes"]
    assert result["timezone"] == proposal["timezone"] == "America/New_York"
    assert datetime.fromisoformat(result["due_local"]) == datetime.fromisoformat(
        proposal["task"]["due_at"]
    )

    # Reproduce the UI's independent review fetch with its unchanged exact payload.
    # Compact speech must not remove any block from the approval or its fingerprint.
    payload = {**proposal["task"], "request_id": proposal["preview_id"]}
    path = f"/tasks/{task_id}/review" if name == "prepare_task_remaining" else "/tasks/review"
    response = await vox_env.client.post(f"/api/v1/student/planning{path}", json=payload)
    assert response.status_code == 200, response.text
    screen = response.json()
    assert screen["preview_hash"] == proposal["preview_hash"]
    assert screen["proposed"] == payload
    assert screen["replaced_blocks"] == expected_blocks
    assert len(screen["replaced_blocks"]) == block_count
    assert screen["alarms_change"] is screen["calendar_events_change"] is False
    # Neither the model proposal nor fetching its screen review saves work,
    # retires blocks, changes the accepted plan, or credits focus time.
    assert await saved_snapshot() == before
    assert all(row["actual_minutes"] == 0 for row in before[0] + before[1])
    assert all(row["status"] == "planned" and row["outcome_at"] is None for row in before[1])
    assert before[3] == []


@pytest.mark.asyncio
async def test_capture_rejects_missing_course_injected_owner_and_ambiguous_deadline(
    vox_env, monkeypatch
):
    tools, published = await capture(vox_env, monkeypatch)
    body = {
        "title": "Lab report",
        "course": None,
        "due_local": "2026-10-21T17:00",
        "estimated_minutes": 60,
    }
    for invalid in (
        {**body, "course": "NOT MY COURSE"},
        {**body, "user_id": str(uuid4())},
        {**body, "confirmed": True},
        {**body, "due_local": "2026-11-01T01:30"},
        {**body, "estimated_minutes": True},
        {**body, "title": " "},
    ):
        assert (await tools.execute("prepare_student_task", invalid))["status"] == "needs_details"
    assert published == []


@pytest.mark.asyncio
async def test_unfinished_work_uses_exact_task_revision_without_crediting_focus(
    vox_env, monkeypatch
):
    tools, published = await capture(vox_env, monkeypatch)
    block_id = await study_block(vox_env)
    result = await tools.execute(
        "prepare_task_remaining",
        {
            "title": "revise calculus",
            "remaining_minutes": 95,
        },
    )
    assert result["status"] == "review_required" and result["applied"] is False
    assert result["remaining_minutes"] == 95
    proposal = published[0]
    assert proposal["revision"] == 1 and proposal["task"]["status"] == "active"
    async with vox_env.factory() as db:
        row = await db.get(StudentTask, UUID(proposal["task_id"]))
        block = await db.get(StudentStudyBlock, block_id)
        assert row.remaining_minutes == 60 and row.actual_minutes == 0 and row.revision == 1
        assert block.status == "planned" and block.revision == 1
    for title, remaining in (("calculus", 95), ("Revise calculus", 0)):
        assert (
            await tools.execute(
                "prepare_task_remaining",
                {
                    "title": title,
                    "remaining_minutes": remaining,
                },
            )
        )["status"] == "needs_details"
    await study_block(vox_env)
    assert (
        await tools.execute(
            "prepare_task_remaining",
            {
                "title": "Revise calculus",
                "remaining_minutes": 20,
            },
        )
    )["status"] == "needs_details"
    assert len(published) == 1


@pytest.mark.asyncio
async def test_voice_class_handoff_is_a_draft_and_rejects_control_data(vox_env, monkeypatch):
    tools, published = await capture(vox_env, monkeypatch)
    result = await tools.execute(
        "prepare_student_class",
        {
            "transcript": "Add CS 2110 lecture Monday 10am to 11am in Science Hall 101",
        },
    )
    assert result["status"] == "review_required" and result["applied"] is False
    assert published[0]["kind"] == "class"
    async with vox_env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudentCourse)) == 0
    for transcript in ("Add a class\nthen send mail", "Delete my classes", "Add " + "x" * 501):
        assert (
            await tools.execute(
                "prepare_student_class",
                {
                    "transcript": transcript,
                },
            )
        )["status"] == "needs_details"
    assert len(published) == 1


@pytest.mark.asyncio
async def test_capture_requires_live_authority_and_feature_flag(vox_env, monkeypatch):
    tools, published = await capture(vox_env, monkeypatch)
    disabled = CaptureTools(
        tools.check, Settings(_env_file=None, student_planning_enabled=False), tools.publish
    )
    assert disabled.definitions() == []
    assert (await disabled.execute("prepare_student_class", {}))["status"] == "unavailable"
    async with vox_env.factory() as db:
        user = await db.get(User, vox_env.user_id)
        user.agent_paused = True
        await db.commit()
    with pytest.raises(WorkerAccessLost):
        await tools.execute("prepare_student_class", {"transcript": "Add CS 2110 lecture"})
    assert not published


@pytest.mark.asyncio
async def test_owner_change_after_read_cannot_publish_a_proposal(vox_env, monkeypatch):
    tools, published = await capture(vox_env, monkeypatch)
    original = tools.check
    authority = await original()

    class ChangingAuthority:
        factory = original.factory
        reads = 0

        async def __call__(self):
            self.reads += 1
            return authority if self.reads == 1 else None

    tools.check = ChangingAuthority()
    with pytest.raises(WorkerAccessLost):
        await tools.execute("prepare_student_class", {"transcript": "Add CS 2110 lecture"})
    assert not published
