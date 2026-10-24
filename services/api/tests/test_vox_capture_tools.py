"""Voice capture proposes owner-bound changes and never applies them."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_schedule_tools import course, study_block, tool

from navox.core.settings import Settings
from navox.db.models import User
from navox.db.student import StudentCourse
from navox.db.student_planning import StudentStudyBlock, StudentTask
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
    assert published[0]["task"]["due_at"] == "2026-10-21T21:00:00Z"
    assert published[0]["kind"] == "task"
    assert "task_id" not in published[0]
    async with vox_env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudentTask)) == 0


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
