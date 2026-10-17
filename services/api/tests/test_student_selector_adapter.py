"""Real adapter protocol with synthetic responses; never provider or rollout traffic."""

import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import test_student_agent_selector as selector_fixtures
from pydantic import SecretStr
from sqlalchemy import select
from test_student_agent_selector import install_runtime, request_for

from navox.ai.conversation import conversation_task
from navox.ai.foundation.adapter import ErrorCode, ProviderRequest
from navox.ai.foundation.contracts import (
    Capability,
    Provider,
    ProviderGrant,
    ProviderPolicy,
    Sensitivity,
)
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.providers import (
    AdapterFailure,
    ClaudeAdapter,
    GeminiAdapter,
    GrokAdapter,
    OpenAIAdapter,
)
from navox.ai.routing import PolicyRules, personal_conversation_rule
from navox.ai.runtime import GatewayUnavailable
from navox.ai.validation import OutputRejected, validate_output
from navox.db.ai_registry import AITaskRun
from navox.db.student import StudentCourse
from navox.student_agent.contracts import Observation, SelectorOutput, canonical, tool_manifest
from navox.student_agent.selector import (
    SELECTOR_CAPABILITIES,
    SELECTOR_INSTRUCTIONS,
    SELECTOR_PROMPT,
    SELECTOR_SCHEMA,
    select_next,
    selector_artifacts,
    selector_task,
    validate_selection,
)
from navox.student_agent.selector_corpus import CORPUS, SelectorCase, meets_expectation
from navox.student_agent.selector_evaluation import case_context

MODEL = "offline-student-selector"
env = selector_fixtures.env


def definition(provider: Provider = Provider.OPENAI, *, opted_in: bool = True) -> ModelDefinition:
    return ModelDefinition(
        reference=ModelRef(provider=provider, model=MODEL),
        capabilities=SELECTOR_CAPABILITIES
        if opted_in
        else frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
        allowed_sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.SENSITIVE}),
        enabled=True,
        context_window=100_000,
        max_output_tokens=1000,
        input_cost_per_million=Decimal("0.01"),
        output_cost_per_million=Decimal("0.01"),
    )


def wire_output(value: dict) -> dict:
    """Strict wire grammar requires known optional args as null, never new keys."""
    result = json.loads(json.dumps(value))
    selection = result["selection"]
    if selection.get("kind") == "tool":
        tool = next(row for row in tool_manifest()["tools"] if row["name"] == selection["name"])
        selection["args"] = {
            key: selection["args"].get(key) for key in tool["input_schema"]["properties"]
        }
    return result


def response(output: dict) -> dict:
    return {
        "model": MODEL,
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": canonical(output)}]}
        ],
        "usage": {"input_tokens": 100, "output_tokens": 30},
    }


def provider_request(case: SelectorCase) -> ProviderRequest:
    _, schema = selector_artifacts()
    return ProviderRequest(
        task_id=uuid4(),
        model=MODEL,
        instructions=SELECTOR_INSTRUCTIONS,
        context=case_context(case),
        output_schema=schema.document,
        schema_ref=SELECTOR_SCHEMA,
        prompt_ref=SELECTOR_PROMPT,
        max_output_tokens=800,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CORPUS, ids=lambda row: row.id)
async def test_real_openai_transport_carries_closed_host_proposals(case):
    seen = []

    def send(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json=response(wire_output(case.expected.model_dump(mode="json")))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        adapter = OpenAIAdapter(
            api_key=SecretStr("synthetic-key"), models=(definition(),), client=client
        )
        result = await adapter.execute(provider_request(case))
    wire = seen[0]
    assert wire["store"] is False and wire["max_output_tokens"] == 800
    assert set(wire) == {"model", "instructions", "input", "store", "max_output_tokens", "text"}
    assert wire["text"]["format"]["type"] == "json_schema"
    assert wire["text"]["format"]["strict"] is True
    assert wire["text"]["format"]["schema"]["additionalProperties"] is False
    assert wire["instructions"] == SELECTOR_INSTRUCTIONS
    assert json.loads(wire["input"]) == json.loads(case_context(case).text)
    _, schema = selector_artifacts()

    def semantic(value):
        validate_selection(SelectorOutput.model_validate(value), case.request())

    validate_output(result.output, schema.document, semantic)
    output = SelectorOutput.model_validate_json(result.output.text)
    assert meets_expectation(output, case)
    if output.selection.kind == "tool":
        assert all(value is not None for value in output.selection.args.values())


def test_only_explicit_openai_model_metadata_opts_in_and_other_adapters_stay_unchanged():
    common = {Capability.TEXT, Capability.STRUCTURED_OUTPUT}
    for cls in (GrokAdapter, GeminiAdapter, ClaudeAdapter):
        adapter = cls(api_key=SecretStr("synthetic"), models=(definition(cls.provider),))
        assert adapter.capabilities(MODEL) == common
    adapter = OpenAIAdapter(api_key=SecretStr("synthetic"), models=(definition(),))
    assert adapter.capabilities(MODEL) == SELECTOR_CAPABILITIES
    assert adapter.capabilities("unknown") == frozenset()
    ordinary = OpenAIAdapter(api_key=SecretStr("synthetic"), models=(definition(opted_in=False),))
    assert ordinary.capabilities(MODEL) == common
    policy = ProviderPolicy(user_id=uuid4(), workspace_id=uuid4(), revision=1)
    ordinary_task = conversation_task(
        user_id=policy.user_id,
        workspace_id=policy.workspace_id,
        policy=policy,
        max_cost=Decimal(".01"),
    )
    assert ordinary_task.capability_requirements == common
    student_task = selector_task(
        user_id=policy.user_id,
        workspace_id=policy.workspace_id,
        policy=policy,
        max_cost=Decimal(".005"),
    )
    operator = PolicyRules(
        grants=(
            ProviderGrant(
                provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PERSONAL})
            ),
        ),
        task_scopes=(),
        allow_personal_conversation=True,
    )
    assert (
        personal_conversation_rule(ordinary_task, operator, owns_personal_workspace=True)
        is not None
    )
    assert personal_conversation_rule(student_task, operator, owns_personal_workspace=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [
        (
            {
                "model": MODEL,
                "status": "completed",
                "output": [{"type": "function_call", "name": "clock.read", "arguments": "{}"}],
            },
            ErrorCode.INVALID_RESPONSE,
        ),
        (
            {
                "model": MODEL,
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "refusal", "refusal": "Declined"}]}
                ],
            },
            ErrorCode.REFUSAL,
        ),
        (
            {
                "model": MODEL,
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            ErrorCode.OUTPUT_LIMIT,
        ),
    ],
)
async def test_native_calls_refusals_and_incomplete_output_are_not_proposals(body, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        adapter = OpenAIAdapter(
            api_key=SecretStr("synthetic"), models=(definition(),), client=client
        )
        with pytest.raises(AdapterFailure) as failed:
            await adapter.execute(provider_request(CORPUS[0]))
        assert failed.value.detail.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "tool", "name": "email.send", "args": {}},
        {"kind": "tool", "name": "clock.read", "args": {"approved": True}},
        {"kind": "tool", "name": "clock.read", "args": {}, "destination": "external"},
        {"kind": "finish", "evidence_call_ids": ["11111111-1111-4111-8111-111111111111"]},
    ],
)
async def test_transport_success_still_requires_host_schema_and_evidence_validation(selection):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response({"selection": selection}))
        )
    ) as client:
        adapter = OpenAIAdapter(
            api_key=SecretStr("synthetic"), models=(definition(),), client=client
        )
        result = await adapter.execute(provider_request(CORPUS[0]))
    _, schema = selector_artifacts()
    with pytest.raises((OutputRejected, ValueError)):
        validate_output(
            result.output,
            schema.document,
            lambda value: validate_selection(
                SelectorOutput.model_validate(value), CORPUS[0].request()
            ),
        )


@pytest.mark.asyncio
async def test_real_adapter_selector_follows_fresh_owned_course_read_to_finish(env):
    runtime, _ = await install_runtime(env)
    course_id = uuid4()
    async with env.factory() as database:
        database.add(
            StudentCourse(
                id=course_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code="CS 2110",
                name="Programming",
                term_start=date(2026, 8, 20),
                term_end=date(2026, 12, 20),
                archived=False,
                revision=1,
            )
        )
        await database.commit()
    seen = []

    def send(wire):
        packet = json.loads(json.loads(wire.content)["input"])
        seen.append(packet)
        selection = (
            {
                "kind": "tool",
                "name": "course.info",
                "args": {"course_id": str(course_id), "code": None},
            }
            if not packet["observations"]
            else {"kind": "finish", "evidence_call_ids": [packet["observations"][0]["call_id"]]}
        )
        return httpx.Response(200, json=response({"selection": selection}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        runtime.adapters[Provider.OPENAI] = OpenAIAdapter(
            api_key=SecretStr("synthetic"), models=(definition(),), client=client
        )
        request = request_for(env, question="Show my saved course details.")
        first = await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request,
        )
        assert first.selection.kind == "tool" and first.selection.args == {
            "course_id": str(course_id)
        }
        saved = (await env.client.get("/api/v1/student/courses")).json()["courses"][0]
        fields = ("id", "code", "name", "professor", "term_start", "term_end")
        observation = Observation(
            call_id=uuid4(),
            name="course.info",
            effect="read",
            data={"courses": [{key: saved[key] for key in fields}], "truncated": False},
        )
        second = await select_next(
            runtime,
            env.settings,
            user_id=env.user_id,
            workspace_id=env.workspace_id,
            request=request.model_copy(update={"step": 2, "observations": (observation,)}),
        )
        assert second.selection.kind == "finish" and second.selection.evidence_call_ids == (
            observation.call_id,
        )
    assert len(seen) == 2 and not first.actions_executed and not second.actions_executed
    assert "files" not in seen[1] and "recent_turns" not in seen[1]
    async with env.factory() as database:
        runs = list(await database.scalars(select(AITaskRun)))
        assert len(runs) == 2 and all(
            row.status == "COMPLETED" and row.error_code is None for row in runs
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["unqualified", "no_sensitive_grant"])
async def test_capability_support_does_not_bypass_qualification_or_sensitive_grant(env, boundary):
    runtime, _ = await install_runtime(
        env, qualified=boundary != "unqualified", grant=boundary != "no_sensitive_grant"
    )
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda wire: (
                seen.append(wire)
                or httpx.Response(
                    200,
                    json=response(
                        {"selection": {"kind": "tool", "name": "clock.read", "args": {}}}
                    ),
                )
            )
        )
    ) as client:
        runtime.adapters[Provider.OPENAI] = OpenAIAdapter(
            api_key=SecretStr("synthetic"), models=(definition(),), client=client
        )
        with pytest.raises((GatewayUnavailable, PermissionError, ValueError)):
            await select_next(
                runtime,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                request=request_for(env),
            )
    assert seen == []


@pytest.mark.asyncio
async def test_real_adapter_cannot_return_course_observation_changed_during_selection(env):
    runtime, _ = await install_runtime(env)
    course_id = uuid4()
    record = {
        "id": str(course_id),
        "code": "CS 2110",
        "name": "Programming",
        "professor": None,
        "term_start": "2026-08-20",
        "term_end": "2026-12-20",
    }
    async with env.factory() as database:
        database.add(
            StudentCourse(
                id=course_id,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                code=record["code"],
                name=record["name"],
                term_start=date(2026, 8, 20),
                term_end=date(2026, 12, 20),
                archived=False,
                revision=1,
            )
        )
        await database.commit()
    observation = Observation(
        call_id=uuid4(),
        name="course.info",
        effect="read",
        data={"courses": [record], "truncated": False},
    )
    seen = []

    async def send(wire):
        seen.append(wire)
        async with env.factory() as database:
            course = await database.get(StudentCourse, course_id)
            course.name = "Changed during generation"
            course.revision += 1
            await database.commit()
        return httpx.Response(
            200,
            json=response(
                {
                    "selection": {
                        "kind": "finish",
                        "evidence_call_ids": [str(observation.call_id)],
                    }
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        runtime.adapters[Provider.OPENAI] = OpenAIAdapter(
            api_key=SecretStr("synthetic"), models=(definition(),), client=client
        )
        with pytest.raises(GatewayUnavailable):
            await select_next(
                runtime,
                env.settings,
                user_id=env.user_id,
                workspace_id=env.workspace_id,
                request=request_for(env, step=2, observations=(observation,)),
            )
    assert len(seen) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
        assert run.status == "FAILED"
