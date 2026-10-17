from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.api import auth, routines
from navox.core.settings import Settings, get_settings
from navox.db.models import (
    Action,
    Commitment,
    Connection,
    Plan,
    PlanStep,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.db.routines import PersonalRoutine, RoutinePreview, RoutineRun
from navox.db.session import get_database_session
from navox.db.thread_watches import ThreadWatch
from navox.routines.activities import RoutineTickInput, tick_routine
from navox.routines.service import RoutineConfig, aware, next_run, preview_result

CONFIG = {
    "name": "Morning priorities",
    "recipe": "morning_priorities",
    "timezone": "UTC",
    "hour": 8,
    "minute": 0,
    "days_of_week": [0, 1, 2, 3, 4],
}


class Dispatcher:
    def __init__(self) -> None:
        self.payloads: list[RoutineTickInput] = []
        self.fail = False

    async def start(self, payload: RoutineTickInput, settings: Settings) -> None:
        self.payloads.append(payload)
        if self.fail:
            raise RuntimeError("synthetic scheduler outage")


@pytest_asyncio.fixture
async def routine_env(ai_database) -> AsyncIterator[SimpleNamespace]:
    factory = ai_database
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(routines.router, prefix="/api/v1")
    settings = Settings(_env_file=None, app_environment="test")
    dispatcher = Dispatcher()

    async def database_override() -> AsyncIterator[AsyncSession]:
        async with factory() as database:
            yield database

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[routines.get_routine_dispatcher] = lambda: dispatcher
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        account = (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "routines@example.com",
                    "password": "twelve-character-password",
                    "display_name": "Owner",
                },
            )
        ).json()
        yield SimpleNamespace(
            client=client,
            factory=factory,
            app=app,
            dispatcher=dispatcher,
            user_id=UUID(account["id"]),
            workspace_id=UUID(account["workspace"]["id"]),
        )


async def enable(
    env: SimpleNamespace, config: dict[str, object] | None = None
) -> dict[str, object]:
    config = config or CONFIG
    preview = await env.client.post("/api/v1/routines/preview", json=config)
    assert preview.status_code == 200, preview.text
    response = await env.client.post(
        "/api/v1/routines",
        json={**config, "enabled": True, "preview_token": preview.json()["preview_token"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    "zone,after,hour,minute,expected",
    [
        ("America/New_York", "2026-03-08T06:00:00+00:00", 2, 30, "2026-03-08T07:00:00+00:00"),
        ("America/New_York", "2026-11-01T04:00:00+00:00", 1, 30, "2026-11-01T05:30:00+00:00"),
        ("America/New_York", "2026-11-01T05:45:00+00:00", 1, 30, "2026-11-02T06:30:00+00:00"),
        ("UTC", "2026-10-16T09:00:00+00:00", 8, 0, "2026-10-17T08:00:00+00:00"),
    ],
)
def test_schedule_dst_and_no_duplicate_fold(zone, after, hour, minute, expected):
    config = RoutineConfig(
        **{
            **CONFIG,
            "timezone": zone,
            "hour": hour,
            "minute": minute,
            "days_of_week": list(range(7)),
        }
    )
    assert next_run(config, datetime.fromisoformat(after)).isoformat() == expected


def test_bad_schedule_rejected():
    for update in (
        {"timezone": "No/Such/Zone"},
        {"days_of_week": []},
        {"days_of_week": [7]},
        {"hour": 24},
        {"minute": -1},
        {"name": "  "},
    ):
        with pytest.raises(ValidationError):
            RoutineConfig(**{**CONFIG, **update})


@pytest.mark.asyncio
async def test_exact_owner_preview_required_and_consumed(routine_env):
    env = routine_env
    no_preview = await env.client.post("/api/v1/routines", json={**CONFIG, "enabled": True})
    assert no_preview.status_code == 409
    preview = (await env.client.post("/api/v1/routines/preview", json=CONFIG)).json()
    assert preview["result"]["basis"] == "saved_data"
    assert "not refreshed" in preview["result"]["notice"]
    changed = await env.client.post(
        "/api/v1/routines",
        json={**CONFIG, "hour": 9, "enabled": True, "preview_token": preview["preview_token"]},
    )
    assert changed.status_code == 409
    created = await env.client.post(
        "/api/v1/routines",
        json={**CONFIG, "enabled": True, "preview_token": preview["preview_token"]},
    )
    assert created.status_code == 201
    assert created.json()["schedule_status"] == "scheduled"
    assert len(env.dispatcher.payloads) == 1
    reused = await env.client.post(
        "/api/v1/routines",
        json={**CONFIG, "enabled": True, "preview_token": preview["preview_token"]},
    )
    assert reused.status_code == 409
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(RoutinePreview)) == 0
        assert await database.scalar(select(func.count()).select_from(RoutineRun)) == 0


@pytest.mark.asyncio
async def test_owner_isolation_and_get_readonly(routine_env):
    env = routine_env
    saved = await enable(env)
    other = (
        await env.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other-routines@example.com",
                "password": "twelve-character-password",
                "display_name": "Other",
            },
        )
    ).json()
    assert other["id"] != str(env.user_id)
    assert (await env.client.get("/api/v1/routines")).json()["routines"] == []
    assert (await env.client.get(f"/api/v1/routines/{saved['id']}/runs")).status_code == 404
    assert (await env.client.delete(f"/api/v1/routines/{saved['id']}")).status_code == 404
    assert (
        await env.client.put(f"/api/v1/routines/{saved['id']}", json={**CONFIG, "enabled": False})
    ).status_code == 404
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(PersonalRoutine)) == 1
        assert await database.scalar(select(func.count()).select_from(RoutineRun)) == 0


@pytest.mark.asyncio
async def test_five_enabled_limit_pause_delete_and_failed_dispatch(routine_env):
    env = routine_env
    saved = [await enable(env, {**CONFIG, "name": f"Morning {index}"}) for index in range(5)]
    preview = (await env.client.post("/api/v1/routines/preview", json=CONFIG)).json()
    blocked = await env.client.post(
        "/api/v1/routines",
        json={**CONFIG, "enabled": True, "preview_token": preview["preview_token"]},
    )
    assert blocked.status_code == 409
    paused = await env.client.put(
        f"/api/v1/routines/{saved[0]['id']}", json={**CONFIG, "enabled": False}
    )
    assert paused.status_code == 200 and paused.json()["next_run_at"] is None
    env.dispatcher.fail = True
    failed = await enable(env)
    assert failed["enabled"] is False
    assert failed["schedule_status"] == "dispatch_failed"
    history = (await env.client.get(f"/api/v1/routines/{failed['id']}/runs")).json()["runs"]
    assert history[0]["status"] == "failed"
    assert "Scheduling could not be confirmed" in history[0]["notice"]
    assert (await env.client.delete(f"/api/v1/routines/{failed['id']}")).status_code == 204


async def add_commitment(
    env,
    *,
    title,
    status="confirmed",
    due_at=None,
    completed_at=None,
    user_id=None,
    workspace_id=None,
):
    async with env.factory() as database:
        item = Commitment(
            user_id=user_id or env.user_id,
            workspace_id=workspace_id or env.workspace_id,
            commitment_type="task",
            title=title,
            status=status,
            priority=3,
            dedupe_key=uuid4().hex,
            due_at=due_at,
            completed_at=completed_at,
        )
        database.add(item)
        await database.commit()
        return item.id


@pytest.mark.asyncio
async def test_recipes_use_saved_owned_facts_and_distinguish_candidates(routine_env):
    env = routine_env
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    due = await add_commitment(env, title="Due report", due_at=now - timedelta(hours=1))
    waiting = await add_commitment(env, title="Await reply", status="waiting")
    complete = await add_commitment(
        env, title="Finished today", status="completed", completed_at=now
    )
    await add_commitment(
        env, title="Old completion", status="completed", completed_at=now - timedelta(days=8)
    )
    await add_commitment(env, title="Unreviewed suggestion", status="candidate")
    async with env.factory() as database:
        morning = await preview_result(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            config=RoutineConfig(**CONFIG),
            now=now,
        )
        assert [item["id"] for item in morning["items"]] == [str(due)]
        catchup = await preview_result(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            config=RoutineConfig(**{**CONFIG, "recipe": "friday_catch_up"}),
            now=now,
        )
        assert {item["id"] for item in catchup["items"]} == {str(due), str(waiting), str(complete)}
        assert await database.scalar(select(func.count()).select_from(RoutineRun)) == 0


@pytest.mark.asyncio
async def test_tick_once_per_slot_and_bounded_history(routine_env):
    env = routine_env
    await add_commitment(env, title="Prepare report")
    saved = await enable(env)
    payload = env.dispatcher.payloads[0]
    start = datetime(2026, 10, 16, 12, tzinfo=UTC)
    for offset in range(23):
        now = start + timedelta(days=offset)
        async with env.factory() as database:
            routine = await database.get(PersonalRoutine, UUID(saved["id"]))
            routine.next_run_at = now
            await database.commit()
            result = await tick_routine(database, payload, now)
            assert result.state == "waiting"
        async with env.factory() as database:
            await tick_routine(database, payload, now)
    async with env.factory() as database:
        runs = list(
            await database.scalars(
                select(RoutineRun).where(RoutineRun.routine_id == UUID(saved["id"]))
            )
        )
        assert len(runs) == 20
        assert all(run.status == "completed" for run in runs)
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        assert aware(routine.next_run_at) > now
        assert routine.last_result["items"][0]["title"] == "Prepare report"


@pytest.mark.asyncio
async def test_pause_quiet_hours_and_revision_stop_old_work(routine_env):
    env = routine_env
    saved = await enable(env)
    payload = env.dispatcher.payloads[0]
    now = datetime(2026, 10, 16, 2, tzinfo=UTC)
    async with env.factory() as database:
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        routine.next_run_at = now
        await database.commit()
        await tick_routine(database, payload, now)
        run = await database.scalar(select(RoutineRun))
        assert run.status == "skipped" and "quiet hours" in run.notice
        routine.next_run_at = now + timedelta(hours=10)
        owner = await database.get(User, env.user_id)
        owner.agent_paused = True
        await database.commit()
        await tick_routine(database, payload, now + timedelta(hours=10))
        runs = list(await database.scalars(select(RoutineRun).order_by(RoutineRun.checked_at)))
        assert runs[-1].status == "skipped" and "paused" in runs[-1].notice
        routine.revision += 1
        await database.commit()
        assert (await tick_routine(database, payload, now + timedelta(days=1))).state == "terminal"
    blocked = await env.client.post("/api/v1/routines", json={**CONFIG, "enabled": True})
    assert blocked.status_code == 409 and "Resume NavoX" in blocked.text


@pytest.mark.asyncio
async def test_lost_owner_membership_stops_background(routine_env):
    env = routine_env
    saved = await enable(env)
    async with env.factory() as database:
        membership = await database.get(WorkspaceMembership, (env.workspace_id, env.user_id))
        membership.role = "viewer"
        await database.commit()
        state = await tick_routine(database, env.dispatcher.payloads[0], datetime.now(UTC))
        assert state.state == "terminal"
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        assert routine.enabled is False and routine.schedule_status == "blocked"
    assert (await env.client.get("/api/v1/routines")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_expired_and_foreign_preview_cannot_enable(routine_env):
    env = routine_env
    receipt = (await env.client.post("/api/v1/routines/preview", json=CONFIG)).json()
    async with env.factory() as database:
        preview = await database.get(RoutinePreview, UUID(receipt["preview_token"]))
        preview.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await database.commit()
    response = await env.client.post(
        "/api/v1/routines",
        json={**CONFIG, "enabled": True, "preview_token": receipt["preview_token"]},
    )
    assert response.status_code == 409
    receipt = (await env.client.post("/api/v1/routines/preview", json=CONFIG)).json()
    await env.client.post(
        "/api/v1/auth/register",
        json={
            "email": "preview-thief@example.com",
            "password": "twelve-character-password",
            "display_name": "Other",
        },
    )
    response = await env.client.post(
        "/api/v1/routines",
        json={**CONFIG, "enabled": True, "preview_token": receipt["preview_token"]},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_newer_pause_wins_during_dispatch(routine_env):
    env = routine_env

    class ConcurrentPause:
        async def start(self, payload, settings):
            async with env.factory() as database:
                routine = await database.get(PersonalRoutine, UUID(payload.routine_id))
                routine.enabled = False
                routine.revision += 1
                routine.schedule_status = "paused"
                routine.next_run_at = None
                await database.commit()

    env.app.dependency_overrides[routines.get_routine_dispatcher] = ConcurrentPause
    saved = await enable(env)
    assert saved["enabled"] is False
    assert saved["schedule_status"] == "paused"
    assert saved["next_run_at"] is None


@pytest.mark.asyncio
async def test_no_reply_recipe_only_reads_saved_due_watches(routine_env):
    env = routine_env
    now = datetime.now(UTC)
    # These are saved synthetic facts with valid foreign keys in both SQLite
    # and PostgreSQL. Preview must inspect them without accessing a provider.
    async with env.factory() as database:
        other = User(email="other-recipe-owner@example.com")
        other_workspace = Workspace(name="Other recipe owner", workspace_type="personal")
        database.add_all([other, other_workspace])
        await database.flush()

        async def saved_watch(user_id, workspace_id, state, subject):
            connection = Connection(
                user_id=user_id,
                workspace_id=workspace_id,
                provider="google",
                external_account_id=uuid4().hex,
                external_email="owner@example.com",
                status="active",
            )
            plan = Plan(
                user_id=user_id,
                workspace_id=workspace_id,
                request_id=uuid4(),
                goal="Saved synthetic send",
                context_hash="a" * 64,
                status="completed",
            )
            database.add_all([connection, plan])
            await database.flush()
            step = PlanStep(
                plan_id=plan.id,
                sequence_number=1,
                action_type="gmail.send",
                description="Saved fixture",
                risk_level="R3",
                status="completed",
            )
            database.add(step)
            await database.flush()
            action = Action(
                user_id=user_id,
                workspace_id=workspace_id,
                plan_step_id=step.id,
                provider="google",
                action_type="gmail.send",
                risk_level="R3",
                status="completed",
                payload_hash="a" * 64,
                idempotency_key=uuid4().hex,
            )
            database.add(action)
            await database.flush()
            database.add(
                ThreadWatch(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    action_id=action.id,
                    connection_id=connection.id,
                    request_id=uuid4(),
                    action_payload_hash="a" * 64,
                    sender_email="owner@example.com",
                    recipient_email="recipient@example.com",
                    subject=subject,
                    sent_message_id=uuid4().hex,
                    thread_id=uuid4().hex,
                    remind_at=now - timedelta(days=1),
                    status=state,
                    last_checked_at=now,
                )
            )

        for state in ("due", "watching", "paused", "replied", "needs_access", "check_failed"):
            await saved_watch(env.user_id, env.workspace_id, state, f"Saved {state}")
        await saved_watch(other.id, other_workspace.id, "due", "Other owner")
        await database.commit()
        result = await preview_result(
            database,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            config=RoutineConfig(**{**CONFIG, "recipe": "no_reply_check"}),
            now=now,
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "Saved due"
        assert "does not check Gmail" in result["notice"]
        assert await database.scalar(select(func.count()).select_from(RoutineRun)) == 0


@pytest.mark.asyncio
async def test_runtime_failure_records_history_and_does_not_clobber_new_revision(
    routine_env, monkeypatch
):
    from navox.routines import activities

    env = routine_env
    saved = await enable(env)
    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    assert await activities.routine_failure_activity(env.dispatcher.payloads[0]) == "failed"
    async with env.factory() as database:
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        assert routine.enabled is False and routine.schedule_status == "failed"
        run = await database.scalar(select(RoutineRun))
        assert run.status == "failed" and "after retries" in run.notice
        routine.revision += 1
        routine.enabled = True
        routine.schedule_status = "scheduled"
        await database.commit()
    assert await activities.routine_failure_activity(env.dispatcher.payloads[0]) == "stopped"
    async with env.factory() as database:
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        assert routine.enabled is True
        assert await database.scalar(select(func.count()).select_from(RoutineRun)) == 1


@pytest.mark.asyncio
async def test_reconciler_recovers_crash_after_opt_in_commit(routine_env):
    import asyncio

    from navox.routines.recovery import reconcile_routines

    env = routine_env

    class ProcessCrash:
        async def start(self, payload, settings):
            raise asyncio.CancelledError("synthetic interruption after DB commit")

    env.app.dependency_overrides[routines.get_routine_dispatcher] = ProcessCrash
    preview = (await env.client.post("/api/v1/routines/preview", json=CONFIG)).json()
    with pytest.raises(asyncio.CancelledError):
        await env.client.post(
            "/api/v1/routines",
            json={**CONFIG, "enabled": True, "preview_token": preview["preview_token"]},
        )
    async with env.factory() as database:
        row = await database.scalar(select(PersonalRoutine))
        assert row.enabled is True and row.schedule_status == "starting"
        payload = RoutineTickInput(
            str(row.id), str(env.user_id), str(env.workspace_id), row.revision
        )
        assert row.next_dispatch_check_at is not None
    dispatcher = Dispatcher()
    settings = Settings(_env_file=None, app_environment="test")
    now = datetime.now(UTC)
    assert (
        await reconcile_routines(env.factory, dispatcher=dispatcher, settings=settings, now=now)
        == 1
    )
    assert dispatcher.payloads == [payload]
    async with env.factory() as database:
        row = await database.scalar(select(PersonalRoutine))
        assert row.enabled is True and row.schedule_status == "scheduled"
        assert row.dispatch_failure_count == 0
        assert aware(row.next_dispatch_check_at) == now + timedelta(minutes=5)
    assert (
        await reconcile_routines(
            env.factory, dispatcher=dispatcher, settings=settings, now=now + timedelta(minutes=1)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_reconciler_bounded_failures_disable_truthfully_and_pause_wins(routine_env):
    from navox.routines.recovery import reconcile_routines

    env = routine_env
    saved = await enable(env)
    start = datetime.now(UTC)
    async with env.factory() as database:
        row = await database.get(PersonalRoutine, UUID(saved["id"]))
        row.next_dispatch_check_at = start
        await database.commit()
    failed = Dispatcher()
    failed.fail = True
    settings = Settings(_env_file=None, app_environment="test")
    for count in range(1, 4):
        assert (
            await reconcile_routines(
                env.factory,
                dispatcher=failed,
                settings=settings,
                now=start + timedelta(minutes=count),
            )
            == 1
        )
        async with env.factory() as database:
            row = await database.get(PersonalRoutine, UUID(saved["id"]))
            assert row.dispatch_failure_count == count
            assert row.enabled is (count < 3)
            assert row.schedule_status == ("starting" if count < 3 else "dispatch_failed")
    async with env.factory() as database:
        row = await database.get(PersonalRoutine, UUID(saved["id"]))
        assert row.next_run_at is None and row.next_dispatch_check_at is None
        history = list(await database.scalars(select(RoutineRun)))
        assert len(history) == 1 and history[0].status == "failed"
        assert "three attempts" in history[0].notice
    assert (
        await reconcile_routines(
            env.factory, dispatcher=failed, settings=settings, now=start + timedelta(days=1)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_dispatch_confirmation_does_not_clobber_new_revision(routine_env):
    from navox.routines.recovery import record_dispatch

    env = routine_env
    saved = await enable(env)
    old = env.dispatcher.payloads[0]
    async with env.factory() as database:
        row = await database.get(PersonalRoutine, UUID(saved["id"]))
        row.revision += 1
        row.enabled = False
        row.schedule_status = "paused"
        await database.commit()
        assert (
            await record_dispatch(database, old, confirmed=True, now=datetime.now(UTC))
            == "superseded"
        )
        assert (
            await record_dispatch(database, old, confirmed=False, now=datetime.now(UTC))
            == "superseded"
        )
        await database.refresh(row)
        assert row.schedule_status == "paused" and row.enabled is False
        assert await database.scalar(select(func.count()).select_from(RoutineRun)) == 0


@pytest.mark.asyncio
async def test_temporal_dispatch_uses_one_revision_identity_and_recovers_closed_runs(monkeypatch):
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from navox.routines import dispatcher as module

    starts = []
    running = set()

    class Client:
        async def start_workflow(self, workflow, payload, **kwargs):
            starts.append(kwargs)
            if kwargs["id"] in running:
                raise WorkflowAlreadyStartedError(kwargs["id"], "PersonalRoutineWorkflow")
            running.add(kwargs["id"])

    async def connect(target):
        return Client()

    monkeypatch.setattr(module.Client, "connect", connect)
    dispatcher = module.TemporalRoutineDispatcher()
    payload = RoutineTickInput(str(uuid4()), str(uuid4()), str(uuid4()), 7)
    settings = Settings(_env_file=None, app_environment="test")
    await dispatcher.start(payload, settings)
    await dispatcher.start(payload, settings)
    assert len(running) == 1
    running.clear()  # A closed/failed run can be recovered under the same DB opt-in.
    await dispatcher.start(payload, settings)
    assert len(running) == 1
    assert len({request["id"] for request in starts}) == 1
    assert all(
        request["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE for request in starts
    )
    assert all("execution_timeout" not in request for request in starts)


@pytest.mark.asyncio
async def test_routine_workflows_load_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from navox.workflows.routines import (
        PersonalRoutineReconciliationWorkflow,
        PersonalRoutineWorkflow,
    )

    runner = SandboxedWorkflowRunner()
    for cls in (PersonalRoutineWorkflow, PersonalRoutineReconciliationWorkflow):
        runner.prepare_workflow(workflow._Definition.must_from_class(cls))


@pytest.mark.asyncio
async def test_routine_tick_locks_fresh_owner_before_routine(routine_env, monkeypatch):
    env = routine_env
    await enable(env)
    payload = env.dispatcher.payloads[0]
    async with env.factory() as database:
        statements = []
        scalar = database.scalar

        async def observed(statement, *args, **kwargs):
            statements.append(statement)
            return await scalar(statement, *args, **kwargs)

        monkeypatch.setattr(database, "scalar", observed)
        await tick_routine(database, payload, datetime.now(UTC))
        assert statements[0].column_descriptions[0]["entity"] is User
        assert statements[0]._for_update_arg is not None
        assert statements[0].get_execution_options()["populate_existing"] is True
        assert statements[1].column_descriptions[0]["entity"] is PersonalRoutine
        assert statements[1]._for_update_arg is not None


@pytest.mark.asyncio
async def test_postgres_delete_snapshot_cannot_leave_new_private_routine_history(
    routine_env, monkeypatch
):
    import asyncio

    from sqlalchemy import delete, update

    from navox.routines import activities

    env = routine_env
    if env.factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locks; exercised by connector PostgreSQL CI")
    private_title = "Private connected title to forget"
    source_id = await add_commitment(env, title=private_title)
    saved = await enable(env)
    payload = env.dispatcher.payloads[0]
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    async with env.factory() as database:
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        routine.next_run_at = now
        await database.commit()
    reached_preview = asyncio.Event()
    original = activities.preview_result

    async def observed_preview(*args, **kwargs):
        reached_preview.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(activities, "preview_result", observed_preview)

    async def run_check():
        async with env.factory() as database:
            return await activities.tick_routine(database, payload, now)

    # Reproduce the connection lifecycle ordering: lock owner, DELETE existing
    # snapshots, clear the routine snapshot, remove source facts, then commit.
    # A worker may not read the old facts and INSERT a new snapshot between the
    # deletion's DELETE statement and commit.
    async with env.factory() as deleting:
        await deleting.scalar(select(User).where(User.id == env.user_id).with_for_update())
        await deleting.execute(delete(RoutineRun).where(RoutineRun.routine_id == UUID(saved["id"])))
        worker = asyncio.create_task(run_check())
        try:
            # Under the old Routine-only lock, the worker reaches preview and
            # commits a private result after the DELETE's statement snapshot.
            # The fixed User-first order blocks until deletion commits instead.
            try:
                await asyncio.wait_for(reached_preview.wait(), timeout=0.3)
            except TimeoutError:
                pass
            else:
                await asyncio.wait_for(asyncio.shield(worker), timeout=5)
            await deleting.execute(
                update(PersonalRoutine)
                .where(PersonalRoutine.id == UUID(saved["id"]))
                .values(last_result=None)
            )
            await deleting.execute(delete(Commitment).where(Commitment.id == source_id))
            await deleting.commit()
            await asyncio.wait_for(asyncio.shield(worker), timeout=5)
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    async with env.factory() as database:
        routine = await database.get(PersonalRoutine, UUID(saved["id"]))
        history = list(
            await database.scalars(select(RoutineRun).where(RoutineRun.routine_id == routine.id))
        )
        assert all(private_title not in str(run.result) for run in history)
        assert private_title not in str(routine.last_result)
