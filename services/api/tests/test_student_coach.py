"""Real owned data, explicit sources, truthful periods and private manual reads."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import event

from navox.api.main import create_app
from navox.connectors.builtin.google_gmail import GMAIL_MANIFEST
from navox.core.settings import Settings, get_settings
from navox.db.lms_feeds import LmsFeed, LmsFeedItem, LmsFeedSource
from navox.db.models import (
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    User,
)
from navox.db.session import get_database_session
from navox.db.student import (
    StudentClassMeeting,
    StudentCourse,
    StudentFixedBlock,
    StudentMeetingException,
)
from navox.db.student_email import EmailTaskSource
from navox.db.student_goals import StudentGoal, StudentGoalCheckIn
from navox.db.student_planning import StudentStudyBlock, StudentTask
from navox.providers.google_sources import GMAIL_READ_SCOPE
from navox.student import service as schedule
from navox.student_coach import service
from navox.student_email.service import source_authority_hash
from navox.student_goals import service as goals
from navox.student_goals.schemas import GoalDefinition

NOW = datetime(2026, 10, 21, 13, tzinfo=UTC)
ROOT = "/api/v1/student/coach/review"


@pytest_asyncio.fixture
async def coach_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    ).model_copy(
        update={
            "student_coach_enabled": True,
            "student_planning_enabled": True,
            "student_goals_enabled": True,
        }
    )
    clock = SimpleNamespace(now=NOW)

    async def now(_database):
        return clock.now

    async def database():
        async with ai_database() as session:
            yield session

    monkeypatch.setattr(service, "database_now", now)
    app = create_app()
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "coach@example.com",
                "password": "twelve-character-password",
                "display_name": "Student",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        user, workspace = UUID(data["id"]), UUID(data["workspace"]["id"])
        async with ai_database() as session:
            row = await session.get(User, user)
            row.timezone, row.agent_paused = "America/New_York", True
            await session.commit()
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            settings=settings,
            clock=clock,
            user_id=user,
            workspace_id=workspace,
            app=app,
        )


async def current(env, kind="morning"):
    response = await env.client.get(ROOT, params={"kind": kind})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_real_application_route_defaults_off_before_auth_or_validation():
    settings = Settings(_env_file=None, app_environment="test")
    assert settings.student_coach_enabled is False
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(ROOT, params={"kind": "private invalid input"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Student day reviews are not enabled"}
    assert response.headers["cache-control"] == "private, no-store"
    assert "private invalid input" not in response.text


async def task(env, **changes):
    row = StudentTask(
        **{
            "id": uuid4(),
            "user_id": env.user_id,
            "workspace_id": env.workspace_id,
            "title": "My saved assignment",
            "estimated_minutes": 60,
            "remaining_minutes": 60,
            "actual_minutes": 0,
            "status": "active",
            "revision": 1,
            "difficulty": 3,
            "priority": 3,
            "course_weight": 1,
            **changes,
        }
    )
    async with env.factory() as database:
        database.add(row)
        await database.commit()
    return row.id


async def meeting(env, *, start="10:00", end="11:00", weekday=2, fixed=False):
    async with env.factory() as database:
        if fixed:
            row = StudentFixedBlock(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                title="Saved shift",
                weekdays=[weekday],
                start_time=start,
                end_time=end,
                timezone="America/New_York",
                term_start=date(2026, 1, 1),
                term_end=date(2026, 12, 31),
                revision=1,
            )
            database.add(row)
            course_id = None
        else:
            course = StudentCourse(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code="SYN 100",
                name="Saved course",
                term_start=date(2026, 1, 1),
                term_end=date(2026, 12, 31),
                archived=False,
                revision=1,
            )
            database.add(course)
            await database.flush()
            row = StudentClassMeeting(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                course_id=course.id,
                kind="lecture",
                weekdays=[weekday],
                start_time=start,
                end_time=end,
                timezone="America/New_York",
                revision=1,
                building="Saved building",
                room="120",
                walk_minutes=None,
            )
            database.add(row)
            course_id = course.id
        await database.commit()
        return row.id, course_id


async def outcome(env, task_id, status, *, when=None, minutes=15):
    async with env.factory() as database:
        database.add(
            StudentStudyBlock(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                task_id=task_id,
                plan_id=uuid4(),
                title="Private block title",
                start_at=NOW - timedelta(hours=2),
                end_at=NOW - timedelta(hours=1),
                minutes=60,
                actual_minutes=minutes,
                status=status,
                revision=1,
                focus_window=False,
                outcome_at=when or env.clock.now,
            )
        )
        await database.commit()


async def goal(env, *, count=None, report=False):
    definition = GoalDefinition(
        title="My self-reported goal",
        category="personal",
        tracking="count" if count is not None else "qualitative",
        cadence="total",
        target=3 if count is not None else None,
        unit="calls" if count is not None else None,
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 12, 1),
    )
    row = StudentGoal(
        id=uuid4(),
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        status="active",
        revision=1,
        definition_revision=1,
    )
    row.encrypted_payload = goals.seal(row, definition.model_dump(mode="json"), env.settings)
    async with env.factory() as database:
        database.add(row)
        await database.flush()
        if report:
            item = StudentGoalCheckIn(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                goal_id=row.id,
                on_date=date(2026, 10, 20),
                revision=1,
                recorded_at=NOW - timedelta(days=1),
            )
            item.encrypted_payload = goals.seal(
                item,
                {
                    "reported_timezone": "America/New_York",
                    "count": count,
                    "completed": False if count is None else None,
                    "note": "Private note is not review content",
                    "goal_revision_when_reported": 1,
                },
                env.settings,
            )
            database.add(item)
        await database.commit()
    return row.id


@pytest.mark.asyncio
async def test_manual_review_works_while_ai_paused_without_model_or_writes(coach_env, monkeypatch):
    env = coach_env
    from navox.ai import configured

    def forbidden(*_args, **_kwargs):
        pytest.fail("Manual coach must not configure a model")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    await meeting(env)
    await meeting(env, start="15:00", end="17:00", fixed=True)
    await task(env, due_at=NOW + timedelta(hours=5))
    statements = []
    engine = env.factory.kw["bind"].sync_engine

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", observe)
    try:
        view = await current(env)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert not any(
        s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements
    )
    assert view["timezone"] == "America/New_York" and view["local_date"] == "2026-10-21"
    assert view["schedule"]["class_count"] == view["schedule"]["fixed_block_count"] == 1
    assert view["work"]["totals"]["due_in_window_tasks"] == 1
    assert view["schedule"]["classes"][0]["walk_minutes"] is None
    assert view["automatic_rescheduling"] is view["attendance_sleep_grades_observed"] is False
    assert view["outcomes"]["done_block_is_assignment_submission"] is False
    assert view["question"]["review_href"] == "/planner"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    ["?kind=private-secret", "?kind=morning&reason=private-secret", "?kind=morning&kind=evening"],
)
async def test_invalid_queries_are_private_and_never_echo_input(coach_env, query):
    response = await coach_env.client.get(ROOT + query)
    assert response.status_code == 422 and "private-secret" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_off_flag_precedes_invalid_query_and_domain_reads(coach_env, monkeypatch):
    object.__setattr__(coach_env.settings, "student_coach_enabled", False)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Disabled review must not read a private domain")

    monkeypatch.setattr(service, "review", forbidden)
    response = await coach_env.client.get(ROOT + "?kind=private-secret")
    assert response.status_code == 404 and "private-secret" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_get_has_no_private_freeform_body_or_mutation_route(coach_env):
    response = await coach_env.client.request("GET", ROOT, json={"mood": "private-secret"})
    assert response.status_code == 422 and "private-secret" not in response.text
    assert (await coach_env.client.post(ROOT, json={})).status_code == 405


@pytest.mark.asyncio
async def test_dependency_flags_skip_their_domains_and_disable_totals(coach_env):
    env = coach_env
    object.__setattr__(env.settings, "student_planning_enabled", False)
    object.__setattr__(env.settings, "student_goals_enabled", False)
    await task(env)
    await goal(env)
    statements = []
    engine = env.factory.kw["bind"].sync_engine

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe)
    try:
        view = await current(env)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert (
        view["work"]["status"]
        == view["outcomes"]["status"]
        == view["goals"]["status"]
        == "disabled"
    )
    assert view["work"]["totals"] is view["outcomes"]["totals"] is None
    assert not any(
        any(name in s for name in ("student_tasks", "student_goals", "student_study_blocks"))
        for s in statements
    )


@pytest.mark.asyncio
async def test_class_and_fixed_gap_completeness_are_independent(coach_env):
    env = coach_env
    env.clock.now = datetime(2026, 3, 8, 5, 30, tzinfo=UTC)
    await meeting(env, start="02:30", end="03:30", weekday=6)
    await meeting(env, start="08:00", end="09:00", weekday=6, fixed=True)
    view = await current(env)
    assert view["schedule"]["class_count"] is None
    assert view["schedule"]["classes_complete"] is False
    assert view["schedule"]["fixed_block_count"] == 1
    assert view["schedule"]["fixed_blocks_complete"] is True
    assert "2026-03-08" in next(
        n["message"] for n in view["notices"] if n["code"] == "class_clock_change"
    )
    assert view["schedule"]["next_class"] is None


@pytest.mark.asyncio
async def test_fixed_gap_does_not_erase_known_class_count(coach_env):
    env = coach_env
    env.clock.now = datetime(2026, 3, 8, 5, 30, tzinfo=UTC)
    await meeting(env, start="02:30", end="03:30", weekday=6, fixed=True)
    await meeting(env, weekday=6)
    view = await current(env)
    assert view["schedule"]["class_count"] == 1 and view["schedule"]["classes_complete"]
    assert view["schedule"]["fixed_block_count"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [7, 9])
async def test_adjacent_fixed_clock_gap_does_not_invalidate_selected_day(coach_env, day):
    env = coach_env
    env.clock.now = datetime(2026, 3, day, 15, tzinfo=UTC)
    await meeting(env, start="02:30", end="03:30", weekday=6, fixed=True)
    view = await current(env)
    assert view["schedule"]["fixed_blocks_complete"] is True
    assert view["schedule"]["fixed_block_count"] == 0
    if day == 7:
        assert view["outlook"]["fixed_blocks_complete"] is False
        assert view["outlook"]["fixed_block_count"] is None
    else:
        assert view["outlook"]["fixed_blocks_complete"] is True
        assert not any(n["code"] == "fixed_blocks_incomplete" for n in view["notices"])


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "hours"), [("evening", 25), ("weekly", 169)])
async def test_calendar_periods_follow_server_zone_across_fall_back(coach_env, kind, hours):
    coach_env.clock.now = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    view = await current(coach_env, kind)
    period = view["window"]
    assert (
        datetime.fromisoformat(period["ends_at"]) - datetime.fromisoformat(period["starts_at"])
    ).total_seconds() == hours * 3600
    assert view["local_date"] == "2026-11-01" and period["observed_through"] == view["server_now"]


@pytest.mark.asyncio
async def test_done_took_longer_skip_are_recorded_blocks_not_submissions(coach_env):
    env = coach_env
    row = await task(env)
    await outcome(env, row, "done", minutes=20)
    await outcome(env, row, "took_longer", minutes=35)
    await outcome(env, row, "skipped", minutes=0)
    await outcome(env, row, "done", when=NOW + timedelta(seconds=1), minutes=50)
    view = await current(env, "evening")
    assert view["outcomes"]["totals"] == {
        "done_blocks": 1,
        "took_longer_blocks": 1,
        "skipped_blocks": 1,
        "recorded_minutes": 55,
    }
    assert "Private block title" not in str(view)
    assert not any(
        word in " ".join(view["summary_lines"]).lower()
        for word in ("submitted", "attended", "best week")
    )


@pytest.mark.asyncio
async def test_goal_unknown_explicit_zero_and_distinct_goal_period(coach_env):
    env = coach_env
    await goal(env, count=0, report=True)
    await goal(env, count=0, report=False)
    view = await current(env, "evening")
    progress = [item["progress"] for item in view["goals"]["items"]]
    assert sorted(p["days_reported"] for p in progress) == [0, 1]
    assert {p["reported_count"] for p in progress} == {None, 0}
    assert all(p["period_start"] == "2026-10-01" for p in progress)
    assert view["window"]["starts_at"].startswith("2026-10-21")
    assert "Private note" not in str(view)
    assert view["question"]["code"] == "goal_checkin"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["evening", "weekly"])
async def test_existing_goal_period_report_does_not_imply_missing_today_checkin(coach_env, kind):
    await goal(coach_env, count=0, report=True)
    view = await current(coach_env, kind)
    progress = view["goals"]["items"][0]["progress"]
    assert progress["days_reported"] == 1
    assert progress["latest_reported_on"] == "2026-10-20"
    assert view["question"]["code"] != "goal_checkin"


@pytest.mark.asyncio
async def test_disabled_email_source_is_omitted_without_source_payload_reads(
    coach_env, monkeypatch
):
    env = coach_env
    await task(env, source_identity="a" * 64)

    async def forbidden(*_args):
        pytest.fail("Disabled email must not query source details or connector authority")

    monkeypatch.setattr(service.sources, "email_current", forbidden)
    view = await current(env)
    assert view["work"]["status"] == "incomplete" and view["work"]["totals"] is None
    assert view["work"]["items"] == [] and "My saved assignment" not in str(view)


@pytest.mark.asyncio
async def test_archived_course_task_and_source_cannot_affect_review(coach_env, monkeypatch):
    env = coach_env
    _, course = await meeting(env)
    row = await task(env, course_id=course, source_identity="a" * 64)
    await outcome(env, row, "done")
    async with env.factory() as database:
        saved = await database.get(StudentCourse, course)
        saved.archived = True
        await database.commit()
    view = await current(env)
    assert view["schedule"]["class_count"] == 0
    assert view["work"]["status"] == "complete" and view["work"]["totals"]["active_tasks"] == 0
    assert view["outcomes"]["totals"]["done_blocks"] == 0
    assert not any(n["code"] == "task_source_unavailable" for n in view["notices"])


async def lms_source(env):
    async with env.factory() as database:
        definition = ConnectorDefinition(
            id=uuid4(),
            connector_key="coach_lms_fixture",
            version="1",
            display_name="Fixture",
            connector_class="TOKEN_API",
            trust_level="OWNER",
            manifest={},
            active=True,
        )
        database.add(definition)
        await database.flush()
        connector = ConnectorConnection(
            id=uuid4(),
            connector_definition_id=definition.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="lms_feed",
            external_account_id="fixture",
            status="CONNECTED",
            authorized_capabilities=["academic.calendar.read"],
            provider_capabilities=["academic.calendar.read"],
        )
        database.add(connector)
        await database.flush()
        feed = LmsFeed(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            connection_id=connector.id,
            domain="school.example",
            name="Fixture",
            platform="canvas",
            timezone="America/New_York",
            all_day_due_time="23:59",
            state="paused",
        )
        item = LmsFeedItem(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            identity_key="a" * 64,
            payload={"title": "Current school source title"},
            revision=1,
            cancelled=False,
            conflicted=False,
        )
        database.add_all([feed, item])
        await database.flush()
        database.add(
            LmsFeedSource(
                id=uuid4(),
                feed_id=feed.id,
                item_id=item.id,
                payload={},
                payload_hash="b" * 64,
                present=True,
            )
        )
        await database.commit()
    row = await task(env, source_item_id=item.id, source_identity="c" * 64, source_revision=1)
    return row, item.id, connector.id


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "cancel", "revoke"])
async def test_current_lms_source_is_rechecked_and_invalid_details_are_omitted(coach_env, change):
    env = coach_env
    object.__setattr__(env.settings, "student_lms_feeds_enabled", True)
    row, item_id, connector_id = await lms_source(env)
    assert (await current(env))["work"]["items"][0]["source_kind"] == "lms"
    async with env.factory() as database:
        if change == "revoke":
            connector = await database.get(ConnectorConnection, connector_id)
            connector.authorized_capabilities = []
        else:
            item = await database.get(LmsFeedItem, item_id)
            if change == "revision":
                item.revision += 1
            else:
                item.cancelled = True
        await database.commit()
    await outcome(env, row, "done")
    view = await current(env)
    assert view["work"]["status"] == view["outcomes"]["status"] == "incomplete"
    assert view["work"]["items"] == [] and view["outcomes"]["totals"] is None
    assert "My saved assignment" not in str(view) and "Current school source" not in str(view)


@pytest.mark.asyncio
async def test_disabled_source_only_selects_content_free_classification(coach_env):
    env = coach_env
    await lms_source(env)
    statements = []
    engine = env.factory.kw["bind"].sync_engine

    def observe(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", observe)
    try:
        view = await current(env)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert view["work"]["items"] == []
    assert not any(
        any(name in s for name in ("from lms_", "from connector_", "student_tasks.title"))
        for s in statements
    )


async def email_source(env):
    async with env.factory() as database:
        definition = ConnectorDefinition(
            id=uuid4(),
            connector_key=GMAIL_MANIFEST.id,
            version="coach-fixture",
            display_name="Fixture",
            connector_class="OAUTH_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest={},
            active=True,
        )
        legacy = Connection(
            id=uuid4(),
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="fixture",
            status="active",
            granted_scopes=[GMAIL_READ_SCOPE],
        )
        database.add_all([definition, legacy])
        await database.flush()
        connector = ConnectorConnection(
            id=uuid4(),
            connector_definition_id=definition.id,
            legacy_connection_id=legacy.id,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            provider="google",
            external_account_id="fixture",
            status="CONNECTED",
            authorized_capabilities=["communication.messages.read"],
            provider_capabilities=["communication.messages.read"],
        )
        database.add(connector)
        await database.flush()
        resource = ConnectorResource(
            id=uuid4(),
            workspace_id=env.workspace_id,
            connector_connection_id=connector.id,
            provider="google",
            resource_type="communication.message",
            external_id="abcdef1234567890",
            canonical={
                "status": "active",
                "text": "Private email body never belongs in a coach review",
            },
            content_hash="a" * 64,
            retrieved_at=NOW,
            deleted=False,
        )
        database.add(resource)
        authority = await source_authority_hash(database, connector, in_flight=False)
        await database.commit()
    row = await task(env, source_identity="e" * 64)
    async with env.factory() as database:
        database.add(
            EmailTaskSource(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                task_id=row,
                source_id=resource.id,
                connector_id=connector.id,
                source_hash="a" * 64,
                document_hash="d" * 64,
                authority_hash=authority,
                revision=1,
            )
        )
        await database.commit()
    return row, resource.id, legacy.id


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hash", "scope"])
async def test_manual_email_metadata_works_paused_then_omits_stale_or_revoked_source(
    coach_env, change
):
    env = coach_env
    object.__setattr__(env.settings, "student_email_enabled", True)
    _, resource_id, legacy_id = await email_source(env)
    view = await current(env)
    assert view["work"]["items"][0]["source_kind"] == "email"
    assert "Private email body" not in str(view)
    async with env.factory() as database:
        if change == "hash":
            resource = await database.get(ConnectorResource, resource_id)
            resource.content_hash = "b" * 64
        else:
            legacy = await database.get(Connection, legacy_id)
            legacy.granted_scopes = []
        await database.commit()
    view = await current(env)
    assert view["work"]["items"] == [] and view["work"]["status"] == "incomplete"


@pytest.mark.asyncio
async def test_only_user_lock_is_requested_before_read_only_email_authority(coach_env):
    from sqlalchemy.orm import Session

    env = coach_env
    object.__setattr__(env.settings, "student_email_enabled", True)
    await email_source(env)
    locks = []

    def observe(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append(str(state.statement))

    event.listen(Session, "do_orm_execute", observe)
    try:
        await current(env)
    finally:
        event.remove(Session, "do_orm_execute", observe)
    assert locks and all("FROM users" in s and "connector_" not in s for s in locks)


@pytest.mark.asyncio
async def test_fresh_review_after_real_reset_has_no_replayed_private_facts(coach_env):
    env = coach_env
    row = await task(env)
    await outcome(env, row, "done")
    await goal(env, count=1, report=True)
    await meeting(env)
    assert (await current(env))["outcomes"]["totals"]["done_blocks"] == 1
    async with env.factory() as database:
        await schedule.reset_data(
            database,
            env.user_id,
            env.workspace_id,
            revision=0,
            request_id=uuid4(),
            settings=env.settings,
        )
    view = await current(env)
    assert view["work"]["items"] == view["goals"]["items"] == []
    assert view["schedule"]["class_count"] == view["outcomes"]["totals"]["done_blocks"] == 0


@pytest.mark.asyncio
async def test_other_owner_cannot_receive_first_owners_facts(coach_env):
    env = coach_env
    await task(env)
    await goal(env)
    await meeting(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as other:
        account = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "other-coach@example.com",
                "password": "twelve-character-password",
                "display_name": "Other",
            },
        )
        assert account.status_code == 201
        response = await other.get(ROOT)
    assert response.status_code == 200
    assert (
        "My saved assignment" not in response.text and "My self-reported goal" not in response.text
    )
    assert response.json()["schedule"]["class_count"] == 0


@pytest.mark.asyncio
async def test_timeout_and_bad_user_zone_have_fixed_private_errors(coach_env, monkeypatch):
    env = coach_env
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "Private/InvalidZone"
        await database.commit()
    response = await env.client.get(ROOT)
    assert response.status_code == 409 and "Private/InvalidZone" not in response.text
    assert response.headers["cache-control"] == "private, no-store"

    async def timeout(*_args):
        raise TimeoutError("Private diagnostic must not leak")

    monkeypatch.setattr(service, "review", timeout)
    response = await env.client.get(ROOT)
    assert response.status_code == 503 and "Private diagnostic" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_task_current_completed_status_is_not_a_completion_event(coach_env):
    await task(coach_env, status="completed", remaining_minutes=0, actual_minutes=999)
    view = await current(coach_env, "evening")
    assert view["work"]["totals"]["active_tasks"] == 0
    assert view["outcomes"]["totals"]["done_blocks"] == 0
    assert not any("Done study" in line for line in view["summary_lines"])


@pytest.mark.asyncio
async def test_cancelled_saved_class_is_excluded_but_fixed_shift_remains(coach_env):
    env = coach_env
    meeting_id, _ = await meeting(env)
    await meeting(env, fixed=True)
    async with env.factory() as database:
        database.add(
            StudentMeetingException(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                meeting_id=meeting_id,
                date=date(2026, 10, 21),
                cancelled=True,
            )
        )
        await database.commit()
    view = await current(env)
    assert view["schedule"]["class_count"] == 0
    assert view["schedule"]["fixed_block_count"] == 1
    assert view["schedule"]["status"] == "complete"


@pytest.mark.asyncio
async def test_dense_overlaps_do_not_become_false_clock_gap_warnings(coach_env):
    env = coach_env
    _, course = await meeting(env)
    async with env.factory() as database:
        for _ in range(24):
            database.add(
                StudentClassMeeting(
                    id=uuid4(),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    course_id=course,
                    kind="lecture",
                    weekdays=[2],
                    start_time="10:00",
                    end_time="11:00",
                    timezone="America/New_York",
                    revision=1,
                )
            )
        await database.commit()
    view = await current(env)
    assert view["schedule"]["class_count"] == 25 and view["schedule"]["status"] == "complete"
    assert not any("clock" in n["code"] for n in view["notices"])


@pytest.mark.asyncio
async def test_fresh_user_timezone_changes_the_actual_day_boundary(coach_env):
    env = coach_env
    await task(env, due_at=datetime(2026, 10, 21, 2, tzinfo=UTC))
    view = await current(env)
    assert view["work"]["totals"]["due_in_window_tasks"] == 0
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "UTC"
        await database.commit()
    view = await current(env)
    assert view["timezone"] == "UTC" and view["work"]["totals"]["due_in_window_tasks"] == 1


@pytest.mark.asyncio
async def test_unverifiable_goal_does_not_leak_plaintext_or_fabricate_progress(coach_env):
    env = coach_env
    identifier = await goal(env)
    async with env.factory() as database:
        saved = await database.get(StudentGoal, identifier)
        saved.encrypted_payload = "Private corrupted goal text"
        await database.commit()
    view = await current(env)
    assert view["goals"]["status"] == "unavailable" and view["goals"]["items"] == []
    assert "Private corrupted" not in str(view) and "My self-reported goal" not in str(view)


@pytest.mark.asyncio
async def test_overflow_is_unknown_and_never_a_false_zero(coach_env):
    env = coach_env
    async with env.factory() as database:
        for _ in range(201):
            database.add(
                StudentTask(
                    id=uuid4(),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    title="Private overflow task",
                    status="active",
                    estimated_minutes=5,
                    remaining_minutes=5,
                    actual_minutes=0,
                    priority=1,
                    difficulty=1,
                    course_weight=1,
                )
            )
        await database.commit()
    view = await current(env)
    assert view["work"]["status"] == view["outcomes"]["status"] == "unavailable"
    assert view["work"]["totals"] is view["work"]["known_totals"] is None
    assert view["outcomes"]["totals"] is view["outcomes"]["known_totals"] is None


@pytest.mark.asyncio
async def test_no_auth_has_private_error_and_no_saved_facts(coach_env):
    await task(coach_env)
    coach_env.client.cookies.clear()
    response = await coach_env.client.get(ROOT)
    assert response.status_code == 401 and "My saved assignment" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_postgres_reset_committing_while_review_waits_wins_fresh_read(coach_env, monkeypatch):
    env = coach_env
    if env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires actual PostgreSQL row locks")
    row = await task(env)
    await outcome(env, row, "done")
    await goal(env, count=1, report=True)
    entered = asyncio.Event()
    original = schedule.owner
    async with env.factory() as reset_database:
        await original(reset_database, env.user_id, env.workspace_id, lock=True)

        async def observed(database, user_id, workspace_id, *, lock=False):
            if lock and database is not reset_database:
                entered.set()
            return await original(database, user_id, workspace_id, lock=lock)

        monkeypatch.setattr(schedule, "owner", observed)
        waiting = asyncio.create_task(env.client.get(ROOT))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await schedule.reset_data(
                reset_database,
                env.user_id,
                env.workspace_id,
                revision=0,
                request_id=uuid4(),
                settings=env.settings,
            )
            response = await asyncio.wait_for(waiting, 5)
        finally:
            if not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["work"]["items"] == view["goals"]["items"] == []
    assert view["outcomes"]["totals"]["done_blocks"] == 0
