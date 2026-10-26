"""Owned allocator certification before private ranked suggestions are published."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_calendar_study_displacement import OTHER_STUDY, PRIVATE_STUDY
from test_calendar_study_displacement import study_env as study_env
from test_student_calendar_consent import calendar_env as calendar_env
from test_student_calendar_events import act
from test_student_calendar_events import event_env as event_env
from test_student_calendar_events import preview as event_preview

from navox.api import student_availability as api
from navox.connectors.sync_state import database_now
from navox.db.models import UserSession
from navox.db.student_calendars import StudentCalendarEvent
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.providers.google_availability import ProviderBusy
from navox.student import service as schedule
from navox.student_availability import service
from navox.student_availability.ranked import ranked_suggestions
from navox.student_availability.schemas import AvailabilityPreview, TimeInterval
from navox.student_availability.selector import (
    MAX_CERTIFIED_CANDIDATES,
    SuggestionRequest,
    suggest,
)
from navox.student_calendars import displacement
from navox.student_calendars import service as calendars
from navox.student_planning import service as planning

ROOT = "/api/v1/student/availability/suggestions"


def choices(env, *, minutes=30, travel="zero"):
    return AvailabilityPreview(
        start_at=env.start_at.replace(hour=7),
        end_at=env.start_at.replace(hour=23),
        duration_minutes=minutes,
        preferences_revision=1,
        expected_timezone="UTC",
        routine_confirmed=True,
        travel_assumption=travel,
    )


async def snapshot(env, **changes):
    return await service.preview(
        env.factory,
        env.settings,
        env.user_id,
        env.workspace_id,
        env.token_hash,
        choices(env, **changes),
    )


async def blocks(env):
    async with env.factory() as database:
        return {
            row.id: planning.block_view(row).model_dump(mode="json")
            for row in await database.scalars(select(StudentStudyBlock))
        }


@pytest.mark.asyncio
async def test_one_owned_context_certifies_exact_study_moves_without_applying_them(
    study_env, monkeypatch
):
    env = study_env
    before = await blocks(env)
    checked = await snapshot(env)
    original_load, original_certify = displacement.load_context, displacement.certify
    loads, contexts = [], []

    async def load(*args, **kwargs):
        context = await original_load(*args, **kwargs)
        loads.append(context)
        return context

    def certify(context, *args, **kwargs):
        contexts.append(context)
        return original_certify(context, *args, **kwargs)

    monkeypatch.setattr(displacement, "load_context", load)
    monkeypatch.setattr(displacement, "certify", certify)
    async with env.factory() as database:
        await calendars.boundary(database, env.user_id, env.workspace_id, env.token_hash)
        request = SuggestionRequest(
            availability=checked.result,
            now=await database_now(database),
            purpose="project_meeting",
            duration_minutes=30,
            other_windows=(TimeInterval(start_at=env.start_at, end_at=env.end_at),),
            require_other_windows=True,
        )
        assert suggest(request).suggestions == ()
        result = await ranked_suggestions(database, env.user_id, env.workspace_id, checked, request)
        assert len(result.suggestions) == 1
        option = result.suggestions[0]
        assert (option.start_at, option.end_at) == (env.start_at, env.end_at)
        assert option.displaced_study_block_ids == (str(env.block_id),)
        assert "moves_study_block" in option.reasons
        assert result.changes_applied is False
        await database.commit()
    assert len(loads) == 1 and contexts and all(context is loads[0] for context in contexts)
    assert len(contexts) <= MAX_CERTIFIED_CANDIDATES
    assert await blocks(env) == before
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentCalendarEvent)) == 0
    assert env.writes.inserts == [] and env.writes.deletes == []


@pytest.mark.asyncio
async def test_actual_route_offers_feasible_lunch_moves_as_unapplied_review_proposals(study_env):
    env = study_env
    before = await blocks(env)
    response = await env.client.post(
        ROOT,
        headers=env.headers,
        json={**choices(env, minutes=60).model_dump(mode="json"), "purpose": "lunch"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    result = response.json()["selection"]
    assert result["suggestions"]
    assert all(
        row["displaced_study_block_ids"] == [str(env.block_id)] for row in result["suggestions"]
    )
    assert all("moves_study_block" in row["reasons"] for row in result["suggestions"])
    assert not result["changes_applied"] and not response.json()["changes_applied"]
    assert PRIVATE_STUDY not in response.text and OTHER_STUDY not in response.text
    assert await blocks(env) == before
    assert not env.writes.inserts


@pytest.mark.asyncio
async def test_infeasible_deadline_replacements_are_never_offered(study_env):
    env = study_env
    env.busy.extra = (
        ProviderBusy(env.start_at.replace(hour=7), env.start_at.replace(hour=11, minute=30)),
    )
    async with env.factory() as database:
        task = await database.get(StudentTask, env.task_id)
        task.due_at = env.start_at.replace(hour=13)
        await database.flush()
        state = await planning.state(database, env.user_id, env.workspace_id)
        state.accepted_source_hash = await planning.stable_source_hash(
            database, env.user_id, env.workspace_id, state
        )
        await database.commit()
    response = await env.client.post(
        ROOT,
        headers=env.headers,
        json={**choices(env, minutes=60).model_dump(mode="json"), "purpose": "lunch"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["selection"]["suggestions"] == []
    assert all(row["revision"] == 1 for row in (await blocks(env)).values())


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", ["coverage", "travel"])
async def test_unknown_information_never_reaches_the_replacement_allocator(
    study_env, monkeypatch, unknown
):
    env = study_env
    if unknown == "coverage":
        env.busy.state = "partial"
    else:
        env.busy.extra = (
            ProviderBusy(env.start_at.replace(hour=9), env.start_at.replace(hour=10)),
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError("Unknown source data cannot certify a study replacement")

    monkeypatch.setattr(displacement, "load_context", forbidden)
    response = await env.client.post(
        ROOT,
        headers=env.headers,
        json={
            **choices(env, travel="unknown" if unknown == "travel" else "zero").model_dump(
                mode="json"
            ),
            "purpose": "coffee",
        },
    )
    assert response.status_code == 200, response.text
    assert not any(
        row["displaced_study_block_ids"] for row in response.json()["selection"]["suggestions"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["certified", "study_moves", "displaced_study_block_ids"])
async def test_browser_cannot_submit_a_replacement_certificate(study_env, field):
    env = study_env
    response = await env.client.post(
        ROOT,
        headers=env.headers,
        json={**choices(env).model_dump(mode="json"), field: "private-forged-certificate"},
    )
    assert response.status_code == 422 and "private-forged-certificate" not in response.text
    assert not env.writes.inserts


@pytest.mark.asyncio
async def test_publication_rechecks_session_after_feasibility_pass(study_env, monkeypatch):
    env = study_env
    original = api.ranked_suggestions

    async def revoked(database, *args, **kwargs):
        result = await original(database, *args, **kwargs)
        session = await database.scalar(
            select(UserSession).where(UserSession.token_hash == env.token_hash)
        )
        session.revoked_at = await database_now(database)
        await database.flush()
        return result

    monkeypatch.setattr(api, "ranked_suggestions", revoked)
    response = await env.client.post(
        ROOT, headers=env.headers, json=choices(env).model_dump(mode="json")
    )
    assert response.status_code == 401, response.text
    assert not env.writes.inserts


@pytest.mark.asyncio
async def test_owned_context_and_expiry_are_required_even_for_internal_callers(study_env):
    env = study_env
    checked = await snapshot(env)
    request = SuggestionRequest(
        availability=checked.result,
        now=checked.checked_at,
        purpose="coffee",
        duration_minutes=30,
    )
    async with env.factory() as database:
        with pytest.raises(schedule.StudentError, match="account"):
            await ranked_suggestions(database, uuid4(), env.workspace_id, checked, request)
        with pytest.raises(schedule.StudentError, match="account"):
            await ranked_suggestions(
                database,
                env.user_id,
                env.workspace_id,
                checked,
                request.model_copy(update={"now": checked.expires_at}),
            )


@pytest.mark.asyncio
async def test_retained_native_event_claims_block_a_lagging_provider_answer(event_env):
    env = event_env
    view = await event_preview(env)
    created = await act(env, view, "approve")
    assert created.status_code == 200, created.text
    env.writes.events.clear()  # Busy-time propagation/absence is not an exact receipt observation.
    checked = await snapshot(env)
    native = [row for row in checked.input.busy if row.source_id == service.NATIVE_CALENDAR]
    assert len(native) == 1
    assert native[0].start_at == env.start_at - timedelta(minutes=10)
    assert native[0].end_at == env.end_at + timedelta(minutes=10)
    assert service.NATIVE_CALENDAR in checked.input.required_source_ids
    assert any(row.source_id == service.NATIVE_CALENDAR for row in checked.source_summary)
    selected = suggest(
        SuggestionRequest(
            availability=checked.result,
            now=checked.checked_at,
            purpose="other",
            duration_minutes=30,
        )
    )
    assert all(
        row.end_at + timedelta(minutes=10) <= native[0].start_at
        or row.start_at - timedelta(minutes=10) >= native[0].end_at
        for row in selected.suggestions
    )
