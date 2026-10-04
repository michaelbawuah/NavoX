"""Hard eligibility gates precede scoring; source text is never a routing input."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import sqrt
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    Contract,
    LatencyClass,
    Profile,
    Provider,
    ProviderGrant,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.foundation.persistence import model_key
from navox.ai.foundation.registry import ModelDefinition, RegistrySnapshot


class RoutingTaskScope(Contract):
    """An optional operator allowlist entry; it can narrow grants, never add them."""

    workspace_id: UUID
    user_id: UUID
    task_type: TaskType
    profile: Profile
    prompt: VersionedRef
    output_schema: VersionedRef
    sensitivity: Sensitivity

    def matches(self, task: AITask) -> bool:
        return all(
            getattr(self, field) == getattr(task, field)
            for field in (
                "workspace_id",
                "user_id",
                "task_type",
                "profile",
                "prompt",
                "output_schema",
                "sensitivity",
            )
        )


class PolicyRules(Contract):
    grants: tuple[ProviderGrant, ...] = ()
    task_scopes: tuple[RoutingTaskScope, ...] | None = Field(default=None, max_length=100)
    # Operator-only rollout; never inferred from a signup or model output.
    allow_personal_conversation: Annotated[bool, Field(strict=True)] = False
    preferred_provider: Provider | None = None
    allow_fallback: bool = False
    max_fallbacks: Annotated[int, Field(strict=True, ge=0, le=3)] = 0
    max_cost: Annotated[Decimal, Field(gt=0, le=10)] = Decimal("0.25")
    allow_shadow: bool = False

    @field_validator("grants")
    @classmethod
    def unique_grants(cls, value: tuple[ProviderGrant, ...]) -> tuple[ProviderGrant, ...]:
        if len({g.provider for g in value}) != len(value):
            raise ValueError("Duplicate provider grant")
        return value


PUBLIC_CONVERSATION_MODEL = "openai:gpt-5.6-luna"


def personal_conversation_rule(
    task: AITask, operator: PolicyRules, *, owns_personal_workspace: bool
) -> PolicyRules | None:
    """Extend only the reviewed ordinary binding, intersecting existing grants.

    The published operator allowlist is left intact. This ephemeral rule admits
    the current principal only; workspace/user denials still intersect it.
    """

    if (
        not operator.allow_personal_conversation
        or not owns_personal_workspace
        or operator.task_scopes is None
        or any(scope.matches(task) for scope in operator.task_scopes)
        or task.task_type != TaskType.REASON
        or task.profile != Profile.ASSISTANT_INTERACTIVE
        or task.prompt != VersionedRef(name="assistant_conversation", version="v1")
        or task.output_schema != VersionedRef(name="assistant_conversation", version="v1")
        or task.sensitivity != Sensitivity.PERSONAL
        or task.capability_requirements
        != frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT})
        or task.context_references
        or task.latency_class != LatencyClass.INTERACTIVE
        or task.quality_class != QualityClass.HIGH
        or task.max_cost > Decimal("0.01")
        or task.max_output_tokens != 1000
        or task.provider_policy.allow_fallback
        or task.provider_policy.max_fallbacks != 0
    ):
        return None
    grants = tuple(
        ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PERSONAL}))
        for grant in operator.grants
        if grant.provider == Provider.OPENAI and Sensitivity.PERSONAL in grant.sensitivities
    )
    scope = RoutingTaskScope(
        workspace_id=task.workspace_id,
        user_id=task.user_id,
        task_type=task.task_type,
        profile=task.profile,
        prompt=task.prompt,
        output_schema=task.output_schema,
        sensitivity=task.sensitivity,
    )
    return operator.model_copy(
        update={
            "task_scopes": (scope,),
            "grants": grants,
            "max_cost": min(operator.max_cost, Decimal("0.01")),
            "allow_fallback": False,
            "max_fallbacks": 0,
            "allow_shadow": False,
        }
    )


class UserPreferences(Contract):
    preferred_provider: Provider | None = None
    allow_fallback: bool = False


def preference_scope_key(user_id: UUID) -> str:
    return f"preference:{user_id}"


class EvaluationEvidence(Contract):
    profile: Profile
    task_type: TaskType
    prompt: VersionedRef
    output_schema: VersionedRef
    quality: Annotated[float, Field(ge=0, le=1)]
    reliability: Annotated[float, Field(ge=0, le=1)]
    p95_latency_ms: Annotated[int, Field(strict=True, ge=0)]
    samples: Annotated[int, Field(strict=True, ge=1)]
    safety_passed: bool
    evaluated_at: datetime
    corpus_version: Annotated[str, Field(min_length=1, max_length=128)]
    review_artifact_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None

    @field_validator("evaluated_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Evaluation timestamp must be timezone aware")
        return value.astimezone(UTC)


class RoutingWeights(Contract):
    quality: Annotated[float, Field(gt=0, le=1)] = 0.35
    reliability: Annotated[float, Field(gt=0, le=1)] = 0.20
    latency: Annotated[float, Field(ge=0, le=1)] = 0.15
    cost: Annotated[float, Field(ge=0, le=1)] = 0.15
    preference: Annotated[float, Field(ge=0, le=1)] = 0.05
    freshness: Annotated[float, Field(ge=0, le=1)] = 0.10
    uncertainty: Annotated[float, Field(ge=0, le=1)] = 0.10


def intersect_policy(task: AITask, rules: tuple[PolicyRules, ...]) -> ProviderPolicy:
    scope_permitted = all(
        rule.task_scopes is None or any(scope.matches(task) for scope in rule.task_scopes)
        for rule in rules
    )
    grants = []
    for provider in Provider:
        allowed = set(Sensitivity)
        for policies in (task.provider_policy.grants, *(r.grants for r in rules)):
            allowed &= next(
                (set(p.sensitivities) for p in policies if p.provider == provider), set()
            )
        if allowed and scope_permitted:
            grants.append(ProviderGrant(provider=provider, sensitivities=frozenset(allowed)))
    fallback = task.provider_policy.allow_fallback and all(r.allow_fallback for r in rules)
    return ProviderPolicy(
        workspace_id=task.workspace_id,
        user_id=task.user_id,
        revision=task.provider_policy.revision,
        grants=tuple(grants),
        allow_fallback=fallback,
        max_fallbacks=min(task.provider_policy.max_fallbacks, *(r.max_fallbacks for r in rules))
        if fallback
        else 0,
    )


def reserve_cost(model: ModelDefinition, input_bound: int, output_bound: int) -> Decimal | None:
    if model.input_cost_per_million is None or model.output_cost_per_million is None:
        return None
    return (
        model.input_cost_per_million * input_bound + model.output_cost_per_million * output_bound
    ) / Decimal(1_000_000)


def rank_eligible(
    task: AITask,
    registry: RegistrySnapshot,
    *,
    policy: ProviderPolicy,
    available: frozenset[str],
    evaluations: dict[str, EvaluationEvidence],
    input_bound: int,
    remaining_budget: Decimal,
    preferred: Provider | None,
    weights: RoutingWeights,
    now: datetime | None = None,
) -> list[tuple[ModelDefinition, Decimal]]:
    now = now or datetime.now(UTC)
    binding = registry.bind_task(task)
    candidates = []
    for reference in binding.profile.assignments:
        model = registry.get_model(reference)
        key = model_key(reference)
        evidence = evaluations.get(key)
        if (
            not model.enabled
            or key not in available
            or evidence is None
            or evidence.profile != task.profile
            or evidence.task_type != task.task_type
            or evidence.prompt != task.prompt
            or evidence.output_schema != task.output_schema
        ):
            continue
        if not task.capability_requirements.issubset(model.capabilities):
            continue
        if task.sensitivity not in model.allowed_sensitivities or not policy.permits(
            reference.provider, task.sensitivity
        ):
            continue
        age = now - evidence.evaluated_at
        threshold = 0.90 if task.quality_class == QualityClass.HIGH else 0.80
        if (
            not evidence.safety_passed
            or evidence.samples < 10
            or evidence.quality < threshold
            or evidence.reliability < 0.95
            or not timedelta(0) <= age <= timedelta(days=30)
        ):
            continue
        if (
            input_bound + task.max_output_tokens > model.context_window
            or task.max_output_tokens > model.max_output_tokens
        ):
            continue
        reservation = reserve_cost(model, input_bound, task.max_output_tokens)
        if reservation is None or reservation > remaining_budget:
            continue
        latency_target = {
            LatencyClass.INTERACTIVE: 3000,
            LatencyClass.BACKGROUND: 60_000,
            LatencyClass.BATCH: 300_000,
        }[task.latency_class]
        latency_fit = max(0.0, 1 - evidence.p95_latency_ms / latency_target)
        cost_fit = 1.0 - float(reservation / remaining_budget) if remaining_budget else 0.0
        score = (
            evidence.quality * weights.quality
            + evidence.reliability * weights.reliability
            + latency_fit * weights.latency
            + cost_fit * weights.cost
            + (reference.provider == preferred) * weights.preference
            + (1 - age.total_seconds() / timedelta(days=30).total_seconds()) * weights.freshness
            - weights.uncertainty / sqrt(evidence.samples)
        )
        candidates.append((score, key, model, reservation))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return [(row[2], row[3]) for row in candidates]
