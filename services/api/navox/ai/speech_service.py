"""M11B bounded SPEC-005 transcription. Fail-closed, content-free, dormant by default.

The route accepts one short uncompressed PCM WAV clip and turns it into bounded
question text. Nothing here can choose a model, widen a grant, reserve fallback
or shadow traffic, retain audio, or convey action authority. Selection reuses
M11A's ``rank_eligible_speech``; execution stays in this module so an audio
payload never has to masquerade as a token-billed text request.
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Final
from uuid import UUID

from navox.ai.foundation.adapter import ErrorCode, ProviderError
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    LatencyClass,
    Profile,
    Provider,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.foundation.persistence import model_key
from navox.ai.foundation.registry import ModelDefinition
from navox.ai.routing import PolicyRules
from navox.ai.runtime import GatewayRuntime
from navox.ai.speech_adapters import (
    MAX_AUDIO_REQUEST_BYTES,
    SpeechAdapter,
    configured_speech_adapters,
)
from navox.ai.speech_routing import rank_eligible_speech
from navox.ai.store import GatewayStore, RoutingSnapshot
from navox.core.settings import Settings
from navox.db.ai_registry import AITaskRun

MAX_AUDIO_BYTES: Final[int] = MAX_AUDIO_REQUEST_BYTES
MIN_AUDIO_MILLISECONDS: Final[int] = 200
MAX_AUDIO_MILLISECONDS: Final[int] = 30_000
TRANSCRIPTION_CHANNELS: Final[int] = 1
TRANSCRIPTION_SAMPLE_RATE: Final[int] = 16_000
TRANSCRIPTION_SAMPLE_WIDTH: Final[int] = 2
MAX_TRANSCRIPT_CHARACTERS: Final[int] = 500
TRANSCRIPTION_HOURLY_LIMIT: Final[int] = 20
TRANSCRIPTION_QUOTA_WINDOW: Final[timedelta] = timedelta(hours=1)
TRANSCRIPTION_MAX_OUTPUT_TOKENS: Final[int] = 1_000
TRANSCRIPTION_DEADLINE_SLACK_SECONDS: Final[float] = 5.0
TRANSCRIPTION_PROMPT: Final[VersionedRef] = VersionedRef(
    name="speech_transcription_prompt", version="v1"
)
TRANSCRIPTION_SCHEMA: Final[VersionedRef] = VersionedRef(name="speech_transcription", version="v1")


class SpeechRequestRejected(ValueError):
    """The clip is malformed or outside the bounded recording contract (422)."""


class SpeechOversized(SpeechRequestRejected):
    """The clip exceeded the byte cap while streaming (413)."""


class SpeechQuotaExceeded(ValueError):
    """The caller's rolling-hour attempt budget is already spent (429)."""


class SpeechUnavailable(RuntimeError):
    """No qualified, configured, still-authorized speech provider is available (503)."""


class SpeechUpstreamFailure(RuntimeError):
    """A bounded upstream result that cannot become a user question (502)."""


@dataclass(frozen=True)
class BoundedAudio:
    """The validated original WAV container plus a duration derived from its frames."""

    duration_milliseconds: int
    data: bytes


async def read_bounded_audio(
    chunks: AsyncIterator[bytes], *, limit: int = MAX_AUDIO_BYTES
) -> bytes:
    """Collect at most ``limit`` bytes; an oversize chunk is rejected unread."""

    audio = bytearray()
    async for chunk in chunks:
        if len(audio) + len(chunk) > limit:
            raise SpeechOversized("The audio clip exceeds the size limit")
        audio.extend(chunk)
    return bytes(audio)


def parse_wav(audio: bytes) -> BoundedAudio:
    """Parse uncompressed PCM WAV: mono, 16 kHz, 16-bit, 200 ms to 30 s.

    The provider receives the validated container bytes, never bare PCM: the
    frames are read only to prove the file is complete and to derive duration.
    """

    if not audio:
        raise SpeechRequestRejected("An audio clip is required")
    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechRequestRejected("The audio clip exceeds the size limit")
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            if handle.getcomptype() != "NONE":
                raise SpeechRequestRejected("Only uncompressed PCM WAV audio is supported")
            if (
                handle.getnchannels() != TRANSCRIPTION_CHANNELS
                or handle.getsampwidth() != TRANSCRIPTION_SAMPLE_WIDTH
                or handle.getframerate() != TRANSCRIPTION_SAMPLE_RATE
            ):
                raise SpeechRequestRejected("Audio must be 16 kHz mono 16-bit PCM")
            frames = handle.getnframes()
            frame_size = handle.getnchannels() * handle.getsampwidth()
            frames_data = handle.readframes(frames)
    except (wave.Error, EOFError):
        raise SpeechRequestRejected("The audio body is not a valid WAV file") from None
    if frames <= 0 or len(frames_data) != frames * frame_size:
        raise SpeechRequestRejected("The WAV file has incomplete frame data")
    duration = frames * 1000 // TRANSCRIPTION_SAMPLE_RATE
    if duration < MIN_AUDIO_MILLISECONDS or duration > MAX_AUDIO_MILLISECONDS:
        raise SpeechRequestRejected("Audio must be between 200 ms and 30000 ms")
    return BoundedAudio(duration_milliseconds=duration, data=audio)


def bounded_transcript(raw: object) -> str:
    """Trim to the assistant's 500-character question limit or reject."""

    if not isinstance(raw, str):
        raise SpeechUpstreamFailure("The transcription provider returned an unusable result")
    text = raw.strip()
    if not text or len(text) > MAX_TRANSCRIPT_CHARACTERS:
        raise SpeechUpstreamFailure("The transcription provider returned an unusable result")
    return text


def transcription_task(*, workspace_id: UUID, user_id: UUID, ceiling: PolicyRules) -> AITask:
    """One fixed SENSITIVE transcription task. No fallback, shadow, or context refs."""

    return AITask(
        workspace_id=workspace_id,
        user_id=user_id,
        task_type=TaskType.TRANSCRIBE,
        profile=Profile.SPEECH_TRANSCRIPTION,
        capability_requirements=frozenset({Capability.TRANSCRIPTION}),
        context_references=(),
        output_schema=TRANSCRIPTION_SCHEMA,
        prompt=TRANSCRIPTION_PROMPT,
        sensitivity=Sensitivity.SENSITIVE,
        latency_class=LatencyClass.INTERACTIVE,
        quality_class=QualityClass.STANDARD,
        max_cost=ceiling.max_cost,
        max_output_tokens=TRANSCRIPTION_MAX_OUTPUT_TOKENS,
        provider_policy=ProviderPolicy(
            workspace_id=workspace_id,
            user_id=user_id,
            revision=1,
            grants=ceiling.grants,
            allow_fallback=False,
            max_fallbacks=0,
        ),
    )


def upstream_failure(detail: ProviderError) -> RuntimeError:
    """Two bounded classifications; never a provider body or credential."""

    if detail.code in {
        ErrorCode.AUTHENTICATION,
        ErrorCode.RATE_LIMIT,
        ErrorCode.TIMEOUT,
        ErrorCode.UNAVAILABLE,
    }:
        return SpeechUnavailable("The speech provider is unavailable")
    return SpeechUpstreamFailure("The speech provider returned an unusable result")


def _adapter_supports(adapter: SpeechAdapter, model: str) -> bool:
    try:
        return Capability.TRANSCRIPTION in adapter.capabilities(model)
    except Exception:
        return False


def _select_eligible(
    candidates: list[tuple[ModelDefinition, Decimal]],
    adapters: Mapping[Provider, SpeechAdapter],
) -> tuple[ModelDefinition, Decimal, SpeechAdapter] | None:
    """Exactly one installed adapter. No second candidate, fallback, or shadow."""

    for model, reservation in candidates:
        adapter = adapters.get(model.reference.provider)
        if adapter is None or not _adapter_supports(adapter, model.reference.model):
            continue
        return model, reservation, adapter
    return None


async def _reauthorize(
    store: GatewayStore,
    task: AITask,
    previous: RoutingSnapshot,
    *,
    key: str,
    units: int,
) -> None:
    """Discard a transcript when authority, catalog or budget moved mid-call."""

    await store.authorize(task)
    try:
        current = await store.snapshot(task)
        candidates = rank_eligible_speech(task, current, units=units)
    except (LookupError, ValueError):
        raise SpeechUnavailable("Speech transcription is no longer qualified") from None
    if current.registry.revision != previous.registry.revision or key not in {
        model_key(model.reference) for model, _ in candidates
    }:
        raise SpeechUnavailable("Speech transcription authority changed")


async def _trace(store: GatewayStore, run: AITaskRun) -> None:
    await asyncio.shield(store.trace(run))


async def transcribe_audio(
    runtime: GatewayRuntime,
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
    audio: bytes,
    adapters: Mapping[Provider, SpeechAdapter] | None = None,
) -> str:
    """Run one bounded transcription attempt and return only validated question text."""

    bounded = parse_wav(audio)
    store = runtime.store
    task = transcription_task(
        workspace_id=workspace_id, user_id=user_id, ceiling=store.operator_policy
    )
    # Missing membership or a paused account fails closed before any selection.
    await store.authorize(task)
    try:
        snapshot = await store.snapshot(task)
        candidates = rank_eligible_speech(task, snapshot, units=bounded.duration_milliseconds)
    except (LookupError, ValueError):
        raise SpeechUnavailable("Speech transcription is not configured") from None
    selected = _select_eligible(
        candidates, adapters if adapters is not None else configured_speech_adapters(settings)
    )
    if selected is None:
        raise SpeechUnavailable("No qualified speech provider is available")
    model, reservation, adapter = selected
    key = model_key(model.reference)

    # The attempt slot is committed under the caller's user row lock before any
    # provider call, so failed attempts count against the same rolling hour.
    run = await store.admit_transcription(
        task, limit=TRANSCRIPTION_HOURLY_LIMIT, window=TRANSCRIPTION_QUOTA_WINDOW
    )
    if run is None:
        raise SpeechQuotaExceeded("Hourly transcription quota is exhausted")
    if not await store.claim(key):
        run.status, run.error_code = "FAILED", "provider_unavailable"
        await _trace(store, run)
        raise SpeechUnavailable("The speech provider is temporarily unavailable")
    run.registry_revision = snapshot.registry.revision
    run.provider, run.model = model.reference.provider.value, model.reference.model
    run.reserved_cost = reservation
    await store.trace(run)

    timeout_seconds = settings.openai_read_timeout_seconds
    started = monotonic()
    status = "FAILED"
    try:
        async with asyncio.timeout(timeout_seconds + TRANSCRIPTION_DEADLINE_SLACK_SECONDS):
            raw = await adapter.transcribe(
                model=model.reference.model, audio=bounded.data, timeout_seconds=timeout_seconds
            )
        transcript = bounded_transcript(raw)
        await store.health(key, None)
        await _reauthorize(store, task, snapshot, key=key, units=bounded.duration_milliseconds)
        # Transcription is priced by duration, so the reservation is the estimate.
        run.estimated_cost = reservation
        run.usage = {}
        status = "COMPLETED"
        return transcript
    except asyncio.CancelledError:
        run.error_code = "cancelled"
        await asyncio.shield(store.health(key, ProviderError(code=ErrorCode.UNAVAILABLE)))
        raise
    except SpeechUpstreamFailure:
        run.error_code = ErrorCode.INVALID_RESPONSE.value
        await asyncio.shield(store.health(key, ProviderError(code=ErrorCode.INVALID_RESPONSE)))
        raise
    except PermissionError:
        run.error_code = "authorization_revoked"
        raise
    except SpeechUnavailable:
        run.error_code = "eligibility_changed"
        raise
    except Exception as error:
        detail = error if isinstance(error, ProviderError) else adapter.classify_error(error)
        run.error_code = detail.code.value
        await asyncio.shield(store.health(key, detail))
        raise upstream_failure(detail) from None
    finally:
        run.status = status
        run.latency_ms = int((monotonic() - started) * 1000)
        run.finished_at = run.finished_at or datetime.now(UTC)
        await _trace(store, run)
