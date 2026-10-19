"""Source-grounded recovery proposals, exact drafts and private receipt erasure."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from navox.api import coursework, student_planning, student_recovery
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.coursework import service as course_sources
from navox.db.coursework import CourseMaterial
from navox.db.lms_feeds import LmsFeed, LmsFeedItem
from navox.db.models import ConnectorConnection, ConnectorDefinition, User
from navox.db.session import get_database_session
from navox.db.student import StudentCourse
from navox.db.student_planning import StudentPlanningPreview, StudentStudyBlock, StudentTask
from navox.db.student_recovery import StudentRecoveryPreview
from navox.student import service as schedule
from navox.student_recovery import service

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
ROOT = "/api/v1/student/recovery"
PLAN = "/api/v1/student/planning"


@pytest_asyncio.fixture
async def recovery_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    ).model_copy(
        update={
            "student_recovery_enabled": True,
            "student_coursework_enabled": True,
            "student_planning_enabled": True,
        }
    )
    clock = SimpleNamespace(now=NOW)

    async def database():
        async with ai_database() as session:
            yield session

    async def now(_database):
        return clock.now

    monkeypatch.setattr(service, "database_now", now)
    app = create_app()
    for router, path in (
        (student_recovery.router, ROOT + "/preview"),
        (student_planning.router, PLAN + "/tasks"),
        (coursework.router, "/api/v1/student/courses/{course_id}/workspace/overview"),
    ):
        if not any(getattr(route, "path", "") == path for route in app.routes):
            app.include_router(router, prefix="/api/v1")
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
                "email": "recovery@example.com",
                "password": "twelve-character-password",
                "display_name": "Student",
            },
        )
        assert response.status_code == 201, response.text
        account = response.json()
        course_data = {
            "code": "CS 2110",
            "name": "Programming",
            "term_start": "2026-08-20",
            "term_end": "2026-12-20",
        }
        response = await client.post("/api/v1/student/courses", json=course_data)
        assert response.status_code == 201, response.text
        async with ai_database() as session:
            user = await session.get(User, UUID(account["id"]))
            user.timezone, user.agent_paused = "America/New_York", True
            await session.commit()
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            app=app,
            settings=settings,
            clock=clock,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
            course_id=UUID(response.json()["id"]),
            course_data=course_data,
        )


async def task(env, **changes):
    response = await env.client.post(
        PLAN + "/tasks",
        json={
            "request_id": str(uuid4()),
            "title": "Private overdue assignment",
            "course_id": str(env.course_id),
            "due_at": (NOW - timedelta(days=1)).isoformat(),
            "estimated_minutes": 180,
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def request(row, **changes):
    return {
        "request_id": str(uuid4()),
        "days": 4,
        "trigger": {"kind": "overdue_task", "task_id": row["id"], "task_revision": row["revision"]},
        "work": [
            {
                "task_id": row["id"],
                "task_revision": row["revision"],
                "remaining_minutes": 180,
                "priority": 5,
                "personal_catchup_target": (NOW + timedelta(days=3)).isoformat(),
            }
        ],
        **changes,
    }


async def proposal(env, body=None):
    if body is None:
        body = request(await task(env))
    response = await env.client.post(ROOT + "/preview", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


async def upload(
    env, *, text="Request extensions from prof@example.edu before the deadline.", kind="syllabus"
):
    response = await env.client.post(
        f"/api/v1/student/courses/{env.course_id}/workspace/materials",
        params={"kind": kind, "revision": 0},
        content=text.encode(),
        headers={
            "Content-Type": "text/plain",
            "X-Material-Title": quote("Private course source", safe=""),
            "X-Request-ID": str(uuid4()),
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response.json(), text


def extension(env, view, **changes):
    return {
        "request_id": str(uuid4()),
        "preview_hash": view["preview_hash"],
        "work_id": view["work"][0]["id"],
        "recipient": {
            "course_id": str(env.course_id),
            "name": "Professor Example",
            "email": "prof@example.edu",
            "confirmed": True,
        },
        "student_reason": "I was sick.\nI am catching up today.",
        "requested_extension_until": (NOW + timedelta(days=2)).isoformat(),
        **changes,
    }


@pytest.mark.asyncio
async def test_overdue_recovery_is_a_bounded_personal_proposal_with_no_saved_plan_or_provider_write(
    recovery_env, monkeypatch
):
    from navox.ai import configured

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Manual recovery must not invoke a model")

    monkeypatch.setattr(configured, "build_runtime", forbidden)
    env = recovery_env
    row = await task(env)
    view, body = await proposal(env, request(row))
    work = view["work"][0]
    assert (
        work["official_due_at"] == row["due_at"] and work["official_deadline_status"] == "overdue"
    )
    assert work["deadline_source"] == "student_recorded"
    assert view["timezone"] == "America/New_York"
    assert work["personal_catchup_target"] != work["official_due_at"]
    assert (
        view["result"]["blocks"]
        and view["proposal_only"]
        and not view["official_deadlines_changed"]
    )
    assert sum(item["minutes"] for item in view["result"]["blocks"]) == 180
    assert all(
        cap["planned_minutes"] <= cap["free_minutes"] - cap["buffer_minutes"]
        for cap in view["result"]["capacities"]
    )
    repeated = await env.client.post(ROOT + "/preview", json=body)
    assert repeated.status_code == 200 and repeated.json()["preview_id"] == view["preview_id"]
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentStudyBlock)) == 0
        assert await database.scalar(select(func.count()).select_from(StudentPlanningPreview)) == 0
        saved = await database.get(StudentTask, UUID(row["id"]))
        assert (
            saved.due_at.replace(tzinfo=UTC) == datetime.fromisoformat(row["due_at"])
            and saved.revision == 1
        )
        receipt = await database.get(StudentRecoveryPreview, UUID(view["preview_id"]))
        assert "Private overdue" not in receipt.encrypted_payload
        assert receipt.payload_hash != body["request_id"]


@pytest.mark.asyncio
async def test_reported_absence_uses_actual_owned_schedule_and_never_invents_missed_topics(
    recovery_env,
):
    env = recovery_env
    row = await task(env)
    meeting = await env.client.post(
        f"/api/v1/student/courses/{env.course_id}/meetings",
        json={
            "weekdays": [2],
            "start_time": "10:00",
            "end_time": "11:00",
            "timezone": "America/New_York",
        },
    )
    assert meeting.status_code == 201
    material, _ = await upload(
        env, kind="material", text="An available file does not prove a missed lecture topic."
    )
    body = request(
        row,
        trigger={
            "kind": "reported_absence",
            "course_ids": [str(env.course_id)],
            "from_day": "2026-10-21",
            "through_day": "2026-10-21",
        },
    )
    view, _ = await proposal(env, body)
    assert len(view["evidence"]) == 1 and view["evidence"][0]["attendance_verified"] is False
    assert view["evidence"][0]["scheduled_start_at"].startswith("2026-10-21T14:00")
    assert view["available_materials"][0]["id"] == material["entity_id"]
    assert any("do not establish" in value for value in view["unknowns"])
    body["request_id"] = str(uuid4())
    body["trigger"]["through_day"] = "2026-10-23"
    denied = await env.client.post(ROOT + "/preview", json=body)
    assert denied.status_code == 422


@pytest.mark.asyncio
async def test_existing_study_classes_travel_and_routine_are_reserved_not_double_booked(
    recovery_env,
):
    env = recovery_env
    row = await task(env)
    await env.client.post(
        f"/api/v1/student/courses/{env.course_id}/meetings",
        json={
            "weekdays": [3],
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "America/New_York",
            "walk_minutes": 20,
        },
    )
    async with env.factory() as database:
        database.add(
            StudentStudyBlock(
                id=uuid4(),
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                task_id=UUID(row["id"]),
                plan_id=uuid4(),
                title=row["title"],
                start_at=NOW + timedelta(hours=3),
                end_at=NOW + timedelta(hours=4),
                minutes=60,
                focus_window=False,
                status="planned",
                revision=1,
                actual_minutes=0,
            )
        )
        await database.commit()
    view, _ = await proposal(env, request(row))
    for block in view["result"]["blocks"]:
        start, end = (
            datetime.fromisoformat(block["start_at"]),
            datetime.fromisoformat(block["end_at"]),
        )
        assert not (start < NOW + timedelta(hours=2) and end > NOW + timedelta(minutes=40))
        assert not (
            start < NOW + timedelta(hours=4, minutes=15)
            and end > NOW + timedelta(hours=2, minutes=45)
        )
    assert any("kept busy" in item for item in view["result"]["warnings"])


@pytest.mark.asyncio
async def test_fake_overdue_and_fake_skip_triggers_are_rejected(recovery_env):
    env = recovery_env
    row = await task(env, due_at=(NOW + timedelta(days=1)).isoformat())
    assert (await env.client.post(ROOT + "/preview", json=request(row))).status_code == 422
    body = request(row, trigger={"kind": "skipped_blocks"})
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 422
    async with env.factory() as database:
        for index in range(3):
            database.add(
                StudentStudyBlock(
                    id=uuid4(),
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    task_id=UUID(row["id"]),
                    plan_id=uuid4(),
                    title=row["title"],
                    start_at=NOW - timedelta(days=index + 1),
                    end_at=NOW - timedelta(days=index + 1) + timedelta(hours=1),
                    minutes=60,
                    focus_window=False,
                    status="skipped",
                    revision=2,
                    actual_minutes=0,
                    outcome_at=NOW - timedelta(days=index + 1),
                )
            )
        await database.commit()
    view, _ = await proposal(env, body)
    assert len(view["evidence"]) == 3 and all(
        item["kind"] == "recorded_skipped_block" for item in view["evidence"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["task", "archive", "material", "timetable"])
async def test_current_task_course_material_and_timetable_changes_fence_private_previews(
    recovery_env, change
):
    env = recovery_env
    row = await task(env)
    material, _ = await upload(env)
    view, _ = await proposal(env, request(row))
    async with env.factory() as database:
        if change == "task":
            saved = await database.get(StudentTask, UUID(row["id"]))
            saved.remaining_minutes += 10
            saved.revision += 1
        elif change == "archive":
            saved = await database.get(StudentCourse, env.course_id)
            saved.archived = True
        elif change == "material":
            saved = await database.get(CourseMaterial, UUID(material["entity_id"]))
            content = course_sources.reveal(saved, env.settings, "material")
            saved.revision += 1
            saved.encrypted_payload = course_sources.seal(
                content,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                course_id=env.course_id,
                entity_id=saved.id,
                revision=saved.revision,
                purpose="material",
            )
        else:
            saved = await database.get(User, env.user_id)
            saved.timezone = "UTC"
        await database.commit()
    response = await env.client.get(ROOT + "/previews/" + view["preview_id"])
    assert response.status_code == 409, response.text
    assert "Private overdue" not in response.text
    draft = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=extension(env, view)
    )
    assert draft.status_code == 409


@pytest.mark.asyncio
async def test_exact_syllabus_quotes_and_material_work_are_validated_without_policy_inference(
    recovery_env,
):
    env = recovery_env
    row = await task(env)
    material, text = await upload(env)
    citation = {
        "material_id": material["entity_id"],
        "revision": material["revision"],
        "page": 1,
        "quote": text,
    }
    body = request(
        row, policies=[{"course_id": str(env.course_id), "kind": "extension", "source": citation}]
    )
    body["work"].append(
        {
            "material_id": material["entity_id"],
            "material_revision": 1,
            "course_id": str(env.course_id),
            "remaining_minutes": 45,
            "priority": 2,
            "personal_catchup_target": (NOW + timedelta(days=2)).isoformat(),
        }
    )
    view, _ = await proposal(env, body)
    assert view["policies"][0]["source"]["quote"] == text
    assert view["work"][1]["official_due_at"] is None
    assert any("No late-work" in item for item in view["unknowns"])
    body["request_id"] = str(uuid4())
    body["policies"][0]["source"]["quote"] = "Automatic extensions are always allowed."
    denied = await env.client.post(ROOT + "/preview", json=body)
    assert denied.status_code == 422


@pytest.mark.asyncio
async def test_extension_is_exact_editable_text_with_student_reason_and_no_invented_docs(
    recovery_env,
):
    env = recovery_env
    view, _ = await proposal(env)
    payload = extension(env, view)
    response = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=payload
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["student_reported_reason"] == payload["student_reason"]
    assert draft["timezone"] == view["timezone"]
    assert (
        payload["student_reason"] in draft["body"] and "documentation" not in draft["body"].lower()
    )
    assert draft["sent"] is False and draft["external_draft_created"] is False
    assert datetime.fromisoformat(draft["expires_at"]) <= datetime.fromisoformat(view["expires_at"])
    assert any("policy is unknown" in value for value in draft["notices"])
    retry = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=payload
    )
    assert retry.status_code == 200 and retry.json()["preview_id"] == draft["preview_id"]
    async with env.factory() as database:
        receipts = list(await database.scalars(select(StudentRecoveryPreview)))
        assert len(receipts) == 2
        assert all("I was sick" not in (row.encrypted_payload or "") for row in receipts)
    payload["request_id"] = str(uuid4())
    payload["recipient"]["source"] = {
        "material_id": str(uuid4()),
        "revision": 1,
        "page": 1,
        "quote": "prof@example.edu",
    }
    denied = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=payload
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_missing_recipient_and_private_validation_inputs_are_not_echoed(recovery_env):
    env = recovery_env
    view, _ = await proposal(env)
    payload = extension(env, view)
    payload["recipient"]["email"] = "PRIVATE INVALID RECIPIENT"
    response = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=payload
    )
    assert (
        response.status_code == 422
        and "PRIVATE INVALID" not in response.text
        and "I was sick" not in response.text
    )
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_forget_erases_draft_children_while_disabled_and_retry_never_resurrects_text(
    recovery_env,
):
    env = recovery_env
    view, body = await proposal(env)
    draft = (
        await env.client.post(
            ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=extension(env, view)
        )
    ).json()
    env.settings.__dict__["student_recovery_enabled"] = False
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 404
    erased = await env.client.request(
        "DELETE",
        ROOT + "/previews/" + view["preview_id"],
        json={"preview_hash": view["preview_hash"]},
    )
    assert erased.status_code == 204, erased.text
    async with env.factory() as database:
        receipts = list(await database.scalars(select(StudentRecoveryPreview)))
        assert len(receipts) == 2 and all(row.encrypted_payload is None for row in receipts)
    env.settings.__dict__["student_recovery_enabled"] = True
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 410
    assert (await env.client.get(ROOT + "/previews/" + draft["preview_id"])).status_code == 410


@pytest.mark.asyncio
async def test_expiry_elapsed_time_and_request_id_reuse_never_present_a_stale_valid_plan(
    recovery_env,
):
    env = recovery_env
    view, body = await proposal(env)
    body["days"] = 3
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 409
    env.clock.now = datetime.fromisoformat(view["result"]["blocks"][0]["start_at"]) + timedelta(
        seconds=1
    )
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 409
    env.clock.now = NOW + timedelta(minutes=21)
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 410


@pytest.mark.asyncio
async def test_receipt_limits_do_not_block_erasure_and_private_helpers_do_not_commit(
    recovery_env, monkeypatch
):
    env = recovery_env
    view, body = await proposal(env)
    monkeypatch.setattr(service, "MAX_ACTIVE", 1)
    body["request_id"] = str(uuid4())
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 429
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.invalidate_recovery_previews(database, env.user_id, env.workspace_id)
        await database.rollback()
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 200
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.forget_recovery_data(database, env.user_id, env.workspace_id)
        await database.rollback()
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 200
    assert (
        await env.client.request(
            "DELETE",
            ROOT + "/previews/" + view["preview_id"],
            json={"preview_hash": view["preview_hash"]},
        )
    ).status_code == 204


@pytest.mark.asyncio
async def test_request_size_type_and_origin_are_bounded_without_echoing_private_body(recovery_env):
    env = recovery_env
    response = await env.client.post(
        ROOT + "/preview",
        content=b"x" * (64 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    response = await env.client.post(
        ROOT + "/preview", content=b"PRIVATE", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415 and "PRIVATE" not in response.text
    response = await env.client.post(
        ROOT + "/preview", json={}, headers={"Origin": "https://foreign.example"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_school_deadline_is_preserved_even_when_user_task_due_was_deliberately_different(
    recovery_env, monkeypatch
):
    from navox.lms_feeds import service as feeds

    env = recovery_env
    row = await task(env)
    item_id = uuid4()
    access = SimpleNamespace(allowed=True)
    official = NOW - timedelta(days=2)
    async with env.factory() as database:
        database.add(
            LmsFeedItem(
                id=item_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                course_id=env.course_id,
                identity_key="a" * 64,
                revision=1,
                cancelled=False,
                conflicted=False,
                payload={"title": "School title", "due_at": official.isoformat()},
            )
        )
        await database.flush()
        saved = await database.get(StudentTask, UUID(row["id"]))
        saved.source_item_id, saved.source_identity, saved.source_revision = item_id, "b" * 64, 1
        await database.commit()

    async def authorized(_database, item, **_kwargs):
        assert item.id == item_id
        return access.allowed

    monkeypatch.setattr(feeds, "item_sources_authorized", authorized)
    view, _ = await proposal(env, request(row))
    assert datetime.fromisoformat(view["work"][0]["official_due_at"]) == official
    assert view["work"][0]["deadline_source"] == "school_assignment"
    access.allowed = False
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 409


@pytest.mark.asyncio
async def test_foreign_owner_cannot_read_erase_or_use_another_students_sources(recovery_env):
    env = recovery_env
    view, _ = await proposal(env)
    other = await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-recovery@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    assert other.status_code == 201
    assert (await env.client.get(ROOT + "/previews/" + view["preview_id"])).status_code == 404
    assert (
        await env.client.request(
            "DELETE",
            ROOT + "/previews/" + view["preview_id"],
            json={"preview_hash": view["preview_hash"]},
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_missing_encryption_configuration_is_an_honest_unavailable_response(recovery_env):
    env = recovery_env
    env.settings.connector_secret_encryption_key = None
    row = await task(env)
    response = await env.client.post(ROOT + "/preview", json=request(row))
    assert response.status_code == 503 and "not configured" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quoted_mailbox,expected",
    [
        ("assistant-prof@example.edu", 422),
        ("prof@example.edu.evil", 422),
        ("prof@example.edu", 200),
    ],
)
async def test_recipient_provenance_requires_the_complete_mailbox_not_a_substring(
    recovery_env, quoted_mailbox, expected
):
    env = recovery_env
    material, _ = await upload(env, text="Ask " + quoted_mailbox + " for extensions.")
    view, _ = await proposal(env)
    payload = extension(env, view)
    payload["recipient"]["source"] = {
        "material_id": material["entity_id"],
        "revision": 1,
        "page": 1,
        "quote": "Ask " + quoted_mailbox + " for extensions.",
    }
    response = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=payload
    )
    assert response.status_code == expected, response.text


@pytest.mark.asyncio
async def test_periodic_ciphertext_cleanup_runs_while_disabled_and_keeps_retry_tombstones(
    recovery_env, monkeypatch
):
    from navox.student_recovery import activities

    env = recovery_env
    view, body = await proposal(env)
    draft = await env.client.post(
        ROOT + "/previews/" + view["preview_id"] + "/extension-draft", json=extension(env, view)
    )
    assert draft.status_code == 200
    env.clock.now = NOW + timedelta(minutes=21)
    env.settings.__dict__["student_recovery_enabled"] = False
    async with env.factory() as database:
        assert await service.purge_expired_recovery_previews(database, limit=1) == 1
        await database.commit()
    async with env.factory() as database:
        rows = list(await database.scalars(select(StudentRecoveryPreview)))
        assert sum(row.encrypted_payload is None for row in rows) == 1
    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    assert await activities.purge_expired_recovery_previews_activity() == 1
    async with env.factory() as database:
        rows = list(await database.scalars(select(StudentRecoveryPreview)))
        assert len(rows) == 2 and all(row.encrypted_payload is None for row in rows)
    env.settings.__dict__["student_recovery_enabled"] = True
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 410


@pytest.mark.asyncio
async def test_cleanup_uses_the_real_zero_argument_session_factory_implementation(
    recovery_env, monkeypatch
):
    from navox.db import session as sessions
    from navox.student_recovery import activities

    env = recovery_env
    view, _ = await proposal(env)
    env.clock.now = NOW + timedelta(minutes=21)
    # Exercise the real factory implementation and signature, bypassing only
    # its global cache and pointing its engine at this isolated test database.
    monkeypatch.setattr(sessions, "get_database_engine", lambda: env.factory.kw["bind"])
    monkeypatch.setattr(activities, "get_session_factory", sessions.get_session_factory.__wrapped__)
    assert await activities.purge_expired_recovery_previews_activity() == 1
    async with env.factory() as database:
        saved = await database.get(StudentRecoveryPreview, UUID(view["preview_id"]))
        assert saved.encrypted_payload is None


@pytest.mark.asyncio
async def test_source_forget_hook_erases_private_copies_and_prevents_retry_resurrection(
    recovery_env,
):
    env = recovery_env
    view, body = await proposal(env)
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.invalidate_recovery_previews(database, env.user_id, env.workspace_id)
        await database.commit()
    async with env.factory() as database:
        row = await database.get(StudentRecoveryPreview, UUID(view["preview_id"]))
        assert row.encrypted_payload is None
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 410


@pytest.mark.asyncio
async def test_cleanup_activity_is_bounded_even_with_a_large_backlog(recovery_env, monkeypatch):
    from navox.student_recovery import activities

    env = recovery_env
    calls = []

    async def backlog(_database, *, limit):
        calls.append(limit)
        return limit

    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    monkeypatch.setattr(activities, "purge_expired_recovery_previews", backlog)
    assert await activities.purge_expired_recovery_previews_activity() == 5000
    assert calls == [500] * 10


@pytest.mark.asyncio
async def test_chunked_private_json_has_an_aggregate_bound_and_request_deadline(
    recovery_env, monkeypatch
):
    env = recovery_env

    async def oversized():
        yield b"x" * (32 * 1024)
        yield b"x" * (33 * 1024)

    response = await env.client.post(
        ROOT + "/preview", content=oversized(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    monkeypatch.setattr(student_recovery, "JSON_DEADLINE_SECONDS", 0.01)

    async def slow():
        yield b'{"private":"'
        await asyncio.sleep(0.03)
        yield b'PRIVATE REASON"}'

    response = await env.client.post(
        ROOT + "/preview", content=slow(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 408 and "PRIVATE REASON" not in response.text
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentRecoveryPreview)) == 0


@pytest.mark.asyncio
async def test_postgres_concurrent_same_request_is_one_receipt_and_erase_blocks_replay(
    recovery_env,
):
    env = recovery_env
    async with env.factory() as database:
        if database.get_bind().dialect.name != "postgresql":
            pytest.skip("Requires PostgreSQL User row locks")
    body = request(await task(env))
    first, second = await asyncio.gather(
        env.client.post(ROOT + "/preview", json=body),
        env.client.post(ROOT + "/preview", json=body),
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["preview_id"] == second.json()["preview_id"]
    view = first.json()
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(StudentRecoveryPreview)) == 1
    erased = await env.client.request(
        "DELETE",
        ROOT + "/previews/" + view["preview_id"],
        json={"preview_hash": view["preview_hash"]},
    )
    assert erased.status_code == 204
    assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 410


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_hash", "expires_at"])
async def test_encrypted_receipt_authenticates_freshness_and_access_expiry_metadata(
    recovery_env, field
):
    env = recovery_env
    view, _ = await proposal(env)
    async with env.factory() as database:
        row = await database.get(StudentRecoveryPreview, UUID(view["preview_id"]))
        if field == "source_hash":
            row.source_hash = "0" * 64
        else:
            row.expires_at += timedelta(days=1)
        await database.commit()
    response = await env.client.get(ROOT + "/previews/" + view["preview_id"])
    assert response.status_code == 503 and "cannot be decrypted" in response.text
    assert "Private overdue" not in response.text
    erased = await env.client.request(
        "DELETE",
        ROOT + "/previews/" + view["preview_id"],
        json={"preview_hash": view["preview_hash"]},
    )
    assert erased.status_code == 204


async def seeded_connector(env):
    connection_id = uuid4()
    async with env.factory() as database:
        definition = ConnectorDefinition(
            id=uuid4(),
            connector_key="recovery-erase-fixture",
            version="1",
            display_name="Synthetic source",
            connector_class="TOKEN_API",
            trust_level="OWNER",
            manifest={},
            active=True,
        )
        database.add(definition)
        await database.flush()
        database.add(
            ConnectorConnection(
                id=connection_id,
                connector_definition_id=definition.id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                provider="lms_feed",
                external_account_id="synthetic-recovery-feed",
                status="CONNECTED",
            )
        )
        await database.flush()
        database.add(
            LmsFeed(
                id=uuid4(),
                connection_id=connection_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                domain="school.example",
                name="Synthetic source",
                platform="canvas",
                timezone="UTC",
                all_day_due_time="23:59",
            )
        )
        await database.commit()
    return connection_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "task",
        "material",
        "planner_reset",
        "coursework_reset",
        "feed",
        "connector_disconnect",
        "connector_delete_data",
        "student_reset",
    ],
)
async def test_real_source_erasure_and_reset_paths_remove_recovery_private_copies(
    recovery_env, operation
):
    from navox.connectors import lifecycle
    from navox.lms_feeds import service as feeds
    from navox.student_planning import service as planner

    env = recovery_env
    connection_id = (
        await seeded_connector(env)
        if operation.startswith("connector") or operation == "feed"
        else None
    )
    if operation == "connector_delete_data":
        async with env.factory() as database:
            await lifecycle.disconnect(
                database,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=connection_id,
                request_id=uuid4(),
            )
    row = await task(env)
    material, _ = await upload(env)
    view, body = await proposal(env, request(row))
    if operation == "task":
        response = await env.client.delete(
            PLAN + f"/tasks/{row['id']}",
            params={"revision": row["revision"], "request_id": str(uuid4())},
        )
        assert response.status_code == 204, response.text
    elif operation == "material":
        response = await env.client.request(
            "DELETE",
            f"/api/v1/student/courses/{env.course_id}/workspace/materials/{material['entity_id']}",
            json={"revision": material["revision"], "request_id": str(uuid4())},
        )
        assert response.status_code == 200, response.text
    elif operation == "student_reset":
        profile = (await env.client.get("/api/v1/student/profile")).json()
        response = await env.client.post(
            "/api/v1/student/data/reset",
            json={"revision": profile["revision"], "request_id": str(uuid4()), "confirmed": True},
        )
        assert response.status_code == 200, response.text
    elif operation.startswith("connector"):
        async with env.factory() as database:
            method = (
                lifecycle.disconnect
                if operation == "connector_disconnect"
                else lifecycle.delete_learned_data
            )
            await method(
                database,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                connection_id=connection_id,
                request_id=uuid4(),
            )
    else:
        async with env.factory() as database:
            if operation == "feed":
                await database.scalar(
                    select(ConnectorConnection)
                    .where(ConnectorConnection.id == connection_id)
                    .with_for_update()
                )
            await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
            if operation == "feed":
                await feeds.erase_feed_data(
                    database,
                    user_id=env.user_id,
                    workspace_id=env.workspace_id,
                    connection_id=connection_id,
                    now=env.clock.now,
                )
            elif operation == "planner_reset":
                await planner.forget_planning_data(database, env.user_id, env.workspace_id)
            else:
                await course_sources.clear_coursework_data(
                    database, user_id=env.user_id, workspace_id=env.workspace_id
                )
            await database.commit()
    async with env.factory() as database:
        receipts = list(
            await database.scalars(
                select(StudentRecoveryPreview).where(
                    StudentRecoveryPreview.user_id == env.user_id,
                    StudentRecoveryPreview.workspace_id == env.workspace_id,
                )
            )
        )
        assert all(receipt.encrypted_payload is None for receipt in receipts)
        assert (len(receipts) == 0) == (operation == "student_reset")
    if operation != "student_reset":
        assert (await env.client.post(ROOT + "/preview", json=body)).status_code == 410


@pytest.mark.asyncio
async def test_cleanup_worker_is_registered_and_started_once_when_recovery_and_ai_are_disabled(
    monkeypatch,
):
    from navox.workflows import worker

    settings = Settings(_env_file=None, ai_provider="disabled")
    assert settings.student_recovery_enabled is False
    calls = []
    registration = {}

    async def start_workflow(run, **kwargs):
        calls.append((run, kwargs))

    async def connect(_target):
        return SimpleNamespace(start_workflow=start_workflow)

    class FakeWorker:
        def __init__(self, _client, **kwargs):
            registration.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FinishedEvent:
        async def wait(self):
            return None

    monkeypatch.setattr(worker, "Client", SimpleNamespace(connect=connect))
    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker.asyncio, "Event", FinishedEvent)
    await worker.main()
    assert registration["workflows"].count(worker.StudentRecoveryCleanupWorkflow) == 1
    assert registration["activities"].count(worker.purge_expired_recovery_previews_activity) == 1
    cleanup = [kwargs for run, kwargs in calls if run == worker.StudentRecoveryCleanupWorkflow.run]
    assert len(cleanup) == 1 and cleanup[0]["id"] == "navox-student-recovery-cleanup-v1"


@pytest.mark.asyncio
async def test_cleanup_workflow_prepares_in_the_actual_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from navox.workflows.student_recovery import StudentRecoveryCleanupWorkflow

    SandboxedWorkflowRunner().prepare_workflow(
        workflow._Definition.must_from_class(StudentRecoveryCleanupWorkflow)
    )


@pytest.mark.asyncio
async def test_framework_private_errors_and_disabled_routes_never_echo_paths_or_read_body(
    recovery_env,
):
    env = recovery_env
    response = await env.client.get(ROOT + "/previews/PRIVATE-SOURCE-PATH")
    assert response.status_code == 422
    assert response.json() == {"detail": "The recovery request is invalid"}
    assert response.headers["cache-control"] == "private, no-store"
    env.settings.student_recovery_enabled = False

    async def unread():
        pytest.fail("A disabled recovery route must not read its body")
        yield b""

    response = await env.client.post(
        ROOT + "/previews/PRIVATE-SOURCE-PATH/extension-draft",
        content=unread(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 404 and "PRIVATE-SOURCE-PATH" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    env.settings.student_recovery_enabled = True
    env.client.cookies.clear()
    response = await env.client.get(ROOT + "/previews/" + str(uuid4()))
    assert response.status_code == 401 and response.headers["cache-control"] == "private, no-store"
