"""Real owned reviews and receipts with synthetic provider transport only."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_student_calendar_consent import calendar_env as calendar_env
from test_student_calendar_events import (
    BASE,
    act,
    draft_body,
    preview,
)
from test_student_calendar_events import (
    event_env as event_env,
)

from navox.coursework import service as coursework
from navox.db.student import StudentCourse
from navox.db.student_calendars import StudentCalendarEvent, StudentCalendarGrant
from navox.db.student_planning import (
    StudentPlanningCommand,
    StudentStudyBlock,
    StudentTask,
)
from navox.providers.google_availability import ProviderBusy
from navox.student import service as schedule
from navox.student_calendars import claims, displacement, scheduling
from navox.student_planning import calendar_busy, completion, moving
from navox.student_planning import service as planning
from navox.student_planning.placement import PlacementInput, place
from navox.student_planning.schemas import (
    AcceptInput,
    MoveInput,
    OutcomeInput,
    PreferencesUpdate,
    TaskCreate,
    UndoInput,
)

PRIVATE_STUDY = "Private quantum reading plan"
OTHER_STUDY = "Independent afternoon revision"


@pytest_asyncio.fixture
async def study_env(event_env, monkeypatch):
    env = event_env
    env.settings.student_planning_enabled = True
    monkeypatch.setattr(claims, "get_settings", lambda: env.settings)
    monkeypatch.setattr(displacement, "get_settings", lambda: env.settings)
    monkeypatch.setattr(calendar_busy, "get_settings", lambda: env.settings)
    monkeypatch.setattr(calendar_busy, "GoogleAvailabilityProvider", lambda: env.busy)

    async def token(database, *, connection, settings):
        from test_student_calendar_events import TOKEN

        return TOKEN

    monkeypatch.setattr(calendar_busy, "access_token_for_connection", token)
    env.task_id, env.block_id, env.other_task_id, env.other_block_id = (uuid4() for _ in range(4))
    async with env.factory() as database:
        for task_id, block_id, title, start, minutes in (
            (env.task_id, env.block_id, PRIVATE_STUDY, env.start_at, 60),
            (env.other_task_id, env.other_block_id, OTHER_STUDY, env.start_at.replace(hour=17), 45),
        ):
            database.add(
                StudentTask(
                    id=task_id,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    title=title,
                    due_at=env.start_at.replace(hour=22),
                    estimated_minutes=minutes,
                    remaining_minutes=minutes,
                    actual_minutes=0,
                    difficulty=3,
                    priority=3,
                    course_weight=1,
                    status="active",
                    revision=1,
                )
            )
            await database.flush()
            database.add(
                StudentStudyBlock(
                    id=block_id,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    task_id=task_id,
                    plan_id=uuid4(),
                    title=title,
                    start_at=start,
                    end_at=start + timedelta(minutes=minutes),
                    minutes=minutes,
                    focus_window=False,
                    revision=1,
                    status="planned",
                    actual_minutes=0,
                )
            )
        await database.flush()
        state = await planning.state(database, env.user_id, env.workspace_id)
        state.window_start = env.start_at.replace(hour=7)
        state.window_end = env.start_at.replace(hour=23)
        state.accepted_source_hash = await planning.stable_source_hash(
            database, env.user_id, env.workspace_id, state
        )
        await database.commit()
    return env


async def saved_block(env, identifier):
    async with env.factory() as database:
        value = await database.get(StudentStudyBlock, identifier)
        return planning.block_view(value).model_dump(mode="json") if value else None


async def receipt(env, identifier):
    response = await env.client.get(BASE + "/events/" + identifier, headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def private_payload(env, identifier):
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(identifier))
        return scheduling._open(row, env.settings)


@pytest.mark.asyncio
async def test_review_is_exact_encrypted_and_does_not_move_until_approved(study_env):
    env = study_env
    original = await saved_block(env, env.block_id)
    other = await saved_block(env, env.other_block_id)
    view = await preview(env, allow_study_displacement=True)
    assert view["study_changes_applied"] is False
    assert view["changes_applied"] is False
    assert len(view["study_moves"]) == 1
    move = view["study_moves"][0]
    assert move["block_id"] == str(env.block_id) and move["minutes"] == 60
    assert move["before_start_at"] == original["start_at"]
    assert move["before_end_at"] == original["end_at"]
    assert move["after_start_at"] != move["before_start_at"]
    assert (await saved_block(env, env.block_id)) == original
    assert (await saved_block(env, env.other_block_id)) == other
    assert env.writes.inserts == []
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(view["id"]))
        assert PRIVATE_STUDY not in row.encrypted_payload
        index = await database.scalar(
            select(StudentPlanningCommand).where(
                StudentPlanningCommand.action == displacement.INDEX_ACTION
            )
        )
        assert PRIVATE_STUDY not in json.dumps(index.result)


@pytest.mark.asyncio
async def test_one_approval_moves_only_reviewed_blocks_and_creates_verified_event(study_env):
    env = study_env
    other = await saved_block(env, env.other_block_id)
    view = await preview(env, allow_study_displacement=True)
    created = await act(env, view, "approve")
    assert created.status_code == 200, created.text
    created = created.json()
    assert created["status"] == "created" and created["study_changes_applied"] is True
    move = created["study_moves"][0]
    block = await saved_block(env, env.block_id)
    assert block["start_at"] == move["after_start_at"] and block["revision"] == 2
    assert (await saved_block(env, env.other_block_id)) == other
    assert len(env.writes.inserts) == 1
    assert env.writes.inserts[0]["description"] == "Added by NavoX"
    assert PRIVATE_STUDY not in json.dumps(env.writes.inserts)
    undone = await act(env, created, "undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "undone"
    assert undone.json()["study_changes_applied"] is True
    assert "study moves remain" in undone.json()["message"]
    assert (await saved_block(env, env.block_id)) == block


@pytest.mark.asyncio
async def test_existing_no_displacement_request_shape_and_conservative_rejection(study_env):
    env = study_env
    response = await env.client.post(
        BASE + "/events/preview", json=await draft_body(env), headers=env.headers
    )
    assert response.status_code == 409
    assert env.writes.inserts == []


@pytest.mark.asyncio
async def test_no_feasible_replacement_before_deadline_is_not_offered(study_env):
    env = study_env
    async with env.factory() as database:
        task = await database.get(StudentTask, env.task_id)
        task.due_at = env.start_at.replace(hour=13)
        state = await planning.state(database, env.user_id, env.workspace_id)
        await database.flush()
        state.accepted_source_hash = await planning.stable_source_hash(
            database, env.user_id, env.workspace_id, state
        )
        await database.commit()
    env.busy.extra = (
        ProviderBusy(env.start_at.replace(hour=7), env.start_at.replace(hour=11, minute=30)),
    )
    response = await env.client.post(
        BASE + "/events/preview",
        json=await draft_body(env, allow_study_displacement=True),
        headers=env.headers,
    )
    assert response.status_code == 409, response.text
    assert env.writes.inserts == []
    assert (await saved_block(env, env.block_id))["revision"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["task", "block", "calendar"])
async def test_changed_review_is_not_replanned_or_applied_silently(study_env, change):
    env = study_env
    view = await preview(env, allow_study_displacement=True)
    if change == "calendar":
        move = view["study_moves"][0]
        from datetime import datetime

        env.busy.extra = (
            ProviderBusy(
                datetime.fromisoformat(move["after_start_at"]),
                datetime.fromisoformat(move["after_end_at"]),
            ),
        )
    else:
        async with env.factory() as database:
            row = await database.get(
                StudentTask if change == "task" else StudentStudyBlock,
                env.task_id if change == "task" else env.block_id,
            )
            row.revision += 1
            await database.commit()
    rejected = await act(env, view, "approve")
    assert rejected.status_code == 409, rejected.text
    assert env.writes.inserts == []


@pytest.mark.asyncio
async def test_unknown_provider_outcome_holds_planner_and_never_repeats_insert(study_env):
    env = study_env
    env.writes.insert_mode = "timeout_before"
    view = await preview(env, allow_study_displacement=True)
    result = await act(env, view, "approve")
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["status"] == "uncertain" and result["changes_applied"] is None
    assert result["study_changes_applied"] is True
    assert (await saved_block(env, env.block_id))["revision"] == 2
    assert len(env.writes.inserts) == 1
    assert (await act(env, result, "approve")).status_code == 409
    assert len(env.writes.inserts) == 1
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError, match="still being checked"):
            await planning.save_task(
                database,
                env.user_id,
                env.workspace_id,
                TaskCreate(request_id=uuid4(), title="Another task"),
            )
    assert (
        await env.client.get("/api/v1/student/planning/plan", headers=env.headers)
    ).status_code == 200
    async with env.factory() as database:
        busy = await calendar_busy.collect(
            database,
            env.user_id,
            env.workspace_id,
            start_at=env.start_at.replace(hour=7),
            end_at=env.start_at.replace(hour=23),
            travel_minutes=15,
        )
        assert any(
            first <= env.start_at - timedelta(minutes=25)
            and last >= env.end_at + timedelta(minutes=25)
            for first, last in busy.intervals
        )


@pytest.mark.asyncio
async def test_known_rejection_keeps_approved_moves_but_releases_mutation_hold(study_env):
    env = study_env
    env.writes.insert_mode = "rejected"
    result = await act(env, await preview(env, allow_study_displacement=True), "approve")
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["status"] == "failed" and value["changes_applied"] is False
    assert value["study_changes_applied"] is True
    assert "not rolled back" in value["message"]
    async with env.factory() as database:
        assert not await claims.pending_displacement(database, env.user_id, env.workspace_id)
        await claims.require_mutable(database, env.user_id, env.workspace_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
async def test_task_forget_redacts_pending_and_admitted_derived_copies(study_env, admitted):
    env = study_env
    view = await preview(env, allow_study_displacement=True)
    if admitted:
        env.writes.insert_mode = "timeout_before"
        response = await act(env, view, "approve")
        assert response.status_code == 200, response.text
    other = await saved_block(env, env.other_block_id)
    async with env.factory() as database:
        await planning.remove_task(
            database, env.user_id, env.workspace_id, env.task_id, revision=1, request_id=uuid4()
        )
    payload = await private_payload(env, view["id"])
    assert PRIVATE_STUDY not in json.dumps(payload)
    assert payload["study_displacement"]["review"]["moves"] == []
    latest = await receipt(env, view["id"])
    assert latest["study_details_redacted"] is True
    assert latest["study_moves"] == []
    assert latest["study_changes_applied"] is admitted
    assert latest["status"] == ("uncertain" if admitted else "failed")
    if admitted:
        assert (await saved_block(env, env.other_block_id)) == other
    assert (await act(env, latest, "approve")).status_code == 409


@pytest.mark.asyncio
async def test_course_workspace_erasure_redacts_only_its_study_review(study_env):
    env = study_env
    course_id = uuid4()
    async with env.factory() as database:
        database.add(
            StudentCourse(
                id=course_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code="ECE",
                name="Private circuits",
                term_start=env.start_at.date(),
                term_end=(env.start_at + timedelta(days=90)).date(),
                archived=False,
                revision=1,
            )
        )
        await database.flush()
        task = await database.get(StudentTask, env.task_id)
        task.course_id = course_id
        state = await planning.state(database, env.user_id, env.workspace_id)
        await database.flush()
        state.accepted_source_hash = await planning.stable_source_hash(
            database, env.user_id, env.workspace_id, state
        )
        await database.commit()
    view = await preview(env, allow_study_displacement=True)
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await coursework.clear_coursework_data(
            database, user_id=env.user_id, workspace_id=env.workspace_id, course_id=course_id
        )
        await database.commit()
    assert PRIVATE_STUDY not in json.dumps(await private_payload(env, view["id"]))
    assert (await receipt(env, view["id"]))["status"] == "failed"


@pytest.mark.asyncio
async def test_full_planning_erasure_redacts_before_removing_opaque_index(study_env):
    env = study_env
    env.writes.insert_mode = "timeout_before"
    view = await preview(env, allow_study_displacement=True)
    assert (await act(env, view, "approve")).status_code == 200
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await planning.forget_planning_data(database, env.user_id, env.workspace_id)
        await database.commit()
        assert await database.scalar(select(func.count()).select_from(StudentPlanningCommand)) == 0
    assert PRIVATE_STUDY not in json.dumps(await private_payload(env, view["id"]))
    latest = await receipt(env, view["id"])
    assert latest["status"] == "uncertain" and latest["study_details_redacted"] is True


@pytest.mark.asyncio
async def test_new_provenance_and_legacy_empty_description_remain_exact(event_env):
    env = event_env
    new = await preview(env)
    created = await act(env, new, "approve")
    assert created.status_code == 200, created.text
    assert env.writes.inserts[0]["description"] == "Added by NavoX"
    assert "study_moves" not in created.json()
    async with env.factory() as database:
        row = await database.get(StudentCalendarEvent, UUID(new["id"]))
        payload = scheduling._open(row, env.settings)
        draft = scheduling.CalendarEventPreview.model_validate(payload["draft"])
        legacy = dict(env.writes.events[row.id.hex])
        legacy.pop("description")
        assert scheduling._matches(row, draft, legacy)
        assert not scheduling._matches(row, draft, legacy, description="Added by NavoX")
        assert scheduling._matches(
            row, draft, env.writes.events[row.id.hex], description="Added by NavoX"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["preferences", "accept", "outcome", "undo", "move", "place", "complete"]
)
async def test_all_actual_planner_mutators_honor_the_durable_hold(study_env, operation):
    env = study_env
    env.writes.insert_mode = "timeout_before"
    view = await preview(env, allow_study_displacement=True)
    assert (await act(env, view, "approve")).status_code == 200
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError, match="still being checked"):
            if operation == "preferences":
                await planning.save_preferences(
                    database,
                    env.user_id,
                    env.workspace_id,
                    PreferencesUpdate(request_id=uuid4(), revision=1),
                )
            elif operation == "accept":
                await planning.accept(
                    database,
                    env.user_id,
                    env.workspace_id,
                    AcceptInput(
                        request_id=uuid4(),
                        preview_id=uuid4(),
                        preview_hash="a" * 64,
                        confirmed=True,
                    ),
                )
            elif operation == "outcome":
                await planning.outcome(
                    database,
                    env.user_id,
                    env.workspace_id,
                    env.block_id,
                    OutcomeInput(
                        request_id=uuid4(),
                        revision=2,
                        task_revision=1,
                        outcome="done",
                        actual_minutes=60,
                    ),
                )
            elif operation == "undo":
                await planning.undo_outcome(
                    database,
                    env.user_id,
                    env.workspace_id,
                    UndoInput(request_id=uuid4(), outcome_request_id=uuid4(), revision=2),
                )
            elif operation == "move":
                await moving.move(
                    database,
                    env.user_id,
                    env.workspace_id,
                    env.block_id,
                    MoveInput(
                        request_id=uuid4(), revision=2, start_at=env.start_at.replace(hour=9)
                    ),
                    session_hash=env.token_hash,
                    expected_preview="a" * 64,
                )
            elif operation == "place":
                await place(
                    database,
                    env.user_id,
                    env.workspace_id,
                    PlacementInput(
                        request_id=uuid4(),
                        task_id=env.task_id,
                        task_revision=1,
                        timezone="UTC",
                        start_at=env.start_at.replace(hour=9),
                        window_end_at=env.start_at.replace(hour=10),
                        minutes=30,
                    ),
                    session_hash=env.token_hash,
                    expected_preview="a" * 64,
                )
            else:
                await completion.complete(
                    database,
                    env.user_id,
                    env.workspace_id,
                    env.task_id,
                    completion.CompleteInput(request_id=uuid4(), revision=1),
                    expected_preview="a" * 64,
                )
    assert len(env.writes.inserts) == 1


@pytest.mark.asyncio
async def test_explicit_unrelated_erasure_preserves_hold_beyond_retry_pruning(study_env):
    env = study_env
    env.writes.insert_mode = "timeout_before"
    view = await preview(env, allow_study_displacement=True)
    assert (await act(env, view, "approve")).status_code == 200
    moved = await saved_block(env, env.block_id)
    async with env.factory() as database:
        index = await database.scalar(
            select(StudentPlanningCommand).where(
                StudentPlanningCommand.action == displacement.INDEX_ACTION
            )
        )
        index.created_at -= timedelta(days=8)
        await database.commit()
    async with env.factory() as database:
        await planning.remove_task(
            database,
            env.user_id,
            env.workspace_id,
            env.other_task_id,
            revision=1,
            request_id=uuid4(),
        )
    assert (await saved_block(env, env.block_id)) == moved
    async with env.factory() as database:
        assert await claims.pending_displacement(database, env.user_id, env.workspace_id)
        assert (
            await database.scalar(
                select(func.count())
                .select_from(StudentPlanningCommand)
                .where(StudentPlanningCommand.action == displacement.INDEX_ACTION)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_native_claims_survive_provider_omission_and_status_churn(study_env):
    env = study_env
    view = await preview(env, allow_study_displacement=True)
    assert (await act(env, view, "approve")).status_code == 200
    env.writes.events.clear()
    async with env.factory() as database:
        state = await planning.state(database, env.user_id, env.workspace_id)
        original = await planning.stable_source_hash(database, env.user_id, env.workspace_id, state)
        busy = await calendar_busy.collect(
            database,
            env.user_id,
            env.workspace_id,
            start_at=env.start_at.replace(hour=7),
            end_at=env.start_at.replace(hour=23),
            travel_minutes=15,
        )
        assert busy.intervals
        row = await database.get(StudentCalendarEvent, UUID(view["id"]))
        row.status, row.revision = "changed", row.revision + 1
        await database.flush()
        assert (
            await planning.stable_source_hash(database, env.user_id, env.workspace_id, state)
            == original
        )
        assert (
            await calendar_busy.collect(
                database,
                env.user_id,
                env.workspace_id,
                start_at=env.start_at.replace(hour=7),
                end_at=env.start_at.replace(hour=23),
                travel_minutes=15,
            )
        ).digest == busy.digest
        row.status = "undone"
        await database.flush()
        assert (
            await planning.stable_source_hash(database, env.user_id, env.workspace_id, state)
            != original
        )
        assert not (await claims.snapshot(database, env.user_id, env.workspace_id))


@pytest.mark.asyncio
async def test_native_claims_remain_busy_after_busy_consent_is_revoked(study_env):
    env = study_env
    view = await preview(env, allow_study_displacement=True)
    assert (await act(env, view, "approve")).status_code == 200
    async with env.factory() as database:
        grants = list(await database.scalars(select(StudentCalendarGrant)))
        from navox.connectors.sync_state import database_now

        for grant in grants:
            grant.status, grant.revoked_at = "revoked", await database_now(database)
        await database.flush()
        busy = await calendar_busy.collect(
            database,
            env.user_id,
            env.workspace_id,
            start_at=env.start_at.replace(hour=7),
            end_at=env.start_at.replace(hour=23),
            travel_minutes=15,
        )
        assert busy.connected is False and busy.intervals
        assert not await claims.snapshot(database, uuid4(), env.workspace_id)
        assert not await claims.snapshot(database, env.user_id, uuid4())


@pytest.mark.asyncio
async def test_partial_source_erasure_keeps_other_approved_destinations_and_details(study_env):
    env = study_env
    env.end_at = env.start_at + timedelta(hours=2)
    async with env.factory() as database:
        other = await database.get(StudentStudyBlock, env.other_block_id)
        other.start_at = env.start_at + timedelta(minutes=75)
        other.end_at = other.start_at + timedelta(minutes=other.minutes)
        state = await planning.state(database, env.user_id, env.workspace_id)
        await database.flush()
        state.accepted_source_hash = await planning.stable_source_hash(
            database, env.user_id, env.workspace_id, state
        )
        await database.commit()
    body = await draft_body(env, allow_study_displacement=True)
    body["availability"]["duration_minutes"] = 120
    response = await env.client.post(BASE + "/events/preview", json=body, headers=env.headers)
    assert response.status_code == 200, response.text
    view = response.json()
    assert len(view["study_moves"]) == 2
    env.writes.insert_mode = "timeout_before"
    assert (await act(env, view, "approve")).status_code == 200
    other = await saved_block(env, env.other_block_id)
    async with env.factory() as database:
        await planning.remove_task(
            database, env.user_id, env.workspace_id, env.task_id, revision=1, request_id=uuid4()
        )
    payload = await private_payload(env, view["id"])
    assert PRIVATE_STUDY not in json.dumps(payload)
    assert OTHER_STUDY in json.dumps(payload)
    assert (await saved_block(env, env.other_block_id)) == other
    assert len((await receipt(env, view["id"]))["study_moves"]) == 1
