"""M11D bounded speech synthesis: contract, quota, provider boundary, fail-closed route."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_ai_speech_routing import (
    SYNTHESIS_PROMPT,
    SYNTHESIS_SCHEMA,
    SYNTHESIZE_MODEL,
    make_registry,
    synthesis_model,
    synthesis_profile,
    transcription_model,
    transcription_profile,
)

from navox.ai.catalog import catalog_template
from navox.ai.control import record_evaluation
from navox.ai.foundation.adapter import ErrorCode, ProviderError
from navox.ai.foundation.contracts import (
    Capability,
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, ModelRef, RegistrySnapshot
from navox.ai.providers import HTTPAdapter
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.runtime import GatewayRuntime
from navox.ai.speech_adapters import (
    AUDIO_MPEG_CONTENT_TYPE,
    MAX_PROVIDER_SPEECH_CHARACTERS,
    MAX_SPEECH_AUDIO_BYTES,
    OPENAI_SPEECH_URL,
    OpenAISpeechAdapter,
    SpeechAdapterFailure,
    SpeechSynthesisAdapter,
)
from navox.ai.speech_service import SpeechUnavailable
from navox.ai.speech_synthesis import (
    MAX_SPEECH_CHARACTERS,
    SPEECH_VOICE,
    SYNTHESIS_HOURLY_LIMIT,
    SYNTHESIS_QUOTA_WINDOW,
    SpeechTextRejected,
    bounded_speech_text,
    synthesis_task,
    synthesize_speech,
)
from navox.ai.store import GatewayStore
from navox.api import ai_operations
from navox.api.ai_operations import SynthesisRequest
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.base import Base
from navox.db.models import User

WORKSPACE = UUID("00000000-0000-4000-8000-000000000031")
USER = UUID("00000000-0000-4000-8000-000000000032")
MP3 = b"\xff\xfb\x90\x00\x00\x00\x00\x00synthetic-mp3-fixture"


def operator_policy(*, max_cost: str = "1") -> PolicyRules:
    grant = ProviderGrant(
        provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.SENSITIVE})
    )
    return PolicyRules(grants=(grant,), max_cost=Decimal(max_cost))


def sample_evidence(**changes: object) -> EvaluationEvidence:
    values: dict[str, object] = {
        "profile": Profile.SPEECH_SYNTHESIS,
        "task_type": TaskType.SYNTHESIZE,
        "prompt": SYNTHESIS_PROMPT,
        "output_schema": SYNTHESIS_SCHEMA,
        "quality": 0.95,
        "reliability": 0.97,
        "p95_latency_ms": 900,
        "samples": 25,
        "safety_passed": True,
        "evaluated_at": datetime.now(UTC),
        "corpus_version": "m11d-fixture-corpus",
    }
    values.update(changes)
    return EvaluationEvidence.model_validate(values)


class FakeSynthesisAdapter:
    """Controlled provider double: it can never make a network request."""

    provider = Provider.OPENAI

    def __init__(self, *, audio: bytes = MP3, during=None, fail=None) -> None:
        self.audio, self.during, self.fail = audio, during, fail
        self.calls: list[tuple[str, str, str, float]] = []

    async def synthesize(
        self, *, model: str, text: str, voice: str, timeout_seconds: float
    ) -> bytes:
        self.calls.append((model, text, voice, timeout_seconds))
        if self.during is not None:
            await self.during()
        if self.fail is not None:
            raise self.fail
        return self.audio

    def classify_error(self, error: Exception) -> ProviderError:
        return HTTPAdapter.classify_error(self, error)  # type: ignore[arg-type]


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def speak(
    adapter: OpenAISpeechAdapter,
    *,
    model: str = "fixture-model",
    text: str = "hello",
    voice: str = "alloy",
    timeout_seconds: float = 5,
) -> bytes:
    """One adapter call with the default bounded inputs, for terse assertions."""

    return await adapter.synthesize(
        model=model, text=text, voice=voice, timeout_seconds=timeout_seconds
    )


# --------------------------------------------------------------------------------------
# Contract helpers: speech bound, fixed task, protocol shape
# --------------------------------------------------------------------------------------


def test_synthesis_contract_constants_are_fixed() -> None:
    assert MAX_SPEECH_CHARACTERS == 600
    assert MAX_PROVIDER_SPEECH_CHARACTERS == 4_096
    assert MAX_SPEECH_AUDIO_BYTES == 2 * 1024 * 1024
    assert SYNTHESIS_HOURLY_LIMIT == 20
    assert SYNTHESIS_QUOTA_WINDOW == timedelta(hours=1)
    assert SPEECH_VOICE == "alloy"
    assert SynthesisRequest.model_fields.keys() == {"text"}


def test_speech_text_is_trimmed_and_bounded() -> None:
    assert bounded_speech_text("  Your class is at 3 PM.  ") == "Your class is at 3 PM."
    assert bounded_speech_text("x" * MAX_SPEECH_CHARACTERS) == "x" * MAX_SPEECH_CHARACTERS
    for invalid in ("", "   ", "\n\t", "x" * (MAX_SPEECH_CHARACTERS + 1), None, 12):
        with pytest.raises(SpeechTextRejected):
            bounded_speech_text(invalid)


def test_synthesis_task_is_fixed_and_grants_nothing_extra() -> None:
    ceiling = operator_policy()
    task = synthesis_task(workspace_id=WORKSPACE, user_id=USER, ceiling=ceiling)
    assert task.task_type is TaskType.SYNTHESIZE
    assert task.profile is Profile.SPEECH_SYNTHESIS
    assert task.capability_requirements == frozenset({Capability.SPEECH_SYNTHESIS})
    assert (task.prompt.name, task.prompt.version) == ("speech_synthesis_prompt", "v1")
    assert (task.output_schema.name, task.output_schema.version) == ("speech_synthesis", "v1")
    assert task.sensitivity is Sensitivity.SENSITIVE
    assert task.context_references == ()
    assert task.max_cost == ceiling.max_cost
    assert task.provider_policy.allow_fallback is False
    assert task.provider_policy.max_fallbacks == 0
    assert task.provider_policy.grants == ceiling.grants


def test_openai_adapter_still_reports_transcription_only_capabilities() -> None:
    """M11D must not widen the M11B transcription selection gate."""

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"))
    assert adapter.capabilities("fixture-transcribe-not-live") == frozenset(
        {Capability.TRANSCRIPTION}
    )
    assert adapter.capabilities("../../evil") == frozenset()
    assert isinstance(adapter, SpeechSynthesisAdapter)


# --------------------------------------------------------------------------------------
# OpenAI speech adapter: fixed destination, JSON payload, bounded MP3
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_adapter_sends_the_selected_model_voice_and_text() -> None:
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = await request.aread()
        return httpx.Response(200, content=MP3, headers={"content-type": AUDIO_MPEG_CONTENT_TYPE})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    audio = await adapter.synthesize(
        model="fixture-synthesize-not-live",
        text="Your class is at 3 PM.",
        voice="alloy",
        timeout_seconds=10,
    )
    assert audio == MP3
    assert seen["url"] == OPENAI_SPEECH_URL
    assert seen["authorization"] == "Bearer test-key"
    assert seen["content_type"] == "application/json"
    body = json.loads(seen["body"])
    assert body == {
        "model": "fixture-synthesize-not-live",
        "input": "Your class is at 3 PM.",
        "voice": "alloy",
        "response_format": "mp3",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "../../evil", "text": "hello", "voice": "alloy"},
        {"model": "fixture-model", "text": "   ", "voice": "alloy"},
        {
            "model": "fixture-model",
            "text": "x" * (MAX_PROVIDER_SPEECH_CHARACTERS + 1),
            "voice": "alloy",
        },
        {"model": "fixture-model", "text": "hello", "voice": "../evil"},
    ],
)
async def test_openai_adapter_rejects_bad_input_before_any_request(kwargs: dict[str, str]) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=MP3, headers={"content-type": AUDIO_MPEG_CONTENT_TYPE})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await adapter.synthesize(timeout_seconds=5, **kwargs)
    assert info.value.detail.code is ErrorCode.INVALID_REQUEST
    assert requests == []


@pytest.mark.asyncio
async def test_openai_adapter_does_not_follow_redirects() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.invalid/collect"})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await speak(adapter)
    assert info.value.detail.code is ErrorCode.UNAVAILABLE
    assert seen == [OPENAI_SPEECH_URL]


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, ErrorCode.AUTHENTICATION),
        (429, ErrorCode.RATE_LIMIT),
        (400, ErrorCode.INVALID_REQUEST),
        (500, ErrorCode.UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_openai_adapter_maps_http_status_without_a_response_body(
    status: int, expected: ErrorCode
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="upstream secret body")

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await speak(adapter)
    assert info.value.detail.code is expected
    assert "secret" not in str(info.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"audio": "not audio"}),
        httpx.Response(200, content=b"", headers={"content-type": AUDIO_MPEG_CONTENT_TYPE}),
        httpx.Response(
            200,
            content=b"\x00" * (MAX_SPEECH_AUDIO_BYTES + 1),
            headers={"content-type": AUDIO_MPEG_CONTENT_TYPE},
        ),
    ],
)
@pytest.mark.asyncio
async def test_openai_adapter_rejects_unusable_audio_responses(response: httpx.Response) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return response

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await speak(adapter)
    assert info.value.detail.code is ErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_openai_adapter_caps_a_stream_without_a_declared_length() -> None:
    async def body():
        for _ in range(MAX_SPEECH_AUDIO_BYTES // 1024 + 2):
            yield b"\x00" * 1024

    async def handler(_request: httpx.Request) -> httpx.Response:
        headers = {"content-type": AUDIO_MPEG_CONTENT_TYPE}
        return httpx.Response(200, content=body(), headers=headers)

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await speak(adapter)
    assert info.value.detail.code is ErrorCode.INVALID_RESPONSE


# --------------------------------------------------------------------------------------
# Quota admission: rolling window under the user row lock
# --------------------------------------------------------------------------------------


class _SessionContext:
    def __init__(self, session: object) -> None:
        self._session = session

    async def __aenter__(self) -> object:
        return self._session

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _RecordingSession:
    def __init__(self, *, user: object, used: int, member: object | None = True) -> None:
        self._user, self._used, self._member = user, used, member
        self.statements: list[object] = []
        self.added: list[object] = []
        self.gets = 0
        self.commits = 0

    async def scalar(self, statement: object) -> object:
        self.statements.append(statement)
        return self._user if len(self.statements) == 1 else self._used

    async def get(self, _entity: object, _key: object, **_kwargs: object) -> object | None:
        self.gets += 1
        return self._member

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def commit(self) -> None:
        self.commits += 1


def _fake_store(session: _RecordingSession) -> GatewayStore:
    return GatewayStore(factory=lambda: _SessionContext(session), operator_policy=operator_policy())


@pytest.mark.asyncio
async def test_synthesis_admission_locks_the_user_row_and_inserts_one_attempt() -> None:
    session = _RecordingSession(user=SimpleNamespace(agent_paused=False), used=0)
    task = synthesis_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    run = await _fake_store(session).admit_synthesis(
        task, limit=SYNTHESIS_HOURLY_LIMIT, window=SYNTHESIS_QUOTA_WINDOW
    )
    assert isinstance(run, AITaskRun)
    assert run.status == "STARTED" and run.task_type == "synthesize"
    assert session.commits == 1 and len(session.added) == 1
    assert session.gets == 1  # membership is re-read under the same lock
    compiled = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled
    assert "count(" in str(session.statements[1]).lower()


@pytest.mark.asyncio
async def test_synthesis_admission_blocks_at_the_limit_without_inserting() -> None:
    session = _RecordingSession(user=SimpleNamespace(agent_paused=False), used=20)
    task = synthesis_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    run = await _fake_store(session).admit_synthesis(
        task, limit=SYNTHESIS_HOURLY_LIMIT, window=SYNTHESIS_QUOTA_WINDOW
    )
    assert run is None
    assert session.added == [] and session.commits == 0


@pytest.mark.asyncio
async def test_synthesis_admission_fails_closed_without_membership() -> None:
    session = _RecordingSession(user=SimpleNamespace(agent_paused=False), used=0, member=None)
    task = synthesis_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    with pytest.raises(PermissionError):
        await _fake_store(session).admit_synthesis(
            task, limit=SYNTHESIS_HOURLY_LIMIT, window=SYNTHESIS_QUOTA_WINDOW
        )
    assert session.added == [] and session.commits == 0


# --------------------------------------------------------------------------------------
# Execution: exact selection, budget, reauthorization
# --------------------------------------------------------------------------------------


class _Runtime:
    def __init__(self, store: GatewayStore) -> None:
        self.store = store


def _settings() -> SimpleNamespace:
    return SimpleNamespace(openai_read_timeout_seconds=30.0)


class _StaticStore:
    """Minimal authorize/snapshot/trace/health seam for execution tests."""

    def __init__(self, snapshot) -> None:
        self._snapshot = snapshot
        self.traces: list[AITaskRun] = []
        self.operator_policy = operator_policy()

    async def authorize(self, _task) -> None:
        return None

    async def snapshot(self, _task):
        return self._snapshot

    async def claim(self, _key: str) -> bool:
        return True

    async def health(self, _key, _error) -> None:
        return None

    async def trace(self, run: AITaskRun) -> None:
        self.traces.append(run)

    async def admit_synthesis(self, task, *, limit: int, window) -> AITaskRun:
        return AITaskRun(
            id=uuid4(),
            task_id=task.id,
            workspace_id=task.workspace_id,
            user_id=task.user_id,
            trace_id=task.trace_id,
            task_type=task.task_type.value,
            profile=task.profile.value,
            prompt=f"{task.prompt.name}@{task.prompt.version}",
            schema=f"{task.output_schema.name}@{task.output_schema.version}",
            status="STARTED",
            usage={},
        )


def _qualified_snapshot(*, extra: ModelDefinition | None = None):
    from test_ai_speech_routing import make_snapshot

    task = synthesis_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    models = [synthesis_model()]
    profiles = [synthesis_profile()]
    if extra is not None:
        models.append(extra)
    registry = make_registry(models=tuple(models), profiles=tuple(profiles))
    key = model_key(SYNTHESIZE_MODEL)
    return make_snapshot(
        task,
        registry=registry,
        evaluations={key: sample_evidence()},
        available=frozenset({key}),
    )


@pytest.mark.asyncio
async def test_execution_selects_one_model_and_reserves_character_cost() -> None:
    snapshot = _qualified_snapshot()
    store = _StaticStore(snapshot)
    adapter = FakeSynthesisAdapter()
    audio = await synthesize_speech(
        _Runtime(store),  # type: ignore[arg-type]
        _settings(),  # type: ignore[arg-type]
        workspace_id=WORKSPACE,
        user_id=USER,
        text="Your next class is ECON 3120 at 3 PM.",
        adapters={Provider.OPENAI: adapter},
    )
    assert audio == MP3
    assert len(adapter.calls) == 1
    model, text, voice, timeout = adapter.calls[0]
    assert model == SYNTHESIZE_MODEL.model
    assert text == "Your next class is ECON 3120 at 3 PM."
    assert voice == SPEECH_VOICE
    assert timeout == 30.0
    run = store.traces[-1]
    assert run.status == "COMPLETED" and run.task_type == "synthesize"
    assert run.estimated_cost == run.reserved_cost
    assert run.reserved_cost > 0
    serialized = json.dumps(
        {column.name: getattr(run, column.name) for column in AITaskRun.__table__.columns},
        default=str,
    )
    assert "ECON" not in serialized


@pytest.mark.asyncio
async def test_execution_requires_a_synthesis_capable_adapter() -> None:
    snapshot = _qualified_snapshot()
    store = _StaticStore(snapshot)

    class OnlyTranscription:
        provider = Provider.OPENAI

        def capabilities(self, _model: str) -> frozenset[Capability]:
            return frozenset({Capability.TRANSCRIPTION})

        async def transcribe(self, **_kwargs: object) -> str:
            return "hi"

        def classify_error(self, _error: Exception) -> ProviderError:
            return ProviderError(code=ErrorCode.UNKNOWN)

    with pytest.raises(SpeechUnavailable):
        await synthesize_speech(
            _Runtime(store),  # type: ignore[arg-type]
            _settings(),  # type: ignore[arg-type]
            workspace_id=WORKSPACE,
            user_id=USER,
            text="hello",
            adapters={Provider.OPENAI: OnlyTranscription()},  # type: ignore[dict-item]
        )


@pytest.mark.asyncio
async def test_execution_maps_a_synthesis_eligibility_change_to_unavailable() -> None:
    snapshot = _qualified_snapshot()

    class MovingStore(_StaticStore):
        async def snapshot(self, _task):
            self.calls += 1
            if self.calls > 1:
                raise LookupError("registry moved")
            return self._snapshot

        calls = 0

    moving = MovingStore(snapshot)
    with pytest.raises(SpeechUnavailable):
        await synthesize_speech(
            _Runtime(moving),  # type: ignore[arg-type]
            _settings(),  # type: ignore[arg-type]
            workspace_id=WORKSPACE,
            user_id=USER,
            text="hello",
            adapters={Provider.OPENAI: FakeSynthesisAdapter()},
        )


# --------------------------------------------------------------------------------------
# Route: authenticated, origin-checked, bounded, fail-closed
# --------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def synthesis_env(monkeypatch):
    from navox.api.main import create_app
    from navox.core.settings import Settings, get_settings
    from navox.db import models  # noqa: F401
    from navox.db.session import get_database_session

    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"speech_synthesis_{uuid4().hex}"
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "speech-synthesis@example.com",
                "password": "twelve-character-password",
                "display_name": "Speech owner",
            },
        )
        assert response.status_code == 201
        yield SimpleNamespace(
            app=app,
            client=client,
            factory=factory,
            settings=settings,
            user_id=UUID(response.json()["id"]),
            workspace_id=UUID(response.json()["workspace"]["id"]),
            monkeypatch=monkeypatch,
            module=ai_operations,
            operator_policy=operator_policy(),
        )
    await engine.dispose()
    if admin:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def synthesis_registry(*, revision: int = 1, models=None, profiles=None) -> RegistrySnapshot:
    changes: dict[str, object] = {"revision": revision}
    if models is not None:
        changes["models"] = models
    if profiles is not None:
        changes["profiles"] = profiles
    return make_registry(**changes)


async def install_synthesis(env, *, adapter=None, registry: RegistrySnapshot | None = None):
    """Publish the operator catalog and inject a controlled synthesis adapter."""

    adapter = adapter if adapter is not None else FakeSynthesisAdapter()
    catalog = registry if registry is not None else synthesis_registry()
    async with env.factory() as database:
        await RegistryStore(database).publish(catalog, expected_revision=0)
        for assignment in (await database.scalars(select(AIProfileAssignment))).all():
            assignment.rollout_percent = 100
        for model in catalog.models:
            if Capability.SPEECH_SYNTHESIS in model.capabilities:
                await record_evaluation(
                    database,
                    model_id=model_key(model.reference),
                    model_digest=digest(canonical(model)),
                    registry_revision=catalog.revision,
                    evidence=sample_evidence(),
                )
        await database.commit()
    runtime = GatewayRuntime(GatewayStore(env.factory, env.operator_policy), {})

    async def build(_settings):
        return runtime

    env.monkeypatch.setattr(env.module, "build_runtime", build)
    env.monkeypatch.setattr(
        env.module, "configured_speech_adapters", lambda _settings: {Provider.OPENAI: adapter}
    )
    return adapter


def speech_headers(env, *, origin: str | None = None) -> dict[str, str]:
    return {
        "Origin": env.settings.web_origin if origin is None else origin,
        "Content-Type": "application/json",
    }


async def post_speech(env, text: str, **kwargs: object) -> httpx.Response:
    return await env.client.post(
        "/api/v1/ai/assistant/speech/synthesize",
        json={"text": text},
        headers=speech_headers(env, **kwargs),
    )


def seeded_attempt(env, *, created_at: datetime) -> AITaskRun:
    return AITaskRun(
        id=uuid4(),
        task_id=uuid4(),
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        trace_id=uuid4(),
        task_type=TaskType.SYNTHESIZE.value,
        profile=Profile.SPEECH_SYNTHESIS.value,
        prompt="speech_synthesis_prompt@v1",
        schema="speech_synthesis@v1",
        status="COMPLETED",
        usage={},
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected_before_any_provider(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.post(
            "/api/v1/ai/assistant/speech/synthesize",
            json={"text": "hello"},
            headers={"Origin": env.settings.web_origin, "Content-Type": "application/json"},
        )
    assert response.status_code == 401
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_untrusted_origin_is_rejected_without_a_provider_call(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env)
    response = await post_speech(env, "hello", origin="https://evil.example")
    assert response.status_code == 403
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_default_catalog_publishes_no_synthesis_profile(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env, registry=catalog_template())
    response = await post_speech(env, "hello")
    assert response.status_code == 503
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_unconfigured_operator_provider_returns_unavailable(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env)
    env.monkeypatch.setattr(env.module, "configured_speech_adapters", lambda _settings: {})
    response = await post_speech(env, "hello")
    assert response.status_code == 503
    assert adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_SPEECH_CHARACTERS + 1)])
async def test_blank_and_overlong_text_is_rejected(synthesis_env, text: str) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env)
    response = await post_speech(env, text)
    assert response.status_code == 422
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_qualified_text_returns_bounded_mp3_and_a_content_free_trace(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env)
    response = await post_speech(env, "Your class is at 3 PM.")
    assert response.status_code == 200, response.text
    assert response.content == MP3
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["Cache-Control"] == "no-store"
    assert len(adapter.calls) == 1
    sent_model, sent_text, sent_voice, sent_timeout = adapter.calls[0]
    assert sent_model == SYNTHESIZE_MODEL.model
    assert sent_text == "Your class is at 3 PM."
    assert sent_voice == SPEECH_VOICE
    assert sent_timeout == env.settings.openai_read_timeout_seconds
    async with env.factory() as database:
        runs = (await database.scalars(select(AITaskRun))).all()
    assert len(runs) == 1
    run = runs[0]
    assert run.task_type == "synthesize"
    assert run.profile == Profile.SPEECH_SYNTHESIS.value
    assert run.prompt == "speech_synthesis_prompt@v1"
    assert run.schema == "speech_synthesis@v1"
    assert run.provider == Provider.OPENAI.value
    assert run.model == SYNTHESIZE_MODEL.model
    assert run.status == "COMPLETED"
    # 22 characters at 0.015 per 1000 characters.
    assert run.reserved_cost == Decimal("0.00033")
    assert run.estimated_cost == Decimal("0.00033")
    assert run.usage == {} and run.fallback_count == 0 and run.shadow is False
    serialized = json.dumps(
        {column.name: getattr(run, column.name) for column in AITaskRun.__table__.columns},
        default=str,
    )
    assert "ECON" not in serialized
    assert "3 PM" not in serialized


@pytest.mark.asyncio
async def test_selection_is_cheapest_first(synthesis_env) -> None:
    env = synthesis_env
    cheap = ModelRef(provider=Provider.OPENAI, model="fixture-synthesis-a-cheap")
    pricey = ModelRef(provider=Provider.OPENAI, model="fixture-synthesis-z-pricey")
    registry = synthesis_registry(
        models=(
            transcription_model(),
            synthesis_model(reference=cheap, synthesis_cost_per_1000_characters=Decimal("0.002")),
            synthesis_model(reference=pricey, synthesis_cost_per_1000_characters=Decimal("0.060")),
        ),
        profiles=(transcription_profile(), synthesis_profile(assignments=(pricey, cheap))),
    )
    adapter = await install_synthesis(env, registry=registry)
    response = await post_speech(env, "x" * 600)
    assert response.status_code == 200, response.text
    assert [call[0] for call in adapter.calls] == [cheap.model]
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.model == cheap.model
    assert run.reserved_cost == Decimal("0.0012")


@pytest.mark.asyncio
async def test_hourly_quota_blocks_the_twenty_first_attempt(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env)
    moment = datetime.now(UTC)
    async with env.factory() as database:
        database.add_all([seeded_attempt(env, created_at=moment) for _ in range(19)])
        await database.commit()
    allowed = await post_speech(env, "hello")
    assert allowed.status_code == 200
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        database.add(seeded_attempt(env, created_at=moment))
        await database.commit()
    denied = await post_speech(env, "hello")
    assert denied.status_code == 429
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_revoked_account_after_the_call_discards_the_audio(synthesis_env) -> None:
    env = synthesis_env

    async def pause_user() -> None:
        async with env.factory() as database:
            user = await database.get(User, env.user_id)
            assert user is not None
            user.agent_paused = True
            await database.commit()

    adapter = await install_synthesis(env, adapter=FakeSynthesisAdapter(during=pause_user))
    response = await post_speech(env, "Your class is at 3 PM.")
    assert response.status_code == 403
    assert response.content != MP3
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "FAILED"
    assert run.error_code == "authorization_revoked"


@pytest.mark.asyncio
async def test_catalog_change_after_the_call_discards_the_audio(synthesis_env) -> None:
    env = synthesis_env

    async def bump_registry() -> None:
        async with env.factory() as database:
            await RegistryStore(database).publish(
                synthesis_registry(revision=2), expected_revision=1
            )
            await database.commit()

    adapter = await install_synthesis(env, adapter=FakeSynthesisAdapter(during=bump_registry))
    response = await post_speech(env, "Your class is at 3 PM.")
    assert response.status_code == 503
    assert response.content != MP3
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "FAILED"
    assert run.error_code == "eligibility_changed"


@pytest.mark.asyncio
async def test_bounded_provider_timeout_returns_503(synthesis_env) -> None:
    env = synthesis_env
    adapter = await install_synthesis(
        env, adapter=FakeSynthesisAdapter(fail=httpx.ConnectTimeout("timed out"))
    )
    response = await post_speech(env, "hello")
    assert response.status_code == 503
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "FAILED" and run.error_code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", [b"", b"\x00" * (MAX_SPEECH_AUDIO_BYTES + 1)])
async def test_unusable_provider_audio_returns_502(synthesis_env, audio: bytes) -> None:
    env = synthesis_env
    adapter = await install_synthesis(env, adapter=FakeSynthesisAdapter(audio=audio))
    response = await post_speech(env, "hello")
    assert response.status_code == 502
    assert len(adapter.calls) == 1
