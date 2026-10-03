"""M11D bounded SPEC-005 speech synthesis. Fail-closed, content-free, dormant by default.

One already-derived, bounded answer string becomes one bounded MP3 payload, or
nothing at all. Nothing here can choose a model, widen a grant, reserve fallback
or shadow traffic, retain audio, accept a caller-supplied voice, or convey action
authority. Selection reuses M11A's ``rank_eligible_speech`` and execution stays in
this module so audio never masquerades as a token-billed text request.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
    MAX_SPEECH_AUDIO_BYTES,
    SpeechAdapter,
    SpeechSynthesisAdapter,
    configured_speech_adapters,
)
from navox.ai.speech_routing import rank_eligible_speech
from navox.ai.speech_service import (
    SpeechQuotaExceeded,
    SpeechUnavailable,
    SpeechUpstreamFailure,
)
from navox.ai.store import GatewayStore, RoutingSnapshot
from navox.core.settings import Settings
from navox.db.ai_registry import AITaskRun

# Mirrors the assistant runtime's spoken-text bound; a longer answer is refused,
# never truncated into a different spoken answer.
MAX_SPEECH_CHARACTERS: Final[int] = 600
SYNTHESIS_HOURLY_LIMIT: Final[int] = 20
SYNTHESIS_QUOTA_WINDOW: Final[timedelta] = timedelta(hours=1)
SYNTHESIS_MAX_OUTPUT_TOKENS: Final[int] = 1_000
SYNTHESIS_DEADLINE_SLACK_SECONDS: Final[float] = 5.0
# One fixed server voice. It can never travel from the browser or the saved turn.
SPEECH_VOICE: Final[str] = "alloy"
SYNTHESIS_PROMPT: Final[VersionedRef] = VersionedRef(name="speech_synthesis_prompt", version="v1")
SYNTHESIS_SCHEMA: Final[VersionedRef] = VersionedRef(name="speech_synthesis", version="v1")


class SpeechTextRejected(ValueError):
    """The derived answer text is blank or outside the spoken bound (422)."""


def bounded_speech_text(raw: object) -> str:
    """Trim one already-derived answer to the spoken bound or reject it."""

    if not isinstance(raw, str):
        raise SpeechTextRejected("A spoken answer is required")
    text = raw.strip()
    if not text or len(text) > MAX_SPEECH_CHARACTERS:
        raise SpeechTextRejected("The spoken answer is outside the supported length")
    return text


def synthesis_task(*, workspace_id: UUID, user_id: UUID, ceiling: PolicyRules) -> AITask:
    """One fixed SENSITIVE synthesis task. No fallback, shadow, or context refs."""

    return AITask(
        workspace_id=workspace_id,
        user_id=user_id,
        task_type=TaskType.SYNTHESIZE,
        profile=Profile.SPEECH_SYNTHESIS,
        capability_requirements=frozenset({Capability.SPEECH_SYNTHESIS}),
        context_references=(),
        output_schema=SYNTHESIS_SCHEMA,
        prompt=SYNTHESIS_PROMPT,
        sensitivity=Sensitivity.SENSITIVE,
        latency_class=LatencyClass.INTERACTIVE,
        quality_class=QualityClass.STANDARD,
        max_cost=ceiling.max_cost,
        max_output_tokens=SYNTHESIS_MAX_OUTPUT_TOKENS,
        provider_policy=ProviderPolicy(
            workspace_id=workspace_id,
            user_id=user_id,
            revision=1,
            grants=ceiling.grants,
            allow_fallback=False,
            max_fallbacks=0,
        ),
    )


def _select_eligible_synthesis(
    candidates: list[tuple[ModelDefinition, Decimal]],
    adapters: Mapping[Provider, SpeechAdapter],
) -> tuple[ModelDefinition, Decimal, SpeechSynthesisAdapter] | None:
    """Exactly one installed synthesis-capable adapter. No fallback or shadow."""

    for model, reservation in candidates:
        adapter = adapters.get(model.reference.provider)
        if adapter is None or not isinstance(adapter, SpeechSynthesisAdapter):
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
    """Discard synthesized audio when authority, catalog or budget moved mid-call."""

    await store.authorize(task)
    try:
        current = await store.snapshot(task)
        candidates = rank_eligible_speech(task, current, units=units)
    except (LookupError, ValueError):
        raise SpeechUnavailable("Speech synthesis is no longer qualified") from None
    if current.registry.revision != previous.registry.revision or key not in {
        model_key(model.reference) for model, _ in candidates
    }:
        raise SpeechUnavailable("Speech synthesis authority changed")


def _upstream_failure(detail: ProviderError) -> RuntimeError:
    if detail.code in {
        ErrorCode.AUTHENTICATION,
        ErrorCode.RATE_LIMIT,
        ErrorCode.TIMEOUT,
        ErrorCode.UNAVAILABLE,
    }:
        return SpeechUnavailable("The speech provider is unavailable")
    return SpeechUpstreamFailure("The speech provider returned an unusable result")


async def _trace(store: GatewayStore, run: AITaskRun) -> None:
    await asyncio.shield(store.trace(run))


async def synthesize_speech(
    runtime: GatewayRuntime,
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
    text: str,
    adapters: Mapping[Provider, SpeechAdapter] | None = None,
) -> bytes:
    """Run one bounded speech attempt and return only validated audio bytes."""

    bounded = bounded_speech_text(text)
    store = runtime.store
    task = synthesis_task(workspace_id=workspace_id, user_id=user_id, ceiling=store.operator_policy)
    # Missing membership or a paused account fails closed before any selection.
    await store.authorize(task)
    try:
        snapshot = await store.snapshot(task)
        candidates = rank_eligible_speech(task, snapshot, units=len(bounded))
    except (LookupError, ValueError):
        raise SpeechUnavailable("Speech synthesis is not configured") from None
    selected = _select_eligible_synthesis(
        candidates, adapters if adapters is not None else configured_speech_adapters(settings)
    )
    if selected is None:
        raise SpeechUnavailable("No qualified speech provider is available")
    model, reservation, adapter = selected
    key = model_key(model.reference)

    # The attempt slot is committed under the caller's user row lock before any
    # provider call, so failed attempts count against the same rolling hour.
    run = await store.admit_synthesis(
        task, limit=SYNTHESIS_HOURLY_LIMIT, window=SYNTHESIS_QUOTA_WINDOW
    )
    if run is None:
        raise SpeechQuotaExceeded("Hourly speech synthesis quota is exhausted")
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
        async with asyncio.timeout(timeout_seconds + SYNTHESIS_DEADLINE_SLACK_SECONDS):
            audio = await adapter.synthesize(
                model=model.reference.model,
                text=bounded,
                voice=SPEECH_VOICE,
                timeout_seconds=timeout_seconds,
            )
        if not isinstance(audio, bytes) or not 0 < len(audio) <= MAX_SPEECH_AUDIO_BYTES:
            raise SpeechUpstreamFailure("The speech provider returned an unusable result")
        await store.health(key, None)
        await _reauthorize(store, task, snapshot, key=key, units=len(bounded))
        # Synthesis is priced by character count, so the reservation is the estimate.
        run.estimated_cost = reservation
        run.usage = {}
        status = "COMPLETED"
        return audio
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
        raise _upstream_failure(detail) from None
    finally:
        run.status = status
        run.latency_ms = int((monotonic() - started) * 1000)
        run.finished_at = run.finished_at or datetime.now(UTC)
        await _trace(store, run)
