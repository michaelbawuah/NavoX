"Real parsers, storage, runtime, resolution and Today; only AI/Temporal I/O is replaced."

import json
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.api import imports
from navox.api.main import create_app
from navox.connectors import activities, import_storage
from navox.connectors.builtin.imports import IMPORT_MANIFEST
from navox.connectors.import_parser import ImportValidationError, parse_import
from navox.connectors.jobs import ConnectorSyncWork
from navox.core.settings import Settings, get_settings
from navox.db.base import Base
from navox.db.models import (
    Commitment,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorImportSnapshot,
    ConnectorResource,
    ConnectorSyncReceipt,
    User,
)
from navox.db.session import get_database_session
from navox.intelligence.extraction import ModelExtractionResponse

ICS = (
    "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:review-1\nS"
    "UMMARY:Review budget\nDESCRIPTION:Attend the budget revi"
    "ew.\nDTSTART:20300404T180000Z\nDTEND:20300404T190000Z\nEND"
    ":VEVENT\nEND:VCALENDAR"
)
SOURCES = {
    "json": (
        '[{"id":"report","title":"Submit report","description":"'
        'Please submit the report by 2030-04-04T17:00:00Z."}]'
    ),
    "csv": (
        "id,title,description,due_at\nreport,Submit report,Please"
        " submit the report.,2030-04-04T17:00:00Z\n"
    ),
    "ics": ICS,
}


@pytest.mark.parametrize("kind", SOURCES)
def test_formats_preserve_source_facts_and_stable_ids(kind):
    a = parse_import(SOURCES[kind], kind, "UTC")
    b = parse_import(SOURCES[kind], kind, "UTC")
    assert a == b and len(a) == 1
    assert a[0].content


@pytest.mark.parametrize(
    "source",
    [
        "[]",
        "[1]",
        '{"title":"a","title":"b"}',
        '[{"title":"a","due_at":"not a date"}]',
        '[{"title":"a","url":"javascript:alert(1)"}]',
        '[{"title":"a","id":"same"},{"title":"b","id":"same"}]',
        '[{"title":"a","value":NaN}]',
        '"password-SENTINEL"',
    ],
)
def test_invalid_json_is_rejected_without_echoing_source(source):
    with pytest.raises(ImportValidationError) as error:
        parse_import(source, "json", "UTC")
    assert "password-SENTINEL" not in str(error.value)


@pytest.mark.parametrize(
    "source", ["title,title\na,b", "title,description\na", "title\na,b", "a,b\n1,2"]
)
def test_invalid_csv_is_not_silently_truncated(source):
    with pytest.raises(ImportValidationError):
        parse_import(source, "csv", "UTC")


def test_bom_multiline_csv_and_unmapped_fields():
    r = parse_import(
        ('\ufeffid,title,description,unmapped\n1,Report,"Line one\nLine two",SECRET-SENTINEL\n'),
        "csv",
        "UTC",
    )[0]
    assert r.content == "Line one\nLine two"
    assert "SECRET-SENTINEL" not in r.model_dump_json()


def test_all_day_and_floating_time_are_not_invented():
    all_day = ICS.replace(
        "DTSTART:20300404T180000Z\nDTEND:20300404T190000Z",
        "DTSTART;VALUE=DATE:20300404\nDTEND;VALUE=DATE:20300405",
    )
    r = parse_import(all_day, "ics", "America/New_York")[0]
    assert "2030-04-04" in r.content and "All-day" in r.content and "T00:00" not in r.content
    floating = ICS.replace("20300404T180000Z", "20300404T180000").replace(
        "20300404T190000Z", "20300404T190000"
    )
    assert "22:00:00+00:00" in parse_import(floating, "ics", "America/New_York")[0].content


@pytest.mark.parametrize(
    "extra", ["RRULE:FREQ=DAILY", "RECURRENCE-ID:20300404T180000Z", "DURATION:PT1H"]
)
def test_unsupported_ics_is_explicit_not_silently_incomplete(extra):
    with pytest.raises(ImportValidationError, match="not supported"):
        parse_import(ICS.replace("END:VEVENT", extra + "\nEND:VEVENT"), "ics", "UTC")


def test_dst_invalid_dates_empty_and_oversized():
    for source in (
        '[{"title":"x","due_at":"2026-03-08T02:30:00"}]',
        '[{"title":"x","due_at":"2026-11-01T01:30:00"}]',
    ):
        with pytest.raises(ImportValidationError, match="ambiguous or nonexistent"):
            parse_import(source, "json", "America/New_York")
    with pytest.raises(ImportValidationError):
        parse_import("x" * 2_000_001, "json", "UTC")
    with pytest.raises(ImportValidationError):
        parse_import('[{"title":"x"}]', "json", "Not/AZone")


class Gateway:
    def __init__(self):
        self.calls = []

    async def extract_operational(self, document, *, owner_email=None):
        self.calls.append(document)
        content = document.content or ""
        kind = "meeting" if document.source_type == "calendar_event" else "task"
        observations = (
            []
            if document.subject == "Newsletter"
            else [
                {
                    "observation_type": kind,
                    "action_text": "attend" if kind == "meeting" else "submit",
                    "object_text": "budget review" if kind == "meeting" else "the report",
                    "confidence": 0.99,
                    "temporal_expression": re.search(
                        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)", content
                    )[0]
                    if re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)", content)
                    else None,
                    "evidence": [
                        {
                            "source": "content",
                            "start_char": 0,
                            "end_char": len(content),
                            "text": content,
                        }
                    ],
                }
            ]
        )
        return ModelExtractionResponse(
            {"schema_version": "operational-extraction.v2", "observations": observations},
            "fixture",
            "fixture-v1",
        )


@pytest_asyncio.fixture
async def env(monkeypatch):
    dsn = os.getenv("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"imports_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    settings = Settings(
        _env_file=None,
        ai_provider="openai",
        openai_api_key="fixture",
        connector_secret_encryption_key=Fernet.generate_key().decode(),
    )

    async def session():
        async with factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_database_session] = session
    app.dependency_overrides[get_settings] = lambda: settings
    gateway = Gateway()
    monkeypatch.setattr(activities, "get_session_factory", lambda: factory)
    monkeypatch.setattr(import_storage, "get_session_factory", lambda: factory)
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    monkeypatch.setattr(activities, "build_ai_gateway", lambda _: gateway)
    queued = []

    async def dispatch(payload, **kwargs):
        queued.append(payload)
        return "fixture-workflow"

    monkeypatch.setattr(imports, "dispatch_connector_sync", dispatch)
    async with factory() as db:
        db.add(
            ConnectorDefinition(
                id=uuid5(NAMESPACE_URL, "navox:generic-import:1.0.0"),
                connector_key="generic-import",
                version="1.0.0",
                display_name="File Import",
                connector_class="IMPORT",
                trust_level="NAVOX_FIRST_PARTY",
                manifest=IMPORT_MANIFEST.model_dump(mode="json", by_alias=True),
                active=True,
            )
        )
        await db.commit()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        r = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "twelve-character-password",
                "display_name": "Owner",
            },
        )
        assert r.status_code == 201
        account = r.json()
        uid, wid = UUID(account["id"]), UUID(account["workspace"]["id"])
        async with factory() as db:
            user = await db.get(User, uid)
            user.timezone = "UTC"
            await db.commit()
        yield SimpleNamespace(
            client=client,
            factory=factory,
            settings=settings,
            model=gateway,
            queued=queued,
            user_id=uid,
            workspace_id=wid,
        )
    await engine.dispose()
    if admin:
        async with admin.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def command(env, kind="json", content=None):
    when = (datetime.now(UTC) + timedelta(hours=6)).replace(microsecond=0)
    source = content or SOURCES[kind]
    source = (
        source.replace("2030-04-04T17:00:00Z", when.isoformat())
        .replace("20300404T180000Z", when.strftime("%Y%m%dT%H%M%SZ"))
        .replace("20300404T190000Z", (when + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ"))
    )
    payload = {"format": kind, "content": source}
    r = await env.client.post("/api/v1/connectors/generic-import/preview", json=payload)
    assert r.status_code == 200, r.text
    return {
        **payload,
        "preview_hash": r.json()["preview_hash"],
        "confirmed": True,
        "request_id": str(uuid4()),
        "name": "My snapshot",
    }


@pytest.mark.asyncio
async def test_preview_does_not_persist_dispatch_or_call_model(env):
    payload = await command(env)
    assert env.model.calls == [] and env.queued == []
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorImportSnapshot)) == 0
    payload["confirmed"] = False
    r = await env.client.post("/api/v1/connectors/generic-import/connect", json=payload)
    assert r.status_code == 422 and SOURCES["json"] not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", SOURCES)
async def test_confirm_runtime_replay_and_today_without_core_changes(env, kind):
    payload = await command(env, kind)
    r = await env.client.post("/api/v1/connectors/generic-import/connect", json=payload)
    assert r.status_code == 201, r.text
    identifier = UUID(r.json()["connection_id"])
    assert r.json()["dispatch_status"] == "queued"
    assert len(env.queued) == 1 and len(env.model.calls) == 0
    count = await activities._connector_sync(env.queued[0])
    assert count >= 1
    assert len(env.model.calls) == 1
    # Replay the same request, then a fresh request: both reuse accepted revisions.
    await activities._connector_sync(env.queued[0])
    await activities._connector_sync(
        ConnectorSyncWork(str(identifier), str(env.user_id), str(env.workspace_id), str(uuid4()))
    )
    assert len(env.model.calls) == 1
    r = await env.client.post(
        "/api/v1/connectors/generic-import/connect", json={**payload, "request_id": str(uuid4())}
    )
    assert (
        r.status_code == 201
        and r.json()["reused"] is True
        and UUID(r.json()["connection_id"]) == identifier
    )
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorImportSnapshot)) == 1
        snapshot = await db.get(ConnectorImportSnapshot, identifier)
        assert (
            "Please submit" not in snapshot.encrypted_payload
            and "budget review" not in snapshot.encrypted_payload
        )
        connection = await db.get(ConnectorConnection, identifier)
        assert "content" not in connection.config and connection.last_synced_at is not None
        resources = list(await db.scalars(select(ConnectorResource)))
        assert len(resources) == 1 and "Please submit" not in json.dumps(resources[0].canonical)
        commitments = list(await db.scalars(select(Commitment)))
        assert commitments
    today = await env.client.get("/api/v1/today")
    assert today.status_code == 200, today.text
    assert "report" in today.text.lower() or "review" in today.text.lower()


@pytest.mark.asyncio
async def test_changed_preview_origin_unknown_fields_and_request_reuse(env):
    p = await command(env)
    for bad in ({**p, "content": '[{"title":"changed"}]'}, {**p, "workspace_id": str(uuid4())}):
        r = await env.client.post("/api/v1/connectors/generic-import/connect", json=bad)
        assert r.status_code in {409, 422}
    assert (
        await env.client.post(
            "/api/v1/connectors/generic-import/connect",
            json=p,
            headers={"Origin": "https://evil.example"},
        )
    ).status_code == 403
    assert (
        await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    ).status_code == 201
    other = await command(env, content='[{"id":"other","title":"Other task"}]')
    other["request_id"] = p["request_id"]
    assert (
        await env.client.post("/api/v1/connectors/generic-import/connect", json=other)
    ).status_code == 409


@pytest.mark.asyncio
async def test_paused_and_ciphertext_swap_fail_closed(env):
    p = await command(env)
    r = await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    identifier = UUID(r.json()["connection_id"])
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, identifier)
        row.status = "PAUSED"
        await db.commit()
    assert await activities._connector_sync(env.queued[0]) == 0
    assert env.model.calls == []
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, identifier)
        row.status = "CONNECTED"
        snapshot = await db.get(ConnectorImportSnapshot, identifier)
        snapshot.encrypted_payload = "invalid"
        await db.commit()
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    assert env.model.calls == []


@pytest.mark.asyncio
async def test_saved_snapshot_survives_dispatch_outage(env, monkeypatch):
    p = await command(env)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("sensitive transport body")

    monkeypatch.setattr(imports, "dispatch_connector_sync", unavailable)
    r = await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    assert (
        r.status_code == 201
        and r.json()["dispatch_status"] == "pending"
        and "sensitive" not in r.text
    )
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorImportSnapshot)) == 1


@pytest.mark.asyncio
async def test_information_only_import_does_not_create_task(env):
    p = await command(
        env,
        content=(
            '[{"id":"news","title":"Newsletter","description":"The a'
            'utumn newsletter is available."}]'
        ),
    )
    assert (
        await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    ).status_code == 201
    assert await activities._connector_sync(env.queued[0]) == 0
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0


@pytest.mark.parametrize("value", ["1e999", "-1e999"])
def test_json_overflow_is_rejected(value):
    with pytest.raises(ImportValidationError):
        parse_import('[{"title":"x","value":' + value + "}]", "json", "UTC")


@pytest.mark.asyncio
async def test_snapshot_reader_rejects_wrong_owner_workspace_and_cipher_swap(env):
    from navox.connectors.contracts import ConnectorRuntimeError
    from navox.connectors.import_storage import ImportSnapshotReader

    a = await command(env)
    first = await env.client.post("/api/v1/connectors/generic-import/connect", json=a)
    first_id = UUID(first.json()["connection_id"])
    b = await command(env, content='[{"id":"two","title":"Second record"}]')
    second = await env.client.post("/api/v1/connectors/generic-import/connect", json=b)
    second_id = UUID(second.json()["connection_id"])
    reader = ImportSnapshotReader(env.settings)
    for workspace, user in ((uuid4(), env.user_id), (env.workspace_id, uuid4())):
        with pytest.raises(ConnectorRuntimeError, match="access denied"):
            await reader.load(first_id, workspace, user, a["preview_hash"])
    async with env.factory() as db:
        first_row = await db.get(ConnectorImportSnapshot, first_id)
        second_row = await db.get(ConnectorImportSnapshot, second_id)
        second_row.encrypted_payload = first_row.encrypted_payload
        await db.commit()
    with pytest.raises(ConnectorRuntimeError, match="access denied"):
        await reader.load(second_id, env.workspace_id, env.user_id, b["preview_hash"])
    assert env.model.calls == []


@pytest.mark.asyncio
async def test_import_permission_revocation_blocks_source_and_reconciliation_skips_completed(env):
    from navox.connectors.contracts import ConnectorRuntimeError
    from navox.connectors.import_storage import ImportSnapshotReader

    p = await command(env)
    response = await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    connection_id = UUID(response.json()["connection_id"])
    await activities._connector_sync(env.queued[0])
    assert await activities.connector_reconciliation_activity() == []
    async with env.factory() as db:
        row = await db.get(ConnectorConnection, connection_id)
        row.authorized_capabilities = []
        await db.commit()
    with pytest.raises(ConnectorRuntimeError, match="access denied"):
        await ImportSnapshotReader(env.settings).load(
            connection_id, env.workspace_id, env.user_id, p["preview_hash"]
        )
    assert len(env.model.calls) == 1


@pytest.mark.asyncio
async def test_boolean_confirmation_and_timezone_change_require_new_review(env):
    p = await command(env)
    for value in (1, "true", None):
        response = await env.client.post(
            "/api/v1/connectors/generic-import/connect", json={**p, "confirmed": value}
        )
        assert response.status_code == 422
    async with env.factory() as db:
        user = await db.get(User, env.user_id)
        user.timezone = "America/New_York"
        await db.commit()
    assert (
        await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    ).status_code == 409
    assert not env.queued and not env.model.calls


@pytest.mark.asyncio
async def test_preview_request_type_size_and_error_redaction(env):
    path = "/api/v1/connectors/generic-import/preview"
    response = await env.client.post(
        path, content="SOURCE-SENTINEL", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415 and "SOURCE-SENTINEL" not in response.text
    response = await env.client.post(
        path, content="SOURCE-SENTINEL", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422 and "SOURCE-SENTINEL" not in response.text
    response = await env.client.post(
        path, content=b"x" * 6_000_001, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert not env.model.calls and not env.queued


@pytest.mark.asyncio
async def test_import_revoked_during_model_io_cannot_accept_facts(env, monkeypatch):
    from temporalio.exceptions import ApplicationError

    p = await command(env)
    response = await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    identifier = UUID(response.json()["connection_id"])
    original = env.model.extract_operational

    async def revoke(document, **kwargs):
        result = await original(document, **kwargs)
        async with env.factory() as db:
            row = await db.get(ConnectorConnection, identifier)
            row.authorized_capabilities = []
            await db.commit()
        return result

    monkeypatch.setattr(env.model, "extract_operational", revoke)
    with pytest.raises(ApplicationError):
        await activities._connector_sync(env.queued[0])
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncReceipt)) == 0
        row = await db.get(ConnectorConnection, identifier)
        assert row.last_synced_at is None


@pytest.mark.asyncio
async def test_import_management_and_fetch_use_the_existing_owner_boundary(env):
    from navox.connectors.builtin.stored_import import StoredImportConnector
    from navox.connectors.contracts import ConnectorRuntimeError, FetchResourceRequest
    from navox.connectors.import_storage import ImportSnapshotReader

    p = await command(env)
    response = await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    identifier = UUID(response.json()["connection_id"])
    listing = await env.client.get("/api/v1/connections")
    assert listing.status_code == 200
    row = next(r for r in listing.json() if r["id"] == str(identifier))
    assert row["sources"][0]["id"] == "snapshot"
    assert row["sources"][0]["can_sync"] is True
    assert "Please submit" not in listing.text
    async with env.factory() as db:
        stored = await db.get(ConnectorConnection, identifier)
        config = stored.config
    connector = StoredImportConnector(config, None, reader=ImportSnapshotReader(env.settings))
    request = FetchResourceRequest(
        connection_id=identifier,
        workspace_id=env.workspace_id,
        resource_type="import.json_item",
        external_id="json:report",
    )
    resource = await connector.fetch_resource(request)
    assert resource.canonical["subject"] == "Submit report"
    with pytest.raises(ConnectorRuntimeError):
        await connector.fetch_resource(request.model_copy(update={"workspace_id": uuid4()}))


@pytest.mark.asyncio
async def test_reused_snapshot_confirmation_id_cannot_bind_a_different_file(env):
    p = await command(env)
    first = await env.client.post("/api/v1/connectors/generic-import/connect", json=p)
    alias = {**p, "request_id": str(uuid4())}
    repeated = await env.client.post("/api/v1/connectors/generic-import/connect", json=alias)
    assert repeated.status_code == 201 and repeated.json()["reused"] is True
    assert repeated.json()["connection_id"] == first.json()["connection_id"]
    changed = await command(env, content='[{"id":"changed","title":"Another snapshot"}]')
    changed["request_id"] = alias["request_id"]
    refused = await env.client.post("/api/v1/connectors/generic-import/connect", json=changed)
    assert refused.status_code == 409
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(ConnectorImportSnapshot)) == 1
