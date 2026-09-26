"""Exercise the automatic gateway inside the real per-resource sync transaction."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_ai_gateway_foundation import CAPABILITIES, USER, WORKSPACE
from test_ai_runtime import FakeAdapter
from test_connector_runtime import FixtureConnector

from navox.ai import configured, features
from navox.ai.catalog import catalog_template
from navox.ai.factory import build_ai_gateway
from navox.ai.foundation.contracts import Profile, Provider, ProviderGrant, Sensitivity, TaskType
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.connectors.contracts import CanonicalResource, SyncPage, stable_resource_id
from navox.connectors.intelligence import ingest_connector_resource
from navox.connectors.registry import ConnectorRegistry
from navox.connectors.runtime import ConnectorRuntime
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment, AITaskRun
from navox.db.models import (
    ConnectorConnection,
    ConnectorDefinition,
    IntelligenceSourceReceipt,
    User,
    Workspace,
    WorkspaceMembership,
)
from navox.intelligence.extraction import OperationalExtraction, OperationalExtractor


class TwoResourceConnector(FixtureConnector):
    async def sync(self, request):
        return SyncPage(
            resources=[
                CanonicalResource(
                    resource_id=stable_resource_id(request.connection_id, "fixture.item", item),
                    workspace_id=request.workspace_id,
                    connector_connection_id=request.connection_id,
                    provider="fixture",
                    resource_type="fixture.item",
                    external_id=item,
                    canonical={"subject": "Status", "content": "No action is required."},
                    retrieved_at=datetime.now(UTC),
                )
                for item in ("item-one", "item-two")
            ],
            next_cursor="both-accepted",
            has_more=False,
        )


@pytest.mark.asyncio
async def test_automatic_ingestion_traces_both_resources_without_holding_authority_locks(
    ai_database, monkeypatch
):
    model = ModelDefinition(
        reference=ModelRef(provider=Provider.OPENAI, model="fixture-ingestion"),
        capabilities=CAPABILITIES,
        enabled=True,
        context_window=100_000,
        max_output_tokens=8000,
        allowed_sensitivities=frozenset({Sensitivity.PERSONAL}),
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    )
    profile = Profile.EXTRACTION_HIGH_ACCURACY
    catalog = catalog_template()
    catalog = catalog.model_copy(
        update={
            "models": (model,),
            "profiles": tuple(
                p.model_copy(update={"assignments": (model.reference,)})
                if p.profile == profile
                else p
                for p in catalog.profiles
            ),
        }
    )
    rules = PolicyRules(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities=model.allowed_sensitivities),)
    )
    settings = Settings(
        _env_file=None,
        ai_provider="automatic",
        ai_provider_policy=rules.model_dump(mode="json"),
        openai_api_key=SecretStr("fixture-provider-credential"),
    )
    adapter = FakeAdapter(Provider.OPENAI, output=OperationalExtraction().model_dump_json())
    monkeypatch.setattr(configured, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(configured, "configured_adapters", lambda *_: {Provider.OPENAI: adapter})
    monkeypatch.setattr(
        features,
        "context_capabilities",
        lambda _: {("fixture-service", "fixture.item"): frozenset({"fixture.items.read"})},
    )
    manifest = TwoResourceConnector({}).get_manifest()
    registry = ConnectorRegistry()
    registry.register(manifest, TwoResourceConnector)
    async with ai_database() as db:
        db.add_all(
            [User(id=USER, email="fixture@example.com"), Workspace(id=WORKSPACE, name="Test")]
        )
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=WORKSPACE, user_id=USER))
        definition = ConnectorDefinition(
            connector_key=manifest.id,
            version=manifest.version,
            display_name=manifest.display_name,
            connector_class=manifest.connector_class,
            trust_level="NAVOX_FIRST_PARTY",
            manifest=manifest.model_dump(mode="json", by_alias=True),
        )
        db.add(definition)
        await db.flush()
        connection = ConnectorConnection(
            connector_definition_id=definition.id,
            user_id=USER,
            workspace_id=WORKSPACE,
            provider="fixture",
            external_account_id="fixture",
            authorized_capabilities=["fixture.items.read"],
            provider_capabilities=["fixture.items.read"],
            config={},
        )
        db.add(connection)
        await RegistryStore(db).publish(catalog, expected_revision=0)
        from navox.ai.prompts import EXTRACTION_PROMPT, EXTRACTION_SCHEMA

        evidence = EvaluationEvidence(
            profile=profile,
            task_type=TaskType.EXTRACT,
            prompt=EXTRACTION_PROMPT,
            output_schema=EXTRACTION_SCHEMA,
            quality=1,
            reliability=1,
            p95_latency_ms=100,
            samples=33,
            safety_passed=True,
            evaluated_at=datetime.now(UTC),
            corpus_version="offline.ingestion.fixture",
        )
        db.add(
            AIEvaluationRun(
                model_id=model_key(model.reference),
                model_digest=digest(canonical(model)),
                registry_revision=1,
                task_type=TaskType.EXTRACT.value,
                prompt="commitment_extraction@v2",
                schema="commitment_extraction@v1",
                profile=profile.value,
                evidence=evidence.model_dump_json(),
                evaluated_at=evidence.evaluated_at,
            )
        )
        (
            await db.get(AIProfileAssignment, (profile.value, model_key(model.reference)))
        ).rollout_percent = 100
        await db.commit()

        async def consume(resource):
            return await ingest_connector_resource(
                db,
                connector_connection=connection,
                resource=resource,
                extractor=OperationalExtractor(
                    build_ai_gateway(
                        settings,
                        database=db,
                        user_id=USER,
                        workspace_id=WORKSPACE,
                        connection_id=connection.id,
                        fetched_source=resource,
                    )
                ),
                timezone_name="UTC",
            )

        # A lock held across the independent trace writer would deadlock here on
        # PostgreSQL (and exhaust SQLite's writer timeout). CI runs this on both.
        async with asyncio.timeout(15):
            run = await ConnectorRuntime(registry).sync(
                db,
                connection_id=connection.id,
                workspace_id=WORKSPACE,
                user_id=USER,
                request_id=uuid4(),
                policy_allowed={"fixture.items.read"},
                consume=consume,
            )
        assert run.status == "completed" and run.resource_count == 2
        assert connection.sync_cursor == "both-accepted"
        assert len(adapter.calls) == 2
        assert len((await db.scalars(select(IntelligenceSourceReceipt))).all()) == 2
        traces = (await db.scalars(select(AITaskRun))).all()
        assert len(traces) == 2 and all(row.status == "COMPLETED" for row in traces)
        assert all(row.user_id == USER and row.workspace_id == WORKSPACE for row in traces)
