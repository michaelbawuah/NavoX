from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

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
from navox.db.models import Connection, User, WorkspaceMembership


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
async def test_retry_and_terminal_error_expose_only_allowlisted_diagnostics(
    owned_sync, monkeypatch
):
    client, workflow_id = owned_sync
    secret = "PRIVATE BODY TOKEN DO NOT RETURN"
    error = ApplicationError(
        secret,
        {"code": "google_api_disabled", "http_status": 403, "message": secret},
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
    assert response.json()["error"] == {"code": "google_api_disabled", "http_status": 403}
    assert secret not in response.text
    handle.result.assert_not_awaited()
    description.status = WorkflowExecutionStatus.FAILED
    handle.result.side_effect = WorkflowFailureError(cause=error)
    response = await client.get(
        "/api/v1/intelligence/sync/status", params={"workflow_id": workflow_id}
    )
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == {"code": "google_api_disabled", "http_status": 403}
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
