"""Saved authored Boards: private bounded snapshots, exact retries and erasure fences.

SQLite tests cover request and persistence semantics. Real User lock interleavings
are covered separately in test_board_postgres.py, never inferred from SQLite.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from navox.api.auth import CurrentAccount, get_current_account
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.db.session import get_database_session
from navox.db.student import StudentCourse

ROOT = "/api/v1/board/saved"
PRIVATE_TITLE = "My private circuit revision"
CATALOG = json.loads(
    (Path(__file__).resolve().parents[1] / "navox/board/catalog.json").read_text()
)["lessons"]
LESSON = CATALOG[0]


@pytest_asyncio.fixture
async def board_env(ai_database):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="disabled",
        connector_secret_encryption_key=SecretStr(Fernet.generate_key().decode()),
    ).model_copy(update={"student_board_enabled": True})

    async def database():
        async with ai_database() as session:
            yield session

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
                "email": "board@example.com",
                "password": "twelve-character-password",
                "display_name": "Board student",
            },
        )
        assert response.status_code == 201, response.text
        account = response.json()
        client.headers["X-NavoX-Account"] = f"{account['workspace']['id']}:{account['id']}"
        course = await client.post(
            "/api/v1/student/courses",
            json={
                "code": "ECE 101",
                "name": "Circuits",
                "term_start": "2026-08-20",
                "term_end": "2026-12-20",
            },
        )
        assert course.status_code == 201, course.text
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            app=app,
            settings=settings,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
            course_id=UUID(course.json()["id"]),
        )


def snapshot(**changes):
    return {
        "version": 1,
        "lesson_id": LESSON["id"],
        "lesson_version": LESSON["lesson_version"],
        "title": PRIVATE_TITLE,
        "step_index": 0,
        "revealed": 0,
        "complete": False,
        "answers": {},
        "marked": [],
        "strokes": [],
        **changes,
    }


def create_body(**changes):
    return {"request_id": str(uuid4()), "generation": 0, "snapshot": snapshot(), **changes}


async def create(env, **changes):
    body = create_body(**changes)
    response = await env.client.post(ROOT, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


async def current(env):
    response = await env.client.get(ROOT)
    assert response.status_code == 200, response.text
    return response.json()


def update_body(row, **changes):
    return {
        "request_id": str(uuid4()),
        "revision": row["revision"],
        "snapshot": row["snapshot"],
        **changes,
    }


async def erase(env, row, **changes):
    body = {"request_id": str(uuid4()), "revision": row["revision"], **changes}
    response = await env.client.request("DELETE", f"{ROOT}/{row['id']}", json=body)
    return response, body


@pytest.mark.asyncio
async def test_empty_collection_save_reopen_and_update_preserve_exact_authored_state(board_env):
    env = board_env
    assert await current(env) == {"generation": 0, "revision": 0, "boards": []}
    state = snapshot(
        step_index=1,
        revealed=3,
        answers={LESSON["steps"][1]["id"]: 0},
        marked=[LESSON["steps"][0]["id"]],
        strokes=[{"id": 7, "points": [{"x": 0, "y": 0}, {"x": 1120, "y": 592}]}],
    )
    row, _ = await create(env, snapshot=state, course_id=str(env.course_id))
    assert row["revision"] == 1 and row["snapshot"] == state
    assert row["course_id"] == str(env.course_id)
    assert (await env.client.get(f"{ROOT}/{row['id']}")).json() == row
    listing = await current(env)
    assert listing["revision"] == 1 and len(listing["boards"]) == 1
    summary = listing["boards"][0]
    assert "snapshot" not in summary and "strokes" not in summary
    assert summary["title"] == PRIVATE_TITLE and summary["step_index"] == 1
    changed = snapshot(title="Renamed privately", step_index=2, revealed=1)
    response = await env.client.patch(
        f"{ROOT}/{row['id']}", json=update_body(row, snapshot=changed)
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["revision"] == 2 and updated["snapshot"] == changed
    assert updated["created_at"] == row["created_at"]
    assert (await current(env))["revision"] == 2
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_private_snapshot_is_encrypted_and_retry_fences_never_contain_content(board_env):
    from navox.db.board import SavedBoard, SavedBoardCommand

    env = board_env
    row, body = await create(
        env, snapshot=snapshot(strokes=[{"id": 1, "points": [{"x": 42, "y": 9}]}])
    )
    async with env.factory() as database:
        saved = await database.get(SavedBoard, UUID(row["id"]))
        assert PRIVATE_TITLE not in saved.encrypted_payload
        assert '"points"' not in saved.encrypted_payload
        commands = list(await database.scalars(select(SavedBoardCommand)))
        assert len(commands) == 1
        assert PRIVATE_TITLE not in repr([command.__dict__ for command in commands])
        assert len(commands[0].payload_hash) == 64
        preimage = {
            "user_id": str(env.user_id),
            "workspace_id": str(env.workspace_id),
            "operation": "create",
            "board_id": None,
            "payload": body,
        }
        unkeyed = hashlib.sha256(
            json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert commands[0].payload_hash != unkeyed


@pytest.mark.asyncio
async def test_exact_retries_are_stable_and_changed_or_cross_operation_requests_conflict(board_env):
    env = board_env
    row, body = await create(env)
    for _ in range(2):
        response = await env.client.post(ROOT, json=body)
        assert response.status_code == 201 and response.json() == row
    changed = deepcopy(body)
    changed["snapshot"]["title"] = "An altered private title"
    rejected = await env.client.post(ROOT, json=changed)
    assert rejected.status_code == 409 and "altered private" not in rejected.text
    patch = update_body(row, snapshot=snapshot(title="Updated title"))
    first = await env.client.patch(f"{ROOT}/{row['id']}", json=patch)
    assert first.status_code == 200, first.text
    replay = await env.client.patch(f"{ROOT}/{row['id']}", json=patch)
    assert replay.status_code == 200 and replay.json() == first.json()
    collision = await env.client.request(
        "DELETE",
        f"{ROOT}/{row['id']}",
        json={"request_id": patch["request_id"], "revision": first.json()["revision"]},
    )
    assert collision.status_code == 409
    assert (await current(env))["revision"] == 2


@pytest.mark.asyncio
async def test_stale_revision_cannot_overwrite_or_delete_a_newer_snapshot(board_env):
    env = board_env
    row, _ = await create(env)
    newer = await env.client.patch(
        f"{ROOT}/{row['id']}", json=update_body(row, snapshot=snapshot(title="Newer state"))
    )
    assert newer.status_code == 200
    stale = await env.client.patch(
        f"{ROOT}/{row['id']}", json=update_body(row, snapshot=snapshot(title="Stale state"))
    )
    assert stale.status_code == 409
    deletion, _ = await erase(env, row)
    assert deletion.status_code == 409
    assert (await env.client.get(f"{ROOT}/{row['id']}")).json() == newer.json()


@pytest.mark.asyncio
async def test_delete_retries_and_delayed_writes_cannot_resurrect_a_board(board_env):
    from navox.db.board import SavedBoard, SavedBoardCommand

    env = board_env
    row, body = await create(env)
    unsent = create_body()
    patch = update_body(row, snapshot=snapshot(title="An in-flight change"))
    response = await env.client.patch(f"{ROOT}/{row['id']}", json=patch)
    assert response.status_code == 200
    row = response.json()
    deletion, deletion_body = await erase(env, row)
    assert deletion.status_code == 204
    assert (
        await env.client.request("DELETE", f"{ROOT}/{row['id']}", json=deletion_body)
    ).status_code == 204
    assert (await env.client.get(f"{ROOT}/{row['id']}")).status_code == 404
    assert (await env.client.post(ROOT, json=body)).status_code == 410
    assert (await env.client.patch(f"{ROOT}/{row['id']}", json=patch)).status_code == 410
    assert (await env.client.post(ROOT, json=unsent)).status_code == 409
    listing = await current(env)
    assert listing["boards"] == [] and listing["generation"] == 1
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(SavedBoard)) == 0
        commands = list(await database.scalars(select(SavedBoardCommand)))
        assert commands and all(
            command.status == "erased" for command in commands if command.operation != "delete"
        )
        assert PRIVATE_TITLE not in repr([command.__dict__ for command in commands])


@pytest.mark.asyncio
async def test_reset_works_with_feature_and_encryption_off_and_fences_pre_reset_forms(board_env):
    from navox.db.board import SavedBoard

    env = board_env
    row, body = await create(env, course_id=str(env.course_id))
    unsent = create_body()
    patch = update_body(row)
    env.settings.student_board_enabled = False
    original_key = env.settings.connector_secret_encryption_key
    env.settings.connector_secret_encryption_key = None
    request = {"request_id": str(uuid4()), "revision": 0, "confirmed": True}
    response = await env.client.post("/api/v1/student/data/reset", json=request)
    assert response.status_code == 200, response.text
    replay = await env.client.post("/api/v1/student/data/reset", json=request)
    assert replay.status_code == 200 and replay.json()["reused"] is True
    env.settings.student_board_enabled = True
    env.settings.connector_secret_encryption_key = original_key
    assert (await env.client.post(ROOT, json=body)).status_code == 410
    assert (await env.client.patch(f"{ROOT}/{row['id']}", json=patch)).status_code in {404, 410}
    assert (await env.client.post(ROOT, json=unsent)).status_code == 409
    listing = await current(env)
    assert listing["boards"] == [] and listing["generation"] == 1
    fresh = await env.client.post(ROOT, json=create_body(generation=listing["generation"]))
    assert fresh.status_code == 201, fresh.text
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(SavedBoard)) == 1


@pytest.mark.asyncio
async def test_erasure_is_available_without_flag_or_key_even_for_corrupt_ciphertext(board_env):
    from navox.db.board import SavedBoard

    env = board_env
    row, _ = await create(env)
    async with env.factory() as database:
        (await database.get(SavedBoard, UUID(row["id"]))).encrypted_payload = "not decryptable"
        await database.commit()
    env.settings.student_board_enabled = False
    env.settings.connector_secret_encryption_key = None
    deletion, body = await erase(env, row)
    assert deletion.status_code == 204, deletion.text
    assert (await env.client.request("DELETE", f"{ROOT}/{row['id']}", json=body)).status_code == 204


@pytest.mark.asyncio
async def test_ciphertext_binding_rejects_cross_board_splicing_without_echoing_private_data(
    board_env,
):
    from navox.db.board import SavedBoard

    env = board_env
    first, _ = await create(env)
    second, _ = await create(env, snapshot=snapshot(title="Another private board"))
    async with env.factory() as database:
        a = await database.get(SavedBoard, UUID(first["id"]))
        b = await database.get(SavedBoard, UUID(second["id"]))
        b.encrypted_payload = a.encrypted_payload
        await database.commit()
    for path in (ROOT, f"{ROOT}/{second['id']}"):
        rejected = await env.client.get(path)
        assert rejected.status_code == 503
        assert PRIVATE_TITLE not in rejected.text
        assert rejected.headers["cache-control"] == "private, no-store"
    assert (await env.client.get(f"{ROOT}/{first['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_another_owner_cannot_list_read_mutate_delete_or_attach_foreign_course(board_env):
    env = board_env
    row, _ = await create(env, course_id=str(env.course_id))
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as other:
        registered = await other.post(
            "/api/v1/auth/register",
            json={
                "email": "other-board@example.com",
                "password": "twelve-character-password",
                "display_name": "Other student",
            },
        )
        assert registered.status_code == 201
        other_account = registered.json()
        other.headers["X-NavoX-Account"] = (
            f"{other_account['workspace']['id']}:{other_account['id']}"
        )
        assert (await other.get(ROOT)).json()["boards"] == []
        assert (await other.get(f"{ROOT}/{row['id']}")).status_code == 404
        assert (await other.patch(f"{ROOT}/{row['id']}", json=update_body(row))).status_code == 404
        assert (
            await other.request(
                "DELETE", f"{ROOT}/{row['id']}", json={"request_id": str(uuid4()), "revision": 1}
            )
        ).status_code == 404
        assert (
            await other.post(ROOT, json=create_body(course_id=str(env.course_id)))
        ).status_code == 404
    assert (await env.client.get(f"{ROOT}/{row['id']}")).json() == row


@pytest.mark.asyncio
async def test_same_owner_different_workspace_has_no_board_or_course_authority(board_env):
    env = board_env
    row, _ = await create(env, course_id=str(env.course_id))
    async with env.factory() as database:
        workspace = Workspace(name="Separate workspace", workspace_type="personal")
        database.add(workspace)
        await database.flush()
        database.add(
            WorkspaceMembership(workspace_id=workspace.id, user_id=env.user_id, role="owner")
        )
        await database.commit()
        user = await database.get(User, env.user_id)
        account = CurrentAccount(user, workspace)
    env.app.dependency_overrides[get_current_account] = lambda: account
    env.client.headers["X-NavoX-Account"] = f"{workspace.id}:{env.user_id}"
    try:
        assert (await env.client.get(ROOT)).json()["boards"] == []
        assert (await env.client.get(f"{ROOT}/{row['id']}")).status_code == 404
        assert (
            await env.client.patch(f"{ROOT}/{row['id']}", json=update_body(row))
        ).status_code == 404
        deletion, _ = await erase(env, row)
        assert deletion.status_code == 404
        assert (
            await env.client.post(ROOT, json=create_body(course_id=str(env.course_id)))
        ).status_code == 404
    finally:
        env.app.dependency_overrides.pop(get_current_account)
        env.client.headers["X-NavoX-Account"] = f"{env.workspace_id}:{env.user_id}"
    assert (await env.client.get(f"{ROOT}/{row['id']}")).json() == row


@pytest.mark.asyncio
async def test_archived_course_retains_history_but_refuses_new_or_changed_board(board_env):
    env = board_env
    row, _ = await create(env, course_id=str(env.course_id))
    async with env.factory() as database:
        (await database.get(StudentCourse, env.course_id)).archived = True
        await database.commit()
    assert (await env.client.get(f"{ROOT}/{row['id']}")).status_code == 200
    assert (
        await env.client.post(ROOT, json=create_body(course_id=str(env.course_id)))
    ).status_code == 409
    assert (await env.client.patch(f"{ROOT}/{row['id']}", json=update_body(row))).status_code == 409
    deletion, _ = await erase(env, row)
    assert deletion.status_code == 204


@pytest.mark.asyncio
async def test_authentication_default_off_private_errors_and_origin_checks(board_env):
    env = board_env
    assert Settings(_env_file=None, app_environment="test").student_board_enabled is False
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as anonymous:
        rejected = await anonymous.get(ROOT)
        assert rejected.status_code == 401
        assert rejected.headers["cache-control"] == "private, no-store"
    env.settings.student_board_enabled = False
    assert (await env.client.get(ROOT)).status_code == 404
    assert (await env.client.post(ROOT, json=create_body())).status_code == 404
    env.settings.student_board_enabled = True
    for method in ("POST",):
        rejected = await env.client.request(
            method, ROOT, json=create_body(), headers={"Origin": "https://other.example"}
        )
        assert rejected.status_code == 403
        assert rejected.headers["cache-control"] == "private, no-store"
    row, _ = await create(env)
    for method, body in (
        ("PATCH", update_body(row)),
        ("DELETE", {"request_id": str(uuid4()), "revision": 1}),
    ):
        rejected = await env.client.request(
            method, f"{ROOT}/{row['id']}", json=body, headers={"Origin": "https://other.example"}
        )
        assert rejected.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"lesson_id": "unknown-lesson"},
        {"lesson_version": "0" * 64},
        {"version": 2},
        {"step_index": -1},
        {"step_index": 3},
        {"step_index": True},
        {"revealed": 4},
        {"revealed": -1},
        {"revealed": True},
        {"complete": True},
        {"title": ""},
        {"title": "x" * 161},
        {"unexpected": "private-secret"},
        {"answers": {"unknown-step": 0}},
        {"answers": {LESSON["steps"][0]["id"]: 0}},
        {"answers": {LESSON["steps"][1]["id"]: 3}},
        {"answers": {LESSON["steps"][1]["id"]: True}},
        {"marked": ["unknown-step"]},
        {"marked": [LESSON["steps"][0]["id"]] * 2},
        {"strokes": [{"id": 1, "points": [{"x": -1, "y": 0}]}]},
        {"strokes": [{"id": 1, "points": [{"x": 1121, "y": 0}]}]},
        {"strokes": [{"id": 1, "points": [{"x": 0, "y": 593}]}]},
        {"strokes": [{"id": True, "points": [{"x": 0, "y": 0}]}]},
        {"strokes": [{"id": 1, "points": []}]},
        {"strokes": [{"id": 1, "points": [{"x": 0, "y": 0}]}] * 2},
    ],
)
async def test_invalid_snapshot_is_sanitized_and_never_written(board_env, changes):
    env = board_env
    rejected = await env.client.post(ROOT, json=create_body(snapshot=snapshot(**changes)))
    assert rejected.status_code == 422, rejected.text
    assert PRIVATE_TITLE not in rejected.text and "private-secret" not in rejected.text
    assert rejected.headers["cache-control"] == "private, no-store"
    assert (await current(env))["boards"] == []


@pytest.mark.asyncio
async def test_complete_requires_final_reveal_and_final_check_but_not_unvisited_earlier_checks(
    board_env,
):
    env = board_env
    last = LESSON["steps"][-1]
    state = snapshot(step_index=len(LESSON["steps"]) - 1, revealed=last["actions"], complete=True)
    invalid = await env.client.post(ROOT, json=create_body(snapshot=state))
    assert invalid.status_code == 422
    state["answers"] = {last["id"]: 0}
    row, _ = await create(env, snapshot=state)
    assert row["complete"] is True and row["snapshot"]["answers"] == {last["id"]: 0}
    state["revealed"] -= 1
    assert (await env.client.post(ROOT, json=create_body(snapshot=state))).status_code == 422


@pytest.mark.asyncio
async def test_non_json_duplicate_keys_nonfinite_points_and_malformed_path_are_private(board_env):
    env = board_env
    for raw, content_type, status in (
        (b'{"snapshot":"private-secret"}', "text/plain", 415),
        (b'{"snapshot":"private-secret","snapshot":null}', "application/json", 422),
        (b'{"snapshot":"private-secret"', "application/json", 422),
    ):
        rejected = await env.client.post(ROOT, content=raw, headers={"Content-Type": content_type})
        assert rejected.status_code == status and "private-secret" not in rejected.text
        assert rejected.headers["cache-control"] == "private, no-store"
    for value in ("NaN", "Infinity", "-Infinity"):
        body = create_body(snapshot=snapshot(strokes=[{"id": 1, "points": [{"x": 0, "y": 0}]}]))
        raw = json.dumps(body).replace('"x": 0', f'"x": {value}')
        rejected = await env.client.post(
            ROOT, content=raw, headers={"Content-Type": "application/json"}
        )
        assert rejected.status_code == 422 and PRIVATE_TITLE not in rejected.text
    invalid_path = await env.client.get(f"{ROOT}/private-secret")
    assert invalid_path.status_code == 422 and "private-secret" not in invalid_path.text
    assert invalid_path.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_body_and_annotation_limits_are_enforced_before_storage(board_env):
    env = board_env
    points = [{"x": 1, "y": 1}] * 1501
    for strokes in (
        [{"id": 1, "points": points}],
        [{"id": index + 1, "points": [{"x": 1, "y": 1}]} for index in range(81)],
        [{"id": index + 1, "points": points[:-1]} for index in range(14)],
    ):
        rejected = await env.client.post(ROOT, json=create_body(snapshot=snapshot(strokes=strokes)))
        assert rejected.status_code == 422, rejected.text
    oversized = await env.client.post(
        ROOT, content=b"x" * (2 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"}
    )
    assert oversized.status_code == 413
    assert (await current(env))["boards"] == []


@pytest.mark.asyncio
async def test_account_scope_header_rejects_cookie_switch_before_private_read_or_write(board_env):
    env = board_env
    row, body = await create(env)
    original_scope = env.client.headers["X-NavoX-Account"]
    async with AsyncClient(
        transport=ASGITransport(app=env.app),
        base_url="http://testserver",
        headers={"Origin": env.settings.web_origin},
    ) as switched:
        registered = await switched.post(
            "/api/v1/auth/register",
            json={
                "email": "switched-board@example.com",
                "password": "twelve-character-password",
                "display_name": "Switched student",
            },
        )
        assert registered.status_code == 201
        other = registered.json()
        own_scope = f"{other['workspace']['id']}:{other['id']}"
        for old_scope in (None, original_scope, f"{other['workspace']['id']}:{env.user_id}"):
            switched.headers.pop("X-NavoX-Account", None)
            if old_scope is not None:
                switched.headers["X-NavoX-Account"] = old_scope
            for method, path, payload in (
                ("GET", ROOT, None),
                ("GET", f"{ROOT}/{row['id']}", None),
                ("POST", ROOT, body),
                ("PATCH", f"{ROOT}/{row['id']}", update_body(row)),
                ("DELETE", f"{ROOT}/{row['id']}", {"request_id": str(uuid4()), "revision": 1}),
            ):
                rejected = await switched.request(method, path, json=payload)
                assert rejected.status_code == 409, rejected.text
                assert PRIVATE_TITLE not in rejected.text
                assert rejected.headers["cache-control"] == "private, no-store"
        switched.headers["X-NavoX-Account"] = original_scope
        courses = await switched.get("/api/v1/student/courses")
        assert courses.status_code == 409 and "Circuits" not in courses.text
        assert courses.headers["cache-control"] == "private, no-store"
        switched.headers["X-NavoX-Account"] = own_scope
        assert (await switched.get("/api/v1/student/courses")).json() == {"courses": []}
        switched.headers.pop("X-NavoX-Account")
        assert (await switched.get("/api/v1/student/courses")).status_code == 200
        switched.headers["X-NavoX-Account"] = own_scope
        assert (await switched.get(ROOT)).json()["boards"] == []
    assert (await env.client.get(f"{ROOT}/{row['id']}")).json() == row


@pytest.mark.asyncio
async def test_course_erasure_helper_only_forgets_that_course_and_preserves_opaque_fences(
    board_env,
):
    from navox.board import service
    from navox.student import service as schedule

    env = board_env
    course_row, course_body = await create(env, course_id=str(env.course_id))
    standalone, standalone_body = await create(env, snapshot=snapshot(title="Unlinked board"))
    async with env.factory() as database:
        await schedule.owner(database, env.user_id, env.workspace_id, lock=True)
        await service.forget_board_data(
            database, env.user_id, env.workspace_id, course_id=env.course_id
        )
        await database.commit()
    listing = await current(env)
    assert [item["id"] for item in listing["boards"]] == [standalone["id"]]
    assert listing["generation"] == 1
    assert (await env.client.get(f"{ROOT}/{course_row['id']}")).status_code == 404
    assert (await env.client.post(ROOT, json=course_body)).status_code == 410
    assert (await env.client.post(ROOT, json=standalone_body)).json() == standalone


@pytest.mark.asyncio
async def test_database_course_cascade_removes_payload_but_retry_cannot_recreate_it(board_env):
    from navox.db.board import SavedBoard, SavedBoardCommand

    env = board_env
    row, body = await create(env, course_id=str(env.course_id))
    async with env.factory() as database:
        await database.delete(await database.get(StudentCourse, env.course_id))
        await database.commit()
        assert await database.get(SavedBoard, UUID(row["id"])) is None
        assert await database.scalar(select(func.count()).select_from(SavedBoardCommand)) == 1
    assert (await env.client.post(ROOT, json=body)).status_code == 410
    assert (await current(env))["boards"] == []


@pytest.mark.asyncio
async def test_prior_lesson_edition_stays_listable_and_erasable_but_cannot_open(
    board_env, monkeypatch
):
    from navox.board import schemas

    env = board_env
    row, _ = await create(env)
    changed_catalog = dict(schemas.authored_catalog())
    lesson = changed_catalog[LESSON["id"]]
    changed_catalog[LESSON["id"]] = lesson.model_copy(update={"lesson_version": "0" * 64})
    monkeypatch.setattr(schemas, "authored_catalog", lambda: changed_catalog)
    listing = await current(env)
    assert listing["boards"][0]["id"] == row["id"]
    unavailable = await env.client.get(f"{ROOT}/{row['id']}")
    assert unavailable.status_code == 503 and PRIVATE_TITLE not in unavailable.text
    assert (await erase(env, row))[0].status_code == 204


@pytest.mark.asyncio
async def test_quotas_bound_new_content_and_request_fences_without_blocking_erasure(
    board_env, monkeypatch
):
    from navox.board import service

    env = board_env
    monkeypatch.setattr(service, "MAX_BOARDS", 1)
    row, _ = await create(env)
    assert (await env.client.post(ROOT, json=create_body())).status_code == 409
    monkeypatch.setattr(service, "MAX_COMMANDS", 1)
    assert (await env.client.patch(f"{ROOT}/{row['id']}", json=update_body(row))).status_code == 409
    env.settings.student_board_enabled = False
    env.settings.connector_secret_encryption_key = None
    assert (await erase(env, row))[0].status_code == 204


@pytest.mark.asyncio
async def test_cors_preflight_allows_account_scope_header_only_for_configured_origin(board_env):
    env = board_env
    headers = {
        "Origin": env.settings.web_origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-navox-account",
    }
    allowed = await env.client.options(ROOT, headers=headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["access-control-allow-origin"] == env.settings.web_origin
    assert "x-navox-account" in allowed.headers["access-control-allow-headers"].lower()
    blocked = await env.client.options(ROOT, headers={**headers, "Origin": "https://other.example"})
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


@pytest.mark.parametrize(
    "title", ["", "   ", "<draft>", "x>y", "\x00private", "\x85private", "private\x85", "x\x9fy"]
)
def test_snapshot_title_rejects_unsafe_text_before_trim(title):
    from pydantic import ValidationError

    from navox.board.schemas import BoardSnapshot

    with pytest.raises(ValidationError):
        BoardSnapshot.model_validate(snapshot(title=title))
