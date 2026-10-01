"""Fail-closed audio qualification for SPEC-005. Pure selection; no provider calls.

Text/embedding registration, credentials, token pricing, and PERSONAL grants never
qualify a speech task here. Voice input and spoken answers are SENSITIVE in this
phase, so the task itself must declare that class, and the candidate model and every
operator/workspace/user policy must permit it. Transcription and synthesis bind only
to their published profiles. Nothing here reserves traffic, shadow-runs, or falls
back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    Profile,
    Provider,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import model_key
from navox.ai.foundation.registry import ModelDefinition
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.store import RoutingSnapshot

MILLISECONDS_PER_MINUTE: Final[Decimal] = Decimal(60_000)
CHARACTERS_PER_PRICE_UNIT: Final[Decimal] = Decimal(1_000)
MAX_EVIDENCE_AGE: Final[timedelta] = timedelta(days=30)
MINIMUM_SAMPLES: Final[int] = 10
MINIMUM_RELIABILITY: Final[float] = 0.95
# Mirrors the existing SPEC-005 text thresholds; audio does not relax them.
QUALITY_THRESHOLD: Final[dict[QualityClass, float]] = {
    QualityClass.STANDARD: 0.80,
    QualityClass.HIGH: 0.90,
}
REQUIRED_AUDIO_SENSITIVITY: Final[Sensitivity] = Sensitivity.SENSITIVE
AUDIO_CAPABILITY: Final[dict[TaskType, Capability]] = {
    TaskType.TRANSCRIBE: Capability.TRANSCRIPTION,
    TaskType.SYNTHESIZE: Capability.SPEECH_SYNTHESIS,
}
AUDIO_PROFILE: Final[dict[TaskType, Profile]] = {
    TaskType.TRANSCRIBE: Profile.SPEECH_TRANSCRIPTION,
    TaskType.SYNTHESIZE: Profile.SPEECH_SYNTHESIS,
}
AUDIO_PRICE_UNIT: Final[dict[TaskType, Decimal]] = {
    TaskType.TRANSCRIBE: MILLISECONDS_PER_MINUTE,
    TaskType.SYNTHESIZE: CHARACTERS_PER_PRICE_UNIT,
}


def _positive_bound(units: int) -> int:
    if isinstance(units, bool) or not isinstance(units, int) or units <= 0:
        raise ValueError("Audio unit bound must be a positive integer")
    return units


def reserve_speech_cost(model: ModelDefinition, task_type: TaskType, units: int) -> Decimal:
    """Decimal reservation for one bounded audio request.

    Transcription takes a millisecond duration charged against a minute;
    synthesis takes a character count charged per 1000 characters. Token prices
    are never consulted, and a missing or wrong-mode audio price is a rejection.
    """

    if task_type not in AUDIO_CAPABILITY:
        raise ValueError("Speech reservation requires an audio task type")
    bound = _positive_bound(units)
    price = (
        model.transcription_cost_per_minute
        if task_type is TaskType.TRANSCRIBE
        else model.synthesis_cost_per_1000_characters
    )
    if price is None:
        raise ValueError("Speech pricing is not configured for this model and task type")
    return price * Decimal(bound) / AUDIO_PRICE_UNIT[task_type]


def speech_budget_ceiling(task: AITask, rules: tuple[PolicyRules, ...]) -> Decimal:
    """Request budget intersected with every operator/workspace/user ceiling."""

    if not rules:
        return task.max_cost
    return min(task.max_cost, *(rule.max_cost for rule in rules))


def _permits_sensitivity(
    model: ModelDefinition,
    policy: ProviderPolicy,
    provider: Provider,
) -> bool:
    """Model allowance plus the effective intersect of every policy layer."""

    if REQUIRED_AUDIO_SENSITIVITY not in model.allowed_sensitivities:
        return False
    return policy.permits(provider, REQUIRED_AUDIO_SENSITIVITY)


def _qualified_evidence(
    evidence: EvaluationEvidence, task: AITask, threshold: float, moment: datetime
) -> bool:
    """Exact fresh task/profile/prompt/schema evidence under SPEC-005 thresholds."""

    age = moment - evidence.evaluated_at
    return (
        evidence.profile == task.profile
        and evidence.task_type == task.task_type
        and evidence.prompt == task.prompt
        and evidence.output_schema == task.output_schema
        and evidence.samples >= MINIMUM_SAMPLES
        and evidence.quality >= threshold
        and evidence.reliability >= MINIMUM_RELIABILITY
        and evidence.safety_passed
        and timedelta(0) <= age <= MAX_EVIDENCE_AGE
    )


def rank_eligible_speech(
    task: AITask,
    snapshot: RoutingSnapshot,
    *,
    units: int,
    now: datetime | None = None,
) -> list[tuple[ModelDefinition, Decimal]]:
    """Deterministic qualified audio candidates with their reservation.

    Every gate is default-deny: published binding, matching audio capability,
    exact task/profile pairing, a SENSITIVE task, enabled model with live rollout/
    health availability, fresh exact evaluation, SENSITIVE model allowance plus
    effective provider grants, configured audio pricing, and a reservation inside
    the request and policy ceilings. The cheapest reservation sorts first and the
    model key breaks ties.
    """

    if task.task_type not in AUDIO_CAPABILITY:
        raise ValueError("Speech selection requires an audio task type")
    _positive_bound(units)
    moment = now or datetime.now(UTC)
    binding = snapshot.registry.bind_task(task)
    if task.sensitivity is not REQUIRED_AUDIO_SENSITIVITY:
        return []
    if task.profile is not AUDIO_PROFILE[task.task_type]:
        return []
    capability = AUDIO_CAPABILITY[task.task_type]
    ceiling = speech_budget_ceiling(task, snapshot.rules)
    threshold = QUALITY_THRESHOLD[task.quality_class]
    candidates: list[tuple[Decimal, str, ModelDefinition]] = []
    for reference in binding.profile.assignments:
        model = snapshot.registry.get_model(reference)
        key = model_key(reference)
        if not model.enabled or key not in snapshot.available:
            continue
        if capability not in binding.profile.required_capabilities:
            continue
        if capability not in task.capability_requirements:
            continue
        if not task.capability_requirements.issubset(model.capabilities):
            continue
        if not _permits_sensitivity(model, snapshot.policy, reference.provider):
            continue
        evidence = snapshot.evaluations.get(key)
        if evidence is None or not _qualified_evidence(evidence, task, threshold, moment):
            continue
        try:
            reservation = reserve_speech_cost(model, task.task_type, units)
        except ValueError:
            continue
        if reservation > ceiling:
            continue
        candidates.append((reservation, key, model))
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    return [(model, reservation) for reservation, _, model in candidates]
