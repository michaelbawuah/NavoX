from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from temporalio.api.enums.v1 import PendingActivityState
from temporalio.api.workflow.v1 import PendingActivityInfo
from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from test_intelligence_runtime import runtime_env  # noqa: F401

from navox.api import intelligence_sync as sync
from navox.api.main import app
from navox.core.settings import Settings, get_settings
from navox.db.models import AuditEvent, Connection, User, WorkspaceMembership


@pytest_asyncio.fixture
async def owned_sync(runtime_env):  # noqa: F811
    client, factory = runtime_env
    async with factory() as db:
        user = await db.scalar(select(User).where(User.email == "owner@example.com"))
        member = await db.scalar(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)
        )
        connection = Connection(
            user_id=user.id,
            workspace_id=member.workspace_id,
            provider="google",
            external_account_id="status-test",
            granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        db.add(connection)
        await db.commit()
        workflow_id = f"intelligence:{connection.id}:gmail:{uuid4()}"
    return client, workflow_id


def fake_temporal(monkeypatch, status, *, pending=(), result=0, failure=None, history_length=12):
    description = SimpleNamespace(
        status=status,
        workflow_type="ProcessSourceEventWorkflow",
        run_id="exact-run",
        history_length=history_length,
        raw_description=DescribeWorkflowExecutionResponse(pending_activities=pending),
        data_converter=DataConverter.default,
    )
    handle = SimpleNamespace(
        describe=AsyncMock(return_value=description),
        result=AsyncMock(return_value=result, side_effect=failure),
    )
    calls = []

    def get_handle(workflow_id, **kwargs):
        calls.append((workflow_id, kwargs))
        return handle

    connect = AsyncMock(return_value=SimpleNamespace(get_workflow_handle=get_handle))
    monkeypatch.setattr(sync.Client, "connect", connect)
    return connect, handle, calls, description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (WorkflowExecutionStatus.RUNNING, "running"),
        (WorkflowExecutionStatus.COMPLETED, "completed"),
        (WorkflowExecutionStatus.CANCELED, "cancelled"),
        (WorkflowExecutionStatus.TERMINATED, "cancelled"),
        (WorkflowExecutionStatus.TIMED_OUT, "timed_out"),
        (WorkflowExecutionStatus.CONTINUED_AS_NEW, "unavailable"),
    ],
)
async def test_owned_sync_reports_actual_state_and_zero_results(
    owned_sync, monkeypatch, state, expected
):
    client, workflow_id = owned_sync
    _, handle, calls, _ = fake_temporal(monkeypatch, state)
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "workflow_id": workflow_id,
        "status": expected,
        "commitment_count": 0 if expected == "completed" else None,
        "error": None,
    }
    assert calls[-1] == (workflow_id, {"run_id": "exact-run"})
    assert handle.result.await_count == (1 if expected == "completed" else 0)


@pytest.mark.asyncio
async def test_scheduled_activity_is_queued_without_waiting_for_result(owned_sync, monkeypatch):
    client, workflow_id = owned_sync
    _, handle, _, _ = fake_temporal(
        monkeypatch,
        WorkflowExecutionStatus.RUNNING,
        pending=[
            PendingActivityInfo(
                state=PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED, attempt=1
            )
        ],
    )
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.json()["status"] == "queued"
    handle.result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostic",
    [
        {"code": "google_api_disabled", "http_status": 403},
        {"code": "provider_request_failed", "provider_code": "timeout"},
        {
            "code": "provider_request_failed",
            "provider_code": "rate_limited",
            "http_status": 429,
        },
    ],
)
async def test_retry_and_terminal_error_expose_only_allowlisted_diagnostics(
    owned_sync, monkeypatch, diagnostic
):
    client, workflow_id = owned_sync
    secret = "PRIVATE BODY TOKEN DO NOT RETURN"
    error = ApplicationError(
        secret,
        {**diagnostic, "message": secret},
        type="IntelligenceProcessingFailure",
    )
    from temporalio.api.failure.v1 import Failure

    failure = Failure()
    await DataConverter.default.encode_failure(error, failure)
    _, handle, _, description = fake_temporal(
        monkeypatch,
        WorkflowExecutionStatus.RUNNING,
        pending=[PendingActivityInfo(attempt=2, last_failure=failure)],
    )
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.json()["status"] == "retrying"
    assert response.json()["error"] == diagnostic
    assert secret not in response.text
    handle.result.assert_not_awaited()
    description.status = WorkflowExecutionStatus.FAILED
    handle.result.side_effect = WorkflowFailureError(cause=error)
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == diagnostic
    assert secret not in response.text


@pytest.mark.asyncio
async def test_unknown_failure_text_and_codes_never_escape(owned_sync, monkeypatch):
    client, workflow_id = owned_sync
    error = ApplicationError(
        "private email",
        {"code": "private email", "http_status": "private token", "secret": "private email"},
        type="IntelligenceProcessingFailure",
    )
    fake_temporal(
        monkeypatch, WorkflowExecutionStatus.FAILED, failure=WorkflowFailureError(cause=error)
    )
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.json()["error"] == {"code": "intelligence_processing_failed"}
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_infrastructure_failure_is_unavailable_not_job_failure(owned_sync, monkeypatch):
    client, workflow_id = owned_sync
    connect = AsyncMock(side_effect=RuntimeError("private infrastructure address"))
    monkeypatch.setattr(sync.Client, "connect", connect)
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.json()["status"] == "unavailable"
    assert response.json()["error"] is None
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_status_rejects_foreign_and_arbitrary_handles_before_temporal(
    owned_sync, monkeypatch
):
    client, workflow_id = owned_sync
    connect, _, _, _ = fake_temporal(monkeypatch, WorkflowExecutionStatus.COMPLETED)
    for invalid in [
        "intelligence-reconcile:" + str(uuid4()),
        workflow_id.replace(":gmail:", ":other:"),
        workflow_id.rsplit(":", 1)[0] + ":not-a-uuid",
        f"intelligence:{uuid4()}:gmail:{uuid4()}",
    ]:
        response = await client.get(
            "/api/v1/intelligence/sync/status", params={"workflow_id": invalid}
        )
        assert response.status_code == 404
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.status_code == 404
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_requires_login(owned_sync, monkeypatch):
    client, workflow_id = owned_sync
    connect, _, _, _ = fake_temporal(monkeypatch, WorkflowExecutionStatus.COMPLETED)
    client.cookies.clear()
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.status_code == 401
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_cooldown_blocks_dispatch_but_not_other_sources(
    owned_sync,
    runtime_env,  # noqa: F811
    monkeypatch,
):
    client, workflow_id = owned_sync
    _, factory = runtime_env
    connection_id = UUID(workflow_id.split(":")[1])
    now = datetime.now(UTC)
    async with factory() as database:
        connection = await database.get(Connection, connection_id)
        connection.granted_scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.events.readonly",
        ]
        audit = AuditEvent(
            user_id=connection.user_id,
            workspace_id=connection.workspace_id,
            event_type="intelligence.source.failed",
            actor_type="system",
            entity_type="connection",
            entity_id=connection_id,
            occurred_at=now,
            event_metadata={
                "source": "gmail",
                "error_diagnostic": {"code": "google_daily_limit_exceeded", "http_status": 403},
                "retry_not_before": (now + timedelta(seconds=300)).isoformat(),
            },
        )
        database.add(audit)
        await database.commit()
        audit_id = audit.id
    app.dependency_overrides[get_settings] = lambda: Settings(
        ai_provider="openai", openai_api_key="test-placeholder"
    )
    dispatch = AsyncMock(return_value=workflow_id)
    monkeypatch.setattr(sync, "dispatch_source", dispatch)
    payload = {"connection_id": str(connection_id), "source": "gmail"}
    response = await client.post("/api/v1/intelligence/sync", json=payload)
    assert response.status_code == 429
    assert 290 <= int(response.headers["retry-after"]) <= 300
    assert "Google Cloud" in response.json()["detail"]
    dispatch.assert_not_awaited()
    response = await client.post("/api/v1/intelligence/sync", json=dict(payload, source="calendar"))
    assert response.status_code == 202
    assert dispatch.await_count == 1
    async with factory() as database:
        audit = await database.get(AuditEvent, audit_id)
        audit.event_metadata = dict(
            audit.event_metadata, retry_not_before=(now - timedelta(seconds=1)).isoformat()
        )
        await database.commit()
    response = await client.post("/api/v1/intelligence/sync", json=payload)
    assert response.status_code == 202
    assert dispatch.await_count == 2
