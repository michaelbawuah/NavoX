"""Bounded owned pagination keeps older uncertain actions discoverable."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_student_calendar_consent import BASE
from test_student_calendar_consent import calendar_env as calendar_env
from test_student_calendar_events import event_env as event_env
from test_student_calendar_events import preview

from navox.db.student_calendars import StudentCalendarEvent


@pytest.mark.asyncio
async def test_activity_pages_prioritize_older_held_requests_and_cover_every_owned_row(event_env):
    env = event_env
    previews = [await preview(env, title=f"Private review {index}") for index in range(25)]
    async with env.factory() as database:
        held = await database.get(StudentCalendarEvent, UUID(previews[0]["id"]))
        held.status, held.revision = "uncertain", 2
        await database.commit()
    first = await env.client.get(BASE + "/events", headers=env.headers)
    assert first.status_code == 200
    body = first.json()
    assert body["events"][0]["id"] == previews[0]["id"]
    assert body["events"][0]["changes_applied"] is None
    assert len(body["events"]) == 20 and body["next_cursor"]
    second = await env.client.get(
        BASE + "/events", params={"cursor": body["next_cursor"]}, headers=env.headers
    )
    assert second.status_code == 200
    tail = second.json()
    assert len(tail["events"]) == 5 and tail["next_cursor"] is None
    ids = [row["id"] for row in body["events"] + tail["events"]]
    assert len(ids) == len(set(ids)) == 25
    assert set(ids) == {row["id"] for row in previews}
    assert env.writes.inserts == [] and env.writes.deletes == []


@pytest.mark.asyncio
async def test_unknown_stale_and_malformed_pagination_cursors_are_private(event_env):
    env = event_env
    view = await preview(env)
    unknown = await env.client.get(
        BASE + "/events", params={"cursor": str(uuid4())}, headers=env.headers
    )
    assert unknown.status_code == 409
    assert unknown.headers["cache-control"] == "private, no-store"
    malformed = await env.client.get(
        BASE + "/events", params={"cursor": "private-cursor-never-echo"}, headers=env.headers
    )
    assert malformed.status_code == 422 and "private-cursor-never-echo" not in malformed.text
    async with env.factory() as database:
        row = await database.scalar(
            select(StudentCalendarEvent).where(StudentCalendarEvent.id == UUID(view["id"]))
        )
        await database.delete(row)
        await database.commit()
    stale = await env.client.get(
        BASE + "/events", params={"cursor": view["id"]}, headers=env.headers
    )
    assert stale.status_code == 409
    assert env.writes.inserts == [] and env.writes.deletes == []


@pytest.mark.asyncio
async def test_distinct_overlapping_approvals_cannot_reuse_another_actions_preinsert_snapshot(
    event_env, monkeypatch
):
    import asyncio

    from test_student_calendar_events import act

    from navox.student_calendars import scheduling

    env = event_env
    first = await preview(env, title="First approved meeting")
    second = await preview(env, title="Second approved meeting")
    checked, release = asyncio.Event(), asyncio.Event()
    original = scheduling._free

    async def hold_second(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[-1].title == "Second approved meeting":
            checked.set()
            await release.wait()
        return result

    monkeypatch.setattr(scheduling, "_free", hold_second)
    pending = asyncio.create_task(act(env, second, "approve"))
    try:
        await asyncio.wait_for(checked.wait(), 10)
        created = await act(env, first, "approve")
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "created"
        release.set()
        stale = await asyncio.wait_for(pending, 10)
        assert stale.status_code == 409, stale.text
        assert len(env.writes.inserts) == 1
        assert env.writes.inserts[0]["summary"] == "First approved meeting"
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_known_receipt_get_recovers_fresh_authority_after_lost_admission_response(event_env):
    from test_student_calendar_events import act

    env = event_env
    pending = await preview(env)
    created = await act(env, pending, "approve")
    assert created.status_code == 200 and created.json()["status"] == "created"
    assert created.json()["calendar_revision"] > pending["calendar_revision"]
    assert (await act(env, pending, "reconcile")).status_code == 409
    before = len(env.writes.reads)
    recovered = await env.client.get(BASE + "/events/" + pending["id"], headers=env.headers)
    assert recovered.status_code == 200
    assert recovered.json()["digest"] == pending["digest"]
    assert recovered.json()["calendar_revision"] == created.json()["calendar_revision"]
    assert len(env.writes.reads) == before
    checked = await act(env, recovered.json(), "reconcile")
    assert checked.status_code == 200 and checked.json()["status"] == "created"
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []


@pytest.mark.asyncio
async def test_concurrent_observer_cannot_replace_the_original_insert_version_or_offer_undo(
    event_env, monkeypatch
):
    import asyncio

    from test_student_calendar_events import act

    from navox.student_calendars import scheduling

    env = event_env
    pending_view = await preview(env)
    entered, release = asyncio.Event(), asyncio.Event()
    original = scheduling._pin_creation

    async def hold_original_version(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(scheduling, "_pin_creation", hold_original_version)
    pending = asyncio.create_task(act(env, pending_view, "approve"))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        env.writes.events[UUID(pending_view["id"]).hex]["etag"] = '"externally-edited-v2"'
        native = await env.client.get(BASE + "/events/" + pending_view["id"], headers=env.headers)
        observed = await act(env, native.json(), "reconcile")
        assert observed.status_code == 200
        assert observed.json()["status"] == "created" and observed.json()["can_undo"] is False
        assert (await act(env, observed.json(), "undo")).status_code == 409
        assert env.writes.deletes == []
        release.set()
        returned = await asyncio.wait_for(pending, 10)
        assert returned.status_code == 200, returned.text
        assert returned.json()["status"] == "changed" and returned.json()["can_undo"] is False
        assert (await act(env, returned.json(), "undo")).status_code == 409
        assert len(env.writes.inserts) == 1 and env.writes.deletes == []
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_get_only_confirmation_after_lost_insert_response_never_authorizes_undo(event_env):
    from test_student_calendar_events import act

    env = event_env
    env.writes.insert_mode = "timeout_after"
    response = await act(env, await preview(env), "approve")
    assert response.status_code == 200
    view = response.json()
    assert view["status"] == "created" and view["changes_applied"] is True
    assert view["can_undo"] is False and "original creation version" in view["message"]
    assert (await act(env, view, "undo")).status_code == 409
    assert len(env.writes.inserts) == 1 and env.writes.deletes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "undo"])
async def test_saturated_calendar_admission_revision_never_submits_another_side_effect(
    event_env, action
):
    from test_student_calendar_events import act, created

    from navox.db.student_calendars import StudentCalendarState
    from navox.student_calendars.schemas import MAX_REVISION

    env = event_env
    view = await created(env) if action == "undo" else await preview(env)
    async with env.factory() as database:
        state = (await database.scalars(select(StudentCalendarState))).one()
        state.revision = MAX_REVISION
        await database.commit()
    recovered = await env.client.get(BASE + "/events/" + view["id"], headers=env.headers)
    assert recovered.status_code == 200
    before = (
        len(env.writes.inserts),
        len(env.writes.deletes),
        len(env.writes.targets),
        len(env.writes.reads),
    )
    denied = await act(env, recovered.json(), action)
    assert denied.status_code == 409
    assert (
        len(env.writes.inserts),
        len(env.writes.deletes),
        len(env.writes.targets),
        len(env.writes.reads),
    ) == before
