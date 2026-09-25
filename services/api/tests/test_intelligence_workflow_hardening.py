import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.exceptions import CancelledError
from temporalio.testing import ActivityEnvironment

from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import Connection, IncomingEvent, User, Workspace
from navox.intelligence import activities
from navox.intelligence.jobs import SourceWork, WorkspaceWork
from navox.workflows import intelligence


@pytest.mark.asyncio
async def test_slow_source_activity_heartbeats_only_identifiers_and_stops_on_completion(
    monkeypatch,
):
    payload = SourceWork(str(uuid4()), str(uuid4()), str(uuid4()), "gmail")
    environment = ActivityEnvironment()
    heartbeats = []
    alive = asyncio.Event()

    def heartbeat(details):
        heartbeats.append(details)
        if len(heartbeats) >= 3:
            alive.set()

    async def slow_source(_payload):
        await asyncio.wait_for(alive.wait(), timeout=1)
        return 3

    environment.on_heartbeat = heartbeat
    monkeypatch.setattr(activities, "HEARTBEAT_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(activities, "_process_source", slow_source)

    assert await environment.run(activities.process_source_activity, payload) == 3
    count = len(heartbeats)
    await asyncio.sleep(0.02)
    assert len(heartbeats) == count
    for details in heartbeats:
        assert details == {
            "connection_id": payload.connection_id,
            "user_id": payload.user_id,
            "workspace_id": payload.workspace_id,
            "elapsed_seconds": 0,
        }


@pytest.mark.asyncio
async def test_activity_cancellation_interrupts_provider_wait_and_stops_heartbeats(monkeypatch):
    payload = SourceWork(str(uuid4()), str(uuid4()), str(uuid4()), "gmail")
    environment = ActivityEnvironment()
    started = asyncio.Event()
    cleaned_up = asyncio.Event()
    heartbeats = []

    async def waiting_source(_payload):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    environment.on_heartbeat = lambda details: heartbeats.append(details)
    monkeypatch.setattr(activities, "HEARTBEAT_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(activities, "_process_source", waiting_source)
    task = asyncio.create_task(environment.run(activities.process_source_activity, payload))
    await asyncio.wait_for(started.wait(), timeout=1)
    environment.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned_up.is_set()
    assert heartbeats
    count = len(heartbeats)
    await asyncio.sleep(0.02)
    assert len(heartbeats) == count


@pytest.mark.asyncio
async def test_source_attempt_has_bounded_long_timeout_and_fast_worker_loss_detection(monkeypatch):
    payload = SourceWork(str(uuid4()), str(uuid4()), str(uuid4()), "gmail")
    recorded = []

    async def execute_activity(fn, received, **options):
        recorded.append((fn, received, options))
        return 2

    async def execute_child(*args, **kwargs):
        return 0

    monkeypatch.setattr(intelligence.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(intelligence.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(intelligence.workflow, "uuid4", uuid4)

    assert await intelligence.ProcessSourceEventWorkflow().run(payload) == 2
    assert recorded[0][0] is activities.process_source_activity
    assert recorded[0][1] == payload
    options = recorded[0][2]
    assert options["start_to_close_timeout"] == timedelta(hours=2)
    assert options["heartbeat_timeout"] == timedelta(seconds=60)
    assert options["retry_policy"].maximum_attempts == 3


@pytest.mark.asyncio
async def test_reconciliation_limits_total_concurrency_and_slow_accounts_do_not_block_others(
    monkeypatch,
):
    workspaces = [WorkspaceWork(f"user-{index}", f"workspace-{index}") for index in range(4)]
    sources = [
        SourceWork(f"connection-{index}", "user", "workspace", "gmail") for index in range(5)
    ]
    active = peak = 0
    finished = []
    fast_work_finished = asyncio.Event()
    release_slow = asyncio.Event()

    async def execute_child(fn, payload, **options):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        try:
            if payload == workspaces[0]:
                await release_slow.wait()
            else:
                await asyncio.sleep(0.005)
            if payload == sources[0]:
                raise RuntimeError("One account is temporarily unavailable")
            return 1
        finally:
            active -= 1
            finished.append(payload)
            if len(finished) == len(workspaces) + len(sources) - 1:
                fast_work_finished.set()

    monkeypatch.setattr(intelligence.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(intelligence.workflow, "uuid4", uuid4)
    monkeypatch.setattr(intelligence.workflow, "logger", logging.getLogger(__name__))
    task = asyncio.create_task(intelligence._reconcile_batch(workspaces, sources))
    try:
        await asyncio.wait_for(fast_work_finished.wait(), timeout=1)
        assert workspaces[0] not in finished
        assert sources[-1] in finished
        assert active == 1
        assert peak == 4
    finally:
        release_slow.set()
        await task
    assert active == 0
    assert len(finished) == 9


@pytest.mark.asyncio
async def test_reconciliation_does_not_swallow_cancellation(monkeypatch):
    async def cancel_child(*args, **kwargs):
        raise CancelledError("cancelled")

    monkeypatch.setattr(intelligence.workflow, "execute_child_workflow", cancel_child)
    monkeypatch.setattr(intelligence.workflow, "uuid4", uuid4)
    with pytest.raises(CancelledError):
        await intelligence._refresh_workspace(WorkspaceWork("user", "workspace"))
    with pytest.raises(CancelledError):
        await intelligence._process_pending(SourceWork("connection", "user", "workspace", "gmail"))


@pytest.mark.asyncio
@pytest.mark.parametrize("patched", [False, True])
async def test_reconciliation_preserves_old_command_order_when_replaying(monkeypatch, patched):
    workspace = WorkspaceWork("user", "workspace")
    source = SourceWork("connection", "user", "workspace", "gmail")
    commands = []

    class EndIteration(BaseException):
        pass

    async def execute_activity(fn, **kwargs):
        if fn is activities.intelligence_workspaces_activity:
            commands.append("workspaces")
            return [workspace]
        commands.append("pending")
        return [source]

    async def execute_child(fn, payload, **kwargs):
        commands.append("refresh" if isinstance(payload, WorkspaceWork) else "process")
        return 1

    async def sleep(_duration):
        raise EndIteration

    monkeypatch.setattr(intelligence.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(intelligence.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(intelligence.workflow, "uuid4", uuid4)
    monkeypatch.setattr(intelligence.workflow, "sleep", sleep)
    monkeypatch.setattr(intelligence.workflow, "patched", lambda _id: patched)
    with pytest.raises(EndIteration):
        await intelligence.IntelligenceReconciliationWorkflow().run()
    expected = (
        ["workspaces", "pending", "refresh", "process"]
        if patched
        else ["workspaces", "refresh", "pending", "process"]
    )
    assert commands == expected


@pytest.mark.asyncio
async def test_pending_reconciliation_coalesces_source_bursts_without_discarding_events(
    monkeypatch,
):
    from navox.intelligence import ingestion

    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as database:
            user = User(email="owner@example.com")
            workspace = Workspace(name="Test")
            database.add_all([user, workspace])
            await database.flush()
            connections = [
                Connection(
                    user_id=user.id,
                    workspace_id=workspace.id,
                    provider="google",
                    external_account_id=f"account-{index}",
                    granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                )
                for index in range(2)
            ]
            database.add_all(connections)
            await database.flush()
            events = [
                IncomingEvent(
                    connection_id=connections[0 if index < 3 else 1].id,
                    user_id=user.id,
                    workspace_id=workspace.id,
                    provider="google",
                    source="gmail",
                    event_type="change",
                    external_event_id=f"event-{index}",
                    payload_hash="a" * 64,
                    received_at=datetime.now(UTC) + timedelta(seconds=index),
                )
                for index in range(4)
            ]
            database.add_all(events)
            await database.commit()

        async def renew_watch(*args, **kwargs):
            pass

        monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
        monkeypatch.setattr(activities, "get_settings", lambda: Settings(ai_provider="openai"))
        monkeypatch.setattr(ingestion, "renew_source_watch", renew_watch)
        pending = await activities.pending_intelligence_activity()
        assert [payload.event_id for payload in pending] == [str(events[0].id), str(events[3].id)]
        async with factory() as database:
            remaining = list(await database.scalars(select(IncomingEvent)))
            assert len(remaining) == 4
            assert all(event.intelligence_status == "pending" for event in remaining)
    finally:
        await engine.dispose()
