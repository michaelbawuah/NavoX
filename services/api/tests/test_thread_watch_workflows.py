"""Durable watches run without AI and recover only explicitly saved work."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from navox.core.settings import Settings
from navox.thread_watches import dispatcher, workflows
from navox.thread_watches.activities import ThreadWatchWork


@pytest.mark.asyncio
async def test_watch_workflows_load_in_temporal_sandbox():
    runner = SandboxedWorkflowRunner()
    for cls in (workflows.ThreadWatchCheckWorkflow, workflows.ThreadWatchReconciliationWorkflow):
        runner.prepare_workflow(workflow._Definition.must_from_class(cls))


@pytest.mark.asyncio
async def test_reconciliation_bounds_checks_and_carries_only_identifiers(monkeypatch):
    pending = [ThreadWatchWork(str(uuid4()), 3) for _ in range(12)]
    state = {"active": 0, "maximum": 0}
    completed = []

    async def activity(*args, **kwargs):
        return pending

    async def child(run, payload, **kwargs):
        assert set(vars(payload)) == {"watch_id", "revision"}
        state["active"] += 1
        state["maximum"] = max(state["maximum"], state["active"])
        await asyncio.sleep(0)
        completed.append(payload.watch_id)
        state["active"] -= 1
        return "watching"

    class StopIterationAfterBatch(Exception):
        pass

    async def sleep(delay):
        assert delay.total_seconds() == 60
        raise StopIterationAfterBatch

    monkeypatch.setattr(workflows.workflow, "execute_activity", activity)
    monkeypatch.setattr(workflows.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(workflows.workflow, "sleep", sleep)
    monkeypatch.setattr(workflows.workflow, "uuid4", uuid4)
    with pytest.raises(StopIterationAfterBatch):
        await workflows.ThreadWatchReconciliationWorkflow().run()
    assert state["maximum"] == 4
    assert set(completed) == {item.watch_id for item in pending}


@pytest.mark.asyncio
async def test_singleton_dispatch_is_idempotent_and_independent_of_ai(monkeypatch):
    calls = []

    async def start(*args, **kwargs):
        calls.append(kwargs)
        raise WorkflowAlreadyStartedError("existing", "ThreadWatchReconciliationWorkflow")

    async def connect(target):
        return SimpleNamespace(start_workflow=start)

    monkeypatch.setattr(dispatcher.Client, "connect", connect)
    settings = Settings(_env_file=None, ai_provider="disabled")
    assert await dispatcher.dispatch_thread_watch_reconciliation(settings=settings) == (
        dispatcher.RECONCILIATION_WORKFLOW_ID
    )
    assert calls[0]["id"] == dispatcher.RECONCILIATION_WORKFLOW_ID
    assert calls[0]["task_queue"] == settings.temporal_task_queue


@pytest.mark.asyncio
async def test_immediate_dispatch_contains_only_watch_id_and_revision(monkeypatch):
    calls = []

    async def start(run, payload, **kwargs):
        calls.append((payload, kwargs))

    async def connect(target):
        return SimpleNamespace(start_workflow=start)

    monkeypatch.setattr(dispatcher.Client, "connect", connect)
    watch_id = uuid4()
    result = await dispatcher.dispatch_thread_watch(
        watch_id=watch_id, revision=7, settings=Settings(_env_file=None, ai_provider="disabled")
    )
    assert calls[0][0] == ThreadWatchWork(str(watch_id), 7)
    assert str(watch_id) in result
