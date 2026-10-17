"""Private email API boundaries with fake domain services/readers and no network."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from navox.api import student_email
from navox.api.auth import CurrentAccount, get_current_account
from navox.core.settings import Settings, get_settings
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.db.session import get_database_session

ROOT = "/api/v1/student/email"


@pytest_asyncio.fixture
async def env(ai_database, monkeypatch):
    settings = Settings(_env_file=None, app_environment="test").model_copy(
        update={"student_email_enabled": True}
    )
    user_id, workspace_id, source_id, review_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with ai_database() as database:
        user = User(id=user_id, email="email-api@example.com")
        workspace = Workspace(id=workspace_id, name="Student", workspace_type="personal")
        database.add_all([user, workspace])
        await database.flush()
        database.add(WorkspaceMembership(user_id=user_id, workspace_id=workspace_id, role="owner"))
        await database.commit()
    account = CurrentAccount(user, workspace)

    async def database():
        async with ai_database() as current:
            yield current

    app = FastAPI()
    app.include_router(student_email.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_account] = lambda: account
    operations = {}
    for name in (
        "list_sources",
        "analyze",
        "read_review",
        "correct",
        "accept_task",
        "erase_review",
        "forget_email_data",
    ):
        operation = AsyncMock(return_value={"operation": name, "actions_executed": False})
        monkeypatch.setattr(student_email.service, name, operation, raising=False)
        operations[name] = operation
    reader = SimpleNamespace(
        read=AsyncMock(side_effect=AssertionError("API must not call provider readers"))
    )

    def getter():
        return reader

    monkeypatch.setattr(student_email, "get_email_reader", getter)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": settings.web_origin},
    ) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            settings=settings,
            factory=ai_database,
            user_id=user_id,
            workspace_id=workspace_id,
            source_id=source_id,
            review_id=review_id,
            operations=operations,
            reader=reader,
            monkeypatch=monkeypatch,
        )


def analyze_input(env):
    return {"request_id": str(uuid4()), "source_id": str(env.source_id), "source_hash": "a" * 64}


def correction_input():
    return {
        "request_id": str(uuid4()),
        "revision": 1,
        "category": "has_deadline",
        "not_important": False,
        "course_id": None,
        "title": "Review assignment",
        "due_at": None,
        "estimated_minutes": 30,
        "confirmed_course": False,
        "confirmed_deadline": False,
    }


def accept_input():
    return {"request_id": str(uuid4()), "revision": 1, "source_hash": "a" * 64, "confirmed": True}


def assert_private(response):
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_overall_disabled_stops_before_body_validation_or_reader_build(env):
    env.app.dependency_overrides[get_settings] = lambda: env.settings.model_copy(
        update={"student_email_enabled": False}
    )

    def forbidden():
        pytest.fail("Disabled feature must not construct a reader")

    env.monkeypatch.setattr(student_email, "get_email_reader", forbidden)

    async def unread():
        pytest.fail("Disabled feature must not consume JSON")
        yield b""

    for method, path in [
        ("GET", "/sources"),
        ("POST", "/analyze"),
        ("GET", "/reviews/invalid-private-id"),
        ("PUT", "/reviews/invalid-private-id"),
        ("POST", "/reviews/invalid-private-id/accept-task"),
    ]:
        response = await env.client.request(
            method, ROOT + path, content=unread() if method in {"POST", "PUT"} else None
        )
        assert response.status_code == 503
        assert_private(response)
    assert all(not operation.called for operation in env.operations.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": "a"},
        {"q": " "},
        {"q": "x" * 121},
        {"q": "secret\nquery"},
        {"q": "deadline", "provider": "gmail"},
        [("q", "first"), ("q", "second")],
    ],
)
async def test_sources_require_one_explicit_bounded_search_without_echo(env, params):
    response = await env.client.get(ROOT + "/sources", params=params)
    assert response.status_code == 422
    assert_private(response)
    assert response.json() == {"detail": "Enter an explicit email search of 2 to 120 characters"}
    env.operations["list_sources"].assert_not_called()


@pytest.mark.asyncio
async def test_sources_forward_only_current_owned_scope_and_explicit_query(env):
    response = await env.client.get(ROOT + "/sources", params={"q": "  homework due  "})
    assert response.status_code == 200
    assert_private(response)
    assert env.operations["list_sources"].call_args.kwargs == {
        "user_id": env.user_id,
        "workspace_id": env.workspace_id,
        "query": "homework due",
    }
    env.reader.read.assert_not_called()


@pytest.mark.asyncio
async def test_valid_routes_dispatch_fixed_services_with_server_scope_and_typed_payloads(env):
    response = await env.client.post(ROOT + "/analyze", json=analyze_input(env))
    assert response.status_code == 200
    assert_private(response)
    call = env.operations["analyze"].call_args
    assert call.kwargs["user_id"] == env.user_id and call.kwargs["workspace_id"] == env.workspace_id
    assert call.kwargs["reader"] is env.reader
    assert call.kwargs["payload"].source_id == env.source_id
    for method, suffix, body, operation in [
        ("GET", "", None, "read_review"),
        ("PUT", "", correction_input(), "correct"),
        ("POST", "/accept-task", accept_input(), "accept_task"),
        ("DELETE", "", {"revision": 1}, "erase_review"),
    ]:
        response = await env.client.request(
            method, f"{ROOT}/reviews/{env.review_id}{suffix}", json=body if body else None
        )
        assert response.status_code == 200, response.text
        assert_private(response)
        assert env.operations[operation].call_args.kwargs["review_id"] == env.review_id
        assert env.operations[operation].call_args.kwargs["user_id"] == env.user_id
    env.reader.read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,method,body",
    [
        ("/analyze", "POST", {"provider": "openai", "raw_message": "PRIVATE BODY"}),
        (
            "/reviews/{review_id}",
            "PUT",
            {"revision": 1, "category": "execute", "secret": "PRIVATE BODY"},
        ),
        (
            "/reviews/{review_id}/accept-task",
            "POST",
            {"revision": 1, "confirmed": 1, "secret": "PRIVATE BODY"},
        ),
        ("/reviews/{review_id}", "DELETE", {"revision": True, "secret": "PRIVATE BODY"}),
    ],
)
async def test_invalid_private_json_is_fixed_and_never_constructs_reader(env, path, method, body):
    def forbidden():
        pytest.fail("Invalid private JSON must not build a reader")

    env.monkeypatch.setattr(student_email, "get_email_reader", forbidden)
    response = await env.client.request(
        method, ROOT + path.format(review_id=env.review_id), json=body
    )
    assert response.status_code == 422
    assert_private(response)
    assert "PRIVATE BODY" not in response.text and "secret" not in response.text
    assert all(not operation.called for operation in env.operations.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/analyze"),
        ("PUT", "/reviews/{review_id}"),
        ("POST", "/reviews/{review_id}/accept-task"),
        ("DELETE", "/reviews/{review_id}"),
        ("DELETE", "/sources/{source_id}"),
    ],
)
async def test_origin_rejected_before_domain_work(env, method, path):
    response = await env.client.request(
        method,
        ROOT + path.format(review_id=env.review_id, source_id=env.source_id),
        json={"secret": "PRIVATE BODY"},
        headers={"Origin": "https://evil.test"},
    )
    assert response.status_code == 403
    assert_private(response)
    assert "PRIVATE BODY" not in response.text
    assert all(not operation.called for operation in env.operations.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["team", "member", "removed"])
async def test_current_personal_owner_is_rechecked_before_reader_or_domain_calls(env, mutation):
    async with env.factory() as database:
        if mutation == "team":
            (await database.get(Workspace, env.workspace_id)).workspace_type = "team"
        else:
            membership = await database.get(WorkspaceMembership, (env.workspace_id, env.user_id))
            if mutation == "member":
                membership.role = "member"
            else:
                await database.delete(membership)
        await database.commit()
    response = await env.client.post(ROOT + "/analyze", json=analyze_input(env))
    assert response.status_code == 403
    assert_private(response)
    assert all(not operation.called for operation in env.operations.values())


@pytest.mark.asyncio
async def test_framework_uuid_errors_and_signed_out_errors_are_private_without_echo(env):
    response = await env.client.get(ROOT + "/reviews/PRIVATE-SOURCE-PATH")
    assert response.status_code == 422
    assert_private(response)
    assert "PRIVATE-SOURCE-PATH" not in response.text
    del env.app.dependency_overrides[get_current_account]
    response = await env.client.get(ROOT + "/sources", params={"q": "due"})
    assert response.status_code == 401
    assert_private(response)
    assert all(not operation.called for operation in env.operations.values())


@pytest.mark.asyncio
async def test_media_length_chunked_bytes_and_timeout_are_bounded_before_reader(env):
    response = await env.client.post(
        ROOT + "/analyze", content="PRIVATE", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415
    for declared in ("8193", "-1", "not-a-length"):
        response = await env.client.post(
            ROOT + "/analyze",
            content="{}",
            headers={"Content-Type": "application/json", "Content-Length": declared},
        )
        assert response.status_code in {413, 422}
        assert_private(response)

    async def chunks():
        yield b"a" * 4096
        yield b"b" * 4097

    response = await env.client.post(
        ROOT + "/analyze", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert_private(response)
    env.monkeypatch.setattr(student_email, "JSON_DEADLINE_SECONDS", 0.005)

    async def drip():
        await asyncio.sleep(0.05)
        yield b"{}"

    response = await env.client.post(
        ROOT + "/analyze", content=drip(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 408
    assert_private(response)
    assert all(not operation.called for operation in env.operations.values())


@pytest.mark.asyncio
async def test_owner_erasure_remains_available_when_feature_disabled(env):
    env.app.dependency_overrides[get_settings] = lambda: env.settings.model_copy(
        update={"student_email_enabled": False}
    )
    response = await env.client.request(
        "DELETE", f"{ROOT}/reviews/{env.review_id}", json={"revision": 1}
    )
    assert response.status_code == 200
    assert_private(response)
    response = await env.client.delete(f"{ROOT}/sources/{env.source_id}")
    assert response.status_code == 200
    assert_private(response)
    assert env.operations["forget_email_data"].call_args.kwargs == {
        "user_id": env.user_id,
        "workspace_id": env.workspace_id,
        "source_id": env.source_id,
    }
    response = await env.client.request(
        "DELETE", f"{ROOT}/sources/{env.source_id}", json={"source_id": str(uuid4())}
    )
    assert response.status_code == 422
    assert env.operations["forget_email_data"].call_count == 1


@pytest.mark.asyncio
async def test_domain_unconfigured_source_changed_or_foreign_failure_preserves_status(env):
    for status in (403, 404, 409, 503):
        env.operations["read_review"].side_effect = student_email.service.EmailError(
            "This email review is unavailable", status
        )
        response = await env.client.get(f"{ROOT}/reviews/{env.review_id}")
        assert response.status_code == status
        assert_private(response)
        assert response.json() == {"detail": "This email review is unavailable"}
