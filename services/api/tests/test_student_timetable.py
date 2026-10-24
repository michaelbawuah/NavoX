from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from navox.api import student
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.models import AuditEvent, User, Workspace
from navox.db.session import get_database_session
from navox.db.student import StudentClassMeeting, StudentCourse, StudentImportPreview
from navox.student import service

MONDAY = datetime(2026, 10, 19, 14, 30, tzinfo=UTC)  # 10:30 New York
COURSE = {
    "code": "CS 2110",
    "name": "Programming",
    "term_start": "2026-08-20",
    "term_end": "2026-12-10",
    "professor": "Professor Example",
}
MEETING = {
    "kind": "lecture",
    "weekdays": [0, 2],
    "start_time": "10:00",
    "end_time": "11:00",
    "timezone": "America/New_York",
    "building": "Science Hall",
    "room": "101",
    "instructor": None,
    "walk_minutes": 9,
}


@pytest.mark.asyncio
async def test_exam_is_one_reviewed_date_and_omitted_dates_survive_edit(student_env):
    await register(student_env)
    course = await create_course(student_env)
    exam = await create_meeting(
        student_env,
        course,
        kind="exam",
        dates=["2026-10-25"],
        weekdays=[6],
        start_time="08:40",
        end_time="10:00",
    )
    response = await student_env.client.get("/api/v1/student/timetable?date=2026-10-25&days=8")
    assert response.status_code == 200, response.text
    occurrences = response.json()["occurrences"]
    assert len(occurrences) == 1
    assert occurrences[0]["kind"] == "exam"
    assert occurrences[0]["start_at"].startswith("2026-10-25")
    changed = await student_env.client.patch(
        f"/api/v1/student/meetings/{exam['id']}",
        json={
            **MEETING,
            "kind": "exam",
            "weekdays": [6],
            "revision": exam["revision"],
            "room": "202",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["dates"] == ["2026-10-25"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"dates": ["2027-10-25"]},
        {"dates": ["2026-10-25"], "weekdays": [0]},
        {"dates": ["2026-10-25", "2026-11-01"]},
    ],
)
async def test_exam_cannot_silently_recur_or_leave_term(student_env, changes):
    await register(student_env)
    course = await create_course(student_env)
    response = await student_env.client.post(
        f"/api/v1/student/courses/{course['id']}/meetings",
        json={**MEETING, "kind": "exam", "weekdays": [6], **changes},
    )
    assert response.status_code == 422, response.text
    saved = await student_env.client.get("/api/v1/student/timetable?date=2026-10-25&days=8")
    assert saved.json()["occurrences"] == []


@pytest_asyncio.fixture
async def student_env(ai_database, monkeypatch):
    settings = Settings(_env_file=None, app_environment="test", ai_provider="disabled")

    async def database():
        async with ai_database() as session:
            yield session

    async def clock(database):
        return MONDAY

    monkeypatch.setattr(service, "database_now", clock)
    app = create_app()
    if not any(getattr(route, "path", "") == "/api/v1/student/profile" for route in app.routes):
        app.include_router(student.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        yield SimpleNamespace(client=client, factory=ai_database, app=app)


async def register(env, email="student@example.com"):
    response = await env.client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "twelve-character-password", "display_name": "Student"},
    )
    assert response.status_code == 201, response.text
    account = response.json()
    async with env.factory() as database:
        user = await database.get(User, UUID(account["id"]))
        user.timezone = "America/New_York"
        user.agent_paused = True
        await database.commit()
    return account


async def create_course(env, **changes):
    response = await env.client.post("/api/v1/student/courses", json={**COURSE, **changes})
    assert response.status_code == 201, response.text
    return response.json()


async def create_meeting(env, course, **changes):
    response = await env.client.post(
        f"/api/v1/student/courses/{course['id']}/meetings", json={**MEETING, **changes}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_readonly_empty_schedule_and_profile_work_with_ai_disabled_and_paused(
    student_env, monkeypatch
):
    await register(student_env)

    async def no_runtime(*args, **kwargs):
        pytest.fail("Student timetable must not construct a model runtime")

    from navox.ai import configured

    monkeypatch.setattr(configured, "build_runtime", no_runtime)
    async with student_env.factory() as database:
        audits = await database.scalar(select(func.count()).select_from(AuditEvent))
    profile = (await student_env.client.get("/api/v1/student/profile")).json()
    assert profile["revision"] == 0 and profile["timezone"] == "America/New_York"
    assert (await student_env.client.get("/api/v1/student/timetable")).json()["occurrences"] == []
    lookup = (await student_env.client.get("/api/v1/student/lookup?mode=now")).json()
    assert lookup["server_now"] == lookup["at"] == "2026-10-19T14:30:00Z"
    assert lookup["reason"] == "no_classes" and lookup["next_upcoming"] is None
    async with student_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AuditEvent)) == audits


@pytest.mark.asyncio
async def test_any_school_profile_updates_authoritative_timezone_and_checks_revision(student_env):
    await register(student_env)
    payload = {
        "school_name": "Any College",
        "lms": "moodle",
        "display_name": "M",
        "timezone": "Europe/London",
        "term_start": "2026-09-01",
        "term_end": "2026-12-20",
        "revision": 0,
    }
    response = await student_env.client.put("/api/v1/student/profile", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert (await student_env.client.get("/api/v1/student/timetable")).json()[
        "timezone"
    ] == "Europe/London"
    assert (
        await student_env.client.put("/api/v1/student/profile", json=payload)
    ).status_code == 409
    payload["timezone"] = "Invented/Zone"
    assert (
        await student_env.client.put("/api/v1/student/profile", json=payload)
    ).status_code == 422


@pytest.mark.asyncio
async def test_now_next_at_and_day_are_data_derived_with_exact_end_boundary(student_env):
    await register(student_env)
    course = await create_course(student_env)
    meeting = await create_meeting(student_env, course)
    await create_meeting(
        student_env,
        course,
        kind="lab",
        weekdays=[0],
        start_time="12:00",
        end_time="13:00",
        room="210",
    )
    now = (
        await student_env.client.get("/api/v1/student/lookup?mode=now&date=2000-01-01&time=00:00")
    ).json()
    assert [item["meeting_id"] for item in now["occurrences"]] == [meeting["id"]]
    assert now["date"] == "2026-10-19"
    next_class = (await student_env.client.get("/api/v1/student/lookup?mode=next")).json()
    assert next_class["occurrences"][0]["kind"] == "lab"
    at_start = (
        await student_env.client.get("/api/v1/student/lookup?mode=at&date=2026-10-19&time=10:00")
    ).json()
    at_end = (
        await student_env.client.get("/api/v1/student/lookup?mode=at&date=2026-10-19&time=11:00")
    ).json()
    assert len(at_start["occurrences"]) == 1 and at_end["occurrences"] == []
    assert (
        len(
            (await student_env.client.get("/api/v1/student/lookup?mode=day&weekday=0")).json()[
                "occurrences"
            ]
        )
        == 2
    )
    assert (await student_env.client.get("/api/v1/student/lookup?mode=day&day_offset=1")).json()[
        "occurrences"
    ] == []
    assert (await student_env.client.get("/api/v1/student/lookup?mode=at")).status_code == 422
    assert (
        await student_env.client.get("/api/v1/student/lookup?mode=day&date=2026-10-19&day_offset=0")
    ).status_code == 422


@pytest.mark.asyncio
async def test_after_last_and_weekend_fallback_never_says_future_day_is_today(student_env):
    account = await register(student_env)
    course = await create_course(student_env)
    await create_meeting(student_env, course)
    async with student_env.factory() as database:
        after = await service.lookup(
            database,
            UUID(account["id"]),
            UUID(account["workspace"]["id"]),
            mode="next",
            now=datetime(2026, 10, 19, 23, 0, tzinfo=UTC),
        )
        assert after.occurrences == [] and after.reason == "after_last_class"
        assert after.next_upcoming.start_at == datetime(2026, 10, 21, 14, tzinfo=UTC)
        weekend = await service.lookup(
            database,
            UUID(account["id"]),
            UUID(account["workspace"]["id"]),
            mode="now",
            now=datetime(2026, 10, 24, 16, tzinfo=UTC),
        )
        assert weekend.occurrences == [] and weekend.reason == "no_classes"
        assert weekend.next_upcoming.start_at == datetime(2026, 10, 26, 14, tzinfo=UTC)


@pytest.mark.asyncio
async def test_conflicts_include_class_and_work_shift_without_selecting_one(student_env):
    await register(student_env)
    first = await create_course(student_env)
    second = await create_course(student_env, code="MATH 2940", name="Math")
    await create_meeting(student_env, first)
    await create_meeting(student_env, second, start_time="10:15", end_time="11:15")
    response = await student_env.client.post(
        "/api/v1/student/fixed-blocks",
        json={
            "title": "Library shift",
            "weekdays": [0],
            "start_time": "09:00",
            "end_time": "10:45",
            "timezone": "America/New_York",
            "term_start": COURSE["term_start"],
            "term_end": COURSE["term_end"],
        },
    )
    assert response.status_code == 201, response.text
    lookup = (await student_env.client.get("/api/v1/student/lookup?mode=now")).json()
    assert len(lookup["occurrences"]) == 2
    assert {item["kind"] for item in lookup["conflicts"]} == {"class_class", "class_fixed"}
    view = (await student_env.client.get("/api/v1/student/timetable?date=2026-10-19&days=1")).json()
    assert len(view["fixed_occurrences"]) == 1


@pytest.mark.asyncio
async def test_one_off_room_time_change_cancellation_restore_and_stale_writes(student_env):
    await register(student_env)
    course = await create_course(student_env)
    meeting = await create_meeting(student_env, course)
    path = f"/api/v1/student/meetings/{meeting['id']}/exceptions/2026-10-19"
    response = await student_env.client.put(
        path,
        json={
            "revision": 1,
            "cancelled": False,
            "start_time": "11:00",
            "end_time": "12:00",
            "building": "Online",
            "room": None,
        },
    )
    assert response.status_code == 200, response.text
    now = (await student_env.client.get("/api/v1/student/lookup?mode=now")).json()
    assert now["occurrences"] == [] and now["next_upcoming"]["building"] == "Online"
    assert (
        await student_env.client.put(path, json={"revision": 1, "cancelled": True})
    ).status_code == 409
    assert (
        await student_env.client.put(path, json={"revision": 2, "cancelled": True})
    ).status_code == 200
    assert (
        await student_env.client.get("/api/v1/student/lookup?mode=at&date=2026-10-19&time=11:30")
    ).json()["occurrences"] == []
    table = (
        await student_env.client.get("/api/v1/student/timetable?date=2026-10-19&days=1")
    ).json()
    assert table["occurrences"][0]["cancelled"] is True
    assert table["exceptions"][0]["date"] == "2026-10-19"
    assert (await student_env.client.delete(path + "?revision=3")).status_code == 200
    assert (
        len((await student_env.client.get("/api/v1/student/lookup?mode=now")).json()["occurrences"])
        == 1
    )
    assert (
        await student_env.client.put(
            path.replace("2026-10-19", "2026-10-20"), json={"revision": 4, "cancelled": True}
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_break_and_archive_remove_classes_from_lookup_without_deleting_records(student_env):
    await register(student_env)
    course = await create_course(student_env)
    await create_meeting(student_env, course)
    saved_break = await student_env.client.post(
        "/api/v1/student/breaks",
        json={"name": "Fall break", "start_date": "2026-10-19", "end_date": "2026-10-20"},
    )
    assert saved_break.status_code == 201
    lookup = (await student_env.client.get("/api/v1/student/lookup?mode=now")).json()
    assert lookup["reason"] == "break" and lookup["occurrences"] == []
    assert lookup["next_upcoming"]["start_at"] == "2026-10-21T14:00:00Z"
    response = await student_env.client.patch(
        f"/api/v1/student/courses/{course['id']}", json={**COURSE, "revision": 1, "archived": True}
    )
    assert response.status_code == 200
    assert (await student_env.client.get("/api/v1/student/courses")).json()["courses"] == []
    archived = (await student_env.client.get("/api/v1/student/courses?archived=true")).json()[
        "courses"
    ]
    assert len(archived[0]["meetings"]) == 1
    assert (await student_env.client.get("/api/v1/student/lookup?mode=now")).json()[
        "next_upcoming"
    ] is None
    response = await student_env.client.patch(
        f"/api/v1/student/courses/{course['id']}", json={**COURSE, "revision": 2, "archived": False}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dst_keeps_wall_clock_time_and_at_lookup_rejects_gap_and_fold(student_env):
    await register(student_env)
    course = await create_course(student_env)
    await create_meeting(student_env, course, weekdays=[0])
    before = (
        await student_env.client.get("/api/v1/student/timetable?date=2026-10-26&days=1")
    ).json()["occurrences"][0]
    after = (
        await student_env.client.get("/api/v1/student/timetable?date=2026-11-02&days=1")
    ).json()["occurrences"][0]
    assert before["start_at"] == "2026-10-26T14:00:00Z"
    assert after["start_at"] == "2026-11-02T15:00:00Z"
    for path in ("date=2026-11-01&time=01:30", "date=2027-03-14&time=02:30"):
        assert (
            await student_env.client.get("/api/v1/student/lookup?mode=at&" + path)
        ).status_code == 422


@pytest.mark.asyncio
async def test_dst_gap_class_returns_actionable_error_instead_of_false_empty_lookup(
    student_env, monkeypatch
):
    await register(student_env)
    course = await create_course(student_env, term_start="2027-03-01", term_end="2027-04-01")
    meeting = await create_meeting(
        student_env, course, weekdays=[6], start_time="02:30", end_time="03:30"
    )

    async def clock(_database):
        return datetime(2027, 3, 14, 7, 15, tzinfo=UTC)

    monkeypatch.setattr(service, "database_now", clock)
    table = await student_env.client.get("/api/v1/student/timetable?date=2027-03-14&days=1")
    assert table.status_code == 200 and table.json()["occurrences"] == []
    assert any("clock change" in item for item in table.json()["warnings"])
    for query in (
        "mode=now",
        "mode=next",
        "mode=day&date=2027-03-14",
        "mode=at&date=2027-03-14&time=03:15",
    ):
        result = await student_env.client.get("/api/v1/student/lookup?" + query)
        assert result.status_code == 409, result.text
        assert "2027-03-14" in result.json()["detail"]
        assert "one-day time correction" in result.json()["detail"]
    corrected = await student_env.client.put(
        f"/api/v1/student/meetings/{meeting['id']}/exceptions/2027-03-14",
        json={"revision": 1, "start_time": "03:30", "end_time": "04:30"},
    )
    assert corrected.status_code == 200, corrected.text
    repaired = await student_env.client.get("/api/v1/student/lookup?mode=next")
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["next_upcoming"]["start_at"] == "2027-03-14T07:30:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("days_before", [2, 42])
async def test_future_search_refuses_to_skip_incomplete_class_before_a_later_valid_one(
    student_env,
    monkeypatch,
    days_before,
):
    from datetime import timedelta

    await register(student_env)
    gap = await create_course(student_env, term_start="2027-03-14", term_end="2027-03-14")
    await create_meeting(student_env, gap, weekdays=[6], start_time="02:30", end_time="03:30")
    later = await create_course(
        student_env, code="MATH 2940", name="Math", term_start="2027-03-15", term_end="2027-04-01"
    )
    await create_meeting(student_env, later, weekdays=[0], start_time="09:00", end_time="10:00")

    async def clock(_database):
        return datetime(2027, 3, 14, 5, tzinfo=UTC) - timedelta(days=days_before)

    monkeypatch.setattr(service, "database_now", clock)
    result = await student_env.client.get("/api/v1/student/lookup?mode=next")
    assert result.status_code == 409, result.text
    assert "2027-03-14" in result.json()["detail"]


@pytest.mark.asyncio
async def test_known_next_class_before_a_future_gap_is_still_returned(student_env, monkeypatch):
    await register(student_env)
    course = await create_course(student_env, term_start="2027-03-01", term_end="2027-04-01")
    await create_meeting(student_env, course, weekdays=[6], start_time="02:30", end_time="03:30")
    await create_meeting(student_env, course, weekdays=[5], start_time="09:00", end_time="10:00")

    async def clock(_database):
        return datetime(2027, 3, 12, 20, tzinfo=UTC)

    monkeypatch.setattr(service, "database_now", clock)
    result = await student_env.client.get("/api/v1/student/lookup?mode=next")
    assert result.status_code == 200, result.text
    assert result.json()["next_upcoming"]["start_at"] == "2027-03-13T14:00:00Z"


@pytest.mark.asyncio
@pytest.mark.parametrize("excluded", ["cancelled", "break"])
async def test_cancelled_or_break_gap_class_does_not_make_lookup_incomplete(
    student_env,
    monkeypatch,
    excluded,
):
    await register(student_env)
    course = await create_course(student_env, term_start="2027-03-14", term_end="2027-03-14")
    meeting = await create_meeting(
        student_env, course, weekdays=[6], start_time="02:30", end_time="03:30"
    )
    if excluded == "cancelled":
        response = await student_env.client.put(
            f"/api/v1/student/meetings/{meeting['id']}/exceptions/2027-03-14",
            json={"revision": 1, "cancelled": True},
        )
        assert response.status_code == 200, response.text
    else:
        response = await student_env.client.post(
            "/api/v1/student/breaks",
            json={
                "name": "Spring break",
                "start_date": "2027-03-14",
                "end_date": "2027-03-14",
            },
        )
        assert response.status_code == 201, response.text

    async def clock(_database):
        return datetime(2027, 3, 14, 7, 15, tzinfo=UTC)

    monkeypatch.setattr(service, "database_now", clock)
    result = await student_env.client.get("/api/v1/student/lookup?mode=now")
    assert result.status_code == 200, result.text
    assert result.json()["occurrences"] == [] and result.json()["next_upcoming"] is None


@pytest.mark.asyncio
async def test_fixed_block_gap_warning_does_not_poison_a_complete_class_lookup(
    student_env, monkeypatch
):
    await register(student_env)
    course = await create_course(student_env, term_start="2027-03-01", term_end="2027-04-01")
    await create_meeting(student_env, course, weekdays=[0], start_time="09:00", end_time="10:00")
    fixed = await student_env.client.post(
        "/api/v1/student/fixed-blocks",
        json={
            "title": "Campus shift",
            "weekdays": [6],
            "start_time": "02:30",
            "end_time": "03:30",
            "timezone": "America/New_York",
            "term_start": "2027-03-01",
            "term_end": "2027-04-01",
        },
    )
    assert fixed.status_code == 201, fixed.text

    async def clock(_database):
        return datetime(2027, 3, 14, 7, 15, tzinfo=UTC)

    monkeypatch.setattr(service, "database_now", clock)
    table = (
        await student_env.client.get("/api/v1/student/timetable?date=2027-03-14&days=1")
    ).json()
    assert any("fixed block" in warning for warning in table["warnings"])
    result = await student_env.client.get("/api/v1/student/lookup?mode=next")
    assert result.status_code == 200, result.text
    assert result.json()["next_upcoming"]["start_at"] == "2027-03-15T13:00:00Z"


@pytest.mark.asyncio
async def test_timetable_requires_auth_owner_and_personal_workspace(student_env):
    assert (await student_env.client.get("/api/v1/student/timetable")).status_code == 401
    first = await register(student_env)
    course = await create_course(student_env)
    meeting = await create_meeting(student_env, course)
    await register(student_env, "other-student@example.com")
    assert (await student_env.client.get("/api/v1/student/timetable")).json()["courses"] == []
    assert (
        await student_env.client.patch(
            f"/api/v1/student/meetings/{meeting['id']}", json={**MEETING, "revision": 1}
        )
    ).status_code == 404
    assert (
        await student_env.client.post(
            f"/api/v1/student/courses/{course['id']}/meetings", json=MEETING
        )
    ).status_code == 404
    async with student_env.factory() as database:
        retained = await database.get(StudentCourse, UUID(course["id"]))
        assert retained.user_id == UUID(first["id"])
        user = await database.scalar(select(User).where(User.email == "other-student@example.com"))
        workspace = await database.scalar(
            select(Workspace)
            .join(StudentCourse, StudentCourse.workspace_id == Workspace.id)
            .where(StudentCourse.user_id == UUID(first["id"]))
        )
        assert user is not None and workspace is not None
        # Change only current other student's workspace type.
        from navox.db.models import WorkspaceMembership

        other_workspace = await database.scalar(
            select(Workspace)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(WorkspaceMembership.user_id == user.id)
        )
        other_workspace.workspace_type = "team"
        await database.commit()
    assert (await student_env.client.get("/api/v1/student/timetable")).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"weekdays": [True]},
        {"weekdays": [7]},
        {"weekdays": [0, 0]},
        {"end_time": "09:00"},
        {"start_time": "25:00"},
        {"timezone": "Unknown"},
        {"walk_minutes": 121},
    ],
)
async def test_manual_meetings_reject_invalid_timing(student_env, change):
    await register(student_env)
    course = await create_course(student_env)
    response = await student_env.client.post(
        f"/api/v1/student/courses/{course['id']}/meetings", json={**MEETING, **change}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reset_is_explicit_idempotent_owner_only_and_forgets_preview_copies(student_env):
    account = await register(student_env)
    course = await create_course(student_env)
    await create_meeting(student_env, course)
    request_id = str(uuid4())
    assert (
        await student_env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": False, "revision": 0, "request_id": request_id},
        )
    ).status_code == 422
    response = await student_env.client.post(
        "/api/v1/student/data/reset",
        json={"confirmed": True, "revision": 0, "request_id": request_id},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"cleared": True, "reused": False}
    assert (
        await student_env.client.post(
            "/api/v1/student/data/reset",
            json={"confirmed": True, "revision": 0, "request_id": request_id},
        )
    ).json()["reused"] is True
    assert (await student_env.client.get("/api/v1/student/timetable")).json()["courses"] == []
    async with student_env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentClassMeeting)) == 0
        assert await database.scalar(select(func.count()).select_from(StudentImportPreview)) == 0
        assert await database.get(User, UUID(account["id"])) is not None
        events = list(
            await database.scalars(
                select(AuditEvent).where(AuditEvent.event_type.like("student.%"))
            )
        )
        assert all(
            "Programming" not in str(item.event_metadata)
            and "Science Hall" not in str(item.event_metadata)
            for item in events
        )
