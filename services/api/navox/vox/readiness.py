"""Read-only voice policy preflight; never spends quota or contacts a provider.

This is a conservative admission check, not an execution or a model evaluation.
Actual turns still reauthorize through the ordinary gateway. Off-record voice
does not inherit the separate entitlement for retained conversation sessions.
"""

import asyncio
from dataclasses import replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.configured import build_runtime
from navox.ai.conversation import (
    MAX_CONVERSATION_HISTORY,
    MAX_CONVERSATION_UTTERANCE,
)
from navox.ai.factory import AIProviderNotConfigured
from navox.ai.foundation.contracts import AITask, ProviderPolicy
from navox.ai.routing import rank_eligible
from navox.ai.speech_adapters import configured_speech_adapters
from navox.ai.speech_routing import rank_eligible_speech
from navox.ai.speech_service import MAX_AUDIO_MILLISECONDS, _select_eligible, transcription_task
from navox.ai.speech_synthesis import (
    MAX_SPEECH_CHARACTERS,
    _select_eligible_synthesis,
    synthesis_task,
)
from navox.ai.store import RoutingSnapshot
from navox.ai.validation import compile_schema
from navox.core.settings import Settings
from navox.db.ai_registry import AIProfileAssignment, AIProviderHealth
from navox.vox.conversation import spoken_answer_task
from navox.vox.provider import MediaUnavailable


async def _stable_snapshot(
    factory: async_sessionmaker[AsyncSession], task: AITask, snapshot: RoutingSnapshot
) -> RoutingSnapshot:
    """A new worker task must not depend on a lucky random rollout bucket or probe."""
    async with factory() as database:
        stable = frozenset(
            await database.scalars(
                select(AIProfileAssignment.model_id)
                .join(AIProviderHealth, AIProviderHealth.model_id == AIProfileAssignment.model_id)
                .where(
                    AIProfileAssignment.profile == task.profile.value,
                    AIProfileAssignment.rollout_percent == 100,
                    AIProfileAssignment.shadow_enabled.is_(False),
                    AIProviderHealth.status == "HEALTHY",
                )
            )
        )
    return replace(snapshot, available=snapshot.available & stable)


def _conversation_input_bound(task: AITask, snapshot: RoutingSnapshot) -> int:
    binding = snapshot.registry.bind_task(task)
    compile_schema(binding.output_schema.document)
    # JSON may escape a character as six bytes (e.g. a control character). The
    # actual worker bounds each preceding answer to the 600-character speech
    # ceiling. Fixed JSON field/structure overhead is below 256 bytes. The other
    # terms mirror GatewayRuntime's published prompt/schema plus envelope bound.
    characters = MAX_CONVERSATION_UTTERANCE + MAX_CONVERSATION_HISTORY * (
        MAX_CONVERSATION_UTTERANCE + MAX_SPEECH_CHARACTERS
    )
    return (
        characters * 6
        + 256
        + len(binding.prompt.instructions.encode())
        + len(binding.output_schema.document.text.encode())
        + 2048
    )


async def require_voice_models(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> None:
    """Require all three real gateway paths before allocating a media intent.

    Only reads, local task descriptions and pure selection are permitted here.
    It does not execute prompts, reserve provider health probes, consume speech
    attempts, create traces or publish/qualify any registry entry.
    """
    try:
        async with asyncio.timeout(5):
            runtime = await build_runtime(settings, factory)
            ceiling = runtime.store.operator_policy
            conversation = spoken_answer_task(
                workspace_id=workspace_id,
                user_id=user_id,
                max_cost=ceiling.max_cost,
                policy=ProviderPolicy(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    revision=1,
                    grants=ceiling.grants,
                    allow_fallback=False,
                    max_fallbacks=0,
                ),
            )
            transcription = transcription_task(
                workspace_id=workspace_id, user_id=user_id, ceiling=ceiling
            )
            synthesis = synthesis_task(workspace_id=workspace_id, user_id=user_id, ceiling=ceiling)
            snapshots = []
            for task in (transcription, conversation, synthesis):
                await runtime.store.authorize(task)
                snapshot = await runtime.store.snapshot(task)
                snapshot = await _stable_snapshot(factory, task, snapshot)
                # The actual text runtime can schedule shadow traffic when all
                # policy layers permit it. Voice admission must remain primary
                # only; this check narrows eligibility and never widens policy.
                if all(rule.allow_shadow for rule in snapshot.rules):
                    raise MediaUnavailable("Vox model policy is not available")
                snapshots.append(snapshot)

            speech_adapters = configured_speech_adapters(settings)
            if (
                _select_eligible(
                    rank_eligible_speech(transcription, snapshots[0], units=MAX_AUDIO_MILLISECONDS),
                    speech_adapters,
                )
                is None
                or _select_eligible_synthesis(
                    rank_eligible_speech(synthesis, snapshots[2], units=MAX_SPEECH_CHARACTERS),
                    speech_adapters,
                )
                is None
            ):
                raise MediaUnavailable("Vox model policy is not available")
            snapshot = snapshots[1]
            preferred = next(
                (
                    rule.preferred_provider
                    for rule in reversed(snapshot.rules)
                    if rule.preferred_provider is not None
                ),
                conversation.preferred_provider,
            )
            candidates = rank_eligible(
                conversation,
                snapshot.registry,
                policy=snapshot.policy,
                available=snapshot.available,
                evaluations=snapshot.evaluations,
                input_bound=_conversation_input_bound(conversation, snapshot),
                remaining_budget=min(
                    conversation.max_cost, *(rule.max_cost for rule in snapshot.rules)
                ),
                preferred=preferred,
                weights=snapshot.weights,
            )
            for model, _ in candidates:
                adapter = runtime.adapters.get(model.reference.provider)
                if adapter is None:
                    continue
                try:
                    if conversation.capability_requirements.issubset(
                        adapter.capabilities(model.reference.model)
                    ):
                        return
                except Exception:
                    continue
            raise MediaUnavailable("Vox model policy is not available")
    except (
        AIProviderNotConfigured,
        LookupError,
        ValueError,
        PermissionError,
        SQLAlchemyError,
        TimeoutError,
    ):
        raise MediaUnavailable("Vox model policy is not available") from None
