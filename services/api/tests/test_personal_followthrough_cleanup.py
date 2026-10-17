"""Connected-data deletion erases follow-through copies, not explicit memory."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_connection_management import env as connection_management_env

from navox.api import actions, thread_watches
from navox.approvals.service import GMAIL_SEND_SCOPE
from navox.db.memory import OwnerMemory
from navox.db.models import (
    Action,
    Approval,
    Connection,
    Plan,
    PlanStep,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.routines import PersonalRoutine, RoutineRun
from navox.db.thread_watches import ThreadWatch

# Re-export the shared SQLite/PostgreSQL fixture without importing a name that
# Ruff would treat as an unused shadowed argument in the tests below.
env = connection_management_env

CONFIG = {
    "name": "Saved priorities",
    "recipe": "morning_priorities",
    "timezone": "UTC",
    "hour": 8,
    "minute": 0,
    "days_of_week": [0, 1, 2, 3, 4],
    "enabled": False,
}


async def seed_followthrough(env, monkeypatch):
    now = datetime.now(UTC)
    dispatches = []

    async def approval_stub(*args, **kwargs):
        dispatches.append("approval")

    async def watch_stub(**kwargs):
        dispatches.append("watch")
        return "synthetic-watch-workflow"

    async def forbidden(*args, **kwargs):
        raise AssertionError("Cleanup verification must not call a provider or Temporal")

    monkeypatch.setattr(actions, "ensure_workflow", approval_stub)
    monkeypatch.setattr(thread_watches, "dispatch_thread_watch", watch_stub)
    from navox.thread_watches import service as watch_service

    monkeypatch.setattr(watch_service, "access_token_for_connection", forbidden)
    async with env.factory() as database:
        connection = await database.get(Connection, env.id)
        connection.granted_scopes = [*connection.granted_scopes, GMAIL_SEND_SCOPE]
        await database.commit()
    commitment = await env.client.post(
        "/api/v1/commitments",
        json={
            "request_id": str(uuid4()),
            "type": "task",
            "title": "Synthetic connected request",
            "priority": 3,
        },
    )
    assert commitment.status_code == 201, commitment.text
    prepared = await env.client.post(
        f"/api/v1/commitments/{commitment.json()['id']}/actions/gmail-send/prepare",
        json={
            "request_id": str(uuid4()),
            "connection_id": str(env.id),
            "to": "synthetic-recipient@example.com",
            "subject": "Connected subject to erase",
            "body_text": "Synthetic prepared fixture. No provider execution occurs.",
            "post_send_state": "waiting",
        },
    )
    assert prepared.status_code == 202, prepared.text
    action_id, plan_id = UUID(prepared.json()["id"]), UUID(prepared.json()["plan_id"])
    # A controlled fixture outcome permits the watch API without sending an email
    # or approving an action. No provider executor or workflow is invoked.
    async with env.factory() as database:
        action = await database.get(Action, action_id)
        action.status = "completed"
        action.executed_at = now - timedelta(hours=2)
        action.verified_at = now - timedelta(hours=1)
        action.result = {"message_id": "fixture_sent", "thread_id": "fixture_thread"}
        step = await database.get(PlanStep, action.plan_step_id)
        step.status = "completed"
        plan = await database.get(Plan, plan_id)
        plan.status = "completed"
        approval = await database.scalar(select(Approval).where(Approval.action_id == action_id))
        approval.status = "consumed"
        await database.commit()
    watched = await env.client.post(
        "/api/v1/thread-watches",
        json={
            "action_id": str(action_id),
            "request_id": str(uuid4()),
            "remind_at": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert watched.status_code == 200, watched.text
    routine = await env.client.post("/api/v1/routines", json=CONFIG)
    assert routine.status_code == 201, routine.text
    memory = await env.client.post(
        "/api/v1/memory",
        json={"kind": "tone", "content": "friendly"},
    )
    assert memory.status_code == 201, memory.text
    copied_result = {
        "checked_at": now.isoformat(),
        "basis": "saved_data",
        "headline": "Connected subject to erase",
        "items": [{"title": "Connected subject to erase", "status": "confirmed"}],
        "notice": "From saved tasks only.",
    }
    async with env.factory() as database:
        row = await database.get(PersonalRoutine, UUID(routine.json()["id"]))
        row.last_result, row.last_run_at = copied_result, now
        watch = await database.get(ThreadWatch, UUID(watched.json()["id"]))
        watch.status, watch.last_checked_at = "due", now
        own_run = RoutineRun(
            routine_id=row.id,
            revision=row.revision,
            scheduled_for=now,
            checked_at=now,
            status="completed",
            result=copied_result,
            notice="Saved source title copied into historical result.",
        )
        other = User(email="other-cleanup-owner@example.com")
        other_workspace = Workspace(name="Other owner", workspace_type="personal")
        database.add_all([own_run, other, other_workspace])
        await database.flush()
        database.add(
            WorkspaceMembership(user_id=other.id, workspace_id=other_workspace.id, role="owner")
        )
        other_routine = PersonalRoutine(
            user_id=other.id,
            workspace_id=other_workspace.id,
            name="Other owner's priorities",
            recipe="morning_priorities",
            timezone="UTC",
            hour=8,
            minute=0,
            days_of_week=[0],
            enabled=False,
            revision=1,
            schedule_status="paused",
            last_result={"headline": "Other owner's private result"},
        )
        database.add(other_routine)
        await database.flush()
        other_run = RoutineRun(
            routine_id=other_routine.id,
            revision=1,
            scheduled_for=now,
            checked_at=now,
            status="completed",
            result={"headline": "Other owner's private result"},
            notice="Other owner's result must survive.",
        )
        database.add(other_run)
        await database.commit()
        return SimpleNamespace(
            action_id=action_id,
            plan_id=plan_id,
            watch_id=UUID(watched.json()["id"]),
            routine_id=row.id,
            run_id=own_run.id,
            memory_id=UUID(memory.json()["id"]),
            other_user_id=other.id,
            other_workspace_id=other_workspace.id,
            other_routine_id=other_routine.id,
            other_run_id=other_run.id,
            dispatches=dispatches,
        )


@pytest.mark.asyncio
async def test_disconnect_delete_data_erases_followthrough_copies_and_preserves_explicit_memory(
    env, monkeypatch
):
    seeded = await seed_followthrough(env, monkeypatch)
    assert seeded.dispatches == ["approval", "watch"]
    path = f"/api/v1/connections/{env.id}"
    disconnected = await env.client.request("DELETE", path, json={"request_id": str(uuid4())})
    assert disconnected.status_code == 200, disconnected.text
    # Disconnect alone retains the saved history; Delete Learned Data is the
    # explicit command that erases source-derived metadata and snapshots.
    async with env.factory() as database:
        assert await database.get(ThreadWatch, seeded.watch_id) is not None
        assert await database.get(RoutineRun, seeded.run_id) is not None
    # A disconnected connection keeps learned data until the explicit deletion.
    # A preview can inspect that saved status without acquiring revoked access.
    preview_config = {key: value for key, value in CONFIG.items() if key != "enabled"}
    preview_config["recipe"] = "no_reply_check"
    before_delete = await env.client.post("/api/v1/routines/preview", json=preview_config)
    assert before_delete.status_code == 200
    assert before_delete.json()["result"]["items"][0]["title"] == "Connected subject to erase"
    assert "does not check Gmail" in before_delete.json()["result"]["notice"]
    request = {"request_id": str(uuid4())}
    for body in (request, request, {"request_id": str(uuid4())}):
        deleted = await env.client.post(f"{path}/delete-data", json=body)
        assert deleted.status_code == 200, deleted.text
        async with env.factory() as database:
            assert await database.get(ThreadWatch, seeded.watch_id) is None
            assert await database.get(Action, seeded.action_id) is None
            assert await database.get(Plan, seeded.plan_id) is None
            assert await database.get(RoutineRun, seeded.run_id) is None
            routine = await database.get(PersonalRoutine, seeded.routine_id)
            assert routine is not None and routine.last_result is None
            memory = await database.get(OwnerMemory, seeded.memory_id)
            assert memory is not None
            assert memory.provenance == "user_saved" and memory.content == "friendly"
            assert await database.get(RoutineRun, seeded.other_run_id) is not None
            other_routine = await database.get(PersonalRoutine, seeded.other_routine_id)
            assert other_routine.last_result == {"headline": "Other owner's private result"}
        after_delete = await env.client.post("/api/v1/routines/preview", json=preview_config)
        assert after_delete.status_code == 200
        assert after_delete.json()["result"]["items"] == []
    assert seeded.dispatches == ["approval", "watch"]


@pytest.mark.asyncio
async def test_foreign_watch_source_edge_blocks_cleanup_before_owner_snapshots_are_erased(
    env, monkeypatch
):
    seeded = await seed_followthrough(env, monkeypatch)
    async with env.factory() as database:
        watch = await database.get(ThreadWatch, seeded.watch_id)
        watch.user_id = seeded.other_user_id
        watch.workspace_id = seeded.other_workspace_id
        await database.commit()
    path = f"/api/v1/connections/{env.id}"
    assert (
        await env.client.request("DELETE", path, json={"request_id": str(uuid4())})
    ).status_code == 200
    deleted = await env.client.post(f"{path}/delete-data", json={"request_id": str(uuid4())})
    assert deleted.status_code == 409
    assert "manual review" in deleted.text
    async with env.factory() as database:
        assert await database.get(ThreadWatch, seeded.watch_id) is not None
        assert await database.get(RoutineRun, seeded.run_id) is not None
        routine = await database.get(PersonalRoutine, seeded.routine_id)
        assert routine.last_result is not None
        assert await database.get(OwnerMemory, seeded.memory_id) is not None
        assert await database.get(RoutineRun, seeded.other_run_id) is not None


@pytest.mark.asyncio
async def test_foreign_watch_action_edge_blocks_cascaded_cross_owner_deletion(env, monkeypatch):
    seeded = await seed_followthrough(env, monkeypatch)
    async with env.factory() as database:
        foreign_connection = Connection(
            user_id=seeded.other_user_id,
            workspace_id=seeded.other_workspace_id,
            provider="google",
            external_account_id="foreign-watch-source",
            external_email="other-cleanup-owner@example.com",
            status="active",
        )
        database.add(foreign_connection)
        await database.flush()
        watch = await database.get(ThreadWatch, seeded.watch_id)
        watch.user_id = seeded.other_user_id
        watch.workspace_id = seeded.other_workspace_id
        watch.connection_id = foreign_connection.id
        await database.commit()
    path = f"/api/v1/connections/{env.id}"
    assert (
        await env.client.request("DELETE", path, json={"request_id": str(uuid4())})
    ).status_code == 200
    deleted = await env.client.post(f"{path}/delete-data", json={"request_id": str(uuid4())})
    assert deleted.status_code == 409
    assert "manual review" in deleted.text
    async with env.factory() as database:
        assert await database.get(ThreadWatch, seeded.watch_id) is not None
        assert await database.get(Action, seeded.action_id) is not None
        assert await database.get(RoutineRun, seeded.run_id) is not None
        assert await database.get(RoutineRun, seeded.other_run_id) is not None
