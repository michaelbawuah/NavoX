"""Calendar migration through real runtime/database sessions, no live accounts."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.connectors.builtin.google import ensure_google_connector_connection
from navox.connectors.builtin.google_calendar import (
    calendar_resource,
    decode_cursor,
)
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.google_calendar_sync import CalendarAuthority, GoogleCalendarTokenBroker
from navox.connectors.normalization import canonical_resource_to_source_document
from navox.connectors.secrets import SecretBrokerError
from navox.db.base import Base
from navox.db.models import (
    Commitment,
    Connection,
    ConnectionCredential,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
    IntelligenceCursor,
    IntelligenceSourceReceipt,
    ObservationEvidence,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence import ingestion
from navox.intelligence.extraction import (
    ModelExtractionResponse,
    OperationalExtractor,
    source_document_hash,
)
from navox.providers.google_sources import (
    CALENDAR_READ_SCOPE,
    GMAIL_READ_SCOPE,
    GoogleSourceError,
    GoogleSourceGateway,
    calendar_document,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def event(identifier="e1", *, status="confirmed", updated="2026-09-24T12:00:00Z"):
    return {
        "id": identifier,
        "summary": f"Budget review {identifier}",
        "description": "Private calendar description stays transient.",
        "updated": updated,
        "status": status,
        "start": {"dateTime": "2026-09-28T16:00:00Z"},
        "end": {"dateTime": "2026-09-28T17:00:00Z"},
    }


@pytest_asyncio.fixture
async def system(tmp_path, monkeypatch):
    dsn = os.environ.get(
        "NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path / 'calendar.db'}"
    )
    schema = f"calendar_{uuid4().hex}"
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
        user, workspace = User(email="calendar@example.com"), Workspace(name="Calendar")
        credential = ConnectionCredential(
            encrypted_refresh_token="protected-original-refresh-token"
        )
        db.add_all([user, workspace, credential])
        await db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id))
        original = Connection(
            user_id=user.id,
            workspace_id=workspace.id,
            provider="google",
            status="active",
            external_account_id="google-account",
            external_email=user.email,
            granted_scopes=[GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE],
            credential_reference=credential.id,
        )
        db.add(original)
        await db.flush()
        db.add(IntelligenceCursor(connection_id=original.id, source="calendar", cursor="old-token"))
        await db.commit()
        state = SimpleNamespace(
            factory=factory,
            connection_id=original.id,
            user_id=user.id,
            workspace_id=workspace.id,
            credential_id=credential.id,
            requests=[],
            model_calls=[],
            refresh_calls=0,
            provider_hook=None,
            model_hook=None,
            refresh_hook=None,
            pages=[{"items": [event()], "nextSyncToken": "new-token"}],
            expired=False,
            known={},
        )

    async def handler(request):
        assert request.headers["Authorization"] == "Bearer transient-access-token"
        if request.url.params.get("fields") == "kind":
            return httpx.Response(200, json={"kind": "calendar#events"})
        state.requests.append(request)
        if state.provider_hook is not None:
            await state.provider_hook(request)
        if request.url.path.endswith("/events"):
            if state.expired and "syncToken" in request.url.params:
                return httpx.Response(410)
            index = int(request.url.params.get("pageToken", "0"))
            return httpx.Response(200, json=state.pages[index])
        value = state.known.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(404) if value is None else httpx.Response(200, json=value)

    async def token(db, *, connection, settings):
        state.refresh_calls += 1
        assert connection.credential_reference == state.credential_id
        if state.refresh_hook is not None:
            await state.refresh_hook(connection)
        connection.access_token_expires_at = NOW + timedelta(hours=24)
        return "transient-access-token"

    gateway = GoogleSourceGateway(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ingestion, "GoogleSourceGateway", lambda: gateway)
    monkeypatch.setattr(ingestion, "access_token_for_connection", token)

    class Model:
        async def extract_operational(self, document, *, owner_email=None):
            state.model_calls.append(document)
            if state.model_hook is not None:
                await state.model_hook(document)
            title = document.subject
            return ModelExtractionResponse(
                provider="fixture",
                model="fixture",
                output={
                    "observations": [
                        {
                            "observation_type": "meeting",
                            "action_text": "attend",
                            "object_text": title,
                            "temporal_expression": None,
                            "confidence": 0.99,
                            "evidence": [
                                {
                                    "source": "subject",
                                    "start_char": 0,
                                    "end_char": len(title),
                                    "text": title,
                                }
                            ],
                        }
                    ],
                },
            )

    state.extractor = OperationalExtractor(Model())

    async def sync():
        async with factory() as db:
            return await ingestion.process_connection(
                db,
                connection_id=state.connection_id,
                source="calendar",
                settings=ingestion.Settings(),
                extractor=state.extractor,
            )

    state.sync = sync
    try:
        yield state
    finally:
        await engine.dispose()
        if administrative is not None:
            async with administrative.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await administrative.dispose()


@pytest.mark.asyncio
async def test_calendar_entrypoint_uses_runtime_pages_and_atomic_legacy_cursor(system):
    system.pages = [
        {"items": [event()], "nextPageToken": "1"},
        {"items": [event("e2")], "nextSyncToken": "new-token"},
    ]

    async def check_cursor(document):
        async with system.factory() as db:
            assert await db.scalar(select(IntelligenceCursor.cursor)) == "old-token"

    system.model_hook = check_cursor
    ids = await system.sync()
    assert len(ids) == 2
    assert [r.url.params["syncToken"] for r in system.requests] == ["old-token", "old-token"]
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "new-token"
        run = await db.scalar(select(ConnectorSyncRun))
        assert run.status == "completed" and run.pages_completed == 2
        assert len(run.result_ids) == 2
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncReceipt)) == 2
        for resource in await db.scalars(select(ConnectorResource)):
            assert resource.canonical["content_persisted"] is False
            serialized = json.dumps([resource.canonical, resource.provider_metadata])
            assert "Private calendar" not in serialized and "Budget review" not in serialized
            assert "token" not in serialized
        assert {r.source for r in await db.scalars(select(IntelligenceSourceReceipt))} == {
            "calendar"
        }
        assert {e.connection_id for e in await db.scalars(select(ObservationEvidence))} == {
            system.connection_id
        }
        original = await db.get(Connection, system.connection_id)
        assert original.credential_reference == system.credential_id
        assert set(original.granted_scopes) == {GMAIL_READ_SCOPE, CALENDAR_READ_SCOPE}
        assert await db.scalar(select(func.count()).select_from(ConnectionCredential)) == 1


@pytest.mark.asyncio
async def test_calendar_reuses_old_intelligence_receipts_and_exact_source_hash(system):
    original_doc = calendar_document(
        event(), workspace_id=system.workspace_id, connection_id=system.connection_id, now=NOW
    )
    async with system.factory() as db:
        connection = await db.get(Connection, system.connection_id)
        user = await db.get(User, system.user_id)
        old_ids, _ = await ingestion._process_document(
            db,
            connection=connection,
            user=user,
            document=original_doc,
            source="calendar",
            extractor=system.extractor,
        )
        await db.commit()
    ids = await system.sync()
    assert set(ids) == old_ids and len(system.model_calls) == 1
    async with system.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 1
        assert await db.scalar(select(func.count()).select_from(IntelligenceSourceReceipt)) == 1
        mirror = await ensure_google_connector_connection(
            db, await db.get(Connection, system.connection_id)
        )
        definition = await db.get(ConnectorDefinition, mirror.connector_definition_id)
        assert definition.connector_key == "google-workspace"


@pytest.mark.asyncio
async def test_calendar_cancelled_resource_bypasses_model_and_supersedes_same_commitment(system):
    ids = await system.sync()
    system.pages = [
        {
            "items": [event(status="cancelled", updated="2026-09-25T12:00:00Z")],
            "nextSyncToken": "cancel-token",
        }
    ]
    await system.sync()
    assert len(system.model_calls) == 1
    async with system.factory() as db:
        assert (await db.get(Commitment, ids[0])).status == "superseded"
        assert (await db.scalar(select(ConnectorResource))).deleted
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "cancel-token"


@pytest.mark.asyncio
async def test_expired_calendar_cursor_reconciles_known_missing_events_before_final_token(system):
    ids = await system.sync()
    system.expired = True
    system.pages = [{"items": [], "nextSyncToken": "reset-token"}]
    await system.sync()
    assert len(system.model_calls) == 1
    assert system.requests[-1].url.path.endswith("/events/e1")
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "reset-token"
        assert (await db.get(Commitment, ids[0])).status == "superseded"


@pytest.mark.asyncio
async def test_partial_page_model_failure_resumes_request_without_repeating_first_model(system):
    # Give the second revision a later timestamp; equal-time UUID order is arbitrary.
    system.pages = [
        {
            "items": [event(), event("e2", updated="2026-09-24T12:01:00Z")],
            "nextSyncToken": "new-token",
        }
    ]

    async def fail_second(document):
        if document.external_id == "e2":
            raise RuntimeError("Transient model failure")

    system.model_hook = fail_second
    with pytest.raises(GoogleSourceError):
        await system.sync()
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old-token"
        run = await db.scalar(select(ConnectorSyncRun))
        request_id = run.request_id
        assert run.status == "failed" and run.resource_count == 1
        connector = await db.scalar(select(ConnectorConnection))
        connector.retry_not_before = None
        await db.commit()
    system.model_hook = None
    ids = await system.sync()
    assert len(ids) == 2
    assert [d.external_id for d in system.model_calls].count("e1") == 1
    async with system.factory() as db:
        run = await db.scalar(select(ConnectorSyncRun))
        assert run.request_id == request_id and run.attempt_count == 2 and run.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("during", ["provider", "model", "refresh"])
@pytest.mark.parametrize("change", ["scope", "pause", "credential", "membership"])
async def test_calendar_revocation_during_io_prevents_acceptance(system, during, change):
    async def revoke(_):
        async with system.factory() as other:
            original = await other.get(Connection, system.connection_id)
            if change == "scope":
                original.granted_scopes = [GMAIL_READ_SCOPE]
            elif change == "pause":
                original.status = "paused"
            elif change == "credential":
                credential = ConnectionCredential(encrypted_refresh_token="new-protected-token")
                other.add(credential)
                await other.flush()
                original.credential_reference = credential.id
            else:
                await other.execute(
                    delete(WorkspaceMembership).where(WorkspaceMembership.user_id == system.user_id)
                )
            await other.commit()

    setattr(system, f"{during}_hook", revoke)
    with pytest.raises((GoogleSourceError, ConnectorRuntimeError)):
        await asyncio.wait_for(system.sync(), timeout=10)
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old-token"
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncReceipt)) == 0


@pytest.mark.asyncio
async def test_calendar_scope_reduction_from_refresh_is_retained_and_stops_network(system):
    async def narrow(detached):
        detached.granted_scopes = [GMAIL_READ_SCOPE]

    system.refresh_hook = narrow
    with pytest.raises(GoogleSourceError):
        await system.sync()
    assert system.requests == [] and system.model_calls == []
    async with system.factory() as db:
        assert (await db.get(Connection, system.connection_id)).granted_scopes == [GMAIL_READ_SCOPE]


@pytest.mark.asyncio
async def test_old_calendar_worker_cannot_overwrite_new_source_cursor(system):
    async def advance(_):
        async with system.factory() as other:
            cursor = await other.scalar(select(IntelligenceCursor))
            cursor.cursor = "other-worker-token"
            await other.commit()

    system.model_hook = advance
    with pytest.raises(GoogleSourceError):
        await system.sync()
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "other-worker-token"
        assert (await db.scalar(select(ConnectorSyncRun))).status == "failed"


@pytest.mark.asyncio
async def test_google_token_broker_rejects_wrong_identity_purpose_or_secret(system):
    await system.sync()
    async with system.factory() as db:
        original = await db.get(Connection, system.connection_id)
        connector = await db.scalar(select(ConnectorConnection))
        authority = CalendarAuthority(
            original.id,
            original.user_id,
            original.workspace_id,
            original.credential_reference,
            original.external_account_id,
            frozenset(original.granted_scopes),
        )
        broker = GoogleCalendarTokenBroker(ingestion.Settings(), authority, connector.id)
        common = dict(
            connection_id=connector.id,
            workspace_id=system.workspace_id,
            user_id=system.user_id,
            purpose="sync.read",
            names={"GOOGLE_ACCESS_TOKEN"},
        )
        for bad in [
            {"workspace_id": uuid4()},
            {"user_id": uuid4()},
            {"connection_id": uuid4()},
            {"purpose": "execute"},
            {"names": {"GOOGLE_REFRESH_TOKEN"}},
        ]:
            with pytest.raises(SecretBrokerError):
                await broker.lease(db, **{**common, **bad})


def test_lossless_calendar_envelope_preserves_evidence_offsets_and_source_hash():
    connection_id, workspace_id = uuid4(), uuid4()
    doc = calendar_document(
        event(), workspace_id=workspace_id, connection_id=connection_id, now=NOW
    )
    doc = doc.model_copy(update={"subject": "  Budget  ", "content": "\nExact text.\n"})
    resource = calendar_resource(doc, uuid4())
    recovered = canonical_resource_to_source_document(
        resource, provenance_connection_id=connection_id
    )
    assert recovered == doc and source_document_hash(recovered) == source_document_hash(doc)
    corrupted = resource.model_copy(update={"workspace_id": uuid4()})
    with pytest.raises(ValueError, match="ownership"):
        canonical_resource_to_source_document(corrupted, provenance_connection_id=connection_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page",
    [
        {"items": []},
        {"items": "bad", "nextSyncToken": "new"},
        {"items": [], "nextPageToken": "1", "nextSyncToken": "new"},
        {"items": [{}], "nextSyncToken": "new"},
    ],
)
async def test_invalid_calendar_pages_never_advance_cursor(system, page):
    system.pages = [page]
    with pytest.raises(GoogleSourceError):
        await system.sync()
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old-token"


@pytest.mark.parametrize(
    "value",
    ["not-json", '{"phase":"other"}', '{"schema_version":"wrong"}', '{"anchor":"2026-01-01"}'],
)
def test_invalid_calendar_cursor_fails_closed(value):
    with pytest.raises(ConnectorRuntimeError):
        decode_cursor(value)


@pytest.mark.asyncio
async def test_calendar_overlapping_sync_is_busy_without_harming_winner(system):
    entered, release = asyncio.Event(), asyncio.Event()

    async def block(_):
        entered.set()
        await release.wait()

    system.provider_hook = block
    first = asyncio.create_task(system.sync())
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        with pytest.raises(GoogleSourceError):
            await asyncio.wait_for(system.sync(), timeout=5)
    finally:
        release.set()
    assert len(await asyncio.wait_for(first, timeout=5)) == 1
    async with system.factory() as db:
        assert (await db.scalar(select(ConnectorSyncRun))).status == "completed"
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncRun)) == 1


@pytest.mark.asyncio
async def test_calendar_finalization_failure_replays_without_provider_or_model(system, monkeypatch):
    from navox.connectors.runtime import ConnectorRuntime

    original_run = ConnectorRuntime._run
    failed = False

    async def run(self, database, ticket, context, manifest, capabilities, consume, finalize):
        async def finalizer(completed):
            nonlocal failed
            await finalize(completed)
            if not failed:
                failed = True
                raise RuntimeError("crash after staging compatibility cursor")

        return await original_run(
            self, database, ticket, context, manifest, capabilities, consume, finalizer
        )

    monkeypatch.setattr(ConnectorRuntime, "_run", run)
    with pytest.raises(GoogleSourceError):
        await system.sync()
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old-token"
        connector = await db.scalar(select(ConnectorConnection))
        assert decode_cursor(connector.sync_cursor).token == "old-token"
        assert (await db.scalar(select(ConnectorSyncRun))).fetch_complete
        connector.retry_not_before = None
        await db.commit()
    count = len(system.requests), len(system.model_calls), system.refresh_calls
    assert len(await system.sync()) == 1
    assert count == (len(system.requests), len(system.model_calls), system.refresh_calls)
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "new-token"


@pytest.mark.asyncio
async def test_calendar_stale_update_cannot_resurrect_cancelled_meeting(system):
    ids = await system.sync()
    system.pages = [
        {
            "items": [event(status="cancelled", updated="2026-09-25T12:00:00Z")],
            "nextSyncToken": "cancel-token",
        }
    ]
    await system.sync()
    system.pages = [{"items": [event()], "nextSyncToken": "old-revision-token"}]
    await system.sync()
    assert len(system.model_calls) == 1
    async with system.factory() as db:
        assert (await db.get(Commitment, ids[0])).status == "superseded"
        assert (await db.scalar(select(ConnectorResource))).deleted


@pytest.mark.asyncio
async def test_bootstrap_pagination_uses_fixed_window_and_no_sync_token(system):
    async with system.factory() as db:
        cursor = await db.scalar(select(IntelligenceCursor))
        cursor.cursor = None
        await db.commit()
    system.pages = [
        {"items": [], "nextPageToken": "1"},
        {"items": [event()], "nextSyncToken": "bootstrap-token"},
    ]
    await system.sync()
    assert all("syncToken" not in r.url.params for r in system.requests)
    first, second = system.requests
    for name in ("timeMin", "timeMax", "maxResults", "singleEvents", "showDeleted"):
        assert first.url.params[name] == second.url.params[name]


@pytest.mark.asyncio
async def test_calendar_provider_page_token_loop_fails_closed(system):
    system.pages = [{"items": [], "nextPageToken": "1"}, {"items": [], "nextPageToken": "1"}]
    with pytest.raises(GoogleSourceError):
        await system.sync()
    assert len(system.requests) == 2
    async with system.factory() as db:
        assert await db.scalar(select(IntelligenceCursor.cursor)) == "old-token"
