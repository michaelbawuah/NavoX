"""Offline student-selector boundaries; no provider, feed, or execution calls."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from test_ai_runtime import FakeAdapter

from navox.ai.catalog import catalog_template
from navox.ai.context import ContextDenied
from navox.ai.control import record_evaluation
from navox.ai.foundation.contracts import (
    Capability,
    Profile,
    Provider,
    ProviderGrant,
    ProviderPolicy,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import (
    RegistryStore,
    digest,
    model_key,
)
from navox.ai.foundation.persistence import (
    canonical as registry_canonical,
)
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.runtime import GatewayRuntime, GatewayUnavailable
from navox.ai.store import GatewayStore
from navox.api import student_agent
from navox.api.main import create_app
from navox.core.settings import Settings, get_settings
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.communications import AssistantSession, AssistantTurn
from navox.db.models import AuditEvent, User, Workspace, WorkspaceMembership
from navox.db.session import get_database_session
from navox.db.student import StudentCourse
from navox.student_agent.contracts import (
    MANIFEST_HASH,
    MAX_OBSERVATION_BYTES,
    Observation,
    SelectorOutput,
    SelectorRequest,
    ToolSelection,
    canonical,
    selector_output_schema,
    tool_manifest,
)
from navox.student_agent.selector import (
    SELECTOR_CAPABILITIES,
    SELECTOR_PROMPT,
    SELECTOR_SCHEMA,
    SelectorContext,
    select_next,
    selector_artifacts,
    selector_task,
    validate_selection,
)


def clock_data():
    return {"server_now": datetime.now(UTC).isoformat(), "timezone": "UTC"}


@pytest.mark.parametrize(
    "data",
    [
        {**clock_data(), "grades": ["private"]},
        {**clock_data(), "files": ["syllabus.pdf"]},
        {**clock_data(), "memory": "hidden preference"},
        {**clock_data(), "approved": True},
        {**clock_data(), "provider": "openai"},
    ],
)
def test_observation_cannot_hide_unapproved_context_under_a_tool_name(data):
    with pytest.raises(ValidationError):
        Observation(call_id=uuid4(), name="clock.read", effect="read", data=data)


def test_nested_course_fields_are_minimized_too():
    with pytest.raises(ValidationError):
        Observation(
            call_id=uuid4(),
            name="course.info",
            effect="read",
            data={
                "courses": [
                    {
                        "id": str(uuid4()),
                        "code": "CS 2110",
                        "name": "Programming",
                        "professor": None,
                        "term_start": "2026-08-20",
                        "term_end": "2026-12-10",
                        "syllabus": "hidden document",
                    }
                ],
                "truncated": False,
            },
        )


@pytest_asyncio.fixture
async def env(ai_database, monkeypatch):
    settings = Settings(_env_file=None, app_environment="test").model_copy(
        update={"student_agent_enabled": True}
    )

    async def database():
        async with ai_database() as session:
            yield session

    app = create_app()
    app.include_router(student_agent.router, prefix="/api/v1")
    app.dependency_overrides[get_database_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "selector@example.com",
                "password": "twelve-character-password",
                "display_name": "Student",
            },
        )
        assert response.status_code == 201
        account = response.json()
        user_id, workspace_id = UUID(account["id"]), UUID(account["workspace"]["id"])
        session = await client.post("/api/v1/ai/sessions")
        assert session.status_code == 201
        yield SimpleNamespace(
            client=client,
            app=app,
            factory=ai_database,
            settings=settings,
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=UUID(session.json()["id"]),
            monkeypatch=monkeypatch,
        )


def request_for(env, **changes):
    return SelectorRequest.model_validate(
        {
            "session_id": env.session_id,
            "turn_id": uuid4(),
            "step": 1,
            "question": "What class do I have next?",
            "manifest_hash": MANIFEST_HASH,
            **changes,
        }
    )


class SelectorAdapter(FakeAdapter):
    def capabilities(self, model):
        return SELECTOR_CAPABILITIES


async def install_runtime(env, *, grant=True, qualified=True, scopes=None, output=None):
    template = catalog_template()
    prompt, schema = selector_artifacts()
    model = ModelDefinition(
        reference=ModelRef(provider=Provider.OPENAI, model="offline-student-selector"),
        capabilities=SELECTOR_CAPABILITIES,
        enabled=True,
        allowed_sensitivities=frozenset({Sensitivity.SENSITIVE}),
        context_window=100_000,
        max_output_tokens=1000,
        input_cost_per_million=Decimal("0.01"),
        output_cost_per_million=Decimal("0.01"),
    )
    registry = template.model_copy(
        update={
            "prompts": (*template.prompts, prompt),
            "schemas": (*template.schemas, schema),
            "models": (model,),
            "profiles": tuple(
                profile.model_copy(
                    update={
                        "assignments": (model.reference,)
                        if profile.profile == Profile.PLANNING_HIGH
                        else ()
                    }
                )
                for profile in template.profiles
            ),
        }
    )
    async with env.factory() as database:
        await RegistryStore(database).publish(registry, expected_revision=0)
        for assignment in await database.scalars(select(AIProfileAssignment)):
            assignment.rollout_percent = 100
        if qualified:
            await record_evaluation(
                database,
                model_id=model_key(model.reference),
                model_digest=digest(registry_canonical(model)),
                registry_revision=1,
                evidence=EvaluationEvidence(
                    profile=Profile.PLANNING_HIGH,
                    task_type=TaskType.PLAN,
                    prompt=SELECTOR_PROMPT,
                    output_schema=SELECTOR_SCHEMA,
                    quality=1,
                    reliability=1,
                    p95_latency_ms=10,
                    samples=10,
                    safety_passed=True,
                    evaluated_at=datetime.now(UTC),
                    corpus_version="offline-student-selector-fixture",
                ),
            )
        await database.commit()
    adapter = SelectorAdapter(
        Provider.OPENAI,
        output=json.dumps(
            output or {"selection": {"kind": "tool", "name": "clock.read", "args": {}}}
        ),
    )
    policy = PolicyRules(
        grants=(
            ProviderGrant(
                provider=Provider.OPENAI,
                sensitivities=frozenset({Sensitivity.SENSITIVE if grant else Sensitivity.PERSONAL}),
            ),
        ),
        task_scopes=scopes,
        max_cost=Decimal("0.005"),
        allow_fallback=False,
        max_fallbacks=0,
        allow_shadow=False,
    )
    runtime = GatewayRuntime(GatewayStore(env.factory, policy), {Provider.OPENAI: adapter})

    async def build(settings):
        return runtime

    env.monkeypatch.setattr(student_agent, "build_runtime", build)
    return runtime, adapter


def enable(env):
    env.app.dependency_overrides[get_settings] = lambda: env.settings.model_copy(
        update={"student_agent_model_enabled": True}
    )


def test_manifest_is_identical_to_typescript_and_artifacts_remain_unpublished():
    path = (
        Path(__file__).resolve().parents[3]
        / "packages/assistant-runtime/src/student-tools-manifest.json"
    )
    assert json.loads(path.read_text()) == tool_manifest()
    assert hashlib.sha256(canonical(tool_manifest()).encode()).hexdigest() == MANIFEST_HASH
    registry = catalog_template()
    assert all(prompt.reference != SELECTOR_PROMPT for prompt in registry.prompts)
    assert all(schema.reference != SELECTOR_SCHEMA for schema in registry.schemas)
    schema = selector_output_schema()
    assert schema["additionalProperties"] is False
    assert len(schema["properties"]["selection"]["anyOf"]) == 11


@pytest.mark.asyncio
async def test_default_disabled_never_constructs_a_runtime(env):
    async def forbidden(*args, **kwargs):
        pytest.fail("Disabled selectors must not build providers")

    env.monkeypatch.setattr(student_agent, "build_runtime", forbidden)
    response = await env.client.post(
        "/api/v1/student-agent/select", json=request_for(env).model_dump(mode="json")
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_native_session_read_is_owned_readonly_and_does_not_need_model_enabled(env):
    async with env.factory() as database:
        before = [
            await database.scalar(select(func.count()).select_from(model))
            for model in (AssistantTurn, AuditEvent, AITaskRun)
        ]
    for _ in range(2):
        response = await env.client.get(f"/api/v1/student-agent/sessions/{env.session_id}")
        assert response.status_code == 200
        assert response.json() == {
            "session_id": str(env.session_id),
            "mode": "PERSONAL",
            "status": "active",
            "actions_executed": False,
        }
        assert response.headers["cache-control"] == "no-store"
    async with env.factory() as database:
        assert before == [
            await database.scalar(select(func.count()).select_from(model))
            for model in (AssistantTurn, AuditEvent, AITaskRun)
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign", "closed", "mode", "paused", "role"])
async def test_session_ownership_mode_status_pause_and_membership_rechecked(env, change):
    identifier = env.session_id
    async with env.factory() as database:
        if change == "foreign":
            identifier, foreign_user, foreign_workspace = uuid4(), uuid4(), uuid4()
            database.add_all(
                [
                    User(id=foreign_user, email="foreign-selector@example.com"),
                    Workspace(id=foreign_workspace, name="Foreign", workspace_type="personal"),
                ]
            )
            await database.flush()
            database.add(
                WorkspaceMembership(
                    user_id=foreign_user, workspace_id=foreign_workspace, role="owner"
                )
            )
            database.add(
                AssistantSession(
                    id=identifier,
                    user_id=foreign_user,
                    workspace_id=foreign_workspace,
                    mode="PERSONAL",
                    status="active",
                )
            )
        if change == "closed":
            (await database.get(AssistantSession, identifier)).status = "closed"
        if change == "mode":
            (await database.get(AssistantSession, identifier)).mode = "WORK"
        if change == "paused":
            (await database.get(User, env.user_id)).agent_paused = True
        if change == "role":
            (
                await database.get(WorkspaceMembership, (env.workspace_id, env.user_id))
            ).role = "member"
        await database.commit()
    response = await env.client.get(f"/api/v1/student-agent/sessions/{identifier}")
    assert response.status_code in {401, 404}


@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "tool", "name": "gmail.send", "args": {}},
        {"kind": "tool", "name": "clock.read", "args": {"approved": True}},
        {"kind": "tool", "name": "clock.read", "args": {"approved": None}},
        {"kind": "tool", "name": "tasks.read", "args": {"limit": 13}},
        {"kind": "tool", "name": "tasks.read", "args": {"limit": True}},
        {"kind": "tool", "name": "feed.refresh_review", "args": {"feed_id": "not-a-uuid"}},
        {"kind": "tool", "name": "classes.lookup", "args": {"mode": "at"}},
        {"kind": "tool", "name": "classes.lookup", "args": {"mode": "day", "time": "12:00"}},
        {"kind": "finish", "evidence_call_ids": []},
        {"kind": "tool", "name": "classes.lookup", "args": {"mode": "day", "date": "2026-02-31"}},
        {"kind": "tool", "name": "classes.lookup", "args": {"mode": "now", "weekday": 3}},
        {"kind": "tool", "name": "plan.preview", "args": {}, "approved": True},
        {"kind": "finish", "evidence_call_ids": [], "answer": "Everything was sent"},
        {"kind": "clarify", "question": "What class?", "provider": "another"},
    ],
)
def test_unknown_names_extra_fields_invalid_args_and_forged_approval_rejected(selection):
    with pytest.raises(ValidationError):
        SelectorOutput.model_validate({"selection": selection})


def test_optional_wire_nulls_become_omitted_host_arguments():
    value = ToolSelection(
        kind="tool",
        name="classes.lookup",
        args={"mode": "next", "date": None, "day_offset": None, "weekday": None, "time": None},
    )
    assert value.args == {"mode": "next"}


@pytest.mark.asyncio
async def test_contract_bounds_duplicate_observations_and_wrong_manifest(env):
    with pytest.raises(ValidationError):
        request_for(env, manifest_hash="0" * 64)
    with pytest.raises(ValidationError):
        request_for(env, step=True)
    with pytest.raises(ValidationError):
        request_for(env, step=5)
    with pytest.raises(ValidationError):
        request_for(env, question="q" * 501)
    with pytest.raises(ValidationError):
        request_for(env, max_cost="0.0051")
    with pytest.raises(ValidationError):
        Observation(call_id=uuid4(), name="plan.preview", effect="read", data={})
    with pytest.raises(ValidationError):
        Observation(call_id=uuid4(), name="clock.read", effect="read", data={"text": "界" * 3000})
    item = Observation(
        call_id=uuid4(),
        name="clock.read",
        effect="read",
        data={"server_now": datetime.now(UTC).isoformat(), "timezone": "UTC"},
    )
    with pytest.raises(ValidationError):
        request_for(env, observations=[item] * 2)
    large = {
        "preview_id": str(uuid4()),
        "preview_hash": "a" * 64,
        "expires_at": datetime.now(UTC).isoformat(),
        "base_revision": 0,
        "requires_acceptance": True,
        "result": {
            "start_at": datetime.now(UTC).isoformat(),
            "end_at": datetime.now(UTC).isoformat(),
            "blocks": [],
            "unfitted_work": [],
            "capacities": [],
            "warnings": ["x" * 500 for _ in range(17)],
            "planning_only": True,
        },
    }
    while len(canonical(large).encode()) > MAX_OBSERVATION_BYTES - 16:
        warning = large["result"]["warnings"][-1]
        if len(warning) == 1:
            large["result"]["warnings"].pop()
        else:
            large["result"]["warnings"][-1] = warning[:-1]
    items = [
        Observation(call_id=uuid4(), name="plan.preview", effect="review", data=large)
        for _ in range(4)
    ]
    with pytest.raises(ValidationError):
        request_for(env, observations=items)


@pytest.mark.asyncio
async def test_context_is_exact_bounded_question_manifest_observations_without_hidden_data(env):
    request = request_for(
        env,
        observations=[
            {
                "call_id": uuid4(),
                "name": "clock.read",
                "effect": "read",
                "data": {"server_now": datetime.now(UTC).isoformat(), "timezone": "UTC"},
            }
        ],
    )
    task = selector_task(
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        policy=ProviderPolicy(user_id=env.user_id, workspace_id=env.workspace_id, revision=1),
        max_cost=Decimal("0.005"),
    )
    context = SelectorContext(
        env.factory,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        request=request,
    )
    built = await context.build(task, {})
    document = json.loads(built.content.text)
    assert set(document) == {
        "schema_version",
        "turn_id",
        "step",
        "question",
        "manifest_hash",
        "manifest",
        "observations",
        "previous_error",
    }
    assert document["question"] == request.question
    assert set(document["observations"][0]["data"]) == {"server_now", "timezone"}
    assert built.source_ids == () and built.sensitivity == Sensitivity.SENSITIVE
    assert task.task_type == TaskType.PLAN and task.profile == Profile.PLANNING_HIGH
    assert Capability.TOOL_PROPOSALS in task.capability_requirements
    async with env.factory() as database:
        (await database.get(AssistantSession, env.session_id)).status = "closed"
        await database.commit()
    with pytest.raises(ContextDenied):
        await context.build(task, {})


@pytest.mark.asyncio
async def test_finish_cannot_cite_another_turn_or_forge_freeform_facts(env):
    request = request_for(env)
    output = SelectorOutput.model_validate(
        {"selection": {"kind": "finish", "evidence_call_ids": [str(uuid4())]}}
    )
    with pytest.raises(ValueError):
        validate_selection(output, request)


@pytest.mark.asyncio
async def test_qualified_offline_runtime_proposes_only_and_records_no_assistant_turn(env):
    runtime, adapter = await install_runtime(env)
    request = request_for(env)
    result = await select_next(
        runtime, env.settings, user_id=env.user_id, workspace_id=env.workspace_id, request=request
    )
    assert result.actions_executed is False
    assert result.selection.kind == "tool" and result.selection.name == "clock.read"
    assert result.manifest_hash == MANIFEST_HASH and result.estimated_cost <= request.max_cost
    assert len(adapter.calls) == 1
    assert adapter.calls[0].prompt_ref == SELECTOR_PROMPT
    assert adapter.calls[0].schema_ref == SELECTOR_SCHEMA
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AssistantTurn)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["personal_grant", "unqualified", "scope"])
async def test_existing_personal_grant_or_unqualified_model_cannot_receive_student_context(
    env, restriction
):
    runtime, adapter = await install_runtime(
        env,
        grant=restriction != "personal_grant",
        qualified=restriction != "unqualified",
        scopes=() if restriction == "scope" else None,
    )
    with pytest.raises((GatewayUnavailable, PermissionError, ValueError)):
        await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request_for(env),
        )
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_api_rechecks_session_before_build_and_rejects_origin_bytes_and_extras(env):
    enable(env)

    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid selectors must never build providers")

    env.monkeypatch.setattr(student_agent, "build_runtime", forbidden)
    command = request_for(env).model_dump(mode="json")
    response = await env.client.post(
        "/api/v1/student-agent/select", json={**command, "provider": "openai"}
    )
    assert response.status_code == 422
    response = await env.client.post(
        "/api/v1/student-agent/select", json=command, headers={"Origin": "https://evil.test"}
    )
    assert response.status_code == 403
    response = await env.client.post(
        "/api/v1/student-agent/select",
        content=b"x" * 40961,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    response = await env.client.post(
        "/api/v1/student-agent/select", json={**command, "session_id": str(uuid4())}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_enabled_api_returns_qualified_proposal_and_no_orm_rollback_identity_error(env):
    enable(env)
    _, adapter = await install_runtime(env)
    response = await env.client.post(
        "/api/v1/student-agent/select", json=request_for(env).model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    assert response.json()["selection"] == {"kind": "tool", "name": "clock.read", "args": {}}
    assert response.json()["actions_executed"] is False and len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_unauthenticated_requests_stop_before_disabled_hook_or_provider_build(env):
    async def forbidden(*args, **kwargs):
        pytest.fail("Unauthenticated requests must not build providers")

    env.monkeypatch.setattr(student_agent, "build_runtime", forbidden)
    env.client.cookies.clear()
    assert (
        await env.client.get(f"/api/v1/student-agent/sessions/{env.session_id}")
    ).status_code == 401
    assert (
        await env.client.post(
            "/api/v1/student-agent/select", json=request_for(env).model_dump(mode="json")
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_chunked_oversized_body_rejected_without_a_content_length(env):
    enable(env)

    async def forbidden(*args, **kwargs):
        pytest.fail("Oversized requests must not build providers")

    async def body():
        yield b"x" * 20_000
        yield b"y" * 21_000

    env.monkeypatch.setattr(student_agent, "build_runtime", forbidden)
    response = await env.client.post(
        "/api/v1/student-agent/select", content=body(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_credential_like_context_never_reaches_provider(env):
    runtime, adapter = await install_runtime(env)
    request = request_for(env, question="Remember my sk-abcdefghijklmnopqrstuv secret")
    with pytest.raises(ContextDenied):
        await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request,
        )
    assert adapter.calls == []
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AITaskRun)) == 0


@pytest.mark.asyncio
async def test_session_closed_during_model_generation_never_returns_a_proposal(env):
    runtime, adapter = await install_runtime(env)

    async def close_session():
        async with env.factory() as database:
            (await database.get(AssistantSession, env.session_id)).status = "closed"
            await database.commit()

    adapter.during = close_session
    with pytest.raises(GatewayUnavailable):
        await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request_for(env),
        )
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AssistantTurn)) == 0


@pytest.mark.asyncio
async def test_clock_uses_database_time_and_saved_timezone_without_classes_or_models(env):
    moment = datetime(2027, 3, 14, 7, 5, tzinfo=UTC)
    calls = []

    async def database_time(database):
        calls.append(database)
        return moment

    async def forbidden(*args, **kwargs):
        pytest.fail("Reading the clock must not construct a model")

    env.monkeypatch.setattr(student_agent, "database_now", database_time)
    env.monkeypatch.setattr(student_agent, "build_runtime", forbidden)
    async with env.factory() as database:
        user = await database.get(User, env.user_id)
        user.timezone = "America/New_York"
        user.agent_paused = True
        (await database.get(AssistantSession, env.session_id)).status = "closed"
        await database.commit()
    response = await env.client.get("/api/v1/student-agent/clock")
    assert response.status_code == 200
    assert response.json() == {"server_now": "2027-03-14T07:05:00Z", "timezone": "America/New_York"}
    assert len(calls) == 1
    assert response.headers["cache-control"] == "no-store"
    env.client.cookies.clear()
    assert (await env.client.get("/api/v1/student-agent/clock")).status_code == 401


@pytest.mark.asyncio
async def test_overall_feature_gate_hides_every_endpoint_before_provider_construction(env):
    async def forbidden(*args, **kwargs):
        pytest.fail("Overall disabled feature must not construct a provider")

    env.monkeypatch.setattr(student_agent, "build_runtime", forbidden)
    env.app.dependency_overrides[get_settings] = lambda: env.settings.model_copy(
        update={"student_agent_enabled": False, "student_agent_model_enabled": True}
    )
    assert (await env.client.get("/api/v1/student-agent/clock")).status_code == 404
    assert (
        await env.client.get(f"/api/v1/student-agent/sessions/{env.session_id}")
    ).status_code == 404
    assert (
        await env.client.post(
            "/api/v1/student-agent/select", json=request_for(env).model_dump(mode="json")
        )
    ).status_code == 404


async def course_observation(env):
    identifier = uuid4()
    async with env.factory() as database:
        database.add(
            StudentCourse(
                id=identifier,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code="CS 2110",
                name="Programming",
                professor=None,
                term_start=date(2026, 8, 20),
                term_end=date(2026, 12, 10),
            )
        )
        await database.commit()
    return {
        "call_id": uuid4(),
        "name": "course.info",
        "effect": "read",
        "data": {
            "courses": [
                {
                    "id": str(identifier),
                    "code": "CS 2110",
                    "name": "Programming",
                    "professor": None,
                    "term_start": "2026-08-20",
                    "term_end": "2026-12-10",
                }
            ],
            "truncated": False,
        },
    }


@pytest.mark.asyncio
async def test_current_owned_minimized_course_can_reach_qualified_selector(env):
    observation = await course_observation(env)
    runtime, adapter = await install_runtime(env)
    result = await select_next(
        runtime,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        request=request_for(env, observations=[observation]),
    )
    assert result.actions_executed is False
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign", "changed", "archived", "deleted"])
async def test_forged_stale_or_foreign_source_never_reaches_selector(env, change):
    observation = await course_observation(env)
    identifier = UUID(observation["data"]["courses"][0]["id"])
    async with env.factory() as database:
        course = await database.get(StudentCourse, identifier)
        if change == "foreign":
            workspace = Workspace(id=uuid4(), name="Other", workspace_type="personal")
            database.add(workspace)
            await database.flush()
            course.workspace_id = workspace.id
        elif change == "changed":
            course.name = "Changed after tool read"
        elif change == "archived":
            course.archived = True
        else:
            await database.delete(course)
        await database.commit()
    runtime, adapter = await install_runtime(env)
    with pytest.raises(ContextDenied):
        await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request_for(env, observations=[observation]),
        )
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_current_owned_plan_preview_context_is_minimized_and_fresh(env):
    from navox.student_planning import service as planning
    from navox.student_planning.schemas import PreviewInput, TaskCreate

    async with env.factory() as database:
        await planning.save_task(
            database,
            env.user_id,
            env.workspace_id,
            TaskCreate(request_id=uuid4(), title="Review circuits"),
        )
        preview = await planning.preview(
            database, env.user_id, env.workspace_id, PreviewInput(days=1)
        )
    observation = {
        "call_id": uuid4(),
        "name": "plan.preview",
        "effect": "review",
        # Match the tool bridge's minimized observation; replacement snapshots
        # belong to the student's approval screen, not selector context.
        "data": preview.model_dump(mode="json", exclude={"replaced_blocks"}),
    }
    runtime, adapter = await install_runtime(env)
    await select_next(
        runtime,
        env.settings,
        user_id=env.user_id,
        workspace_id=env.workspace_id,
        request=request_for(env, observations=[observation]),
    )
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        await planning.save_task(
            database,
            env.user_id,
            env.workspace_id,
            TaskCreate(request_id=uuid4(), title="Changed task list"),
        )
    adapter.calls.clear()
    with pytest.raises(ContextDenied):
        await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request_for(env, observations=[observation]),
        )
    assert adapter.calls == []
