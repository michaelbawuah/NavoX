"""Real registry/grants are read before RTC admission; no provider is contacted."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select
from test_ai_speech_routing import make_registry as speech_registry
from test_ai_speech_routing import synthesis_model, transcription_model
from test_vox_calls import ROOT, start
from test_vox_calls import vox_env as shared_vox_fixture
from test_vox_provider import FakeProvider

from navox.ai import configured
from navox.ai.catalog import catalog_template
from navox.ai.control import record_evaluation
from navox.ai.conversation import CONVERSATION_PROMPT, CONVERSATION_SCHEMA
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
from navox.ai.routing import EvaluationEvidence, PolicyRules, RoutingTaskScope
from navox.ai.runtime import GatewayRuntime
from navox.ai.speech_adapters import OpenAISpeechAdapter
from navox.ai.store import GatewayStore
from navox.api.vox import media_provider
from navox.db.ai_registry import (
    AIEvaluationRun,
    AIModel,
    AIProfileAssignment,
    AIProviderHealth,
    AIRoutingPolicy,
    AITaskRun,
)
from navox.db.communications import AssistantSession
from navox.db.vox_media import VoxMediaConnection
from navox.db.vox_provision import VoxMediaProvisionAttempt
from navox.vox import readiness

vox_env = shared_vox_fixture
PROFILES = (Profile.SPEECH_TRANSCRIPTION, Profile.ASSISTANT_INTERACTIVE, Profile.SPEECH_SYNTHESIS)


@pytest_asyncio.fixture
async def ready_voice(vox_env, monkeypatch):
    env = vox_env
    models = (
        transcription_model(),
        ModelDefinition(
            reference=ModelRef(provider=Provider.OPENAI, model="fixture-voice-reason-not-live"),
            capabilities=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
            allowed_sensitivities=frozenset({Sensitivity.PERSONAL}),
            enabled=True,
            context_window=100_000,
            max_output_tokens=4000,
            input_cost_per_million=Decimal("0.01"),
            output_cost_per_million=Decimal("0.01"),
        ),
        synthesis_model(),
    )
    references = dict(zip(PROFILES, (model.reference for model in models), strict=True))
    template = catalog_template()
    speech = speech_registry()
    registry = template.model_copy(
        update={
            "models": models,
            "profiles": tuple(
                profile.model_copy(
                    update={
                        "assignments": (references[profile.profile],)
                        if profile.profile in references
                        else (),
                    }
                )
                for profile in (*template.profiles, *speech.profiles)
            ),
            "prompts": (*template.prompts, *speech.prompts),
            "schemas": (*template.schemas, *speech.schemas),
        }
    )
    bindings = (
        (
            Profile.SPEECH_TRANSCRIPTION,
            TaskType.TRANSCRIBE,
            Sensitivity.SENSITIVE,
            "speech_transcription_prompt",
            "speech_transcription",
        ),
        (
            Profile.ASSISTANT_INTERACTIVE,
            TaskType.REASON,
            Sensitivity.PERSONAL,
            "vox_spoken_answer",
            "vox_spoken_answer",
        ),
        (
            Profile.SPEECH_SYNTHESIS,
            TaskType.SYNTHESIZE,
            Sensitivity.SENSITIVE,
            "speech_synthesis_prompt",
            "speech_synthesis",
        ),
    )
    from navox.ai.foundation.contracts import VersionedRef

    scopes = tuple(
        RoutingTaskScope(
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            profile=profile,
            task_type=kind,
            sensitivity=sensitivity,
            prompt=VersionedRef(name=prompt, version="v1"),
            output_schema=VersionedRef(name=schema, version="v1"),
        )
        for profile, kind, sensitivity, prompt, schema in bindings
    )
    policy = PolicyRules(
        grants=(
            ProviderGrant(
                provider=Provider.OPENAI,
                sensitivities=frozenset({Sensitivity.PERSONAL, Sensitivity.SENSITIVE}),
            ),
        ),
        task_scopes=scopes,
        max_cost=Decimal("1"),
    )
    async with env.factory() as database:
        await RegistryStore(database).publish(registry, expected_revision=0)
        for assignment in await database.scalars(select(AIProfileAssignment)):
            assignment.rollout_percent = 100
            assignment.shadow_enabled = False
        for model, (profile, kind, _sensitivity, prompt, schema) in zip(
            models, bindings, strict=True
        ):
            await record_evaluation(
                database,
                model_id=model_key(model.reference),
                model_digest=digest(canonical(model)),
                registry_revision=1,
                evidence=EvaluationEvidence(
                    profile=profile,
                    task_type=kind,
                    prompt=VersionedRef(name=prompt, version="v1"),
                    output_schema=VersionedRef(name=schema, version="v1"),
                    quality=1,
                    reliability=1,
                    p95_latency_ms=100,
                    samples=10,
                    safety_passed=True,
                    evaluated_at=datetime.now(UTC),
                    corpus_version="offline-voice-policy-fixture",
                ),
            )
        await database.commit()
    env.settings.ai_provider = "automatic"
    env.settings.openai_api_key = SecretStr("synthetic-voice-key-never-used")
    env.settings.ai_provider_policy = policy.model_dump(mode="json")
    env.voice_policy, env.voice_models = policy, dict(zip(PROFILES, models, strict=True))
    env.media_provider = FakeProvider()
    env.app.dependency_overrides[media_provider] = lambda: env.media_provider

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("A readiness read attempted execution, quota, trace or network I/O")

    def no_session_entitlement(*_args, **_kwargs):
        raise AssertionError("Off-record voice must not use retained-session entitlement")

    for name in ("claim", "trace", "admit_transcription", "admit_synthesis"):
        monkeypatch.setattr(GatewayStore, name, forbidden)
    monkeypatch.setattr(GatewayStore, "for_conversation", no_session_entitlement)
    monkeypatch.setattr(GatewayRuntime, "execute", forbidden)
    monkeypatch.setattr(OpenAISpeechAdapter, "transcribe", forbidden)
    monkeypatch.setattr(OpenAISpeechAdapter, "synthesize", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    return env


async def assert_no_model_or_media_writes(env):
    async with env.factory() as database:
        for model in (AITaskRun, AssistantSession, VoxMediaConnection, VoxMediaProvisionAttempt):
            assert await database.scalar(select(func.count()).select_from(model)) == 0
        for health in await database.scalars(select(AIProviderHealth)):
            assert health.probe_until is None
    assert env.media_provider.intents == []


@pytest.mark.asyncio
async def test_qualified_ordinary_grants_admit_without_writes_or_session_entitlement(
    ready_voice,
):
    env = ready_voice
    verbs = []

    def statement(_connection, _cursor, sql, _parameters, _context, _many):
        verbs.append(sql.lstrip().split()[0].upper())

    engine = env.factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", statement)
    try:
        await readiness.require_voice_models(
            env.settings, env.factory, workspace_id=env.workspace_id, user_id=env.user_id
        )
    finally:
        event.remove(engine, "before_cursor_execute", statement)
    assert verbs and set(verbs) == {"SELECT"}
    await assert_no_model_or_media_writes(env)
    row, _ = await start(env)
    response = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    assert len(env.media_provider.intents) == 1
    async with env.factory() as database:
        assert await database.scalar(select(func.count()).select_from(AITaskRun)) == 0
        assert await database.scalar(select(func.count()).select_from(AssistantSession)) == 0
    # A subsequent provider-policy shutdown must never stop the owner retiring
    # this admitted epoch through the normal private close endpoint.
    env.settings.ai_provider = "disabled"
    closed = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/media/{response.json()['connection_id']}/close",
        json={"generation": row["generation"]},
    )
    assert closed.status_code == 204


class TranscriptionOnly:
    provider = Provider.OPENAI

    def capabilities(self, _model):
        return frozenset({Capability.TRANSCRIPTION})

    async def transcribe(self, **_kwargs):
        raise AssertionError("Readiness cannot transcribe")

    def classify_error(self, _error):
        raise AssertionError("Readiness cannot call a provider")


class SynthesisOnly:
    provider = Provider.OPENAI

    def capabilities(self, _model):
        return frozenset()

    async def synthesize(self, **_kwargs):
        raise AssertionError("Readiness cannot synthesize")

    def classify_error(self, _error):
        raise AssertionError("Readiness cannot call a provider")


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("failure", ["grant", "qualification", "disabled", "adapter"])
async def test_any_denied_voice_stage_fails_before_intent_room_token_or_usage(
    ready_voice, monkeypatch, profile, failure
):
    env = ready_voice
    key = model_key(env.voice_models[profile].reference)
    if failure == "grant":
        allowed = tuple(scope for scope in env.voice_policy.task_scopes if scope.profile != profile)
        env.settings.ai_provider_policy = env.voice_policy.model_copy(
            update={"task_scopes": allowed}
        ).model_dump(mode="json")
    elif failure == "adapter":
        if profile == Profile.ASSISTANT_INTERACTIVE:
            monkeypatch.setattr(configured, "configured_adapters", lambda *_args: {})
        else:
            narrowed = (
                SynthesisOnly() if profile == Profile.SPEECH_TRANSCRIPTION else TranscriptionOnly()
            )
            monkeypatch.setattr(
                readiness,
                "configured_speech_adapters",
                lambda _settings: {Provider.OPENAI: narrowed},
            )
    else:
        async with env.factory() as database:
            if failure == "qualification":
                await database.execute(
                    delete(AIEvaluationRun).where(AIEvaluationRun.model_id == key)
                )
            else:
                (await database.get(AIModel, key)).enabled = False
            await database.commit()
    row, _ = await start(env)
    response = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert key not in response.text and "synthetic-voice-key" not in response.text
    await assert_no_model_or_media_writes(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "gateway-off",
        "no-key",
        "unhealthy",
        "partial-rollout",
        "shadow",
        "workspace-denial",
        "user-denial",
        "retained-entitlement-only",
        "budget",
    ],
)
async def test_unready_routing_never_spends_quota_or_allocates_rtc(ready_voice, failure):
    env = ready_voice
    if failure == "gateway-off":
        env.settings.ai_provider = "disabled"
    elif failure == "no-key":
        env.settings.openai_api_key = None
    elif failure == "retained-entitlement-only":
        scopes = tuple(
            scope.model_copy(update={"user_id": uuid4()})
            if scope.profile == Profile.ASSISTANT_INTERACTIVE
            else scope
            for scope in env.voice_policy.task_scopes
        )
        env.settings.ai_provider_policy = env.voice_policy.model_copy(
            update={
                "allow_personal_conversation": True,
                "task_scopes": scopes,
            }
        ).model_dump(mode="json")
    elif failure == "budget":
        env.settings.ai_provider_policy = env.voice_policy.model_copy(
            update={"max_cost": Decimal("0.00000001")}
        ).model_dump(mode="json")
    else:
        async with env.factory() as database:
            key = model_key(env.voice_models[Profile.ASSISTANT_INTERACTIVE].reference)
            if failure == "unhealthy":
                # Ordinary runtime could reserve a recovery probe; preflight may
                # not claim one or admit an RTC call on uncertain health.
                (await database.get(AIProviderHealth, key)).status = "UNAVAILABLE"
            elif failure in {"partial-rollout", "shadow"}:
                assignment = await database.get(
                    AIProfileAssignment, (Profile.ASSISTANT_INTERACTIVE.value, key)
                )
                if failure == "partial-rollout":
                    assignment.rollout_percent = 99
                else:
                    assignment.shadow_enabled = True
            else:
                database.add(
                    AIRoutingPolicy(
                        workspace_id=env.workspace_id,
                        scope_key="workspace"
                        if failure == "workspace-denial"
                        else str(env.user_id),
                        revision=1,
                        policy=PolicyRules(grants=()).model_dump_json(),
                    )
                )
            await database.commit()
    row, _ = await start(env)
    response = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 503, response.text
    await assert_no_model_or_media_writes(env)


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinary_only", ["qualification", "grant"])
async def test_ordinary_text_approval_never_qualifies_the_new_spoken_answer(
    ready_voice, ordinary_only
):
    env = ready_voice
    if ordinary_only == "grant":
        scopes = tuple(
            scope.model_copy(
                update={"prompt": CONVERSATION_PROMPT, "output_schema": CONVERSATION_SCHEMA}
            )
            if scope.profile == Profile.ASSISTANT_INTERACTIVE
            else scope
            for scope in env.voice_policy.task_scopes
        )
        env.settings.ai_provider_policy = env.voice_policy.model_copy(
            update={"task_scopes": scopes}
        ).model_dump(mode="json")
    else:
        async with env.factory() as database:
            key = model_key(env.voice_models[Profile.ASSISTANT_INTERACTIVE].reference)
            for evaluation in await database.scalars(
                select(AIEvaluationRun).where(AIEvaluationRun.model_id == key)
            ):
                evaluation.prompt = "assistant_conversation@v1"
                evaluation.schema = "assistant_conversation@v1"
            await database.commit()
    row, _ = await start(env)
    response = await env.client.post(
        f"{ROOT}/{row['client_call_id']}/media",
        json={"generation": row["generation"], "request_id": str(uuid4())},
    )
    assert response.status_code == 503, response.text
    assert response.headers["cache-control"] == "private, no-store"
    await assert_no_model_or_media_writes(env)
