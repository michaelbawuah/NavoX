"""Ordinary conversation stays session-bound, qualified and unable to execute."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from test_ai_evaluation import model_for
from test_ai_intent_plan import intent_env as intent_env
from test_ai_runtime import FakeAdapter

from navox.ai.catalog import catalog_template
from navox.ai.control import record_evaluation
from navox.ai.conversation import (
    CONVERSATION_PROMPT,
    CONVERSATION_SCHEMA,
    ConversationAnswer,
    ConversationRequest,
)
from navox.ai.foundation.adapter import ErrorCode
from navox.ai.foundation.contracts import (
    FinishReason,
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
    Usage,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.providers import AdapterFailure, OpenAIAdapter
from navox.ai.routing import EvaluationEvidence, PolicyRules, RoutingTaskScope
from navox.ai.runtime import GatewayRuntime
from navox.ai.store import GatewayStore
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.communications import AssistantSession, AssistantTurn


async def install_conversation_runtime(env, *, failure=None, output=None):
    model = model_for(Provider.OPENAI).model_copy(
        update={
            "enabled": True,
            "allowed_sensitivities": frozenset({Sensitivity.PERSONAL}),
            "input_cost_per_million": Decimal("0.1"),
            "output_cost_per_million": Decimal("0.2"),
        }
    )
    template = catalog_template()
    registry = template.model_copy(
        update={
            "models": (model,),
            "profiles": tuple(
                profile.model_copy(
                    update={
                        "assignments": (model.reference,)
                        if profile.profile == Profile.ASSISTANT_INTERACTIVE
                        else ()
                    }
                )
                for profile in template.profiles
            ),
        }
    )
    async with env.factory() as database:
        await RegistryStore(database).publish(registry, expected_revision=0)
        for assignment in (await database.scalars(select(AIProfileAssignment))).all():
            assignment.rollout_percent = 100
        if failure != "qualification":
            await record_evaluation(
                database,
                model_id=model_key(model.reference),
                model_digest=digest(canonical(model)),
                registry_revision=1,
                evidence=EvaluationEvidence(
                    profile=Profile.ASSISTANT_INTERACTIVE,
                    task_type=TaskType.REASON,
                    prompt=CONVERSATION_PROMPT,
                    output_schema=CONVERSATION_SCHEMA,
                    quality=1,
                    reliability=1,
                    p95_latency_ms=10,
                    samples=10,
                    safety_passed=True,
                    evaluated_at=datetime.now(UTC),
                    corpus_version="offline-conversation-fixture",
                ),
            )
        await database.commit()
    policy = PolicyRules(
        grants=(
            ProviderGrant(
                provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PERSONAL})
            ),
        ),
        task_scopes=(
            RoutingTaskScope(
                workspace_id=env.workspace_id,
                user_id=uuid4() if failure == "policy" else env.user_id,
                task_type=TaskType.REASON,
                profile=Profile.ASSISTANT_INTERACTIVE,
                prompt=CONVERSATION_PROMPT,
                output_schema=CONVERSATION_SCHEMA,
                sensitivity=Sensitivity.PERSONAL,
            ),
        ),
        max_cost=Decimal("0.01"),
    )
    adapter = FakeAdapter(Provider.OPENAI, output=output or '{"answer":"A short explanation."}')
    runtime = GatewayRuntime(GatewayStore(env.factory, policy), {Provider.OPENAI: adapter})

    async def build(_settings):
        return runtime

    env.monkeypatch.setattr(env.module, "build_runtime", build)
    return adapter


async def new_conversation(env):
    response = await env.client.post("/api/v1/ai/sessions")
    assert response.status_code == 201
    return response.json()["id"]


def test_conversation_artifact_is_new_and_has_only_an_answer():
    registry = catalog_template()
    prompt = next(item for item in registry.prompts if item.reference == CONVERSATION_PROMPT)
    schema = next(item for item in registry.schemas if item.reference == CONVERSATION_SCHEMA)
    assert prompt.output_schema == schema.reference
    document = json.loads(schema.document.text)
    assert document["additionalProperties"] is False
    assert set(document["properties"]) == {"answer"}
    assert document["properties"]["answer"]["maxLength"] == 3000
    previous = next(
        item
        for item in registry.prompts
        if item.reference.name == "assistant" and item.reference.version == "v3"
    )
    assert "Do not return invented values or free-form answers" in previous.instructions
    assert "no tools or sources" in prompt.instructions


@pytest.mark.parametrize(
    "change",
    [
        {"utterance": " "},
        {"utterance": "x" * 501},
        {"provider": "xai"},
        {"action_grant": True},
        {"recent_turns": [{"task_id": str(uuid4()), "question": "Q", "answer": "A"}] * 5},
    ],
)
def test_conversation_request_rejects_unbounded_or_authoritative_fields(change):
    with pytest.raises(ValidationError):
        ConversationRequest.model_validate(
            {"session_id": str(uuid4()), "utterance": "Hello", **change}
        )


@pytest.mark.parametrize("output", [{"answer": "A", "action": "send"}, {"answer": "x" * 3001}])
def test_answer_contract_cannot_expand_into_an_action(output):
    with pytest.raises(ValidationError):
        ConversationAnswer.model_validate(output)


@pytest.mark.asyncio
async def test_conversation_records_an_answer_and_reuses_exact_same_session_context(intent_env):
    env = intent_env
    adapter = await install_conversation_runtime(env)
    session_id = await new_conversation(env)
    question = "Explain revenue and profit."
    first = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": question},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["actions_executed"] is False
    assert body["session_id"] == session_id and body["turn_sequence"] == 1
    assert first.headers["cache-control"] == "no-store"
    second = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={
            "session_id": session_id,
            "utterance": "Make that shorter.",
            "recent_turns": [
                {"task_id": body["task_id"], "question": question, "answer": body["answer"]}
            ],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["turn_sequence"] == 2
    context = json.loads(adapter.calls[1].context.text)
    assert context["recent_conversation"] == [{"question": question, "answer": body["answer"]}]
    assert set(context) == {"schema_version", "question", "recent_conversation"}
    async with env.factory() as database:
        runs = (await database.scalars(select(AITaskRun))).all()
        assert len(runs) == 2
        assert all(run.prompt == "assistant_conversation@v1" for run in runs)
        assert all(run.status == "COMPLETED" and not run.shadow for run in runs)
        turns = (await database.scalars(select(AssistantTurn))).all()
        assert len(turns) == 2 and all(turn.action_refs == [] for turn in turns)
        assert all(body["answer"] not in turn.output_reference for turn in turns)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["qualification", "policy"])
async def test_missing_exact_qualification_or_principal_scope_never_calls_a_provider(
    intent_env, failure
):
    env = intent_env
    adapter = await install_conversation_runtime(env, failure=failure)
    session_id = await new_conversation(env)
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "How are you?"},
    )
    assert response.status_code == 503
    assert adapter.calls == []
    async with env.factory() as database:
        assert await database.scalar(select(AssistantTurn)) is None


@pytest.mark.asyncio
async def test_credential_input_is_rejected_before_provider_egress(intent_env):
    env = intent_env
    adapter = await install_conversation_runtime(env)
    session_id = await new_conversation(env)
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "api_key=abcdef1234567890"},
    )
    assert response.status_code == 403 and adapter.calls == []
    assert "abcdef1234567890" not in response.text


@pytest.mark.asyncio
async def test_conversation_requires_authentication_and_a_native_session(intent_env):
    env = intent_env
    adapter = await install_conversation_runtime(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.post(
            "/api/v1/ai/assistant/conversation",
            json={"session_id": str(uuid4()), "utterance": "Hello."},
        )
    assert response.status_code == 401
    missing_session = await env.client.post(
        "/api/v1/ai/assistant/conversation", json={"utterance": "Hello."}
    )
    assert missing_session.status_code == 422 and adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["user", "workspace", "closed"])
async def test_foreign_or_closed_session_cannot_supply_context(intent_env, change):
    env = intent_env
    adapter = await install_conversation_runtime(env)
    session_id = await new_conversation(env)
    from navox.db.models import User, Workspace

    async with env.factory() as database:
        session = await database.get(AssistantSession, UUID(session_id))
        if change == "user":
            other = User(id=uuid4(), email="other-conversation@example.com")
            database.add(other)
            await database.flush()
            session.user_id = other.id
        elif change == "workspace":
            other = Workspace(id=uuid4(), name="Other workspace")
            database.add(other)
            await database.flush()
            session.workspace_id = other.id
        else:
            session.status = "closed"
        await database.commit()
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "Explain a concept."},
    )
    assert response.status_code == 403 and adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["question", "answer", "task", "session", "artifact"])
async def test_history_requires_exact_text_and_completed_ordinary_session_provenance(
    intent_env, change
):
    env = intent_env
    adapter = await install_conversation_runtime(env)
    session_id = await new_conversation(env)
    question = "Explain profit."
    first = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": question},
    )
    assert first.status_code == 200
    body = first.json()
    history = {"task_id": body["task_id"], "question": question, "answer": body["answer"]}
    if change in {"question", "answer"}:
        history[change] = "Replacement private source text."
    elif change == "task":
        history["task_id"] = str(uuid4())
    elif change == "session":
        session_id = await new_conversation(env)
    else:
        async with env.factory() as database:
            run = await database.scalar(select(AITaskRun))
            run.prompt = "communication_draft@v2"
            await database.commit()
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "Shorter.", "recent_turns": [history]},
    )
    assert response.status_code == 403
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_model_action_fields_fail_validation_and_never_append_a_turn(intent_env):
    env = intent_env
    adapter = await install_conversation_runtime(
        env, output='{"answer":"Sent.","action_grant":{"send":true}}'
    )
    session_id = await new_conversation(env)
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "Send this."},
    )
    assert response.status_code == 503 and len(adapter.calls) == 1
    async with env.factory() as database:
        assert await database.scalar(select(AssistantTurn)) is None


@pytest.mark.asyncio
async def test_session_revoked_during_answer_is_rechecked_before_return(intent_env):
    env = intent_env
    adapter = await install_conversation_runtime(env)
    session_id = await new_conversation(env)

    async def close_session():
        async with env.factory() as database:
            session = await database.get(AssistantSession, UUID(session_id))
            session.status = "closed"
            await database.commit()

    adapter.during = close_session
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "Hello."},
    )
    assert response.status_code == 503 and len(adapter.calls) == 1
    async with env.factory() as database:
        assert await database.scalar(select(AssistantTurn)) is None


@pytest.mark.parametrize(
    "reason,expected",
    [("max_output_tokens", ErrorCode.OUTPUT_LIMIT), ("content_filter", ErrorCode.INVALID_RESPONSE)],
)
def test_openai_incomplete_output_has_only_a_fixed_limit_classification(reason, expected):
    adapter = OpenAIAdapter(api_key=SecretStr("offline-fixture"), models=())
    with pytest.raises(AdapterFailure) as caught:
        adapter.normalize_response(
            {
                "status": "incomplete",
                "incomplete_details": {"reason": reason, "private": "private-output-text"},
                "output": [{"private": "private-output-text"}],
            }
        )
    assert caught.value.detail.code == expected
    assert "private-output-text" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["characters", "usage", "finish", "incomplete"])
async def test_output_limit_is_explicit_without_truncation_retry_or_saved_answer(intent_env, limit):
    env = intent_env
    adapter = await install_conversation_runtime(
        env, output=json.dumps({"answer": "x" * 3001}) if limit == "characters" else None
    )
    execute = adapter.execute

    async def limited(request):
        result = await execute(request)
        if limit == "incomplete":
            raise AdapterFailure(ErrorCode.OUTPUT_LIMIT)
        if limit == "usage":
            return result.model_copy(update={"usage": Usage(input_tokens=10, output_tokens=1001)})
        if limit == "finish":
            return result.model_copy(update={"finish_reason": FinishReason.LENGTH})
        return result

    adapter.execute = limited
    session_id = await new_conversation(env)
    response = await env.client.post(
        "/api/v1/ai/assistant/conversation",
        json={"session_id": session_id, "utterance": "Explain a concept."},
    )
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "detail": {
            "code": "conversation_output_limit",
            "message": "The answer reached its length limit. Please ask for a shorter answer.",
        }
    }
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        assert await database.scalar(select(AssistantTurn)) is None
        run = await database.scalar(select(AITaskRun))
        assert run.status == "FAILED" and run.error_code == "output_limit"
        assert "answer" not in run.usage
        if limit == "usage":
            assert run.usage["output_tokens"] == 1001
            assert run.estimated_cost == Decimal("0.0002012")
