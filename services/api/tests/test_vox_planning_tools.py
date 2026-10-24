"""Proposals prepare real plans but never apply changes through model arguments."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_schedule_tools import course, study_block, tool

from navox.core.settings import Settings
from navox.db.models import User
from navox.db.student_planning import StudentPlanningPreview, StudentStudyBlock
from navox.vox.planning_tools import PlanningTools
from navox.vox.worker import WorkerAccessLost

vox_env = shared_vox_fixture


@pytest.mark.asyncio
async def test_voice_proposal_uses_real_classes_and_leaves_blocks_untouched(vox_env, monkeypatch):
    reader = await tool(vox_env, monkeypatch)
    await course(vox_env)
    block_id = await study_block(vox_env)
    published = []

    async def publish(value):
        published.append(value)

    tools = PlanningTools(
        reader.check, Settings(_env_file=None, student_planning_enabled=True), publish
    )
    result = await tools.execute("prepare_study_plan", {"date": None, "days": 7})
    assert result["status"] == "review_required" and result["applied"] is False
    assert result["replaced_count"] == 1 and result["proposed_count"] > 0
    assert result["unchanged"] == ["classes", "calendar events", "alarms"]
    assert len(published) == 1 and published[0]["kind"] == "study-plan"
    async with vox_env.factory() as database:
        block = await database.get(StudentStudyBlock, block_id)
        assert block.status == "planned" and block.revision == 1
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == 1
        assert await database.scalar(select(func.count()).select_from(StudentPlanningPreview)) == 1
    invalid = await tools.execute(
        "prepare_study_plan", {"date": None, "days": 7, "confirmed": True}
    )
    assert invalid["status"] == "invalid_arguments" and len(published) == 1
    injected = await tools.execute(
        "prepare_study_plan", {"date": None, "days": 7, "user_id": str(uuid4())}
    )
    assert injected["status"] == "invalid_arguments" and len(published) == 1


@pytest.mark.asyncio
async def test_voice_openings_disclose_missing_coverage_and_require_active_authority(
    vox_env, monkeypatch
):
    reader = await tool(vox_env, monkeypatch)

    async def publish(value):
        pytest.fail("An availability read must not open an action")

    tools = PlanningTools(
        reader.check, Settings(_env_file=None, student_planning_enabled=True), publish
    )
    result = await tools.execute("get_student_free_time", {"days": 1})
    assert result["status"] == "ok"
    assert result["calendar_checked"] is False and result["routine_confirmed"] is False
    async with vox_env.factory() as database:
        user = await database.get(User, vox_env.user_id)
        user.agent_paused = True
        await database.commit()
    with pytest.raises(WorkerAccessLost):
        await tools.execute("prepare_study_plan", {"date": None, "days": 1})


@pytest.mark.asyncio
async def test_voice_planning_rolls_back_to_unavailable_when_feature_disabled(vox_env, monkeypatch):
    reader = await tool(vox_env, monkeypatch)

    async def publish(value):
        pytest.fail("A disabled tool must not publish")

    tools = PlanningTools(
        reader.check, Settings(_env_file=None, student_planning_enabled=False), publish
    )
    assert tools.definitions() == []
    assert (await tools.execute("prepare_study_plan", {"date": None, "days": 1}))[
        "status"
    ] == "unavailable"


@pytest.mark.asyncio
async def test_alarm_commands_prepare_exact_days_without_saving(vox_env, monkeypatch):
    from navox.student_planning import service as planning

    reader = await tool(vox_env, monkeypatch)
    published = []

    async def publish(value):
        published.append(value)

    tools = PlanningTools(
        reader.check, Settings(_env_file=None, student_planning_enabled=True), publish
    )
    result = await tools.execute(
        "prepare_alarm_change", {"action": "set_wake_time", "time": "07:15", "weekdays": [0, 2, 4]}
    )
    assert result["status"] == "review_required" and result["applied"] is False
    assert result["native_ringing_available"] is False
    assert published[0]["kind"] == "routine"
    prefs = published[0]["preferences"]
    assert prefs["wake_times"][0] == "07:15" and prefs["wake_times"][1] == "07:00"
    assert prefs["alarm"]["days"] == [0, 2, 4] and prefs["alarm"]["enabled"] is True
    async with vox_env.factory() as database:
        saved = await planning.preferences(database, vox_env.user_id, vox_env.workspace_id)
        assert saved.alarm.enabled is False and saved.revision == 0
    assert (
        await tools.execute(
            "prepare_alarm_change", {"action": "set_wake_time", "time": "25:00", "weekdays": [0]}
        )
    )["status"] == "invalid_arguments"
    assert len(published) == 1
    result = await tools.execute(
        "prepare_alarm_change", {"action": "skip_tomorrow", "time": None, "weekdays": None}
    )
    assert result["status"] == "review_required"
    assert published[-1]["preferences"]["alarm"]["skip_date"] == "2026-10-20"
