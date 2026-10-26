"""Actual call/session/calendar receipts with synthetic provider transports."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_student_calendar_consent import calendar_env as shared_calendar_env
from test_student_calendar_events import event_env as shared_event_env
from test_vox_media import begin, confirm

from navox.connectors.sync_state import database_now
from navox.db.student import StudentFixedBlock
from navox.db.student_calendars import StudentCalendarEvent
from navox.db.vox import VoxCall
from navox.student_calendars.provider import CalendarTarget
from navox.student_planning import service as planning
from navox.vox.calendar_approval import CalendarApprovalCoordinator
from navox.vox.calendar_tools import PREPARE_TOOL, SUGGEST_TOOL, UNDO_TOOL, CalendarTools
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost, WorkerAssignment

calendar_env, event_env = shared_calendar_env, shared_event_env


@pytest_asyncio.fixture
async def calendar_voice_env(event_env):
    env = event_env
    env.settings.student_vox_enabled = True
    env.client.headers.update(env.headers)
    call, intent = await begin(env)
    await confirm(env, intent)
    env.call_id = UUID(call["id"])
    env.published = []

    async def publish(value):
        env.published.append(value)

    env.check = DatabaseAuthority(
        env.factory,
        WorkerAssignment(
            intent.connection_id, intent.room_name, intent.client_identity, intent.agent_identity
        ),
    )
    env.approval = CalendarApprovalCoordinator()
    env.tools = CalendarTools(
        env.check, env.settings, env.approval, publish, provider=env.writes, voice_available=True
    )
    return env


def arguments(env, **changes):
    return {
        "title": "Review circuits",
        "location": "Study room",
        "start_at": env.start_at.isoformat(),
        "duration_minutes": 30,
        "calendar_name": None,
        "account_name": None,
        "allow_study_displacement": False,
        **changes,
    }


async def prepare(env, **changes):
    result = await env.tools.execute(PREPARE_TOOL, arguments(env, **changes))
    assert result["status"] == "review_required", result
    return result


async def play_review(env):
    reply = await env.approval.after_tool()
    assert reply is not None
    await env.approval.played(reply.answer)
    return reply.answer


@pytest.mark.asyncio
async def test_voice_calendar_full_prepare_yes_verify_and_new_exact_undo_review(calendar_voice_env):
    env = calendar_voice_env
    await prepare(env)
    assert env.writes.inserts == []
    spoken = await play_review(env)
    assert "Review circuits" in spoken and "Study room" in spoken
    assert spoken.index("private calendar review") < spoken.index("Review circuits")
    assert "Private personal calendar" in spoken and "UTC" in spoken
    assert "Google calendar" in spoken
    assert env.start_at.strftime("%I:%M %p") in spoken
    added = await env.approval.input("of course")
    assert "confirmed this exact event was added" in added.answer
    assert len(env.writes.inserts) == 1 and env.published[-1]["status"] == "created"
    assert await env.approval.input("yes") is None
    assert len(env.writes.inserts) == 1
    prepared = await env.tools.execute(UNDO_TOOL, {})
    assert prepared["status"] == "review_required" and env.writes.deletes == []
    undo_readback = await play_review(env)
    assert "Remove only" in undo_readback and "Review circuits" in undo_readback
    removed = await env.approval.input("go ahead")
    assert "confirmed this exact calendar event is absent" in removed.answer
    assert len(env.writes.deletes) == 1


@pytest.mark.asyncio
async def test_unplayed_review_and_forged_model_authority_never_submit(calendar_voice_env):
    env = calendar_voice_env
    for extra in (
        {"confirmed": True},
        {"session_hash": "private"},
        {"user_id": str(uuid4())},
        {"event_id": str(uuid4())},
    ):
        result = await env.tools.execute(PREPARE_TOOL, arguments(env, **extra))
        assert result["status"] == "invalid_arguments"
    await prepare(env)
    assert "no longer active" in (await env.approval.input("yes")).answer
    assert env.writes.inserts == []
    names = {definition["name"] for definition in env.tools.definitions()}
    assert not any("approve" in name or "delete" in name for name in names)


@pytest.mark.asyncio
async def test_readonly_ranked_options_are_minimized_and_cannot_create_an_event(calendar_voice_env):
    env = calendar_voice_env
    result = await env.tools.execute(
        SUGGEST_TOOL,
        {
            "purpose": "lunch",
            "date": env.start_at.date().isoformat(),
            "days": 1,
            "duration_minutes": None,
        },
    )
    assert result["status"] == "ok" and len(result["options"]) == 2
    assert result["duration_minutes"] == 60 and not result["changes_applied"]
    assert all("lunch_time" in option["reasons"] for option in result["options"])
    assert "private-calendar" not in str(result) and "synthetic" not in str(result)
    assert env.writes.inserts == [] and env.published == []
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentCalendarEvent)) == 0
    missing = await env.tools.execute(
        SUGGEST_TOOL, {"purpose": "other", "date": None, "days": 1, "duration_minutes": None}
    )
    assert missing["status"] == "needs_duration"


@pytest.mark.asyncio
async def test_changed_internal_source_after_readback_requires_new_review(calendar_voice_env):
    env = calendar_voice_env
    await prepare(env)
    await play_review(env)
    async with env.factory() as database:
        database.add(
            StudentFixedBlock(
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                title="New commitment",
                term_start=env.start_at.date(),
                term_end=env.start_at.date(),
                weekdays=[env.start_at.weekday()],
                start_time="12:00",
                end_time="13:00",
                timezone="UTC",
            )
        )
        await database.commit()
    result = await env.approval.input("yes")
    assert "changed" in result.answer and env.writes.inserts == []


@pytest.mark.asyncio
async def test_long_or_unqualified_readback_stays_screen_only(calendar_voice_env):
    env = calendar_voice_env
    result = await prepare(env, location="L" * 500)
    assert result["voice_approval_available"] is False
    assert await env.approval.after_tool() is None
    assert await env.approval.input("yes") is None
    assert env.writes.inserts == []
    assert set(env.published[0]) == {
        "kind",
        "event_id",
        "status",
        "voice_approval_available",
        "destination",
    }
    env.tools.voice_available = False
    result = await prepare(env, start_at=(env.start_at + timedelta(hours=2)).isoformat())
    assert not result["voice_approval_available"] and not env.approval.needs_input


@pytest.mark.asyncio
async def test_multiple_writable_targets_require_exact_choice(calendar_voice_env):
    env = calendar_voice_env

    async def targets(**kwargs):
        await kwargs["check"]()
        return (CalendarTarget("one", "First", "UTC"), CalendarTarget("two", "Second", "UTC"))

    env.writes.owned_calendars = targets
    result = await env.tools.execute(PREPARE_TOOL, arguments(env))
    assert result["status"] == "unavailable" and "Name one exact" in result["message"]
    assert env.writes.inserts == [] and not env.approval.needs_input


@pytest.mark.asyncio
async def test_hangup_during_submitted_operation_stays_held_and_never_replays(calendar_voice_env):
    env = calendar_voice_env
    await prepare(env)
    await play_review(env)

    async def hangup():
        async with env.factory() as database:
            call = await database.get(VoxCall, env.call_id)
            call.status, call.end_reason, call.ended_at = (
                "ended",
                "hang-up",
                await database_now(database),
            )
            await database.commit()

    env.writes.during_insert = hangup
    with pytest.raises(WorkerAccessLost):
        await env.approval.input("yes")
    assert len(env.writes.inserts) == 1
    async with env.factory() as database:
        row = await database.scalar(select(StudentCalendarEvent))
        assert row.status == "executing"
    with pytest.raises(WorkerAccessLost):
        await env.tools.execute(PREPARE_TOOL, arguments(env))
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
async def test_overnight_awake_event_reads_both_exact_local_dates(calendar_voice_env):
    env = calendar_voice_env
    async with env.factory() as database:
        state = await planning.state(database, env.user_id, env.workspace_id)
        state.preferences = {**state.preferences, "wake_time": "20:00", "sleep_time": "08:00"}
        state.preferences_revision += 1
        await database.commit()
    start = env.start_at.replace(hour=23, minute=30)
    await prepare(env, start_at=start.isoformat(), duration_minutes=120)
    spoken = await play_review(env)
    end = start + timedelta(minutes=120)
    assert start.strftime("%A, %B") in spoken and end.strftime("%A, %B") in spoken
    assert "11:30 PM" in spoken and "01:30 AM" in spoken
    assert env.writes.inserts == []
