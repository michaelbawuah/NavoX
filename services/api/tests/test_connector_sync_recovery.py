"""Recovery/authorization tests use real database sessions, not mock ORM rows.

Set NAVOX_CONNECTOR_TEST_DSN to run the same scenarios against a disposable
PostgreSQL database. Each fixture creates and drops a unique schema there.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.connectors import activities, sync_state
from navox.connectors import runtime as runtime_module
from navox.connectors.contracts import (
    CanonicalResource,
    ConnectorHealth,
    ConnectorManifest,
    ConnectorRuntimeError,
    SyncPage,
    stable_resource_id,
)
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
    User,
    Workspace,
    WorkspaceMembership,
)


@pytest_asyncio.fixture
async def system(tmp_path):
    url = os.environ.get(
        "NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}"
    )
    schema = f"recovery_{uuid4().hex}"
    administrative = None
    if url.startswith("postgresql"):
        administrative = create_async_engine(url)
        async with administrative.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manifest = ConnectorManifest.model_validate(
        {
            "id": "recovery-fixture",
            "version": "1.0.0",
            "displayName": "Recovery fixture",
            "category": "test",
            "connectorClass": "GENERIC_API",
            "auth": [{"kind": "none", "label": "None"}],
            "resourceTypes": ["test.item"],
            "capabilities": {
                "read": [{"name": "test.items.read", "description": "Read"}],
                "incrementalSync": True,
            },
            "requiredSecrets": [],
            "rateLimitStrategy": "none",
            "minimumNavoxConnectorApiVersion": "1",
        }
    )
    state = SimpleNamespace(calls=[], model_calls=[], effect_ids=[], health_calls=0)
    async with factory() as db:
        user, workspace = User(email="recovery@example.com"), Workspace(name="Recovery")
        db.add_all([user, workspace])
        await db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id))
        definition = ConnectorDefinition(
            connector_key=manifest.id,
            version=manifest.version,
            display_name=manifest.display_name,
            connector_class=manifest.connector_class,
            trust_level="USER_PRIVATE",
            manifest=manifest.model_dump(mode="json", by_alias=True),
        )
        db.add(definition)
        await db.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider="fixture",
            external_account_id="account",
            status="CONNECTED",
            authorized_capabilities=["test.items.read"],
            provider_capabilities=["test.items.read"],
            config={},
            sync_cursor="initial",
        )
        db.add(connection)
        await db.commit()
        state.ids = {
            "connection_id": connection.id,
            "user_id": user.id,
            "workspace_id": workspace.id,
        }
        state.definition_id = definition.id
    state.request_id = uuid4()
    state.factory = factory

    def resource(
        name, *, version=1, status="active", provider="fixture", resource_type="test.item"
    ):
        return CanonicalResource(
            resource_id=stable_resource_id(state.ids["connection_id"], resource_type, name),
            workspace_id=state.ids["workspace_id"],
            connector_connection_id=state.ids["connection_id"],
            provider=provider,
            resource_type=resource_type,
            external_id=name,
            canonical={"subject": name, "version": version, "status": status},
            updated_at=datetime(2026, 9, 24, 12, 0, tzinfo=UTC) + timedelta(minutes=version),
            retrieved_at=datetime.now(UTC),
        )

    state.resource = resource

    async def fetch(request):
        return SyncPage(resources=[resource("one")], next_cursor="finished")

    state.fetch = fetch
    state.health = ConnectorHealth(state="CONNECTED", checked_at=datetime.now(UTC))

    class Adapter:
        def __init__(self, config, secrets):
            pass

        def get_manifest(self):
            return manifest

        async def authorize(self, context):
            raise NotImplementedError

        async def health(self, context):
            state.health_calls += 1
            if isinstance(state.health, Exception):
                raise state.health
            return state.health

        async def sync(self, request):
            state.calls.append(request.cursor)
            return await state.fetch(request)

        async def fetch_resource(self, request):
            raise NotImplementedError

        async def execute(self, request):
            raise NotImplementedError

    registry = ConnectorRegistry()
    registry.register(manifest, Adapter)
    state.registry = registry
    state.runtime = ConnectorRuntime(registry)

    async def default_consume(db, item):
        state.model_calls.append(item.external_id)
        effect_id = uuid4()
        db.add(
            AuditEvent(
                id=effect_id,
                user_id=state.ids["user_id"],
                workspace_id=state.ids["workspace_id"],
                event_type="fixture.accepted",
                actor_type="system",
                entity_type="resource",
                entity_id=item.resource_id,
                event_metadata={},
            )
        )
        state.effect_ids.append(effect_id)
        return [effect_id]

    state.consume = default_consume

    async def run(request_id=None, runtime=None):
        async with factory() as db:

            async def consumer(item):
                return await state.consume(db, item)

            return await (runtime or state.runtime).sync(
                db,
                **state.ids,
                request_id=request_id or state.request_id,
                policy_allowed={"test.items.read"},
                consume=consumer,
            )

    state.run = run

    async def clear_backoff():
        async with factory() as db:
            row = await db.get(ConnectorConnection, state.ids["connection_id"])
            row.retry_not_before = datetime.now(UTC) - timedelta(days=1)
            await db.commit()

    state.clear_backoff = clear_backoff

    async def snapshot():
        async with factory() as db:
            return SimpleNamespace(
                connection=await db.get(ConnectorConnection, state.ids["connection_id"]),
                runs=list(
                    await db.scalars(select(ConnectorSyncRun).order_by(ConnectorSyncRun.generation))
                ),
                resources=list(await db.scalars(select(ConnectorResource))),
                receipts=list(await db.scalars(select(ConnectorSyncReceipt))),
                effects=list(
                    await db.scalars(
                        select(AuditEvent).where(AuditEvent.event_type == "fixture.accepted")
                    )
                ),
            )

    state.snapshot = snapshot
    try:
        yield state
    finally:
        await engine.dispose()
        if administrative is not None:
            async with administrative.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await administrative.dispose()


@pytest.mark.asyncio
async def test_retry_continues_last_accepted_page_and_replays_completed_result(system, measure):
    fail = True

    async def fetch(request):
        nonlocal fail
        if request.cursor == "initial":
            return SyncPage(
                resources=[system.resource("one")], next_cursor="page-two", has_more=True
            )
        if fail:
            fail = False
            raise ConnectorRuntimeError("TEMPORARY_FAILURE")
        return SyncPage(resources=[system.resource("two")], next_cursor="finished")

    system.fetch = fetch
    with pytest.raises(ConnectorRuntimeError):
        await system.run()
    before = await system.snapshot()
    assert before.connection.sync_cursor == "initial"
    assert before.runs[0].checkpoint_cursor == "page-two"
    assert before.runs[0].resource_count == 1
    assert len(before.effects) == len(before.receipts) == 1
    await system.clear_backoff()
    result = await system.run()
    assert result.status == "completed"
    assert result.attempt_count == 2
    assert result.resource_count == result.processed_count == 2
    assert len(result.result_ids) == 2
    assert system.calls == ["initial", "page-two", "page-two"]
    calls = system.calls.copy()
    models = system.model_calls.copy()
    health = system.health_calls
    replay = await system.run()
    assert replay.id == result.id and replay.result_ids == result.result_ids
    assert system.calls == calls and system.model_calls == models and system.health_calls == health
    measure("cursor_recovery", 1, 1, "page checkpoint after provider interruption", database=True)


@pytest.mark.asyncio
async def test_partial_page_replay_skips_accepted_revision_but_retries_rolled_back_consumer(
    system, measure
):
    async def fetch(_):
        return SyncPage(
            resources=[system.resource("one"), system.resource("two")], next_cursor="final"
        )

    system.fetch = fetch
    original = system.consume
    failed = False

    async def consume(db, item):
        nonlocal failed
        result = await original(db, item)
        if item.external_id == "two" and not failed:
            failed = True
            await db.flush()
            raise RuntimeError("SENSITIVE PROVIDER BODY")
        return result

    system.consume = consume
    with pytest.raises(ConnectorRuntimeError) as error:
        await system.run()
    assert "SENSITIVE" not in str(error.value)
    before = await system.snapshot()
    assert len(before.resources) == len(before.receipts) == len(before.effects) == 1
    assert before.runs[0].pages_completed == 0
    await system.clear_backoff()
    result = await system.run()
    assert result.resource_count == 2 and len(result.result_ids) == 2
    assert system.model_calls == ["one", "two", "two"]
    after = await system.snapshot()
    assert len(after.effects) == 2 and len(after.resources) == 2
    measure("cursor_recovery", 1, 1, "partial page consumer rollback", database=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [True, False])
async def test_overlapping_live_sync_fails_busy_before_contacting_provider(system, same_request):
    started, release = asyncio.Event(), asyncio.Event()

    async def fetch(_):
        started.set()
        await release.wait()
        return SyncPage(resources=[system.resource("one")], next_cursor="winner")

    system.fetch = fetch
    task = asyncio.create_task(system.run())
    try:
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(ConnectorRuntimeError, match="Another sync"):
            await asyncio.wait_for(system.run(None if same_request else uuid4()), 5)
        assert system.health_calls == 1 and system.calls == ["initial"]
    finally:
        release.set()
        await task
    assert (await system.snapshot()).connection.sync_cursor == "winner"


@pytest.mark.asyncio
async def test_expired_worker_cannot_commit_over_a_successor(system):
    started, release = asyncio.Event(), asyncio.Event()
    first = True

    async def fetch(_):
        nonlocal first
        if first:
            first = False
            started.set()
            await release.wait()
            return SyncPage(resources=[system.resource("one", version=1)], next_cursor="stale")
        return SyncPage(resources=[system.resource("one", version=2)], next_cursor="winner")

    system.fetch = fetch
    task = asyncio.create_task(system.run())
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with system.factory() as db:
            conn = await db.get(ConnectorConnection, system.ids["connection_id"])
            conn.sync_lease_expires_at = datetime.now(UTC) - timedelta(days=1)
            await db.commit()
        successor = await system.run(uuid4())
        assert successor.status == "completed"
    finally:
        release.set()
    with pytest.raises(sync_state.SyncLeaseLost):
        await task
    snap = await system.snapshot()
    assert snap.connection.sync_cursor == "winner"
    assert [r.status for r in snap.runs] == ["superseded", "completed"]
    assert len(snap.effects) == 1
    assert snap.resources[0].canonical["version"] == 2
    # The old id cannot quietly become a new run after the winner's cursor advances.
    with pytest.raises(ConnectorRuntimeError, match="cannot resume"):
        await system.run()


@pytest.mark.asyncio
async def test_crashed_attempt_resumes_same_request_only_after_lease_expiration(system):
    class SimulatedCrash(BaseException):
        pass

    original = system.fetch
    crashed = False

    async def fetch(request):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise SimulatedCrash()
        return await original(request)

    system.fetch = fetch
    with pytest.raises(SimulatedCrash):
        await system.run()
    snap = await system.snapshot()
    assert snap.runs[0].status == "running" and snap.connection.sync_lease_token is not None
    with pytest.raises(ConnectorRuntimeError, match="Another sync"):
        await system.run()
    async with system.factory() as db:
        conn = await db.get(ConnectorConnection, system.ids["connection_id"])
        conn.sync_lease_expires_at = datetime.now(UTC) - timedelta(days=1)
        await db.commit()
    completed = await system.run()
    assert (
        completed.attempt_count == 2
        and completed.generation == 1
        and completed.status == "completed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["provider", "consumer"])
@pytest.mark.parametrize(
    "change",
    [
        "pause",
        "disconnect",
        "user_pause",
        "membership",
        "capabilities",
        "provider_capabilities",
        "configuration",
        "definition",
        "version",
        "credentials",
    ],
)
async def test_authorization_changes_during_io_cannot_publish_results(system, phase, change):
    started, release = asyncio.Event(), asyncio.Event()
    original_fetch, original_consume = system.fetch, system.consume

    async def fetch(request):
        if phase == "provider":
            started.set()
            await release.wait()
        return await original_fetch(request)

    async def consume(db, item):
        if phase == "consumer":
            started.set()
            await release.wait()
        return await original_consume(db, item)

    system.fetch, system.consume = fetch, consume
    task = asyncio.create_task(system.run())
    try:
        await asyncio.wait_for(started.wait(), 5)

        async def revoke():
            async with system.factory() as db:
                conn = await db.get(ConnectorConnection, system.ids["connection_id"])
                if change == "pause":
                    conn.status = "PAUSED"
                elif change == "disconnect":
                    conn.status = "DISCONNECTED"
                elif change == "user_pause":
                    (await db.get(User, system.ids["user_id"])).agent_paused = True
                elif change == "membership":
                    await db.execute(delete(WorkspaceMembership))
                elif change == "capabilities":
                    conn.authorized_capabilities = []
                elif change == "provider_capabilities":
                    conn.provider_capabilities = []
                elif change == "configuration":
                    conn.config = {"changed": True}
                elif change == "definition":
                    (await db.get(ConnectorDefinition, system.definition_id)).active = False
                elif change == "version":
                    (await db.get(ConnectorDefinition, system.definition_id)).version = "2.0.0"
                else:
                    from navox.db.models import ConnectionCredential

                    credential = ConnectionCredential(encrypted_refresh_token="fixture")
                    db.add(credential)
                    await db.flush()
                    conn.credential_reference = credential.id
                await db.commit()

        # Detect regressions which hold the authorization lock over provider/model I/O.
        await asyncio.wait_for(revoke(), 5)
    finally:
        release.set()
    with pytest.raises(ConnectorRuntimeError):
        await task
    snap = await system.snapshot()
    assert snap.connection.sync_cursor == "initial"
    assert snap.resources == snap.receipts == snap.effects == []
    assert snap.runs[0].status == "failed"
    if phase == "provider":
        assert system.model_calls == []


@pytest.mark.asyncio
async def test_consumer_cannot_commit_ahead_of_final_permission_fence(system):
    original = system.consume

    async def commit_early(db, item):
        await original(db, item)
        await db.commit()

    system.consume = commit_early
    with pytest.raises(ConnectorRuntimeError, match="Consumer cannot commit"):
        await system.run()
    snap = await system.snapshot()
    assert snap.effects == snap.resources == snap.receipts == []
    assert snap.connection.sync_cursor == "initial"


@pytest.mark.asyncio
async def test_failure_after_final_page_retries_finalization_without_rereading(
    system, monkeypatch, measure
):
    original = runtime_module.guard_sync
    fail = True

    async def guard(db, ticket, **kw):
        nonlocal fail
        conn, run = await original(db, ticket, **kw)
        if run.fetch_complete and fail:
            fail = False
            raise RuntimeError("crash after final page checkpoint")
        return conn, run

    monkeypatch.setattr(runtime_module, "guard_sync", guard)
    with pytest.raises(ConnectorRuntimeError):
        await system.run()
    snap = await system.snapshot()
    assert snap.runs[0].fetch_complete and snap.connection.sync_cursor == "initial"
    await system.clear_backoff()
    result = await system.run()
    assert result.status == "completed" and len(result.result_ids) == 1
    assert (
        system.calls == ["initial"] and system.model_calls == ["one"] and system.health_calls == 1
    )
    measure("cursor_recovery", 1, 1, "finalization after last page", database=True)


@pytest.mark.asyncio
async def test_rate_limit_backoff_is_durable_and_new_request_does_not_slide_deadline(system):
    system.health = ConnectorHealth(
        state="RATE_LIMITED",
        checked_at=datetime.now(UTC),
        reason_code="RATE_LIMITED",
        retry_after_seconds=3600,
    )
    with pytest.raises(ConnectorRuntimeError) as error:
        await system.run()
    assert error.value.retry_after_seconds == 3600
    first = await system.snapshot()
    assert first.connection.health_state == "RATE_LIMITED"
    assert first.runs[0].status == "failed"
    for request in (None, uuid4()):
        with pytest.raises(ConnectorRuntimeError, match="backoff"):
            await system.run(request)
    later = await system.snapshot()
    assert first.connection.retry_not_before == later.connection.retry_not_before
    assert system.health_calls == 1 and system.calls == []


@pytest.mark.asyncio
async def test_cancellation_is_not_success_and_releases_claim_for_retry(system, measure):
    started, release = asyncio.Event(), asyncio.Event()
    original = system.fetch

    async def fetch(request):
        started.set()
        await release.wait()
        return await original(request)

    system.fetch = fetch
    task = asyncio.create_task(system.run())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snap = await system.snapshot()
    assert snap.connection.sync_lease_token is None and snap.runs[0].status == "interrupted"
    release.set()
    assert (await system.run()).status == "completed"
    measure("cursor_recovery", 1, 1, "cancelled attempt retry", database=True)


@pytest.mark.asyncio
async def test_duplicate_revision_is_accepted_once_within_a_page(system):
    async def fetch(_):
        return SyncPage(
            resources=[system.resource("one"), system.resource("one")], next_cursor="final"
        )

    system.fetch = fetch
    result = await system.run()
    assert result.resource_count == 1 and system.model_calls == ["one"]
    snap = await system.snapshot()
    assert len(snap.resources) == len(snap.receipts) == len(snap.effects) == 1


@pytest.mark.asyncio
async def test_tombstone_is_not_resurrected_by_older_revision(system):
    async def fetch(_):
        return SyncPage(
            resources=[system.resource("one", version=3, status="deleted")], next_cursor="new"
        )

    system.fetch = fetch
    await system.run()

    async def stale(_):
        return SyncPage(resources=[system.resource("one", version=1)], next_cursor="delta")

    system.fetch = stale
    result = await system.run(uuid4())
    assert result.stale_count == 1 and result.processed_count == 0
    snap = await system.snapshot()
    assert snap.resources[0].deleted and snap.resources[0].canonical["version"] == 3
    assert system.model_calls == ["one"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["none", "same", "cycle", "undeclared_provider", "undeclared_type"]
)
async def test_invalid_provider_pages_do_not_commit_source_cursor(system, mode):
    async def fetch(request):
        if mode == "none":
            return SyncPage(next_cursor=None, has_more=True)
        if mode == "same":
            return SyncPage(next_cursor=request.cursor, has_more=True)
        if mode == "cycle":
            return SyncPage(next_cursor="a" if request.cursor != "a" else "b", has_more=True)
        item = system.resource(
            "one",
            provider="spoof" if mode == "undeclared_provider" else "fixture",
            resource_type="undeclared.resource" if mode == "undeclared_type" else "test.item",
        )
        return SyncPage(resources=[item], next_cursor="forged")

    system.fetch = fetch
    with pytest.raises(ConnectorRuntimeError) as error:
        await system.run()
    assert error.value.code == "INVALID_PROVIDER_RESPONSE"
    snap = await system.snapshot()
    assert snap.connection.sync_cursor == "initial" and snap.runs[0].status == "failed"
    assert system.model_calls == []


@pytest.mark.asyncio
async def test_heartbeat_renews_using_separate_session_and_stops_on_exit(system, monkeypatch):
    monkeypatch.setattr(sync_state, "SYNC_RENEW_SECONDS", 0.01)
    started, release = asyncio.Event(), asyncio.Event()
    original = system.fetch

    async def fetch(request):
        started.set()
        await release.wait()
        return await original(request)

    system.fetch = fetch
    task = asyncio.create_task(system.run())
    try:
        await asyncio.wait_for(started.wait(), 5)
        async with system.factory() as db:
            conn = await db.get(ConnectorConnection, system.ids["connection_id"])
            soon = datetime.now(UTC) + timedelta(seconds=5)
            conn.sync_lease_expires_at = soon
            await db.commit()
        for _ in range(100):
            snap = await system.snapshot()
            if sync_state.utc(snap.connection.sync_lease_expires_at) > soon + timedelta(seconds=30):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Lease was not renewed")
    finally:
        release.set()
        await task
    await asyncio.sleep(0.04)
    assert (await system.snapshot()).connection.sync_lease_token is None


@pytest.mark.asyncio
async def test_reconciliation_reuses_failed_request_and_respects_backoff(system, monkeypatch):
    async def fail(_):
        raise ConnectorRuntimeError("TEMPORARY_FAILURE")

    system.fetch = fail
    with pytest.raises(ConnectorRuntimeError):
        await system.run()
    monkeypatch.setattr(activities, "get_session_factory", lambda: system.factory)
    monkeypatch.setattr(activities, "build_connector_registry", lambda _: system.registry)
    assert await activities.connector_reconciliation_activity() == []
    await system.clear_backoff()
    pending = await activities.connector_reconciliation_activity()
    assert len(pending) == 1 and pending[0].request_id == str(system.request_id)


@pytest.mark.asyncio
async def test_unchanged_resource_across_requests_reuses_transactional_acceptance(system):
    first = await system.run()
    second = await system.run(uuid4())
    assert system.model_calls == ["one"]
    assert first.result_ids == second.result_ids
    assert second.duplicate_count == 1 and second.processed_count == 0
    assert len((await system.snapshot()).effects) == 1


@pytest.mark.asyncio
async def test_nested_savepoints_are_allowed_but_outer_consumer_commit_is_not(system):
    original = system.consume

    async def with_savepoint(db, item):
        async with db.begin_nested():
            return await original(db, item)

    system.consume = with_savepoint
    assert (await system.run()).status == "completed"
    assert len((await system.snapshot()).effects) == 1


@pytest.mark.asyncio
async def test_explicit_new_consumer_version_reprocesses_but_cannot_rebind_old_request(system):
    await system.run()
    async with system.factory() as db:

        async def consume(item):
            return await system.consume(db, item)

        with pytest.raises(ConnectorRuntimeError, match="consumer version"):
            await system.runtime.sync(
                db,
                **system.ids,
                request_id=system.request_id,
                policy_allowed={"test.items.read"},
                consume=consume,
                consumer_version="upgraded.v2",
            )
    async with system.factory() as db:

        async def consume(item):
            return await system.consume(db, item)

        run = await system.runtime.sync(
            db,
            **system.ids,
            request_id=uuid4(),
            policy_allowed={"test.items.read"},
            consume=consume,
            consumer_version="upgraded.v2",
        )
    assert run.status == "completed" and system.model_calls == ["one", "one"]


def intelligence_gateway(system, *, started=None, release=None):
    from navox.intelligence.extraction import ModelExtractionResponse

    class Gateway:
        async def extract_operational(self, document, *, owner_email=None):
            system.model_calls.append(document.external_id)
            if started is not None:
                started.set()
                await release.wait()
            content = document.content
            return ModelExtractionResponse(
                provider="fixture",
                model="fixture",
                output={
                    "schema_version": "operational-extraction.v2",
                    "observations": [
                        {
                            "observation_type": "request",
                            "action_text": "send",
                            "object_text": "the budget",
                            "confidence": 0.99,
                            "evidence": [
                                {
                                    "source": "content",
                                    "start_char": 0,
                                    "end_char": len(content),
                                    "text": content,
                                }
                            ],
                        }
                    ],
                },
            )

    return Gateway()


def enable_intelligence_source(system):
    async def fetch(_):
        item = system.resource("budget")
        item = item.model_copy(
            update={
                "canonical": {
                    "source_type": "fixture_request",
                    "subject": "Budget",
                    "content": "Please send the budget.",
                    "occurred_at": "2026-09-24T12:00:00Z",
                    "status": "active",
                    "author": {
                        "identity_type": "email",
                        "identity_value": "pat@example.com",
                        "display_name": "Pat",
                    },
                }
            }
        )
        return SyncPage(resources=[item], next_cursor="done")

    system.fetch = fetch


@pytest.mark.asyncio
async def test_activity_persists_real_intelligence_and_replay_returns_original_count(
    system, monkeypatch
):
    from navox.connectors.jobs import ConnectorSyncWork
    from navox.db.models import Commitment, ObservationEvidence, Person

    enable_intelligence_source(system)
    monkeypatch.setattr(activities, "get_session_factory", lambda: system.factory)
    monkeypatch.setattr(activities, "build_connector_registry", lambda _: system.registry)
    monkeypatch.setattr(activities, "build_ai_gateway", lambda _: intelligence_gateway(system))
    payload = ConnectorSyncWork(
        **{k: str(v) for k, v in system.ids.items()}, request_id=str(system.request_id)
    )
    assert await activities.connector_sync_activity(payload) == 1
    assert await activities.connector_sync_activity(payload) == 1
    assert system.model_calls == ["budget"]
    async with system.factory() as db:
        commitments = list(await db.scalars(select(Commitment)))
        assert len(commitments) == 1 and commitments[0].title == "send the budget"
        assert await db.scalar(select(func.count()).select_from(ObservationEvidence)) == 1
        assert await db.scalar(select(func.count()).select_from(Person)) == 1
    snap = await system.snapshot()
    assert len(snap.receipts) == 1 and snap.connection.sync_cursor == "done"


@pytest.mark.asyncio
async def test_real_model_wait_does_not_lock_revocation_or_publish_learned_data(
    system, monkeypatch
):
    from navox.connectors.jobs import ConnectorSyncWork
    from navox.db.models import Commitment, Connection, ObservationEvidence, Person

    enable_intelligence_source(system)
    started, release = asyncio.Event(), asyncio.Event()
    gateway = intelligence_gateway(system, started=started, release=release)
    monkeypatch.setattr(activities, "get_session_factory", lambda: system.factory)
    monkeypatch.setattr(activities, "build_connector_registry", lambda _: system.registry)
    monkeypatch.setattr(activities, "build_ai_gateway", lambda _: gateway)
    payload = ConnectorSyncWork(
        **{k: str(v) for k, v in system.ids.items()}, request_id=str(system.request_id)
    )
    task = asyncio.create_task(activities.connector_sync_activity(payload))
    try:
        await asyncio.wait_for(started.wait(), 5)

        async def pause():
            async with system.factory() as db:
                connection = await db.get(ConnectorConnection, system.ids["connection_id"])
                connection.status = "PAUSED"
                await db.commit()

        await asyncio.wait_for(pause(), 5)
    finally:
        release.set()
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError):
        await task
    async with system.factory() as db:
        for model in (
            Commitment,
            Connection,
            ObservationEvidence,
            Person,
            ConnectorResource,
            ConnectorSyncReceipt,
        ):
            assert await db.scalar(select(func.count()).select_from(model)) == 0
    snap = await system.snapshot()
    assert snap.connection.sync_cursor == "initial" and snap.runs[0].status == "failed"


@pytest.mark.asyncio
async def test_measured_incremental_corpus_and_replay(system, measure):
    # Four pages first, then modifications, tombstones, unchanged and new records.
    initial = [system.resource(f"item-{i}") for i in range(100)]
    delta = [system.resource(f"item-{i}", version=2) for i in range(50)]
    delta += [system.resource(f"item-{i}", version=2, status="deleted") for i in range(50, 75)]
    delta += [system.resource(f"item-{i}") for i in range(75, 125)]
    current = initial

    async def pages(request):
        index = (
            int(request.cursor.split(":")[1]) if (request.cursor or "").startswith("page:") else 0
        )
        end = min(index + 25, len(current))
        return SyncPage(
            resources=current[index:end],
            next_cursor=f"page:{end}" if end < len(current) else "complete",
            has_more=end < len(current),
        )

    system.fetch = pages
    await system.run()
    current = delta
    await system.run(uuid4())
    snapshot = await system.snapshot()
    actual = {
        row.external_id: (row.canonical["version"], row.deleted) for row in snapshot.resources
    }
    expected = {f"item-{i}": (2 if i < 75 else 1, 50 <= i < 75) for i in range(125)}
    correct = sum(actual.get(key) == state for key, state in expected.items())
    measure(
        "incremental_correctness",
        correct,
        len(expected),
        "100 initial records; 50 updates, 25 tombstones, 25 unchanged and 25 additions",
        database=True,
    )
    assert actual == expected and snapshot.connection.sync_cursor == "complete"
    before_resources, before_effects = len(snapshot.resources), len(snapshot.effects)
    replay = await system.run(uuid4())
    after = await system.snapshot()
    extra = max(0, len(after.resources) - before_resources)
    measure(
        "replay_duplication",
        extra,
        len(current),
        "new request replays all 125 delta revisions",
        database=True,
    )
    assert extra == 0 and len(after.effects) == before_effects
    assert replay.processed_count == 0 and replay.duplicate_count == 125


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reported,expected",
    [
        *[
            (state, state)
            for state in (
                "CONNECTED",
                "DEGRADED",
                "AUTH_EXPIRED",
                "RATE_LIMITED",
                "SYNC_FAILED",
                "PAUSED",
                "DISCONNECTED",
            )
        ],
        ("error:AUTH_EXPIRED", "AUTH_EXPIRED"),
        ("error:AUTH_REVOKED", "AUTH_EXPIRED"),
        ("error:RATE_LIMITED", "RATE_LIMITED"),
        *[
            (f"error:{code}", "SYNC_FAILED")
            for code in (
                "PROVIDER_UNAVAILABLE",
                "RESOURCE_NOT_FOUND",
                "PERMISSION_DENIED",
                "INVALID_PROVIDER_RESPONSE",
                "UNSUPPORTED_CAPABILITY",
                "TEMPORARY_FAILURE",
                "PERMANENT_FAILURE",
            )
        ],
        ("unexpected", "DEGRADED"),
    ],
)
async def test_measured_health_classification(system, monkeypatch, measure, reported, expected):
    from temporalio.exceptions import ApplicationError

    from navox.connectors.jobs import ConnectorHealthWork
    from navox.core.settings import Settings

    monkeypatch.setattr(activities, "get_settings", lambda: Settings())
    monkeypatch.setattr(activities, "get_session_factory", lambda: system.factory)
    monkeypatch.setattr(activities, "build_connector_registry", lambda _: system.registry)
    if reported.startswith("error:"):
        code = reported.split(":", 1)[1]
        system.health = ConnectorRuntimeError(
            code, retry_after_seconds=120 if code == "RATE_LIMITED" else None
        )
    elif reported == "unexpected":
        system.health = RuntimeError("private provider detail")
    else:
        system.health = ConnectorHealth(state=reported, checked_at=datetime.now(UTC))
    work = ConnectorHealthWork(**{key: str(value) for key, value in system.ids.items()})
    if isinstance(system.health, Exception):
        with pytest.raises(ApplicationError) as error:
            await activities.connector_health_activity(work)
        assert "private provider detail" not in str(error.value)
        if reported == "error:RATE_LIMITED":
            assert error.value.next_retry_delay == timedelta(seconds=120)
    else:
        assert await activities.connector_health_activity(work) == expected
    snapshot = await system.snapshot()
    correct = snapshot.connection.health_state == expected
    measure(
        "health_accuracy",
        int(correct),
        1,
        f"provider outcome {reported} -> {expected}",
        database=True,
    )
    assert correct
