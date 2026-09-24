"""Gmail migration through real sessions and the shared runtime (provider fixtures)."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_gmail_sync_resume import NOW, PRIVATE_BODY, PRIVATE_TOKEN, Mailbox, Model, account, mail

from navox.connectors.builtin.google_gmail import (
    GMAIL_CAPABILITY,
    GMAIL_MANIFEST,
    GoogleGmailConnector,
    decode_gmail_cursor,
    gmail_resource,
)
from navox.connectors.contracts import ConnectorRuntimeError, SyncRequest
from navox.connectors.runtime import ConnectorRuntime
from navox.db.base import Base
from navox.db.models import (
    AuditEvent,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
    GmailSyncPlan,
    IntelligenceCursor,
    IntelligenceSourceReceipt,
    User,
    WorkspaceMembership,
)
from navox.intelligence import ingestion
from navox.intelligence.extraction import OperationalExtractor, source_document_hash
from navox.providers.google_sources import (
    GMAIL_READ_SCOPE,
    GoogleSourceAuthorizationError,
    GoogleSourceError,
    GoogleSourceGateway,
    gmail_document,
)


@pytest_asyncio.fixture
async def system(tmp_path, monkeypatch):
    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path / 'gmail.db'}")
    schema = f"gmail_{uuid4().hex}"
    administrative = None
    if dsn.startswith("postgresql"):
        administrative = create_async_engine(dsn)
        async with administrative.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        identifier = await account(db)
        original = await db.get(Connection, identifier)
        state = SimpleNamespace(
            factory=factory,
            connection_id=identifier,
            user_id=original.user_id,
            workspace_id=original.workspace_id,
            connector_id=uuid5(NAMESPACE_URL, f"navox:google-gmail:{identifier}"),
            provider_hook=None,
            model_hook=None,
            refresh_hook=None,
            refresh_calls=0,
            owners=[],
        )
    mailbox = Mailbox()
    mailbox.install(monkeypatch)
    state.mailbox = mailbox

    async def handler(request):
        if state.provider_hook:
            await state.provider_hook(request)
        return mailbox.handle(request)

    async def token(db, *, connection, settings):
        state.refresh_calls += 1
        if state.refresh_hook:
            await state.refresh_hook(connection)
        connection.access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        return PRIVATE_TOKEN

    gateway = GoogleSourceGateway(
        transport=httpx.MockTransport(handler),
        sleep=mailbox.sleep,
        monotonic=lambda: mailbox.time,
        now=lambda: NOW + timedelta(seconds=mailbox.time),
        jitter=lambda: 0,
    )
    monkeypatch.setattr(ingestion, "GoogleSourceGateway", lambda: gateway)
    monkeypatch.setattr(ingestion, "access_token_for_connection", token)

    class HookedModel(Model):
        async def extract_operational(self, document, *, owner_email=None):
            state.owners.append(owner_email)
            if state.model_hook:
                await state.model_hook(document)
            return await super().extract_operational(document, owner_email=owner_email)

    state.model = HookedModel()
    state.extractor = OperationalExtractor(state.model)

    async def sync():
        async with factory() as db:
            return await ingestion.process_connection(
                db,
                connection_id=identifier,
                source="gmail",
                settings=ingestion.Settings(),
                extractor=state.extractor,
            )

    async def expire_backoff():
        async with factory() as db:
            connector = await db.get(ConnectorConnection, state.connector_id)
            if connector:
                connector.retry_not_before = datetime.now(UTC) - timedelta(seconds=1)
                await db.commit()

    state.sync, state.expire_backoff = sync, expire_backoff
    try:
        yield state
    finally:
        await engine.dispose()
        if administrative is not None:
            async with administrative.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await administrative.dispose()


@pytest.mark.asyncio
async def test_gmail_uses_shared_runtime_and_preserves_owner_aware_relevance(system, monkeypatch):
    invoked = []
    original = ConnectorRuntime.sync

    async def runtime(self, *args, **kwargs):
        invoked.append(kwargs["connection_id"])
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ConnectorRuntime, "sync", runtime)
    await system.sync()
    assert invoked == [system.connector_id]
    assert system.model.calls == ["first", "second"]
    assert system.owners == ["owner@example.com", "owner@example.com"]
    assert system.refresh_calls == 1  # operation handles reuse one in-memory access token
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "new"
        assert await db.scalar(select(GmailSyncPlan)) is None
        connector = await db.get(ConnectorConnection, system.connector_id)
        assert connector.authorized_capabilities == [GMAIL_CAPABILITY]
        assert connector.config["managed_by_source_workflow"] is True
        run = await db.get(ConnectorSyncRun, connector.sync_run_id)
        assert run.status == "completed" and run.fetch_complete
        assert run.resource_count == 2 and run.pages_completed > 2
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncReceipt)) == 2
        assert (await db.get(Connection, system.connection_id)).granted_scopes == [GMAIL_READ_SCOPE]


@pytest.mark.asyncio
async def test_gmail_registry_plans_and_runtime_receipts_never_store_body_or_token(system):
    await system.sync()
    async with system.factory() as db:
        resources = list(await db.scalars(select(ConnectorResource)))
        assert len(resources) == 2
        assert all(row.canonical["content_persisted"] is False for row in resources)
        rows = []
        for model in (
            ConnectorConnection,
            ConnectorResource,
            ConnectorSyncReceipt,
            ConnectorSyncRun,
            GmailSyncPlan,
            AuditEvent,
        ):
            for row in await db.scalars(select(model)):
                rows.append(
                    {
                        column.key: getattr(row, column.key)
                        for column in model.__table__.columns
                        if hasattr(row, column.key)
                    }
                )
        stored = json.dumps(rows, default=str)
        assert PRIVATE_BODY not in stored and PRIVATE_TOKEN not in stored
        assert "manager@example.com" not in stored
        assert "payload" not in resources[0].canonical


@pytest.mark.asyncio
async def test_unchanged_history_reuses_accepted_revisions_and_original_source_hashes(system):
    await system.sync()
    system.model.calls.clear()
    await system.sync()
    assert system.model.calls == []
    async with system.factory() as db:
        assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 2
        for receipt in await db.scalars(select(IntelligenceSourceReceipt)):
            document = gmail_document(
                system.mailbox.messages[receipt.external_id],
                connection_id=system.connection_id,
                workspace_id=system.workspace_id,
                now=NOW,
            )
            assert receipt.source == "gmail"
            assert receipt.source_hash == source_document_hash(document)


async def revoke(system, denial):
    async with system.factory() as db:
        original = await db.get(Connection, system.connection_id)
        connector = await db.get(ConnectorConnection, system.connector_id)
        if denial == "legacy_scope":
            original.granted_scopes = []
        elif denial == "legacy_disconnect":
            original.status = "disconnected"
        elif denial == "owner_pause":
            (await db.get(User, system.user_id)).agent_paused = True
        elif denial == "membership":
            await db.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.user_id == system.user_id)
            )
        elif denial == "connector_pause":
            connector.status = "PAUSED"
        elif denial == "capability":
            connector.authorized_capabilities = []
        elif denial == "definition":
            (await db.get(ConnectorDefinition, connector.connector_definition_id)).active = False
        elif denial == "credentials":
            replacement = ConnectionCredential(encrypted_refresh_token="replacement-fixture")
            db.add(replacement)
            await db.flush()
            original.credential_reference = replacement.id
        elif denial == "legacy_cursor":
            (await db.scalar(select(IntelligenceCursor))).cursor = "other-worker"
        else:
            raise AssertionError(denial)
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["provider", "model"])
@pytest.mark.parametrize(
    "denial",
    [
        "legacy_scope",
        "legacy_disconnect",
        "owner_pause",
        "membership",
        "connector_pause",
        "capability",
        "definition",
        "credentials",
        "legacy_cursor",
    ],
)
async def test_committed_revocation_during_io_cannot_accept_facts_or_advance_cursor(
    system, during, denial
):
    changed = False

    async def hook(_):
        nonlocal changed
        if not changed:
            changed = True
            await revoke(system, denial)

    setattr(system, f"{during}_hook", hook)
    with pytest.raises(GoogleSourceAuthorizationError):
        await system.sync()
    async with system.factory() as db:
        assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectorResource)) == 0
        assert await db.scalar(select(IntelligenceCursor.cursor)) == (
            "other-worker" if denial == "legacy_cursor" else "old"
        )


@pytest.mark.asyncio
async def test_late_oauth_refresh_cannot_restore_revoked_grants(system):
    async def refresh(detached):
        await revoke(system, "legacy_scope")
        detached.granted_scopes = [GMAIL_READ_SCOPE]

    system.refresh_hook = refresh
    with pytest.raises(GoogleSourceAuthorizationError):
        await system.sync()
    assert system.mailbox.calls == []
    async with system.factory() as db:
        assert (await db.get(Connection, system.connection_id)).granted_scopes == []


@pytest.mark.asyncio
async def test_retry_deadline_blocks_immediate_resume_and_then_resumes_one_message(system):
    system.mailbox.fail_at = ("/messages/second", "full")
    with pytest.raises(GoogleSourceError):
        await system.sync()
    system.mailbox.fail_at = None
    system.mailbox.calls.clear()
    with pytest.raises(GoogleSourceError) as error:
        await system.sync()
    assert error.value.retry_after_seconds > 0
    assert system.mailbox.calls == []
    await system.expire_backoff()
    await system.sync()
    assert system.mailbox.calls == [("/messages/second", "full")]
    assert system.model.calls == ["first", "second"]


@pytest.mark.asyncio
async def test_partial_model_failure_preserves_progress_and_chronology(system):
    system.model.fail_id = "second"
    with pytest.raises(RuntimeError, match="temporary model failure"):
        await system.sync()
    async with system.factory() as db:
        assert (await db.scalar(select(GmailSyncPlan))).position == 1
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old"
    system.mailbox.calls.clear()
    system.model.fail_id = None
    await system.expire_backoff()
    await system.sync()
    assert system.mailbox.calls == [("/messages/second", "full")]
    assert system.model.calls == ["first", "second", "second"]


@pytest.mark.asyncio
async def test_competing_sync_is_busy_and_cannot_create_another_plan(system):
    entered, release = asyncio.Event(), asyncio.Event()

    async def block(_):
        entered.set()
        await release.wait()

    system.model_hook = block
    task = asyncio.create_task(system.sync())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        with pytest.raises(GoogleSourceError, match="already active"):
            await system.sync()
    finally:
        release.set()
        await task
    async with system.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncRun)) == 1
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "new"


@pytest.mark.asyncio
async def test_cancellation_releases_lease_without_accepting_model_output(system):
    entered = asyncio.Event()

    async def block(_):
        entered.set()
        await asyncio.Event().wait()

    system.model_hook = block
    task = asyncio.create_task(system.sync())
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with system.factory() as db:
        connector = await db.get(ConnectorConnection, system.connector_id)
        assert connector.sync_lease_token is None
        assert (await db.get(ConnectorSyncRun, connector.sync_run_id)).status == "interrupted"
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old"
        assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 0
    system.model_hook = None
    await system.sync()


@pytest.mark.asyncio
async def test_finalization_replay_does_not_repeat_provider_or_model_calls(system, monkeypatch):
    failed = False
    original = ConnectorRuntime.sync

    async def failing_finalizer(self, *args, **kwargs):
        finalizer = kwargs["finalize"]

        async def finalize(run):
            nonlocal failed
            if not failed:
                failed = True
                raise ConnectorRuntimeError("TEMPORARY_FAILURE", "finalizer fixture failure")
            await finalizer(run)

        kwargs["finalize"] = finalize
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ConnectorRuntime, "sync", failing_finalizer)
    with pytest.raises(GoogleSourceError):
        await system.sync()
    await system.expire_backoff()
    system.mailbox.calls.clear()
    system.model.calls.clear()
    await system.sync()
    assert system.mailbox.calls == [] and system.model.calls == []


@pytest.mark.asyncio
async def test_legacy_partially_processed_plan_is_adopted_without_reenumeration(system):
    async with system.factory() as db:
        db.add(
            GmailSyncPlan(
                connection_id=system.connection_id,
                initial_cursor="old",
                cursor="new",
                phase="process",
                entries=[
                    {
                        "id": "second",
                        "deleted": False,
                        "occurred_at": (NOW + timedelta(minutes=1)).isoformat(),
                    }
                ],
                position=0,
                created_at=NOW,
            )
        )
        await db.commit()
    await system.sync()
    assert system.mailbox.calls == [("/messages/second", "full")]
    assert system.model.calls == ["second"]


@pytest.mark.asyncio
async def test_more_than_fifty_checkpoints_do_not_trip_the_generic_page_budget(system):
    # Gmail needs metadata and body checkpoints, not one 240-second mega-page.
    system.mailbox.messages = {str(i): mail(str(i), i) for i in range(28)}
    system.mailbox.listed = list(system.mailbox.messages)[::-1]
    await system.sync()
    assert system.model.calls == [str(i) for i in range(28)]
    async with system.factory() as db:
        run = await db.scalar(select(ConnectorSyncRun))
        assert run.pages_completed > 50 and run.status == "completed"


@pytest.mark.parametrize(
    "change",
    [
        {"anchor": "2026-09-24T12:00:00"},
        {"position": 2},
        {"phase": "ready", "cursor": None},
        {"restarts": 4},
        {"entries": [{"id": "x", "deleted": False}, {"id": "x", "deleted": False}]},
    ],
)
def test_invalid_checkpoints_fail_closed(change):
    payload = {"plan_id": str(uuid4()), "anchor": NOW.isoformat(), **change}
    with pytest.raises(ConnectorRuntimeError):
        decode_gmail_cursor(json.dumps(payload))


@pytest.mark.asyncio
async def test_read_adapter_cannot_execute_writes_or_self_grant():
    adapter = GoogleGmailConnector({"legacy_connection_id": str(uuid4())}, None)
    assert not GMAIL_MANIFEST.capabilities.write
    with pytest.raises(ConnectorRuntimeError, match="SPEC-001"):
        await adapter.execute(None)
    with pytest.raises(ConnectorRuntimeError, match="permission"):
        await adapter.sync(
            SyncRequest(
                connection_id=uuid4(),
                workspace_id=uuid4(),
                capabilities=frozenset({"communication.messages.send"}),
            )
        )


def test_resource_preserves_original_document_identity_and_hash():
    identifier, workspace = uuid4(), uuid4()
    document = gmail_document(
        mail("one", 0), connection_id=identifier, workspace_id=workspace, now=NOW
    )
    resource = gmail_resource(document, uuid4())
    assert resource.canonical["source_document"]["id"] == str(document.id)
    assert "retrieved_at" not in resource.canonical["source_document"]
    assert resource.resource_type == "communication.message"


@pytest.mark.asyncio
async def test_historical_id_limit_does_not_block_normal_incremental_sync(system):
    from navox.connectors.builtin.google_gmail import GmailCheckpoint
    from navox.connectors.secrets import ScopedSecretLease

    class Gateway:
        async def gmail_page(self, *_args, **_kwargs):
            return SimpleNamespace(message_ids={}, cursor="next-token", next_page_token=None)

    lease = ScopedSecretLease(system.connector_id, "sync.read", {"GOOGLE_ACCESS_TOKEN": "fixture"})
    adapter = GoogleGmailConnector(
        {"legacy_connection_id": str(system.connection_id)},
        lease,
        gateway=Gateway(),
        known_ids=tuple(str(i) for i in range(2001)),
    )
    state = GmailCheckpoint(plan_id=uuid4(), anchor=NOW, initial_cursor="old", cursor="old")
    request = SyncRequest(
        connection_id=system.connector_id,
        workspace_id=system.workspace_id,
        capabilities=frozenset({GMAIL_CAPABILITY}),
        cursor=state.encode(),
    )
    try:
        page = await adapter.sync(request)
        assert decode_gmail_cursor(page.next_cursor).phase == "metadata"
        reset = state.model_copy(update={"reset": True, "cursor": "fresh-anchor"})
        with pytest.raises(ConnectorRuntimeError, match="Google"):
            await adapter.sync(request.model_copy(update={"cursor": reset.encode()}))
    finally:
        lease.close()


def test_naive_metadata_time_is_rejected_before_chronology_sorting():
    payload = {
        "plan_id": str(uuid4()),
        "anchor": NOW.isoformat(),
        "phase": "metadata",
        "entries": [{"id": "x", "deleted": False, "occurred_at": "2026-09-24T12:00:00"}],
    }
    with pytest.raises(ConnectorRuntimeError, match="checkpoint"):
        decode_gmail_cursor(json.dumps(payload))


def test_gmail_resource_links_back_to_the_original_message():
    document = gmail_document(
        mail("message-one", 0), connection_id=uuid4(), workspace_id=uuid4(), now=NOW
    )
    resource = gmail_resource(document, uuid4())
    assert resource.source_url == "https://mail.google.com/mail/u/0/#all/message-one"


@pytest.mark.asyncio
async def test_staged_finalization_failure_rolls_back_cursor_and_plan_deletion(system, monkeypatch):
    failed = False
    original = ConnectorRuntime.sync

    async def inject(self, *args, **kwargs):
        finalize = kwargs["finalize"]

        async def staged(run):
            nonlocal failed
            await finalize(run)
            if not failed:
                failed = True
                raise RuntimeError("crash after staging original cursor and plan deletion")

        kwargs["finalize"] = staged
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ConnectorRuntime, "sync", inject)
    with pytest.raises(GoogleSourceError):
        await system.sync()
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old"
        assert await db.scalar(select(GmailSyncPlan)) is not None
        assert (await db.scalar(select(ConnectorSyncRun))).fetch_complete
    await system.expire_backoff()
    system.mailbox.calls.clear()
    system.model.calls.clear()
    await system.sync()
    assert system.mailbox.calls == [] and system.model.calls == []
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "new"
        assert await db.scalar(select(GmailSyncPlan)) is None


@pytest.mark.asyncio
async def test_legacy_plan_replacement_during_provider_io_is_not_overwritten(system):
    replaced = False
    replacement_id = uuid4()

    async def replace(_):
        nonlocal replaced
        if replaced:
            return
        replaced = True
        async with system.factory() as db:
            await db.execute(delete(GmailSyncPlan))
            db.add(
                GmailSyncPlan(
                    id=replacement_id,
                    connection_id=system.connection_id,
                    initial_cursor="old",
                    cursor="old",
                    created_at=NOW,
                    phase="list",
                    entries=[],
                )
            )
            await db.commit()

    system.provider_hook = replace
    with pytest.raises(GoogleSourceAuthorizationError):
        await system.sync()
    assert system.model.calls == []
    async with system.factory() as db:
        assert (await db.scalar(select(GmailSyncPlan))).id == replacement_id
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old"
        assert await db.scalar(select(func.count()).select_from(ConnectorResource)) == 0


@pytest.mark.asyncio
async def test_access_token_refreshes_before_expiry_during_a_long_plan(system):
    expired = False

    async def expire(_):
        nonlocal expired
        if expired:
            return
        expired = True
        async with system.factory() as db:
            row = await db.get(Connection, system.connection_id)
            row.access_token_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

    system.model_hook = expire
    await system.sync()
    assert system.refresh_calls == 2
    assert system.model.calls == ["first", "second"]


@pytest.mark.asyncio
async def test_cached_tokens_still_use_closed_operation_leases_before_model_processing(
    system, monkeypatch
):
    from navox.connectors.google_calendar_sync import GoogleCalendarTokenBroker
    from navox.connectors.secrets import SecretBrokerError

    issued = []
    original = GoogleCalendarTokenBroker.lease

    async def lease(self, *args, **kwargs):
        handle = await original(self, *args, **kwargs)
        issued.append(handle)
        return handle

    async def check_closed(_):
        assert issued
        for handle in issued:
            with pytest.raises(SecretBrokerError, match="closed"):
                handle.get("GOOGLE_ACCESS_TOKEN")

    monkeypatch.setattr(GoogleCalendarTokenBroker, "lease", lease)
    system.model_hook = check_closed
    await system.sync()
    await check_closed(None)
    assert system.refresh_calls == 1 and len(issued) > 2


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage", ["provider", "model"])
async def test_competing_source_activity_defers_without_provider_cooldown(
    system, monkeypatch, blocked_stage
):
    from temporalio.exceptions import ApplicationError

    from navox.db.models import IncomingEvent
    from navox.intelligence import activities
    from navox.intelligence.jobs import SourceWork
    from navox.intelligence.source_cooldown import source_cooldown

    entered, release = asyncio.Event(), asyncio.Event()
    blocked = False

    async def block_once(value):
        nonlocal blocked
        if blocked_stage == "provider" and value.url.params.get("format") != "full":
            return
        if not blocked:
            blocked = True
            entered.set()
            await release.wait()

    if blocked_stage == "provider":
        system.provider_hook = block_once
    else:
        system.model_hook = block_once
    monkeypatch.setattr(activities, "get_session_factory", lambda: system.factory)
    monkeypatch.setattr(
        activities, "get_settings", lambda: ingestion.Settings(ai_provider="openai")
    )
    monkeypatch.setattr(activities, "build_ai_gateway", lambda _: system.model)
    event_id = uuid4()
    async with system.factory() as db:
        db.add(
            IncomingEvent(
                id=event_id,
                connection_id=system.connection_id,
                user_id=system.user_id,
                workspace_id=system.workspace_id,
                provider="google",
                source="gmail",
                event_type="gmail.changed",
                external_event_id=str(event_id),
                payload_hash="0" * 64,
            )
        )
        await db.commit()
    payload = SourceWork(
        str(system.connection_id), str(system.user_id), str(system.workspace_id), "gmail"
    )
    queued = SourceWork(
        payload.connection_id, payload.user_id, payload.workspace_id, "gmail", str(event_id)
    )
    winner = asyncio.create_task(activities.process_source_activity(payload))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        before_calls = list(system.mailbox.calls)
        async with system.factory() as db:
            row = await db.get(ConnectorConnection, system.connector_id)
            before_fence = (row.sync_run_id, row.sync_generation, row.sync_lease_token)
        for _ in range(2):
            with pytest.raises(ApplicationError) as error:
                await activities.process_source_activity(queued)
            assert error.value.non_retryable is False
            assert error.value.next_retry_delay == timedelta(seconds=15)
            assert error.value.details == (
                {"code": "google_provider_unavailable", "retry_after_seconds": 15},
            )
            async with system.factory() as db:
                assert await source_cooldown(db, system.connection_id, "gmail") is None
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(AuditEvent.event_type == "intelligence.source.failed")
                    )
                    == 0
                )
                event = await db.get(IncomingEvent, event_id)
                assert event.intelligence_status == "pending" and event.processed_at is None
                row = await db.get(ConnectorConnection, system.connector_id)
                assert (row.sync_run_id, row.sync_generation, row.sync_lease_token) == before_fence
                assert row.retry_not_before is None
                assert await db.scalar(select(func.count()).select_from(ConnectorSyncRun)) == 1
            assert list(system.mailbox.calls) == before_calls
    finally:
        release.set()
        assert await asyncio.wait_for(winner, timeout=15) == 0
    # The queued event remains replayable immediately after the winning sync,
    # with no fabricated cooldown and no duplicate model work.
    assert await activities.process_source_activity(queued) == 0
    assert system.model.calls == ["first", "second"]
    async with system.factory() as db:
        event = await db.get(IncomingEvent, event_id)
        assert event.intelligence_status == "completed"
        assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 2
