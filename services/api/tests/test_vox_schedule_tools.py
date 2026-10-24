"""Real schedule reads and authority boundaries; no model, microphone, or provider calls."""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, event
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_media import begin, confirm

from navox.db.models import User, UserSession, Workspace, WorkspaceMembership
from navox.db.student import StudentClassMeeting, StudentCourse, StudentMeetingException
from navox.db.student_planning import StudentPlanningState, StudentStudyBlock, StudentTask
from navox.student import service as schedule
from navox.student_planning import service as planning
from navox.vox import schedule_tools
from navox.vox.schedule_tools import TOOL_NAME, ScheduleTools
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost, WorkerAssignment, WorkerAuthority

vox_env = shared_vox_fixture
MONDAY = datetime(2026, 10, 19, 14, 30, tzinfo=UTC)


async def tool(env, monkeypatch, *, moment=MONDAY, timezone="America/New_York"):
    async def clock(database):
        return moment

    for module in (schedule_tools, schedule, planning):
        monkeypatch.setattr(module, "database_now", clock)
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = timezone
        await database.commit()
    _, intent = await begin(env)
    await confirm(env, intent)
    check = DatabaseAuthority(
        env.factory,
        WorkerAssignment(
            intent.connection_id, intent.room_name, intent.client_identity, intent.agent_identity
        ),
    )
    return ScheduleTools(check)


async def course(
    env,
    *,
    name="Programming",
    user_id=None,
    workspace_id=None,
    start="10:00",
    end="11:00",
    weekdays=None,
    term_start=date(2026, 8, 20),
    term_end=date(2026, 12, 10),
    timezone="America/New_York",
):
    owner = user_id or env.user_id
    workspace = workspace_id or env.workspace_id
    async with env.factory() as database:
        row = StudentCourse(
            id=uuid4(),
            user_id=owner,
            workspace_id=workspace,
            code="CS 2110",
            name=name,
            term_start=term_start,
            term_end=term_end,
        )
        database.add(row)
        await database.flush()
        meeting = StudentClassMeeting(
            id=uuid4(),
            user_id=owner,
            workspace_id=workspace,
            course_id=row.id,
            kind="lecture",
            weekdays=[0, 2] if weekdays is None else weekdays,
            start_time=start,
            end_time=end,
            timezone=timezone,
            building="Science Hall",
            room="101",
        )
        database.add(meeting)
        await database.commit()
        return meeting.id


async def study_block(env, *, user_id=None, workspace_id=None, **changes):
    owner = user_id or env.user_id
    workspace = workspace_id or env.workspace_id
    async with env.factory() as database:
        task = StudentTask(
            id=uuid4(),
            user_id=owner,
            workspace_id=workspace,
            title="Revise calculus",
            estimated_minutes=60,
            remaining_minutes=60,
        )
        database.add(task)
        await database.flush()
        block = StudentStudyBlock(
            id=uuid4(),
            user_id=owner,
            workspace_id=workspace,
            task_id=task.id,
            plan_id=uuid4(),
            title="Revise calculus",
            start_at=MONDAY + timedelta(hours=1),
            end_at=MONDAY + timedelta(hours=2),
            minutes=60,
            **changes,
        )
        database.add(block)
        await database.commit()
        return block.id


@pytest.mark.asyncio
async def test_now_next_day_week_read_real_classes_and_do_not_write(vox_env, monkeypatch):
    tools = await tool(vox_env, monkeypatch)
    await course(vox_env)
    await course(vox_env, name="Afternoon lab", start="12:00", end="13:00", weekdays=[0])
    await study_block(vox_env)
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    async with vox_env.factory() as database:
        engine = database.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        now = await tools.execute(TOOL_NAME, {"mode": "now"})
        upcoming = await tools.execute(TOOL_NAME, {"mode": "next", "date": None})
        day = await tools.execute(TOOL_NAME, {"mode": "day"})
        week = await tools.execute(TOOL_NAME, {"mode": "week"})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert now["server_now"] == MONDAY.isoformat()
    assert [item["course_name"] for item in now["occurrences"]] == ["Programming"]
    assert [item["course_name"] for item in upcoming["occurrences"]] == ["Afternoon lab"]
    assert upcoming["next_upcoming"]["course_name"] == "Afternoon lab"
    assert len(day["occurrences"]) == 2 and len(week["occurrences"]) == 3
    assert week["date"] == "2026-10-19"
    assert week["window_start"] == "2026-10-19T04:00:00+00:00"
    assert week["window_end"] == "2026-10-26T04:00:00+00:00"
    assert len(day["study_blocks"]) == len(upcoming["study_blocks"]) == 1
    assert day["study_blocks"][0]["title"] == "Revise calculus"
    assert not {"INSERT", "UPDATE", "DELETE"} & set(statements)
    assert not any(key in json.dumps(day) for key in ("user_id", "workspace_id", "task_id"))


@pytest.mark.asyncio
async def test_foreign_courses_meetings_and_plans_never_leave_owner_scope(vox_env, monkeypatch):
    tools = await tool(vox_env, monkeypatch)
    await course(vox_env)
    other_user, other_workspace = uuid4(), uuid4()
    async with vox_env.factory() as database:
        database.add(User(id=other_user, email="other-schedule@example.com"))
        database.add(Workspace(id=other_workspace, name="Other personal workspace"))
        await database.flush()
        database.add(
            WorkspaceMembership(user_id=other_user, workspace_id=other_workspace, role="owner")
        )
        await database.commit()
    await course(
        vox_env, name="FOREIGN secret course", user_id=other_user, workspace_id=other_workspace
    )
    block_id = await study_block(vox_env, user_id=other_user, workspace_id=other_workspace)
    async with vox_env.factory() as database:
        block = await database.get(StudentStudyBlock, block_id)
        block.title = "FOREIGN secret plan"
        await database.commit()
    result = await tools.execute(TOOL_NAME, {"mode": "week"})
    assert "FOREIGN" not in json.dumps(result)
    assert result["study_blocks"] == []
    assert len(result["occurrences"]) == 2
    injected = await tools.execute(
        TOOL_NAME, {"mode": "day", "user_id": str(other_user), "workspace_id": str(other_workspace)}
    )
    assert injected["status"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_cancellations_course_terms_and_next_day_are_authoritative(vox_env, monkeypatch):
    tools = await tool(vox_env, monkeypatch)
    meeting = await course(vox_env)
    await course(
        vox_env, name="Expired course", term_start=date(2026, 1, 1), term_end=date(2026, 5, 1)
    )
    async with vox_env.factory() as database:
        database.add(
            StudentMeetingException(
                meeting_id=meeting,
                user_id=vox_env.user_id,
                workspace_id=vox_env.workspace_id,
                date=date(2026, 10, 19),
                cancelled=True,
            )
        )
        await database.commit()
    now = await tools.execute(TOOL_NAME, {"mode": "now"})
    upcoming = await tools.execute(TOOL_NAME, {"mode": "next"})
    day = await tools.execute(TOOL_NAME, {"mode": "day"})
    assert now["occurrences"] == [] and upcoming["occurrences"] == []
    assert upcoming["next_upcoming"]["start_at"] == "2026-10-21T14:00:00Z"
    assert len(day["occurrences"]) == 1 and day["occurrences"][0]["cancelled"] is True
    assert "Expired course" not in json.dumps(day)
    after_term = await tools.execute(TOOL_NAME, {"mode": "day", "date": "2027-01-01"})
    assert after_term["occurrences"] == []


@pytest.mark.parametrize(
    "timezone,expected_day,expected_week",
    [
        ("America/New_York", "2026-10-18", "2026-10-12"),
        ("Pacific/Kiritimati", "2026-10-19", "2026-10-19"),
    ],
)
@pytest.mark.asyncio
async def test_local_today_and_week_follow_saved_zone_not_utc(
    vox_env, monkeypatch, timezone, expected_day, expected_week
):
    tools = await tool(
        vox_env,
        monkeypatch,
        moment=datetime(2026, 10, 19, 0, 30, tzinfo=UTC),
        timezone=timezone,
    )
    day = await tools.execute(TOOL_NAME, {"mode": "day"})
    week = await tools.execute(TOOL_NAME, {"mode": "week"})
    assert day["date"] == expected_day and week["date"] == expected_week
    explicit = await tools.execute(TOOL_NAME, {"mode": "week", "date": "2026-10-25"})
    assert explicit["date"] == "2026-10-19"


@pytest.mark.asyncio
async def test_nonexistent_dst_class_is_unavailable_instead_of_empty_or_guessed(
    vox_env, monkeypatch
):
    tools = await tool(vox_env, monkeypatch)
    await course(
        vox_env,
        name="DST class",
        weekdays=[6],
        start="02:15",
        end="03:15",
        term_start=date(2027, 3, 1),
        term_end=date(2027, 3, 30),
    )
    result = await tools.execute(TOOL_NAME, {"mode": "day", "date": "2027-03-14"})
    assert result["status"] == "unavailable"
    assert "clock change" in result["message"] and "occurrences" not in result


@pytest.mark.asyncio
async def test_stale_plan_is_returned_as_needing_review(vox_env, monkeypatch):
    tools = await tool(vox_env, monkeypatch)
    await study_block(vox_env)
    async with vox_env.factory() as database:
        database.add(
            StudentPlanningState(
                user_id=vox_env.user_id,
                workspace_id=vox_env.workspace_id,
                preferences={},
                needs_review=True,
                window_start=MONDAY,
                window_end=MONDAY + timedelta(days=7),
            )
        )
        await database.commit()
    result = await tools.execute(TOOL_NAME, {"mode": "day"})
    assert result["plan_needs_review"] is True
    assert result["plan_review_notice"]
    assert result["study_blocks"][0]["status"] == "needs_review"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("invent_schedule", {"mode": "day"}),
        (TOOL_NAME, {"mode": "year"}),
        (TOOL_NAME, {"mode": "day", "date": "20261019"}),
        (TOOL_NAME, {"mode": "day", "date": "2026-W43-1"}),
        (TOOL_NAME, {"mode": "day", "date": "2026-02-30"}),
        (TOOL_NAME, {"mode": "day", "date": "2040-01-01"}),
        (TOOL_NAME, {"mode": "week", "date": "0001-01-01"}),
        (TOOL_NAME, {"mode": "now", "date": "2026-10-19"}),
        (TOOL_NAME, {"mode": "day", "timezone": "UTC"}),
        (TOOL_NAME, {"mode": "day", "now": "2040-01-01T00:00:00Z"}),
        (TOOL_NAME, {"mode": "day", "date": 20261019}),
    ],
)
@pytest.mark.asyncio
async def test_tool_arguments_are_strict_bounded_and_cannot_choose_clock(
    vox_env, monkeypatch, name, arguments
):
    tools = await tool(vox_env, monkeypatch)
    assert (await tools.execute(name, arguments))["status"] == "invalid_arguments"


@pytest.mark.asyncio
async def test_revoked_session_blocks_every_tool_call(vox_env, monkeypatch):
    tools = await tool(vox_env, monkeypatch)
    assert (await tools.execute(TOOL_NAME, {"mode": "day"}))["status"] == "ok"
    async with vox_env.factory() as database:
        session = await database.get(UserSession, vox_env.session_id)
        session.revoked_at = datetime.now(UTC)
        await database.commit()
    with pytest.raises(WorkerAccessLost):
        await tools.execute(TOOL_NAME, {"mode": "day"})


@pytest.mark.asyncio
async def test_membership_revocation_between_read_and_result_discards_private_data(
    vox_env, monkeypatch
):
    tools = await tool(vox_env, monkeypatch)
    await course(vox_env, name="Never disclose after revocation")
    original = planning.plan_view

    async def revoke_after_read(database, user_id, workspace_id):
        result = await original(database, user_id, workspace_id)
        async with vox_env.factory() as other:
            await other.execute(
                delete(WorkspaceMembership).where(
                    WorkspaceMembership.user_id == user_id,
                    WorkspaceMembership.workspace_id == workspace_id,
                )
            )
            await other.commit()
        return result

    monkeypatch.setattr(planning, "plan_view", revoke_after_read)
    with pytest.raises(WorkerAccessLost):
        await tools.execute(TOOL_NAME, {"mode": "day"})


@pytest.mark.asyncio
async def test_owner_change_between_proofs_fails_closed(vox_env, monkeypatch):
    tools = await tool(vox_env, monkeypatch)
    original = tools.check
    calls = 0

    class ChangingAuthority:
        factory = original.factory

        async def __call__(self):
            nonlocal calls
            calls += 1
            authority = await original()
            if calls > 1:
                return WorkerAuthority(uuid4(), authority.workspace_id)
            return authority

    tools.check = ChangingAuthority()
    with pytest.raises(WorkerAccessLost):
        await tools.execute(TOOL_NAME, {"mode": "day"})
