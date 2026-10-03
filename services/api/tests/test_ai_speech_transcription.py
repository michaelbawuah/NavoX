"""M11B bounded transcription: contract, quota, provider boundary, and fail-closed route."""

from __future__ import annotations

import io
import json
import os
import wave
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
    TRANSCRIBE_MODEL,
    TRANSCRIPTION_PROMPT,
    TRANSCRIPTION_SCHEMA,
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
from navox.ai.foundation.registry import ModelRef, RegistrySnapshot
from navox.ai.providers import HTTPAdapter
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.runtime import GatewayRuntime
from navox.ai.speech_adapters import (
    MAX_AUDIO_REQUEST_BYTES,
    OPENAI_TRANSCRIPTIONS_URL,
    OpenAISpeechAdapter,
    SpeechAdapterFailure,
)
from navox.ai.speech_service import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_MILLISECONDS,
    MAX_TRANSCRIPT_CHARACTERS,
    MIN_AUDIO_MILLISECONDS,
    TRANSCRIPTION_HOURLY_LIMIT,
    TRANSCRIPTION_QUOTA_WINDOW,
    SpeechOversized,
    SpeechRequestRejected,
    SpeechUpstreamFailure,
    bounded_transcript,
    parse_wav,
    read_bounded_audio,
    transcription_task,
)
from navox.ai.store import GatewayStore
from navox.api import ai_operations
from navox.api.ai_operations import TranscriptionResponse
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.base import Base
from navox.db.models import User

WORKSPACE = UUID("00000000-0000-4000-8000-000000000021")
USER = UUID("00000000-0000-4000-8000-000000000022")
SAMPLE_RATE = 16_000


def wav_bytes(
    milliseconds: int,
    *,
    channels: int = 1,
    sample_width: int = 2,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    frames = max(0, sample_rate * milliseconds // 1000)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00" * frames * channels * sample_width)
    return buffer.getvalue()


WAV_CONTAINER = wav_bytes(300)


def operator_policy(*, max_cost: str = "1") -> PolicyRules:
    grant = ProviderGrant(
        provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.SENSITIVE})
    )
    return PolicyRules(grants=(grant,), max_cost=Decimal(max_cost))


def sample_evidence(**changes: object) -> EvaluationEvidence:
    values: dict[str, object] = {
        "profile": Profile.SPEECH_TRANSCRIPTION,
        "task_type": TaskType.TRANSCRIBE,
        "prompt": TRANSCRIPTION_PROMPT,
        "output_schema": TRANSCRIPTION_SCHEMA,
        "quality": 0.95,
        "reliability": 0.97,
        "p95_latency_ms": 900,
        "samples": 25,
        "safety_passed": True,
        "evaluated_at": datetime.now(UTC),
        "corpus_version": "m11b-fixture-corpus",
    }
    values.update(changes)
    return EvaluationEvidence.model_validate(values)


class FakeSpeechAdapter:
    """Controlled provider double: it can never make a network request."""

    provider = Provider.OPENAI

    def __init__(self, *, text: str = "What did Sarah email me", during=None, fail=None) -> None:
        self.text, self.during, self.fail = text, during, fail
        self.calls: list[tuple[str, bytes, float]] = []

    def capabilities(self, model: str) -> frozenset[Capability]:
        return frozenset({Capability.TRANSCRIPTION})

    def classify_error(self, error: Exception) -> ProviderError:
        return HTTPAdapter.classify_error(self, error)  # type: ignore[arg-type]

    async def transcribe(self, *, model: str, audio: bytes, timeout_seconds: float) -> str:
        self.calls.append((model, audio, timeout_seconds))
        if self.during is not None:
            await self.during()
        if self.fail is not None:
            raise self.fail
        return self.text


# --------------------------------------------------------------------------------------
# Contract helpers: WAV parsing, transcript bounds, fixed task
# --------------------------------------------------------------------------------------


def test_audio_contract_constants_are_fixed() -> None:
    assert MAX_AUDIO_BYTES == MAX_AUDIO_REQUEST_BYTES == 1_000_000
    assert (MIN_AUDIO_MILLISECONDS, MAX_AUDIO_MILLISECONDS) == (200, 30_000)
    assert MAX_TRANSCRIPT_CHARACTERS == 500
    assert TRANSCRIPTION_HOURLY_LIMIT == 20
    assert TRANSCRIPTION_QUOTA_WINDOW == timedelta(hours=1)


@pytest.mark.parametrize("milliseconds", [MIN_AUDIO_MILLISECONDS, 1_000, MAX_AUDIO_MILLISECONDS])
def test_valid_recordings_bind_duration_from_frames(milliseconds: int) -> None:
    clip = wav_bytes(milliseconds)
    bounded = parse_wav(clip)
    assert bounded.duration_milliseconds == milliseconds
    # The provider must receive the WAV container, never bare PCM bytes.
    assert bounded.data == clip
    assert bounded.data[:4] == b"RIFF" and bounded.data[8:12] == b"WAVE"


@pytest.mark.asyncio
async def test_read_bounded_audio_rejects_one_oversized_chunk_without_reading_more() -> None:
    produced: list[int] = []

    async def chunks():
        produced.append(1)
        yield b"\x00" * (MAX_AUDIO_BYTES + 1)
        produced.append(2)
        yield b"more"

    with pytest.raises(SpeechOversized):
        await read_bounded_audio(chunks())
    assert produced == [1]


@pytest.mark.asyncio
async def test_read_bounded_audio_accepts_exactly_the_limit() -> None:
    async def chunks():
        yield b"\x00" * MAX_AUDIO_BYTES

    assert len(await read_bounded_audio(chunks())) == MAX_AUDIO_BYTES


@pytest.mark.parametrize("milliseconds", [199, 30_001, 31_250])
def test_out_of_range_durations_are_rejected(milliseconds: int) -> None:
    with pytest.raises(SpeechRequestRejected):
        parse_wav(wav_bytes(milliseconds))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"channels": 2},
        {"sample_width": 1},
        {"sample_rate": 8_000},
    ],
)
def test_non_pcm_16k_mono_audio_is_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(SpeechRequestRejected):
        parse_wav(wav_bytes(1_000, **kwargs))


def test_incomplete_frame_data_is_rejected() -> None:
    clip = wav_bytes(1_000)
    with pytest.raises(SpeechRequestRejected):
        parse_wav(clip[:-8])
    with pytest.raises(SpeechRequestRejected):
        parse_wav(b"")
    with pytest.raises(SpeechRequestRejected):
        parse_wav(b"this is not a wav file at all")


def test_unsupported_wav_format_tag_is_rejected() -> None:
    clip = bytearray(wav_bytes(1_000))
    clip[20:22] = b"\x03\x00"  # IEEE float, not uncompressed PCM
    with pytest.raises(SpeechRequestRejected):
        parse_wav(bytes(clip))


def test_oversized_audio_is_rejected_before_parsing() -> None:
    with pytest.raises(SpeechRequestRejected):
        parse_wav(b"\x00" * (MAX_AUDIO_BYTES + 1))


def test_transcript_is_trimmed_and_bounded() -> None:
    assert bounded_transcript("  What did Sarah email me  ") == "What did Sarah email me"
    assert bounded_transcript("x" * MAX_TRANSCRIPT_CHARACTERS) == "x" * MAX_TRANSCRIPT_CHARACTERS
    for invalid in ("", "   ", "\n\t", "x" * (MAX_TRANSCRIPT_CHARACTERS + 1), None, 12):
        with pytest.raises(SpeechUpstreamFailure):
            bounded_transcript(invalid)


def test_transcription_task_is_fixed_and_grants_nothing_extra() -> None:
    ceiling = operator_policy()
    task = transcription_task(workspace_id=WORKSPACE, user_id=USER, ceiling=ceiling)
    assert task.task_type is TaskType.TRANSCRIBE
    assert task.profile is Profile.SPEECH_TRANSCRIPTION
    assert task.capability_requirements == frozenset({Capability.TRANSCRIPTION})
    assert task.prompt == TRANSCRIPTION_PROMPT
    assert task.output_schema == TRANSCRIPTION_SCHEMA
    assert (task.prompt.name, task.prompt.version) == ("speech_transcription_prompt", "v1")
    assert (task.output_schema.name, task.output_schema.version) == ("speech_transcription", "v1")
    assert task.sensitivity is Sensitivity.SENSITIVE
    assert task.context_references == ()
    assert task.max_cost == ceiling.max_cost
    assert task.provider_policy.allow_fallback is False
    assert task.provider_policy.max_fallbacks == 0
    assert task.provider_policy.grants == ceiling.grants
    assert TranscriptionResponse.model_fields.keys() == {"text"}


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
async def test_quota_admission_locks_the_user_row_and_inserts_one_attempt() -> None:
    session = _RecordingSession(user=SimpleNamespace(agent_paused=False), used=0)
    task = transcription_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    run = await _fake_store(session).admit_transcription(
        task, limit=TRANSCRIPTION_HOURLY_LIMIT, window=TRANSCRIPTION_QUOTA_WINDOW
    )
    assert isinstance(run, AITaskRun)
    assert run.status == "STARTED" and run.usage == {} and run.shadow is False
    assert session.commits == 1 and len(session.added) == 1
    assert session.gets == 1  # membership is re-read under the same lock
    compiled = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in compiled
    assert "count(" in str(session.statements[1]).lower()


@pytest.mark.asyncio
async def test_quota_admission_blocks_at_the_limit_without_inserting() -> None:
    session = _RecordingSession(user=SimpleNamespace(agent_paused=False), used=20)
    task = transcription_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    run = await _fake_store(session).admit_transcription(
        task, limit=TRANSCRIPTION_HOURLY_LIMIT, window=TRANSCRIPTION_QUOTA_WINDOW
    )
    assert run is None
    assert session.added == [] and session.commits == 0


@pytest.mark.asyncio
async def test_quota_admission_fails_closed_for_a_paused_caller() -> None:
    session = _RecordingSession(user=SimpleNamespace(agent_paused=True), used=0)
    task = transcription_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    with pytest.raises(PermissionError):
        await _fake_store(session).admit_transcription(
            task, limit=TRANSCRIPTION_HOURLY_LIMIT, window=TRANSCRIPTION_QUOTA_WINDOW
        )
    assert session.added == []


@pytest.mark.asyncio
async def test_quota_admission_fails_closed_without_membership() -> None:
    """A revoked workspace membership must not reserve a paid attempt."""

    session = _RecordingSession(user=SimpleNamespace(agent_paused=False), used=0, member=None)
    task = transcription_task(workspace_id=WORKSPACE, user_id=USER, ceiling=operator_policy())
    with pytest.raises(PermissionError):
        await _fake_store(session).admit_transcription(
            task, limit=TRANSCRIPTION_HOURLY_LIMIT, window=TRANSCRIPTION_QUOTA_WINDOW
        )
    assert session.gets == 1 and session.added == [] and session.commits == 0


# --------------------------------------------------------------------------------------
# OpenAI speech adapter: fixed destination, bounded response, no redirects
# --------------------------------------------------------------------------------------


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_openai_adapter_sends_the_selected_model_and_wav_bytes() -> None:
    seen: dict[str, object] = {}
    clip = wav_bytes(1_000)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = await request.aread()
        return httpx.Response(200, json={"text": " hello world "})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    text = await adapter.transcribe(
        model="fixture-transcribe-not-live", audio=clip, timeout_seconds=10
    )
    assert text == " hello world "
    assert seen["url"] == OPENAI_TRANSCRIPTIONS_URL
    assert seen["authorization"] == "Bearer test-key"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="model"' in body and b"fixture-transcribe-not-live" in body
    assert b'filename="audio.wav"' in body and b"audio/wav" in body
    # Wire-level: the multipart part carries the RIFF/WAVE container itself, and
    # omission of a response model is accepted (the documented response shape).
    assert b"RIFF" in body and b"WAVE" in body and clip in body


@pytest.mark.asyncio
async def test_openai_adapter_rejects_bare_pcm_mislabeled_as_wav() -> None:
    """A regression that strips the container must fail before any request."""

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": "hello"})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await adapter.transcribe(model="fixture-model", audio=b"\x00" * 4_096, timeout_seconds=5)
    assert info.value.detail.code is ErrorCode.INVALID_REQUEST
    assert requests == []


@pytest.mark.asyncio
async def test_openai_adapter_accepts_a_matching_returned_model() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "hello", "model": "fixture-model"})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    assert (
        await adapter.transcribe(model="fixture-model", audio=WAV_CONTAINER, timeout_seconds=5)
        == "hello"
    )


@pytest.mark.asyncio
async def test_openai_adapter_rejects_a_contradicting_returned_model() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "hello", "model": "some-other-model"})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await adapter.transcribe(model="fixture-model", audio=WAV_CONTAINER, timeout_seconds=5)
    assert info.value.detail.code is ErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_openai_adapter_does_not_follow_redirects() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.invalid/collect"})

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await adapter.transcribe(model="fixture-model", audio=WAV_CONTAINER, timeout_seconds=5)
    assert info.value.detail.code is ErrorCode.UNAVAILABLE
    assert seen == [OPENAI_TRANSCRIPTIONS_URL]


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
        await adapter.transcribe(model="fixture-model", audio=WAV_CONTAINER, timeout_seconds=5)
    assert info.value.detail.code is expected
    assert "secret" not in str(info.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"text": "   "}),
        httpx.Response(200, json={"error": {"message": "boom"}}),
        httpx.Response(200, json={"text": "x" * 5_000}),
        httpx.Response(200, content=json.dumps({"text": "x" * 300_000}).encode()),
    ],
)
@pytest.mark.asyncio
async def test_openai_adapter_rejects_unusable_responses(response: httpx.Response) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return response

    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"), client=mock_client(handler))
    with pytest.raises(SpeechAdapterFailure) as info:
        await adapter.transcribe(model="fixture-model", audio=WAV_CONTAINER, timeout_seconds=5)
    assert info.value.detail.code is ErrorCode.INVALID_RESPONSE


def test_openai_adapter_capabilities_are_transcription_only() -> None:
    adapter = OpenAISpeechAdapter(api_key=SecretStr("test-key"))
    assert adapter.capabilities("fixture-transcribe-not-live") == frozenset(
        {Capability.TRANSCRIPTION}
    )
    assert adapter.capabilities("../../evil") == frozenset()
    assert adapter.provider is Provider.OPENAI


# --------------------------------------------------------------------------------------
# Route: authenticated, origin-checked, bounded, fail-closed
# --------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def speech_env(monkeypatch):
    from navox.api.main import create_app
    from navox.core.settings import Settings, get_settings
    from navox.db import models  # noqa: F401
    from navox.db.session import get_database_session

    dsn = os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "sqlite+aiosqlite://")
    schema = f"speech_api_{uuid4().hex}"
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
                "email": "speech-transcription@example.com",
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


def speech_registry(*, revision: int = 1, models=None, profiles=None) -> RegistrySnapshot:
    changes: dict[str, object] = {"revision": revision}
    if models is not None:
        changes["models"] = models
    if profiles is not None:
        changes["profiles"] = profiles
    return make_registry(**changes)


async def install_speech(env, *, adapter=None, registry: RegistrySnapshot | None = None):
    """Publish the operator catalog and inject a controlled speech adapter."""

    adapter = adapter if adapter is not None else FakeSpeechAdapter()
    catalog = registry if registry is not None else speech_registry()
    async with env.factory() as database:
        await RegistryStore(database).publish(catalog, expected_revision=0)
        for assignment in (await database.scalars(select(AIProfileAssignment))).all():
            assignment.rollout_percent = 100
        for model in catalog.models:
            if Capability.TRANSCRIPTION in model.capabilities:
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


def clip_headers(
    env, *, content_type: str = "audio/wav", origin: str | None = None
) -> dict[str, str]:
    return {
        "Origin": env.settings.web_origin if origin is None else origin,
        "Content-Type": content_type,
    }


async def post_clip(env, body: bytes, **kwargs: object) -> httpx.Response:
    return await env.client.post(
        "/api/v1/ai/assistant/speech/transcribe",
        content=body,
        headers=clip_headers(env, **kwargs),
    )


def seeded_attempt(env, *, created_at: datetime) -> AITaskRun:
    return AITaskRun(
        id=uuid4(),
        task_id=uuid4(),
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        trace_id=uuid4(),
        task_type=TaskType.TRANSCRIBE.value,
        profile=Profile.SPEECH_TRANSCRIPTION.value,
        prompt="speech_transcription_prompt@v1",
        schema="speech_transcription@v1",
        status="COMPLETED",
        usage={},
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected_before_any_provider(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(env)
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as anonymous:
        response = await anonymous.post(
            "/api/v1/ai/assistant/speech/transcribe",
            content=wav_bytes(1_000),
            headers={
                "Origin": env.settings.web_origin,
                "Content-Type": "audio/wav",
            },
        )
    assert response.status_code == 401
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_untrusted_origin_is_rejected_without_a_provider_call(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(env)
    response = await post_clip(env, wav_bytes(1_000), origin="https://evil.example")
    assert response.status_code == 403
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_default_catalog_publishes_no_speech_profile_and_denies_transcription(
    speech_env,
) -> None:
    env = speech_env
    adapter = await install_speech(env, registry=catalog_template())
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 503
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_unconfigured_operator_provider_returns_unavailable(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(env)
    env.monkeypatch.setattr(env.module, "configured_speech_adapters", lambda _settings: {})
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 503
    assert adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    ["application/json", "multipart/form-data; boundary=abc", "text/plain", "audio/mpeg"],
)
async def test_wrong_media_type_is_rejected(speech_env, content_type: str) -> None:
    env = speech_env
    adapter = await install_speech(env)
    response = await post_clip(env, b"{}", content_type=content_type)
    assert response.status_code == 415
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_without_buffering_the_rest(speech_env) -> None:
    """ASGITransport delivers this body as one chunk; it must be rejected unread."""

    env = speech_env
    adapter = await install_speech(env)
    response = await post_clip(env, b"\x00" * (MAX_AUDIO_BYTES + 64))
    assert response.status_code == 413
    assert adapter.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not a wav file",
        wav_bytes(199),
        wav_bytes(30_001),
        wav_bytes(1_000)[:-8],
        wav_bytes(1_000, channels=2),
        wav_bytes(1_000, sample_width=1),
        wav_bytes(1_000, sample_rate=8_000),
    ],
)
async def test_malformed_or_unsupported_clips_return_422(speech_env, body: bytes) -> None:
    env = speech_env
    adapter = await install_speech(env)
    response = await post_clip(env, body)
    assert response.status_code == 422
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_qualified_clip_returns_bounded_text_and_records_a_content_free_trace(
    speech_env,
) -> None:
    env = speech_env
    adapter = await install_speech(env)
    clip = wav_bytes(30_000)
    response = await post_clip(env, clip)
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "What did Sarah email me"}
    assert response.headers["Cache-Control"] == "no-store"
    assert len(adapter.calls) == 1
    sent_model, sent_audio, sent_timeout = adapter.calls[0]
    assert sent_model == TRANSCRIBE_MODEL.model
    assert sent_timeout == env.settings.openai_read_timeout_seconds
    # The provider receives the validated WAV container, header included.
    assert sent_audio == clip
    assert sent_audio[:4] == b"RIFF" and sent_audio[8:12] == b"WAVE"
    assert len(sent_audio) == len(clip)
    async with env.factory() as database:
        runs = (await database.scalars(select(AITaskRun))).all()
    assert len(runs) == 1
    run = runs[0]
    assert run.task_type == "transcribe"
    assert run.profile == Profile.SPEECH_TRANSCRIPTION.value
    assert run.prompt == "speech_transcription_prompt@v1"
    assert run.schema == "speech_transcription@v1"
    assert run.provider == Provider.OPENAI.value
    assert run.model == TRANSCRIBE_MODEL.model
    assert run.status == "COMPLETED"
    assert run.reserved_cost == Decimal("0.003")
    assert run.estimated_cost == Decimal("0.003")
    assert run.usage == {} and run.fallback_count == 0 and run.shadow is False
    serialized = json.dumps(
        {column.name: getattr(run, column.name) for column in AITaskRun.__table__.columns},
        default=str,
    )
    assert "Sarah" not in serialized
    assert "RIFF" not in serialized


@pytest.mark.asyncio
async def test_selection_is_exact_and_cheapest_first(speech_env) -> None:
    env = speech_env
    cheap = ModelRef(provider=Provider.OPENAI, model="fixture-transcription-a-cheap")
    pricey = ModelRef(provider=Provider.OPENAI, model="fixture-transcription-z-pricey")
    registry = speech_registry(
        models=(
            transcription_model(reference=cheap, transcription_cost_per_minute=Decimal("0.002")),
            transcription_model(reference=pricey, transcription_cost_per_minute=Decimal("0.060")),
            synthesis_model(),
        ),
        profiles=(
            transcription_profile(assignments=(pricey, cheap)),
            synthesis_profile(),
        ),
    )
    adapter = await install_speech(env, registry=registry)
    response = await post_clip(env, wav_bytes(30_000))
    assert response.status_code == 200, response.text
    assert [call[0] for call in adapter.calls] == [cheap.model]
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.model == cheap.model
    assert run.reserved_cost == Decimal("0.001")


@pytest.mark.asyncio
async def test_hourly_quota_blocks_the_twenty_first_attempt(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(env)
    moment = datetime.now(UTC)
    async with env.factory() as database:
        database.add_all([seeded_attempt(env, created_at=moment) for _ in range(19)])
        await database.commit()
    allowed = await post_clip(env, wav_bytes(1_000))
    assert allowed.status_code == 200
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        database.add(seeded_attempt(env, created_at=moment))
        await database.commit()
    denied = await post_clip(env, wav_bytes(1_000))
    assert denied.status_code == 429
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_only_in_window_attempts_count_against_the_quota(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(env)
    stale = datetime.now(UTC) - TRANSCRIPTION_QUOTA_WINDOW - timedelta(minutes=5)
    async with env.factory() as database:
        database.add_all([seeded_attempt(env, created_at=stale) for _ in range(25)])
        await database.commit()
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 200
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_revoked_account_after_the_call_discards_the_transcript(speech_env) -> None:
    env = speech_env

    async def pause_user() -> None:
        async with env.factory() as database:
            user = await database.get(User, env.user_id)
            assert user is not None
            user.agent_paused = True
            await database.commit()

    adapter = await install_speech(env, adapter=FakeSpeechAdapter(during=pause_user))
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 403
    assert "Sarah" not in response.text
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "FAILED"
    assert run.error_code == "authorization_revoked"


@pytest.mark.asyncio
async def test_catalog_change_after_the_call_discards_the_transcript(speech_env) -> None:
    env = speech_env

    async def bump_registry() -> None:
        async with env.factory() as database:
            await RegistryStore(database).publish(speech_registry(revision=2), expected_revision=1)
            await database.commit()

    adapter = await install_speech(env, adapter=FakeSpeechAdapter(during=bump_registry))
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 503
    assert "Sarah" not in response.text
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "FAILED"
    assert run.error_code == "eligibility_changed"


@pytest.mark.asyncio
async def test_bounded_provider_timeout_returns_503(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(
        env, adapter=FakeSpeechAdapter(fail=httpx.ConnectTimeout("timed out"))
    )
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 503
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "FAILED" and run.error_code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_TRANSCRIPT_CHARACTERS + 1)])
async def test_unusable_provider_output_returns_502(speech_env, text: str) -> None:
    env = speech_env
    adapter = await install_speech(env, adapter=FakeSpeechAdapter(text=text))
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 502
    assert response.json() == {"detail": "The speech provider failed"}
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_transcript_yes_conveys_no_action_authority(speech_env) -> None:
    env = speech_env
    adapter = await install_speech(env, adapter=FakeSpeechAdapter(text="yes"))
    response = await post_clip(env, wav_bytes(1_000))
    assert response.status_code == 200
    assert response.json() == {"text": "yes"}
    assert set(response.json()) == {"text"}
    assert len(adapter.calls) == 1
    async with env.factory() as database:
        run = await database.scalar(select(AITaskRun))
    assert run is not None and run.status == "COMPLETED"
