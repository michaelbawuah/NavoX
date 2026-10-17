"""Email wiring and scheduled expiry cleanup, with no mail/model/provider calls."""

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from navox.core.settings import Settings, get_settings
from navox.db.models import User, Workspace
from navox.db.student_email import StudentEmailReview
from navox.student_email import activities, reader
from navox.workflows import student_email as email_workflow


def test_email_is_default_off_and_metadata_registers_without_service_import(monkeypatch):
    monkeypatch.delenv("STUDENT_EMAIL_ENABLED", raising=False)
    assert Settings(_env_file=None, app_environment="test").student_email_enabled is False
    # A fresh process proves models.py itself registers both new tables.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import navox.db.models\n"
            "from navox.db.base import Base\n"
            "assert 'student_email_reviews' in Base.metadata.tables\n"
            "assert 'student_email_task_sources' in Base.metadata.tables\n",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_main_router_registered_once_but_disabled_before_reader_or_database(monkeypatch):
    from navox.api import student_email
    from navox.api.main import create_app
    from navox.db.session import get_database_session

    settings = Settings(_env_file=None, app_environment="test", student_email_enabled=False)
    app = create_app()

    def forbidden():
        pytest.fail("Default-off email must not construct a reader")

    async def no_database():
        pytest.fail("Default-off email must not open its database dependency")
        yield

    monkeypatch.setattr(student_email, "get_email_reader", forbidden)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_database_session] = no_database
    expected = {
        ("GET", "/api/v1/student/email/sources"),
        ("POST", "/api/v1/student/email/analyze"),
        ("GET", "/api/v1/student/email/reviews/{review_id}"),
        ("PUT", "/api/v1/student/email/reviews/{review_id}"),
        ("POST", "/api/v1/student/email/reviews/{review_id}/accept-task"),
        ("DELETE", "/api/v1/student/email/reviews/{review_id}"),
        ("DELETE", "/api/v1/student/email/sources/{source_id}"),
    }
    # FastAPI keeps included routers deferred; OpenAPI resolves their routes.
    assert (
        sum(getattr(route, "original_router", None) is student_email.router for route in app.routes)
        == 1
    )
    paths = app.openapi()["paths"]
    for method, path in expected:
        assert method.lower() in paths[path]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/student/email/analyze", content="private malformed body"
        )
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"
        assert "private malformed body" not in response.text


@pytest.mark.asyncio
async def test_email_cleanup_activity_commits_expiry_with_creation_and_ai_disabled(
    ai_database, monkeypatch
):
    now = datetime.now(UTC)
    user_id, workspace_id, review_id = uuid4(), uuid4(), uuid4()
    async with ai_database() as database:
        database.add_all(
            [
                User(id=user_id, email="cleanup@example.com"),
                Workspace(id=workspace_id, name="Cleanup", workspace_type="personal"),
            ]
        )
        await database.flush()
        database.add(
            StudentEmailReview(
                id=review_id,
                user_id=user_id,
                workspace_id=workspace_id,
                request_id=uuid4(),
                payload_hash="a" * 64,
                source_id=None,
                source_hash="b" * 64,
                authority_hash="c" * 64,
                revision=1,
                encrypted_payload="opaque-ciphertext",
                expires_at=now - timedelta(seconds=1),
                purge_after=now + timedelta(days=6),
            )
        )
        await database.commit()

    # Exact production factory signature: passing settings here must fail this test.
    def factory():
        return ai_database

    async def forbidden(*args, **kwargs):
        pytest.fail("Expiry cleanup must not read mail or call a provider")

    monkeypatch.setattr(activities, "get_session_factory", factory)
    monkeypatch.setattr(reader.GmailEmailReader, "read", forbidden)
    assert (
        Settings(
            _env_file=None,
            app_environment="test",
            student_email_enabled=False,
            ai_provider="disabled",
        ).student_email_enabled
        is False
    )
    assert await activities.cleanup_email_reviews_activity() == 1
    async with ai_database() as database:
        row = await database.get(StudentEmailReview, review_id)
        assert row is not None
        assert row.encrypted_payload is None and row.source_id is None and row.source_hash is None
        assert row.payload_hash == "a" * 64  # A content-free retry fence remains.
    assert await activities.cleanup_email_reviews_activity() == 0


@pytest.mark.asyncio
async def test_email_cleanup_activity_caps_batches_and_commits_each(monkeypatch):
    commits, limits = [], []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def commit(self):
            commits.append(True)

    def factory():
        return Transaction

    async def backlog(database, *, limit):
        limits.append(limit)
        return limit

    monkeypatch.setattr(activities, "get_session_factory", factory)
    monkeypatch.setattr(activities, "cleanup_email_reviews", backlog)
    assert await activities.cleanup_email_reviews_activity() == 2000
    assert limits == [200] * 10 and len(commits) == 10


@pytest.mark.asyncio
async def test_email_cleanup_workflow_calls_only_cleanup_and_waits_one_minute(monkeypatch):
    calls = []

    class Stopped(Exception):
        pass

    async def execute(target, **kwargs):
        calls.append((target, kwargs))
        return 0

    async def sleep(delay):
        assert delay == timedelta(minutes=1)
        raise Stopped

    monkeypatch.setattr(email_workflow.workflow, "execute_activity", execute)
    monkeypatch.setattr(email_workflow.workflow, "sleep", sleep)
    with pytest.raises(Stopped):
        await email_workflow.StudentEmailCleanupWorkflow().run()
    assert len(calls) == 1 and calls[0][0] is activities.cleanup_email_reviews_activity
    assert calls[0][1]["start_to_close_timeout"] == timedelta(minutes=2)
    assert calls[0][1]["retry_policy"].maximum_attempts == 2


@pytest.mark.asyncio
async def test_worker_registers_and_starts_email_singleton_while_flags_disabled(monkeypatch):
    from navox.workflows import worker

    settings = Settings(
        _env_file=None, app_environment="test", student_email_enabled=False, ai_provider="disabled"
    )
    registration, calls = {}, []

    async def start(run, **kwargs):
        calls.append((run, kwargs))

    async def connect(*args, **kwargs):
        return SimpleNamespace(start_workflow=start)

    class FakeWorker:
        def __init__(self, client, **kwargs):
            registration.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def stop():
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker.Client, "connect", connect)
    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker.asyncio, "Event", lambda: SimpleNamespace(wait=stop))
    with pytest.raises(asyncio.CancelledError):
        await worker.main()
    assert registration["workflows"].count(email_workflow.StudentEmailCleanupWorkflow) == 1
    assert registration["activities"].count(activities.cleanup_email_reviews_activity) == 1
    selected = [
        kwargs for run, kwargs in calls if run == email_workflow.StudentEmailCleanupWorkflow.run
    ]
    assert len(selected) == 1 and selected[0]["id"] == "navox-student-email-cleanup-v1"
    assert selected[0]["task_queue"] == settings.temporal_task_queue


@pytest.mark.asyncio
async def test_email_cleanup_prepares_in_actual_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    SandboxedWorkflowRunner().prepare_workflow(
        workflow._Definition.must_from_class(email_workflow.StudentEmailCleanupWorkflow)
    )
