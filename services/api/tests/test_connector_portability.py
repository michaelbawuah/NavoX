"""Unknown REST -> unchanged runtime -> SPEC-002 -> Today acceptance.

Only HTTP and model responses are fixtures. Production canonical conversion,
validation, authorization, persistence, revision receipts, and Today run here.
These tests do not certify live endpoints, domain validation, or onboarding UI.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from navox.connectors.builtin.generic_api import GenericAPIConnector
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.intelligence import ingest_connector_resource
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.base import Base
from navox.db.models import (
    Action,
    Approval,
    AuditEvent,
    Commitment,
    ConnectorConnection,
    ConnectorDefinition,
    ConnectorResource,
    ConnectorSyncReceipt,
    ConnectorSyncRun,
    ObservationEvidence,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence.extraction import ModelExtractionResponse, OperationalExtractor
from navox.today.projection import build_today_projection

NOW = datetime(2030, 4, 3, 12, tzinfo=UTC)
DUE = NOW + timedelta(hours=6)
CAPABILITY = "external.obligations.read"
TOKEN = "synthetic-portability-token-not-a-real-credential"


class RecordedGateway:
    def __init__(self):
        self.calls = []
        self.attack = False
        self.leases = []

    async def extract_operational(self, source, *, owner_email=None):
        assert TOKEN not in source.model_dump_json()
        for lease in self.leases:
            with pytest.raises(SecretBrokerError, match="closed|expired"):
                lease.get("API_TOKEN")
        self.calls.append(source)
        content = source.content
        assert content is not None
        output = {
            "schema_version": "operational-extraction.v2",
            "observations": [
                {
                    "observation_type": "request",
                    "action_text": "submit",
                    "object_text": "the external checkpoint",
                    "temporal_expression": DUE.isoformat(),
                    "confidence": 0.98,
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
        }
        if self.attack:
            output["granted_permissions"] = ["communication.messages.send"]
        return ModelExtractionResponse(output, "recorded-fixture", "portability-v1")


@pytest_asyncio.fixture(params=["none", "bearer"])
async def portable(tmp_path, request):
    dsn = os.environ.get(
        "NAVOX_CONNECTOR_TEST_DSN", f"sqlite+aiosqlite:///{tmp_path / 'portable.db'}"
    )
    schema = f"portable_{uuid4().hex}"
    administrative = None
    if dsn.startswith("postgresql"):
        administrative = create_async_engine(dsn)
        async with administrative.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # The provider identity is deliberately generated, never known to core code.
    provider = f"unknown_{uuid4().hex[:12]}"
    config = {
        "display_name": "Configured unknown service",
        "provider": provider,
        "base_url": "https://portable.example.invalid",
        "auth": request.param,
        "endpoints": [
            {
                "name": "obligations",
                "path": "/v1/items",
                "capability": CAPABILITY,
                "resource_type": "external.obligation",
                "items_field": "records",
                "id_field": "key",
                "subject_field": "summary",
                "content_field": "instructions",
                "occurred_at_field": "changed",
                "source_url_field": "permalink",
            }
        ],
    }
    calls = []
    uses_token = request.param == "bearer"

    async def http(request):
        assert request.method == "GET"
        assert str(request.url) == "https://portable.example.invalid/v1/items"
        if uses_token:
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        else:
            assert "Authorization" not in request.headers
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "key": "record-7",
                        "summary": "External checkpoint",
                        "instructions": (
                            f"Please submit the external checkpoint by {DUE.isoformat()}."
                        ),
                        "changed": NOW.isoformat(),
                        "permalink": "https://portable.example.invalid/items/record-7",
                    }
                ]
            },
        )

    def connector_factory(configuration, secrets):
        return GenericAPIConnector(configuration, secrets, transport=httpx.MockTransport(http))

    manifest = connector_factory(config, None).get_manifest()
    registry = ConnectorRegistry()
    registry.register(manifest, connector_factory)
    async with factory() as db:
        user = User(email=f"{uuid4().hex}@example.com", timezone="UTC")
        workspace = Workspace(name="Portability workspace")
        other = Workspace(name="Different workspace")
        db.add_all([user, workspace, other])
        await db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id))
        definition = ConnectorDefinition(
            connector_key=manifest.id,
            version=manifest.version,
            display_name=manifest.display_name,
            connector_class=manifest.connector_class,
            trust_level="WORKSPACE_PRIVATE",
            manifest=manifest.model_dump(mode="json", by_alias=True),
        )
        db.add(definition)
        await db.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=user.id,
            workspace_id=workspace.id,
            provider=provider,
            external_account_id="fixture-account",
            authorized_capabilities=[CAPABILITY],
            provider_capabilities=[CAPABILITY],
            config=config,
        )
        db.add(connection)
        await db.commit()
        ids = (connection.id, user.id, workspace.id, other.id)
    model = RecordedGateway()

    class RecordingBroker(SecretBroker):
        async def lease(self, *args, **kwargs):
            handle = await super().lease(*args, **kwargs)
            model.leases.append(handle)
            return handle

    broker = RecordingBroker(
        Settings(connector_secret_encryption_key=Fernet.generate_key().decode())
    )
    if uses_token:
        async with factory() as db:
            await broker.store(
                db,
                {"API_TOKEN": TOKEN},
                connection_id=ids[0],
                workspace_id=ids[2],
                user_id=ids[1],
            )
            await db.commit()
    extractor = OperationalExtractor(model)
    runtime = ConnectorRuntime(registry, secret_broker=broker, retain_canonical_content=False)

    async def sync(*, policy=None, user_id=None, workspace_id=None, request_id=None):
        async with factory() as db:
            row = await db.get(ConnectorConnection, ids[0])

            async def consume(resource):
                return await ingest_connector_resource(
                    db,
                    connector_connection=row,
                    resource=resource,
                    extractor=extractor,
                    timezone_name="UTC",
                )

            return await runtime.sync(
                db,
                connection_id=ids[0],
                user_id=user_id or ids[1],
                workspace_id=workspace_id or ids[2],
                request_id=request_id or uuid4(),
                policy_allowed={CAPABILITY} if policy is None else policy,
                consume=consume,
                consumer_version=extractor.extractor_version,
            )

    try:
        yield SimpleNamespace(
            factory=factory,
            sync=sync,
            ids=ids,
            model=model,
            calls=calls,
            provider=provider,
            registry=registry,
        )
    finally:
        await engine.dispose()
        if administrative:
            async with administrative.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await administrative.dispose()


@pytest.mark.asyncio
async def test_unknown_rest_reaches_today_and_replay_does_not_duplicate_intelligence(
    portable, measure
):
    result = await portable.sync()
    assert result.status == "completed" and result.processed_count == 1
    assert len(portable.model.calls) == 1
    async with portable.factory() as db:
        projection = await build_today_projection(
            db,
            user_id=portable.ids[1],
            workspace_id=portable.ids[2],
            timezone_name="UTC",
            now=NOW,
        )
        items = [*projection.needs_attention, *projection.coming_up]
        assert len(items) == 1
        item = items[0]
        assert "external checkpoint" in item.title
        assert item.sources and item.sources[0].provider == portable.provider
        assert item.sources[0].external_resource_id == "obligations:record-7"
        assert item.due_at is not None and item.due_at.replace(tzinfo=UTC) == DUE
        assert await db.scalar(select(func.count()).select_from(ObservationEvidence)) == 1
        resource = await db.scalar(select(ConnectorResource))
        assert resource is not None
        audits = list(await db.scalars(select(AuditEvent)))
        assert TOKEN not in json.dumps([event.event_metadata for event in audits])
        assert TOKEN not in json.dumps(resource.canonical)
        saved = json.dumps([resource.canonical, resource.provider_metadata])
        assert "Please submit" not in saved and "External checkpoint" not in saved
        assert await db.scalar(select(func.count()).select_from(Action)) == 0
        assert await db.scalar(select(func.count()).select_from(Approval)) == 0
    measure(
        "secret_to_llm",
        0,
        len(portable.model.calls),
        "unknown REST document excludes synthetic bearer token; lease closed",
        database=True,
    )
    replay = await portable.sync()
    assert replay.status == "completed" and replay.duplicate_count == 1
    assert len(portable.model.calls) == 1
    async with portable.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 1
        # Each run records its own acceptance; replay reuses the same revision/result.
        receipts = list(await db.scalars(select(ConnectorSyncReceipt)))
        assert len(receipts) == 2
        assert len({receipt.resource_id for receipt in receipts}) == 1
        assert len({receipt.content_hash for receipt in receipts}) == 1
        assert receipts[0].result_ids == receipts[1].result_ids
        assert await db.scalar(select(func.count()).select_from(ConnectorResource)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["policy", "owner", "workspace", "paused", "grant"])
async def test_unknown_rest_denied_before_provider_or_model_io(portable, denied):
    kwargs = {}
    if denied == "policy":
        kwargs["policy"] = set()
    elif denied == "owner":
        kwargs["user_id"] = uuid4()
    elif denied == "workspace":
        kwargs["workspace_id"] = portable.ids[3]
    else:
        async with portable.factory() as db:
            row = await db.get(ConnectorConnection, portable.ids[0])
            if denied == "paused":
                row.status = "PAUSED"
            else:
                row.authorized_capabilities = []
            await db.commit()
    with pytest.raises(ConnectorRuntimeError):
        await portable.sync(**kwargs)
    assert portable.calls == [] and portable.model.calls == []
    async with portable.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
        assert await db.scalar(select(func.count()).select_from(ConnectorSyncRun)) == 0


@pytest.mark.asyncio
async def test_unknown_service_cannot_grant_model_write_authority(portable):
    portable.model.attack = True
    result = await portable.sync()
    assert result.status == "completed"
    async with portable.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Commitment)) == 0
        assert await db.scalar(select(func.count()).select_from(Action)) == 0
        assert await db.scalar(select(func.count()).select_from(Approval)) == 0
        row = await db.get(ConnectorConnection, portable.ids[0])
        assert row.authorized_capabilities == [CAPABILITY]


def acceptance_setup(monkeypatch, portable):
    from navox.evaluation import connector_live

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(connector_live, "datetime", Clock)
    monkeypatch.setattr(connector_live, "build_connector_registry", lambda _: portable.registry)
    monkeypatch.setattr(connector_live, "get_session_factory", lambda: portable.factory)
    monkeypatch.setattr(connector_live, "get_settings", lambda: Settings(_env_file=None))
    return connector_live


@pytest.mark.asyncio
async def test_live_acceptance_command_links_its_own_run_to_today_and_redacts_report(
    portable, monkeypatch
):
    connector_live = acceptance_setup(monkeypatch, portable)
    dispatched = []

    async def dispatch(payload, *, settings):
        dispatched.append(payload)
        await portable.sync(request_id=UUID(payload.request_id))
        return "test-workflow"

    async def result():
        return 1

    async def connect(_):
        return SimpleNamespace(get_workflow_handle=lambda _: SimpleNamespace(result=result))

    monkeypatch.setattr(connector_live, "dispatch_connector_sync", dispatch)
    monkeypatch.setattr(connector_live, "Client", SimpleNamespace(connect=connect))
    request_id = uuid4()
    report = await connector_live.run_live_acceptance(portable.ids[0], request_id=request_id)
    assert len(dispatched) == 1 and dispatched[0].request_id == str(request_id)
    assert report["passed"] is True and report["today_matches"] == 1
    assert report["resources_seen"] == 1 and report["accepted_receipts"] == 1
    assert all(report["checks"].values())
    assert TOKEN not in json.dumps(report)
    assert "Please submit" not in json.dumps(report)
    assert "portable.example.invalid" not in json.dumps(report)
    with pytest.raises(connector_live.LiveAcceptanceError, match="fresh_request_id"):
        await connector_live.run_live_acceptance(portable.ids[0], request_id=request_id)
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_live_acceptance_rejects_old_today_results_without_new_run_receipts(
    portable, monkeypatch
):
    connector_live = acceptance_setup(monkeypatch, portable)
    await portable.sync()
    request_id = uuid4()
    async with portable.factory() as db:
        db.add(
            ConnectorSyncRun(
                connector_connection_id=portable.ids[0],
                workspace_id=portable.ids[2],
                request_id=request_id,
                status="completed",
                fetch_complete=True,
                resource_count=0,
            )
        )
        await db.commit()
        report = await connector_live.inspect_run(
            db,
            connection_id=portable.ids[0],
            request_id=request_id,
            settings=Settings(_env_file=None),
        )
    assert report["passed"] is False and report["today_matches"] == 0
    assert report["checks"]["source_linked_results_in_today"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["paused", "wrong_workspace", "first_party", "approval_removed"])
async def test_live_acceptance_checks_current_approval_before_dispatch(portable, monkeypatch, case):
    connector_live = acceptance_setup(monkeypatch, portable)
    async with portable.factory() as db:
        connection = await db.get(ConnectorConnection, portable.ids[0])
        if case == "paused":
            connection.status = "PAUSED"
        elif case == "wrong_workspace":
            connection.workspace_id = portable.ids[3]
        elif case == "first_party":
            connection.provider = "google"
        else:
            monkeypatch.setattr(
                connector_live, "build_connector_registry", lambda _: ConnectorRegistry()
            )
        await db.commit()

    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid acceptance target must not dispatch provider I/O")

    monkeypatch.setattr(connector_live, "dispatch_connector_sync", forbidden)
    with pytest.raises(connector_live.LiveAcceptanceError):
        await connector_live.run_live_acceptance(portable.ids[0], request_id=uuid4())
    assert not portable.calls and not portable.model.calls


def test_live_acceptance_cli_does_not_print_exception_credentials(monkeypatch, capsys):
    import sys

    from navox.evaluation import connector_live

    async def failed(*args, **kwargs):
        raise RuntimeError("postgresql://secret:password@database/private")

    monkeypatch.setattr(sys, "argv", ["connector_live", "--connection-id", str(uuid4()), "--live"])
    monkeypatch.setattr(connector_live, "run_live_acceptance", failed)
    assert connector_live.main() == 1
    report = capsys.readouterr().out
    assert json.loads(report)["error"] == "live_run_unavailable"
    assert "password" not in report and "secret" not in report
