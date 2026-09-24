import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from navox.connectors import activities
from navox.connectors.builtin.canvas import CanvasConnector
from navox.connectors.contracts import ConnectorConnectionContext
from navox.connectors.errors import retry_after_seconds
from navox.connectors.jobs import ConnectorSyncWork
from navox.connectors.secrets import ScopedSecretLease
from navox.workflows import connectors


@pytest.mark.asyncio
async def test_sync_activity_heartbeats_identifiers_and_propagates_cancellation(monkeypatch):
    from navox.intelligence import activities as intelligence_activities

    monkeypatch.setattr(intelligence_activities, "HEARTBEAT_INTERVAL_SECONDS", 0.005)
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def block(payload):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(activities, "_connector_sync", block)
    environment = ActivityEnvironment()
    seen = []
    environment.on_heartbeat = lambda *details: seen.append(details)
    payload = ConnectorSyncWork(*[str(uuid4()) for _ in range(4)])
    task = asyncio.create_task(environment.run(activities.connector_sync_activity, payload))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.sleep(0.02)
    environment.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and len(seen) >= 2
    for entry in seen:
        assert set(entry[0]) == {"connection_id", "user_id", "workspace_id", "elapsed_seconds"}
    count = len(seen)
    await asyncio.sleep(0.02)
    assert len(seen) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True])
async def test_workflow_heartbeat_timeout_preserves_old_history(monkeypatch, patched):
    seen = []

    async def activity(fn, payload, **options):
        seen.append(options)
        return 4

    monkeypatch.setattr(connectors.workflow, "execute_activity", activity)
    monkeypatch.setattr(connectors.workflow, "patched", lambda _: patched)
    assert (
        await connectors.ConnectorIncrementalSyncWorkflow().run(
            ConnectorSyncWork(*[str(uuid4()) for _ in range(4)])
        )
        == 4
    )
    assert seen[0]["heartbeat_timeout"] == (timedelta(seconds=60) if patched else None)
    assert seen[0]["retry_policy"].maximum_attempts == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True])
async def test_reconciliation_reexecutes_workflow_with_same_durable_request(monkeypatch, patched):
    seen = []

    async def child(fn, payload, **options):
        seen.append((payload, options["id"]))

    monkeypatch.setattr(connectors.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(connectors.workflow, "patched", lambda _: patched)
    monkeypatch.setattr(connectors.workflow, "uuid4", uuid4)
    payload = ConnectorSyncWork(*[str(uuid4()) for _ in range(4)])
    await connectors._run_one(payload, asyncio.Semaphore(1))
    await connectors._run_one(payload, asyncio.Semaphore(1))
    assert seen[0][0] == seen[1][0] == payload
    assert (seen[0][1] != seen[1][1]) is patched


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("60", 60),
        ("999999999", 86400),
        ("0", None),
        ("-3", None),
        ("NaN", None),
        ("private provider body", None),
        ("x" * 130, None),
        ("Thu, 24 Sep 2026 13:05:00 GMT", 300),
        ("Thu, 24 Sep 2026 12:00:00 GMT", None),
    ],
)
def test_retry_after_is_normalized_to_bounded_duration(value, expected):
    assert retry_after_seconds(value, now=datetime(2026, 9, 24, 13, tzinfo=UTC)) == expected


@pytest.mark.asyncio
async def test_canvas_health_preserves_provider_retry_after_without_header_text():
    async def handler(request):
        return httpx.Response(429, headers={"Retry-After": "123"})

    with ScopedSecretLease(uuid4(), "health.read", {"CANVAS_API_TOKEN": "fixture"}) as lease:
        canvas = CanvasConnector(
            {"base_url": "https://canvas.example.edu"},
            lease,
            transport=httpx.MockTransport(handler),
        )
        result = await canvas.health(
            ConnectorConnectionContext(
                id=lease.connection_id,
                workspace_id=uuid4(),
                user_id=uuid4(),
                connector_id="canvas-lms",
                provider="canvas",
                external_account_id="fixture",
                status="CONNECTED",
            )
        )
    assert result.state == "RATE_LIMITED" and result.retry_after_seconds == 123


def test_temporal_failure_carries_only_code_and_retry_duration():
    failure = activities._failure("RATE_LIMITED", retry_after_seconds=123)
    assert isinstance(failure, ApplicationError)
    assert failure.details == ({"code": "RATE_LIMITED", "retry_after_seconds": 123},)
    assert failure.next_retry_delay == timedelta(seconds=123)
    assert failure.non_retryable is False
