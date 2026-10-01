"""SPEC-008 intent planning: bounded registered plans, honest failures, no actions."""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_ai_evaluation import model_for
from test_ai_gateway_foundation import CAPABILITIES
from test_ai_runtime import FakeAdapter

from navox.ai.catalog import catalog_template
from navox.ai.control import record_evaluation
from navox.ai.foundation.contracts import (
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import RegistrySnapshot
from navox.ai.intent_plan import (
    INTENT_PLAN_PROMPT,
    INTENT_PLAN_SCHEMA,
    MAX_RECENT_REFERENCES,
    MAX_REFERENCE_LENGTH,
    MAX_UTTERANCE_LENGTH,
    IntentPlan,
    IntentPlanRequest,
    intent_plan_json_schema,
    validate_plan_spans,
)
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.runtime import GatewayRuntime
from navox.ai.store import GatewayStore
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.base import Base
from navox.db.communications import AssistantSession


def plan_payload(route: str = "email.search", **changes: object) -> dict[str, object]:
    intent: dict[str, object] = {
        "route": route,
        "question": "What did Sarah email me",
        "entity": {"kind": "PERSON", "value": "Sarah", "confidence": 0.9},
        "time": {"kind": "NONE", "expression": None, "confidence": 1},
        "reference": {"kind": "NONE", "ordinal": None},
        "confidence": 0.85,
        "requires_clarification": False,
        "clarification": None,
    }
    intent.update(changes)
    return {"version": 1, "intents": [intent]}


def intent_first_intent() -> dict[str, object]:
    intents = plan_payload()["intents"]
    assert isinstance(intents, list)
    return intents[0]  # type: ignore[no-any-return]


def intent_runtime_registry(*, models: bool) -> RegistrySnapshot:
    template = catalog_template()
    if not models:
        return template
    definitions = (
        model_for(Provider.OPENAI).model_copy(
            update={
                "enabled": True,
                "allowed_sensitivities": frozenset({Sensitivity.PUBLIC, Sensitivity.PERSONAL}),
            }
        ),
    )
    return template.model_copy(
        update={
            "models": definitions,
            "profiles": tuple(
                profile.model_copy(
                    update={
                        "assignments": tuple(model.reference for model in definitions)
                        if profile.profile == Profile.PLANNING_HIGH
                        else ()
                    }
                )
                for profile in template.profiles
            ),
        }
    )


def intent_policy() -> PolicyRules:
    return PolicyRules(
        grants=(
            ProviderGrant(
                provider=Provider.OPENAI,
                sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.PERSONAL}),
            ),
        ),
        max_cost=Decimal("1"),
        preferred_provider=Provider.OPENAI,
    )


async def _unconfigured_runtime(_settings):  # pragma: no cover - replaced per test
    from navox.ai.factory import AIProviderNotConfigured

    raise AIProviderNotConfigured("The registered gateway is not enabled")


@pytest_asyncio.fixture
async def intent_env(monkeypatch):
    """An authenticated API client whose catalog is published per test."""

    from navox.api import ai_operations
    from navox.api.main import create_app
    from navox.core.settings import Settings, get_settings
    from navox.db import models  # noqa: F401
    from navox.db.session import get_database_session

    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"intent_plan_api_{uuid4().hex}"
    admin = None
    if dsn.startswith("postgresql"):
        admin = create_async_engine(dsn)
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(dsn)
    async with engine.begin() as connection:
        if not admin:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, app_environment="test")

    async def database_override():
        async with factory() as database:
            yield database

    app = create_app()
    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(ai_operations, "build_runtime", _unconfigured_runtime)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "intent-plan@example.com",
                "password": "twelve-character-password",
                "display_name": "Intent owner",
            },
        )
        assert response.status_code == 201
        yield SimpleNamespace(
            app=app,
            client=client,
            factory=factory,
            user_id=UUID(response.json()["id"]),
            workspace_id=UUID(response.json()["workspace"]["id"]),
            monkeypatch=monkeypatch,
            module=ai_operations,
        )
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def install_runtime(env, *, models: bool, adapter_output: str | None = None):
    registry = intent_runtime_registry(models=models)
    async with env.factory() as database:
        await RegistryStore(database).publish(registry, expected_revision=0)
        if models:
            for assignment in (await database.scalars(select(AIProfileAssignment))).all():
                assignment.rollout_percent = 100
            for model in registry.models:
                await record_evaluation(
                    database,
                    model_id=model_key(model.reference),
                    model_digest=digest(canonical(model)),
                    registry_revision=1,
                    evidence=EvaluationEvidence(
                        profile=Profile.PLANNING_HIGH,
                        task_type=TaskType.PLAN,
                        prompt=INTENT_PLAN_PROMPT,
                        output_schema=INTENT_PLAN_SCHEMA,
                        quality=1,
                        reliability=1,
                        p95_latency_ms=10,
                        samples=12,
                        safety_passed=True,
                        evaluated_at=datetime.now(UTC),
                        corpus_version="authored-intent-fixture",
                    ),
                )
        await database.commit()
    adapter = FakeAdapter(Provider.OPENAI, output=adapter_output or json.dumps(plan_payload()))
    runtime = GatewayRuntime(GatewayStore(env.factory, intent_policy()), {Provider.OPENAI: adapter})

    async def build(_settings):
        return runtime

    env.monkeypatch.setattr(env.module, "build_runtime", build)
    return adapter


def test_catalog_registers_one_bounded_plan_artifact():
    snapshot = catalog_template()
    prompt = next(item for item in snapshot.prompts if item.reference == INTENT_PLAN_PROMPT)
    assert prompt.output_schema == INTENT_PLAN_SCHEMA
    assert next(item for item in snapshot.schemas if item.reference == INTENT_PLAN_SCHEMA)
    profile = next(item for item in snapshot.profiles if item.profile == Profile.PLANNING_HIGH)
    assert TaskType.PLAN in profile.task_types
    assert CAPABILITIES.issuperset(profile.required_capabilities)
    schema = intent_plan_json_schema()
    intents = schema["properties"]["intents"]
    assert intents["maxItems"] == MAX_RECENT_REFERENCES
    assert set(intents["items"]["properties"]) == {
        "route",
        "question",
        "entity",
        "time",
        "reference",
        "confidence",
        "requires_clarification",
        "clarification",
    }
    current_routes = intents["items"]["properties"]["route"]["enum"]
    assert "class.next" in current_routes
    assert "time.now" in current_routes
    assert "action.history" in current_routes


def test_historical_read_only_plan_artifacts_remain_bounded():
    snapshot = catalog_template()
    v1 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v1"
    )
    v2 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v2"
    )
    v3 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v3"
    )
    v4 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v4"
    )
    v5 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v5"
    )
    v6 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v6"
    )
    v7 = next(
        item
        for item in snapshot.schemas
        if item.reference.name == "assistant_intent_plan" and item.reference.version == "v7"
    )
    v8 = next(item for item in snapshot.schemas if item.reference == INTENT_PLAN_SCHEMA)
    old_routes = json.loads(v1.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    middle_routes = json.loads(v2.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    news_routes = json.loads(v3.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    weather_routes = json.loads(v4.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    new_routes = json.loads(v5.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    class_routes = json.loads(v6.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    time_routes = json.loads(v7.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    history_routes = json.loads(v8.document.text)["properties"]["intents"]["items"]["properties"][
        "route"
    ]["enum"]
    assert "subscription.search" not in old_routes
    assert "news.read" not in old_routes
    assert "subscription.search" in middle_routes
    assert "news.read" not in middle_routes
    assert "news.read" in news_routes
    assert "weather.read" not in news_routes
    assert "weather.read" in weather_routes
    assert "class.next" not in new_routes
    assert "class.next" in class_routes
    assert "time.now" not in class_routes
    assert "time.now" in time_routes
    assert "action.history" not in time_routes
    assert "action.history" in history_routes
    assert "time.now" in history_routes
    old_fields = json.loads(v4.document.text)["properties"]["intents"]["items"]["properties"]
    assert "question" not in old_fields
    assert "subscription.search" in new_routes
    assert "news.read" in new_routes
    assert "weather.read" in new_routes
    old_prompt = next(item for item in snapshot.prompts if item.reference == v2.reference)
    assert "never turn that request into a read-only lookup" in old_prompt.instructions
    assert "news.read" not in old_prompt.instructions
    prompt = next(item for item in snapshot.prompts if item.reference == INTENT_PLAN_PROMPT)
    assert "never turn that request into a read-only lookup" in prompt.instructions
    assert "Trending activity is not verification" in prompt.instructions
    assert "weather.read" in prompt.instructions
    assert "exact contiguous question span" in prompt.instructions
    assert "class.next" in prompt.instructions
    assert "time.now" in prompt.instructions
    assert "action.history" in prompt.instructions
    v6_prompt = next(item for item in snapshot.prompts if item.reference == v6.reference)
    assert "class.next" in v6_prompt.instructions
    assert "time.now" not in v6_prompt.instructions
    assert "action.history" not in v6_prompt.instructions
    v7_prompt = next(item for item in snapshot.prompts if item.reference == v7.reference)
    assert "time.now" in v7_prompt.instructions
    assert "action.history" not in v7_prompt.instructions
    assert IntentPlan.model_validate(plan_payload(route="action.history")).intents[0].route == (
        "action.history"
    )
    assert IntentPlan.model_validate(plan_payload(route="time.now")).intents[0].route == "time.now"
    with pytest.raises(ValidationError):
        IntentPlan.model_validate(plan_payload(route="clock.now"))
    assert (
        IntentPlan.model_validate(
            plan_payload(
                route="subscription.search",
                entity={"kind": "ORGANIZATION", "value": "Netflix", "confidence": 0.9},
            )
        )
        .intents[0]
        .route
        == "subscription.search"
    )
    assert (
        IntentPlan.model_validate(
            plan_payload(
                route="news.read",
                entity={"kind": "PERSON", "value": "Jane Doe", "confidence": 0.9},
            )
        )
        .intents[0]
        .route
        == "news.read"
    )


def test_no_historical_plan_schema_names_a_later_route():
    """A route added later must stay out of every published historical artifact."""

    snapshot = catalog_template()

    def route_enum(version: str) -> list[str]:
        schema = next(
            item
            for item in snapshot.schemas
            if item.reference.name == "assistant_intent_plan" and item.reference.version == version
        )
        document = json.loads(schema.document.text)
        enum: list[str] = document["properties"]["intents"]["items"]["properties"]["route"]["enum"]
        return enum

    for version in ("v1", "v2", "v3", "v4", "v5", "v6"):
        assert "time.now" not in route_enum(version), version
        assert "action.history" not in route_enum(version), version
    assert "class.next" in route_enum("v6")
    assert "class.next" in route_enum("v7")
    assert "time.now" in route_enum("v7")
    assert "action.history" not in route_enum("v7")
    assert "time.now" in route_enum(INTENT_PLAN_SCHEMA.version)
    assert "action.history" in route_enum(INTENT_PLAN_SCHEMA.version)
    assert INTENT_PLAN_SCHEMA.version == "v8"


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "intents": []},
        {"version": 1, "intents": [intent_first_intent()] * 5},
        plan_payload(route="files.delete"),
        plan_payload(clarification="Which email?"),
        plan_payload(route="assistant.clarify", requires_clarification=False),
        plan_payload(action_grant={"send": True}),
    ],
)
def test_plan_contract_rejects_unregistered_or_incoherent_routes(payload):
    with pytest.raises(ValidationError):
        IntentPlan.model_validate(payload)


def test_compound_spans_are_exact_ordered_and_slot_grounded():
    utterance = "What did Sarah email me and what's the weather today?"
    first = intent_first_intent().copy()
    first["question"] = "What did Sarah email me"
    second = intent_first_intent().copy()
    second.update(
        route="weather.read",
        question="what's the weather today?",
        entity={"kind": "NONE", "value": None, "confidence": 1},
        time={"kind": "RELATIVE", "expression": "today", "confidence": 0.9},
    )
    plan = IntentPlan.model_validate({"version": 1, "intents": [first, second]})
    validate_plan_spans(plan, utterance)
    for changed in (
        {**second, "question": "weather tomorrow?"},
        {**second, "question": "What did Sarah email me"},
        {**second, "entity": {"kind": "PERSON", "value": "Sarah", "confidence": 0.9}},
    ):
        with pytest.raises(ValueError):
            validate_plan_spans(
                IntentPlan.model_validate({"version": 1, "intents": [first, changed]}),
                utterance,
            )


def test_plan_contract_has_no_action_surface():
    assert set(IntentPlan.model_fields) == {"version", "intents"}
    assert set(intent_first_intent()) == {
        "route",
        "question",
        "entity",
        "time",
        "reference",
        "confidence",
        "requires_clarification",
        "clarification",
    }
    with pytest.raises(ValidationError):
        IntentPlanRequest(utterance="Hi", recent_references=["a", "b", "c", "d", "e"])
    with pytest.raises(ValidationError):
        IntentPlanRequest(utterance="Hi", recent_references=["x" * (MAX_REFERENCE_LENGTH + 1)])
    with pytest.raises(ValidationError):
        IntentPlanRequest(utterance="x" * (MAX_UTTERANCE_LENGTH + 1))
    assert MAX_RECENT_REFERENCES == 4


@pytest.mark.asyncio
async def test_authenticated_plan_returns_routes_and_audits_the_task(intent_env):
    env = intent_env
    adapter = await install_runtime(env, models=True)
    created = await env.client.post("/api/v1/ai/sessions")
    assert created.status_code == 201
    session_id = created.json()["id"]
    response = await env.client.post(
        "/api/v1/ai/assistant/intents",
        json={
            "utterance": "What did Sarah email me about the renewal?",
            "recent_references": ["What needs my attention today?"],
            "session_id": session_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["actions_executed"] is False
    assert body["session_id"] == session_id
    assert body["turn_sequence"] == 1
    first = body["plan"]["intents"][0]
    assert first["route"] == "email.search"
    assert first["entity"]["value"] == "Sarah"
    assert set(first) == {
        "route",
        "question",
        "entity",
        "time",
        "reference",
        "confidence",
        "requires_clarification",
        "clarification",
    }
    assert len(adapter.calls) == 1
    payload = json.loads(adapter.calls[0].context.text)
    assert payload["utterance"] == "What did Sarah email me about the renewal?"
    assert payload["recent_user_questions"] == ["What needs my attention today?"]
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
        assert run is not None
        assert run.prompt == "assistant_intent_plan@v8"
        assert run.schema == "assistant_intent_plan@v8"
        assert run.profile == Profile.PLANNING_HIGH.value
        assert run.status == "COMPLETED" and run.shadow is False


@pytest.mark.asyncio
async def test_plan_request_bounds_and_authentication(intent_env):
    env = intent_env
    await install_runtime(env, models=True)
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as anonymous:
        unauthorized = await anonymous.post(
            "/api/v1/ai/assistant/intents", json={"utterance": "Hello"}
        )
    assert unauthorized.status_code == 401
    for body in (
        {"utterance": "x" * (MAX_UTTERANCE_LENGTH + 1)},
        {"utterance": "Hello", "recent_references": ["a", "b", "c", "d", "e"]},
        {"utterance": "Hello", "tool_name": "gmail.send"},
        {"utterance": "Hello", "action_grant": {"send": True}},
        {"utterance": "Hello", "provider": "xai"},
    ):
        response = await env.client.post("/api/v1/ai/assistant/intents", json=body)
        assert response.status_code == 422, body


@pytest.mark.asyncio
async def test_no_qualified_provider_returns_an_honest_unavailable(intent_env):
    env = intent_env
    adapter = await install_runtime(env, models=False)
    response = await env.client.post(
        "/api/v1/ai/assistant/intents", json={"utterance": "What did Sarah email me?"}
    )
    assert response.status_code == 503
    assert adapter.calls == []
    env.monkeypatch.setattr(env.module, "build_runtime", _unconfigured_runtime)
    unconfigured = await env.client.post(
        "/api/v1/ai/assistant/intents", json={"utterance": "What did Sarah email me?"}
    )
    assert unconfigured.status_code == 503


@pytest.mark.asyncio
async def test_model_output_outside_the_registered_routes_is_rejected(intent_env):
    env = intent_env
    adapter = await install_runtime(
        env, models=True, adapter_output=json.dumps(plan_payload(route="files.delete"))
    )
    response = await env.client.post(
        "/api/v1/ai/assistant/intents", json={"utterance": "Delete every file"}
    )
    assert response.status_code == 503
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_credential_like_utterance_never_reaches_the_model(intent_env):
    env = intent_env
    adapter = await install_runtime(env, models=True)
    response = await env.client.post(
        "/api/v1/ai/assistant/intents",
        json={"utterance": "Forward api_key=abcdef1234567890 to Sarah"},
    )
    assert response.status_code == 503
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_a_foreign_session_cannot_be_extended(intent_env):
    env = intent_env
    await install_runtime(env, models=True)
    created = await env.client.post("/api/v1/ai/sessions")
    assert created.status_code == 201
    session_id = created.json()["id"]
    from navox.db.models import User

    other_user = uuid4()
    async with env.factory() as database:
        database.add(User(id=other_user, email="someone-else@example.com"))
        await database.flush()
        row = await database.get(AssistantSession, UUID(session_id))
        assert row is not None
        row.user_id = other_user
        await database.commit()
    response = await env.client.post(
        "/api/v1/ai/assistant/intents",
        json={"utterance": "What did Sarah email me?", "session_id": session_id},
    )
    assert response.status_code == 409
