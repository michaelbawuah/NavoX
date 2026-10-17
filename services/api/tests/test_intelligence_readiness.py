"""Read-only setup readiness reflects scoped, qualified extraction configuration."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from navox.ai.catalog import catalog_template
from navox.ai.foundation.contracts import (
    Capability,
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.prompts import EXTRACTION_PROMPT, EXTRACTION_SCHEMA
from navox.ai.providers import HTTPAdapter
from navox.ai.routing import EvaluationEvidence, PolicyRules, RoutingTaskScope
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.ai_registry import (
    AIEvaluationRun,
    AIModel,
    AIProfileAssignment,
    AIProvider,
    AIProviderHealth,
    AIRoutingPolicy,
    AITaskRun,
)
from navox.db.models import User
from navox.db.session import get_database_session


@pytest_asyncio.fixture
async def readiness_env(ai_database, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider="automatic",
        openai_api_key="fixture-key-not-used",
    )
    app = create_app()

    async def database():
        async with ai_database() as session:
            yield session

    async def no_provider(*args, **kwargs):
        raise AssertionError("A readiness read must never call a provider")

    monkeypatch.setattr(HTTPAdapter, "_request", no_provider)
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "readiness@example.com",
                "display_name": "Readiness owner",
                "password": "twelve-character-password",
            },
        )
        account = response.json()
        user_id, workspace_id = UUID(account["id"]), UUID(account["workspace"]["id"])
        scope = RoutingTaskScope(
            user_id=user_id,
            workspace_id=workspace_id,
            task_type=TaskType.EXTRACT,
            profile=Profile.EXTRACTION_HIGH_ACCURACY,
            prompt=EXTRACTION_PROMPT,
            output_schema=EXTRACTION_SCHEMA,
            sensitivity=Sensitivity.PERSONAL,
        )
        policy = PolicyRules(
            grants=(
                ProviderGrant(
                    provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PERSONAL})
                ),
            ),
            task_scopes=(scope,),
        )
        settings.ai_provider_policy = policy.model_dump(mode="json")
        reference = ModelRef(provider=Provider.OPENAI, model="fixture-extraction")
        model = ModelDefinition(
            reference=reference,
            capabilities=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
            enabled=True,
            allowed_sensitivities=frozenset({Sensitivity.PERSONAL}),
            context_window=100_000,
            max_output_tokens=4000,
            input_cost_per_million=Decimal("0.01"),
            output_cost_per_million=Decimal("0.01"),
        )
        base = catalog_template()
        registry = base.model_copy(
            update={
                "models": (model,),
                "profiles": tuple(
                    profile.model_copy(update={"assignments": (reference,)})
                    if profile.profile == Profile.EXTRACTION_HIGH_ACCURACY
                    else profile
                    for profile in base.profiles
                ),
            }
        )
        evidence = EvaluationEvidence(
            profile=scope.profile,
            task_type=scope.task_type,
            prompt=scope.prompt,
            output_schema=scope.output_schema,
            quality=0.99,
            reliability=0.99,
            p95_latency_ms=100,
            samples=20,
            safety_passed=True,
            evaluated_at=datetime.now(UTC),
            corpus_version="synthetic-readiness.v1",
        )
        async with ai_database() as session:
            await RegistryStore(session).publish(registry, expected_revision=0)
            assignment = await session.get(
                AIProfileAssignment, (scope.profile.value, model_key(reference))
            )
            assignment.rollout_percent = 100
            session.add(
                AIEvaluationRun(
                    model_id=model_key(reference),
                    model_digest=digest(canonical(model)),
                    registry_revision=registry.revision,
                    task_type=scope.task_type.value,
                    profile=scope.profile.value,
                    prompt=f"{scope.prompt.name}@{scope.prompt.version}",
                    schema=f"{scope.output_schema.name}@{scope.output_schema.version}",
                    evidence=evidence.model_dump_json(),
                    evaluated_at=evidence.evaluated_at,
                )
            )
            await session.commit()
        yield SimpleNamespace(
            client=client,
            factory=ai_database,
            settings=settings,
            policy=policy,
            scope=scope,
            user_id=user_id,
            workspace_id=workspace_id,
            model_id=model_key(reference),
        )


@pytest.mark.asyncio
async def test_automatic_ready_only_for_exact_source_binding_and_no_read_side_effects(
    readiness_env,
):
    env = readiness_env
    for _ in range(2):
        response = await env.client.get("/api/v1/intelligence/status")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["processing_ready"] is True
        assert "read access" in response.json()["readiness_message"]
        assert response.json()["observation_count"] == 0
    async with env.factory() as session:
        assert await session.scalar(select(func.count()).select_from(AITaskRun)) == 0
        health = await session.get(AIProviderHealth, env.model_id)
        assert health.probe_until is None and health.failures == 0
        assignment = await session.get(AIProfileAssignment, (env.scope.profile.value, env.model_id))
        assert assignment.rollout_percent == 100
    assert env.settings.ai_provider_policy == env.policy.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked",
    [
        "key",
        "no_grant",
        "foreign_scope",
        "ordinary_chat_only",
        "workspace_denial",
        "user_denial",
        "paused",
        "traffic",
        "model_disabled",
        "provider_disabled",
        "provider_cooldown",
        "qualification_expired",
        "qualification_wrong_prompt",
    ],
)
async def test_automatic_config_does_not_imply_source_permission(readiness_env, blocked):
    env = readiness_env
    if blocked == "key":
        env.settings.openai_api_key = None
    elif blocked == "no_grant":
        env.settings.ai_provider_policy = {}
    elif blocked == "foreign_scope":
        foreign = env.scope.model_copy(update={"user_id": uuid4()})
        env.settings.ai_provider_policy = env.policy.model_copy(
            update={"task_scopes": (foreign,)}
        ).model_dump(mode="json")
    elif blocked == "ordinary_chat_only":
        env.settings.ai_provider_policy = env.policy.model_copy(
            update={"task_scopes": (), "allow_personal_conversation": True}
        ).model_dump(mode="json")
    async with env.factory() as session:
        if blocked in {"workspace_denial", "user_denial"}:
            session.add(
                AIRoutingPolicy(
                    workspace_id=env.workspace_id,
                    scope_key="workspace" if blocked == "workspace_denial" else str(env.user_id),
                    revision=1,
                    policy=PolicyRules().model_dump_json(),
                )
            )
        elif blocked == "paused":
            user = await session.get(User, env.user_id)
            user.agent_paused = True
        elif blocked == "traffic":
            row = await session.get(AIProfileAssignment, (env.scope.profile.value, env.model_id))
            row.rollout_percent = 0
        elif blocked == "model_disabled":
            row = await session.get(AIModel, env.model_id)
            row.enabled = False
        elif blocked == "provider_disabled":
            row = await session.get(AIProvider, Provider.OPENAI.value)
            row.enabled = False
        elif blocked == "provider_cooldown":
            row = await session.get(AIProviderHealth, env.model_id)
            row.retry_after = datetime.now(UTC) + timedelta(minutes=5)
        elif blocked.startswith("qualification_"):
            row = await session.scalar(select(AIEvaluationRun))
            if blocked == "qualification_wrong_prompt":
                row.prompt = "commitment_extraction@v1"
            else:
                evidence = EvaluationEvidence.model_validate_json(row.evidence)
                row.evidence = evidence.model_copy(
                    update={"evaluated_at": datetime.now(UTC) - timedelta(days=31)}
                ).model_dump_json()
        await session.commit()
    response = await env.client.get("/api/v1/intelligence/status")
    assert response.status_code == 200
    assert response.json()["processing_ready"] is False
    assert "fixture-key" not in response.text
    assert "commitment_extraction" not in response.text
    if blocked != "paused":
        assert "add a task yourself" in response.json()["readiness_message"]


@pytest.mark.asyncio
async def test_positive_canary_is_stable_readiness_not_random_admission(readiness_env):
    env = readiness_env
    async with env.factory() as session:
        row = await session.get(AIProfileAssignment, (env.scope.profile.value, env.model_id))
        row.rollout_percent = 5
        await session.commit()
    for _ in range(5):
        response = await env.client.get("/api/v1/intelligence/status")
        assert response.json()["processing_ready"] is True
    async with env.factory() as session:
        assert await session.scalar(select(func.count()).select_from(AITaskRun)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,has_key,expected",
    [("openai", True, True), ("openai", False, False), ("disabled", True, False)],
)
async def test_deliberate_legacy_extraction_mode_is_preserved(
    readiness_env, mode, has_key, expected
):
    env = readiness_env
    env.settings.ai_provider = mode
    if not has_key:
        env.settings.openai_api_key = None
    response = await env.client.get("/api/v1/intelligence/status")
    assert response.json()["processing_ready"] is expected
