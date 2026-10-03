"""M11A audio qualification: offline, provider-neutral, default-deny regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from navox.ai.catalog import catalog_template
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    JSONDocument,
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
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.foundation.registry import (
    ModelDefinition,
    ModelRef,
    ProfileDefinition,
    PromptDefinition,
    RegistrySnapshot,
    SchemaDefinition,
)
from navox.ai.routing import (
    EvaluationEvidence,
    PolicyRules,
    RoutingWeights,
    intersect_policy,
)
from navox.ai.speech_routing import rank_eligible_speech, reserve_speech_cost
from navox.ai.store import RoutingSnapshot
from navox.db.ai_registry import AIRegistryRevision, AIRegistryState

WORKSPACE = UUID("00000000-0000-4000-8000-000000000011")
USER = UUID("00000000-0000-4000-8000-000000000012")
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

TRANSCRIPTION_PROMPT = VersionedRef(name="speech_transcription_prompt", version="v1")
TRANSCRIPTION_SCHEMA = VersionedRef(name="speech_transcription", version="v1")
SYNTHESIS_PROMPT = VersionedRef(name="speech_synthesis_prompt", version="v1")
SYNTHESIS_SCHEMA = VersionedRef(name="speech_synthesis", version="v1")

TRANSCRIBE_MODEL = ModelRef(provider=Provider.OPENAI, model="fixture-transcribe-not-live")
SYNTHESIZE_MODEL = ModelRef(provider=Provider.OPENAI, model="fixture-synthesize-not-live")
TEXT_MODEL = ModelRef(provider=Provider.GEMINI, model="fixture-text-not-live")

TRANSCRIPTION_PRICE = Decimal("0.006")
SYNTHESIS_PRICE = Decimal("0.015")


def transcription_model(**changes: object) -> ModelDefinition:
    values: dict[str, object] = {
        "reference": TRANSCRIBE_MODEL,
        "capabilities": frozenset({Capability.TRANSCRIPTION}),
        "allowed_sensitivities": frozenset({Sensitivity.SENSITIVE}),
        "enabled": True,
        "context_window": 8000,
        "max_output_tokens": 2000,
        "transcription_cost_per_minute": TRANSCRIPTION_PRICE,
    }
    values.update(changes)
    return ModelDefinition.model_validate(values)


def synthesis_model(**changes: object) -> ModelDefinition:
    values: dict[str, object] = {
        "reference": SYNTHESIZE_MODEL,
        "capabilities": frozenset({Capability.SPEECH_SYNTHESIS}),
        "allowed_sensitivities": frozenset({Sensitivity.SENSITIVE}),
        "enabled": True,
        "context_window": 8000,
        "max_output_tokens": 2000,
        "synthesis_cost_per_1000_characters": SYNTHESIS_PRICE,
    }
    values.update(changes)
    return ModelDefinition.model_validate(values)


def transcription_profile(**changes: object) -> ProfileDefinition:
    values: dict[str, object] = {
        "profile": Profile.SPEECH_TRANSCRIPTION,
        "task_types": frozenset({TaskType.TRANSCRIBE}),
        "required_capabilities": frozenset({Capability.TRANSCRIPTION}),
        "assignments": (TRANSCRIBE_MODEL,),
    }
    values.update(changes)
    return ProfileDefinition.model_validate(values)


def synthesis_profile(**changes: object) -> ProfileDefinition:
    values: dict[str, object] = {
        "profile": Profile.SPEECH_SYNTHESIS,
        "task_types": frozenset({TaskType.SYNTHESIZE}),
        "required_capabilities": frozenset({Capability.SPEECH_SYNTHESIS}),
        "assignments": (SYNTHESIZE_MODEL,),
    }
    values.update(changes)
    return ProfileDefinition.model_validate(values)


def make_registry(**changes: object) -> RegistrySnapshot:
    values: dict[str, object] = {
        "revision": 1,
        "models": (transcription_model(), synthesis_model()),
        "profiles": (transcription_profile(), synthesis_profile()),
        "schemas": (
            SchemaDefinition(reference=TRANSCRIPTION_SCHEMA, document=JSONDocument(text="{}")),
            SchemaDefinition(reference=SYNTHESIS_SCHEMA, document=JSONDocument(text="{}")),
        ),
        "prompts": (
            PromptDefinition(
                reference=TRANSCRIPTION_PROMPT,
                output_schema=TRANSCRIPTION_SCHEMA,
                instructions="Synthetic transcription fixture prompt, not a production prompt.",
            ),
            PromptDefinition(
                reference=SYNTHESIS_PROMPT,
                output_schema=SYNTHESIS_SCHEMA,
                instructions="Synthetic synthesis fixture prompt, not a production prompt.",
            ),
        ),
    }
    values.update(changes)
    return RegistrySnapshot.model_validate(values)


def make_policy(**changes: object) -> ProviderPolicy:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "user_id": USER,
        "revision": 1,
        "grants": (ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
    }
    values.update(changes)
    return ProviderPolicy.model_validate(values)


def make_task(task_type: TaskType = TaskType.TRANSCRIBE, **changes: object) -> AITask:
    transcribing = task_type is TaskType.TRANSCRIBE
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "user_id": USER,
        "task_type": task_type,
        "profile": Profile.SPEECH_TRANSCRIPTION if transcribing else Profile.SPEECH_SYNTHESIS,
        "capability_requirements": frozenset(
            {Capability.TRANSCRIPTION if transcribing else Capability.SPEECH_SYNTHESIS}
        ),
        "output_schema": TRANSCRIPTION_SCHEMA if transcribing else SYNTHESIS_SCHEMA,
        "prompt": TRANSCRIPTION_PROMPT if transcribing else SYNTHESIS_PROMPT,
        "sensitivity": Sensitivity.SENSITIVE,
        "latency_class": LatencyClass.INTERACTIVE,
        "quality_class": QualityClass.STANDARD,
        "max_cost": Decimal("5"),
        "max_output_tokens": 2000,
        "provider_policy": make_policy(),
    }
    values.update(changes)
    return AITask.model_validate(values)


def make_evidence(task: AITask, **changes: object) -> EvaluationEvidence:
    values: dict[str, object] = {
        "profile": task.profile,
        "task_type": task.task_type,
        "prompt": task.prompt,
        "output_schema": task.output_schema,
        "quality": 0.95,
        "reliability": 0.97,
        "p95_latency_ms": 900,
        "samples": 25,
        "safety_passed": True,
        "evaluated_at": NOW,
        "corpus_version": "audio-corpus-v1",
    }
    values.update(changes)
    return EvaluationEvidence.model_validate(values)


def default_rules(**changes: object) -> tuple[PolicyRules, ...]:
    values: dict[str, object] = {
        "grants": (ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
        "max_cost": Decimal("5"),
    }
    values.update(changes)
    return (PolicyRules.model_validate(values),)


def make_snapshot(
    task: AITask,
    *,
    registry: RegistrySnapshot | None = None,
    policy: ProviderPolicy | None = None,
    rules: tuple[PolicyRules, ...] | None = None,
    evaluations: dict[str, EvaluationEvidence] | None = None,
    available: frozenset[str] | None = None,
) -> RoutingSnapshot:
    return RoutingSnapshot(
        registry=registry if registry is not None else make_registry(),
        policy=policy if policy is not None else task.provider_policy,
        rules=rules if rules is not None else default_rules(),
        available=available if available is not None else frozenset(evaluations or ()),
        evaluations=evaluations if evaluations is not None else {},
        weights=RoutingWeights(),
    )


def qualified_snapshot(task: AITask, **changes: object) -> RoutingSnapshot:
    """Snapshot where the assigned audio model clears every gate for ``task``."""

    key = model_key(TRANSCRIBE_MODEL if task.task_type is TaskType.TRANSCRIBE else SYNTHESIZE_MODEL)
    changes.setdefault("evaluations", {key: make_evidence(task)})
    changes.setdefault("available", frozenset({key}))
    return make_snapshot(task, **changes)


LEGACY_SNAPSHOT: dict[str, object] = {
    "revision": 7,
    "models": [
        {
            "reference": {"provider": "openai", "model": "legacy-text-model"},
            "capabilities": ["structured_output", "text"],
            "allowed_sensitivities": ["PERSONAL"],
            "enabled": True,
            "context_window": 8000,
            "max_output_tokens": 2000,
            "input_cost_per_million": "1.5",
            "output_cost_per_million": "4.5",
        }
    ],
    "profiles": [
        {
            "profile": "EXTRACTION_FAST",
            "task_types": ["classify", "extract"],
            "required_capabilities": ["structured_output", "text"],
            "assignments": [{"provider": "openai", "model": "legacy-text-model"}],
        }
    ],
    "schemas": [
        {
            "reference": {"name": "legacy_extraction", "version": "v1"},
            "document": {"text": '{"type":"object"}'},
        }
    ],
    "prompts": [
        {
            "reference": {"name": "legacy_extraction_prompt", "version": "v1"},
            "output_schema": {"name": "legacy_extraction", "version": "v1"},
            "instructions": "Legacy snapshot fixture instructions.",
        }
    ],
}


def test_historical_snapshot_keeps_exact_canonical_json_and_digest() -> None:
    legacy = json.dumps(LEGACY_SNAPSHOT, sort_keys=True, separators=(",", ":"))
    assert "transcription_cost_per_minute" not in legacy
    assert "synthesis_cost_per_1000_characters" not in legacy

    snapshot = RegistrySnapshot.model_validate_json(legacy)

    assert canonical(snapshot) == legacy
    assert digest(canonical(snapshot)) == digest(legacy)
    model = snapshot.models[0]
    assert model.transcription_cost_per_minute is None
    assert model.synthesis_cost_per_1000_characters is None
    assert model.output_cost_per_million == Decimal("4.5")


@pytest.mark.asyncio
async def test_legacy_registry_revision_still_loads_without_conflict(ai_database) -> None:
    """A stored pre-M11 row must survive the new fields and digest checks."""

    legacy = json.dumps(LEGACY_SNAPSHOT, sort_keys=True, separators=(",", ":"))
    async with ai_database() as database:
        database.add(AIRegistryRevision(revision=7, snapshot=legacy, digest=digest(legacy)))
        database.add(AIRegistryState(id=1, revision=7))
        await database.commit()
    async with ai_database() as database:
        snapshot = await RegistryStore(database).load()
        assert snapshot is not None
        assert snapshot.revision == 7
        assert canonical(snapshot) == legacy


def test_audio_prices_serialize_only_when_set() -> None:
    payload = json.loads(canonical(transcription_model()))
    assert payload["transcription_cost_per_minute"] == "0.006"
    assert "synthesis_cost_per_1000_characters" not in payload

    both = transcription_model(
        capabilities=frozenset({Capability.TRANSCRIPTION, Capability.SPEECH_SYNTHESIS}),
        synthesis_cost_per_1000_characters=SYNTHESIS_PRICE,
    )
    assert json.loads(canonical(both))["synthesis_cost_per_1000_characters"] == "0.015"


@pytest.mark.parametrize("price", ["0", "-0.5", "NaN", "Infinity", "1000001"])
def test_audio_prices_must_be_positive_and_bounded(price: str) -> None:
    with pytest.raises(ValidationError):
        transcription_model(transcription_cost_per_minute=Decimal(price))
    with pytest.raises(ValidationError):
        synthesis_model(synthesis_cost_per_1000_characters=Decimal(price))


def test_transcription_reservation_is_proportional_to_minutes() -> None:
    model = transcription_model(transcription_cost_per_minute=Decimal("0.006"))
    assert reserve_speech_cost(model, TaskType.TRANSCRIBE, 60_000) == Decimal("0.006")
    assert reserve_speech_cost(model, TaskType.TRANSCRIBE, 30_000) == Decimal("0.003")
    assert reserve_speech_cost(model, TaskType.TRANSCRIBE, 1_000) == Decimal("0.0001")


def test_synthesis_reservation_is_proportional_to_characters() -> None:
    model = synthesis_model(synthesis_cost_per_1000_characters=Decimal("0.015"))
    assert reserve_speech_cost(model, TaskType.SYNTHESIZE, 1_000) == Decimal("0.015")
    assert reserve_speech_cost(model, TaskType.SYNTHESIZE, 2_000) == Decimal("0.03")
    assert reserve_speech_cost(model, TaskType.SYNTHESIZE, 250) == Decimal("0.00375")


def test_reservation_never_uses_token_prices() -> None:
    model = transcription_model(
        input_cost_per_million=Decimal("1000"), output_cost_per_million=Decimal("2000")
    )
    assert reserve_speech_cost(model, TaskType.TRANSCRIBE, 60_000) == Decimal("0.006")


def test_reservation_rejects_missing_and_wrong_mode_prices() -> None:
    unpriced = transcription_model(transcription_cost_per_minute=None)
    with pytest.raises(ValueError, match="not configured"):
        reserve_speech_cost(unpriced, TaskType.TRANSCRIBE, 1_000)
    with pytest.raises(ValueError, match="not configured"):
        reserve_speech_cost(unpriced, TaskType.SYNTHESIZE, 1_000)
    transcription_only = transcription_model()
    with pytest.raises(ValueError, match="not configured"):
        reserve_speech_cost(transcription_only, TaskType.SYNTHESIZE, 1_000)
    synthesis_only = synthesis_model()
    with pytest.raises(ValueError, match="not configured"):
        reserve_speech_cost(synthesis_only, TaskType.TRANSCRIBE, 1_000)


def test_reservation_rejects_non_audio_task_type() -> None:
    for task_type in (TaskType.EXTRACT, TaskType.EMBED, TaskType.DRAFT_COMMUNICATION):
        with pytest.raises(ValueError, match="audio task type"):
            reserve_speech_cost(transcription_model(), task_type, 1_000)


@pytest.mark.parametrize("units", [0, -1, True])
def test_reservation_rejects_nonpositive_units(units: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        reserve_speech_cost(transcription_model(), TaskType.TRANSCRIBE, units)  # type: ignore[arg-type]


def test_default_catalog_publishes_no_speech_profile_and_denies_speech() -> None:
    catalog = catalog_template()
    assert {item.profile for item in catalog.profiles}.isdisjoint(
        {Profile.SPEECH_TRANSCRIPTION, Profile.SPEECH_SYNTHESIS}
    )
    task = make_task()
    with pytest.raises(LookupError):
        rank_eligible_speech(task, make_snapshot(task, registry=catalog), units=30_000, now=NOW)


def test_qualified_transcription_and_synthesis_models_are_selected() -> None:
    transcription = make_task()
    candidates = rank_eligible_speech(
        transcription, qualified_snapshot(transcription), units=30_000, now=NOW
    )
    assert [(model.reference, cost) for model, cost in candidates] == [
        (TRANSCRIBE_MODEL, Decimal("0.003"))
    ]

    synthesis = make_task(TaskType.SYNTHESIZE)
    candidates = rank_eligible_speech(
        synthesis, qualified_snapshot(synthesis), units=2_000, now=NOW
    )
    assert [(model.reference, cost) for model, cost in candidates] == [
        (SYNTHESIZE_MODEL, Decimal("0.03"))
    ]


def test_unpriced_model_is_rejected() -> None:
    task = make_task()
    registry = make_registry(
        models=(transcription_model(transcription_cost_per_minute=None), synthesis_model())
    )
    snapshot = qualified_snapshot(task, registry=registry)
    assert rank_eligible_speech(task, snapshot, units=30_000, now=NOW) == []


def test_text_registration_and_token_prices_cannot_qualify() -> None:
    text_model = ModelDefinition(
        reference=TEXT_MODEL,
        capabilities=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
        allowed_sensitivities=frozenset(Sensitivity),
        enabled=True,
        context_window=8000,
        max_output_tokens=2000,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    )
    with pytest.raises(ValidationError, match="required profile capability"):
        make_registry(
            models=(text_model, synthesis_model()),
            profiles=(transcription_profile(assignments=(TEXT_MODEL,)), synthesis_profile()),
        )

    registry = make_registry(
        models=(text_model, synthesis_model()),
        profiles=(
            transcription_profile(
                required_capabilities=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
                assignments=(TEXT_MODEL,),
            ),
            synthesis_profile(),
        ),
    )
    task = make_task(
        capability_requirements=frozenset(
            {Capability.TRANSCRIPTION, Capability.TEXT, Capability.STRUCTURED_OUTPUT}
        )
    )
    snapshot = make_snapshot(
        task,
        registry=registry,
        evaluations={model_key(TEXT_MODEL): make_evidence(task)},
        available=frozenset({model_key(TEXT_MODEL)}),
    )
    assert rank_eligible_speech(task, snapshot, units=30_000, now=NOW) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": Profile.SPEECH_SYNTHESIS},
        {"task_type": TaskType.EXTRACT},
        {"prompt": VersionedRef(name="other_prompt", version="v1")},
        {"output_schema": VersionedRef(name="other_schema", version="v1")},
        {"quality": 0.79},
        {"reliability": 0.94},
        {"safety_passed": False},
        {"samples": 9},
        {"evaluated_at": NOW - timedelta(days=31)},
        {"evaluated_at": NOW + timedelta(seconds=1)},
    ],
)
def test_unqualified_or_stale_evidence_is_rejected(changes: dict[str, object]) -> None:
    task = make_task()
    key = model_key(TRANSCRIBE_MODEL)
    snapshot = qualified_snapshot(task, evaluations={key: make_evidence(task, **changes)})
    assert rank_eligible_speech(task, snapshot, units=30_000, now=NOW) == []


def test_high_quality_class_uses_the_stricter_threshold() -> None:
    strict = make_task(quality_class=QualityClass.HIGH)
    key = model_key(TRANSCRIBE_MODEL)
    weak = qualified_snapshot(strict, evaluations={key: make_evidence(strict, quality=0.85)})
    assert rank_eligible_speech(strict, weak, units=30_000, now=NOW) == []

    standard = make_task(quality_class=QualityClass.STANDARD)
    same = qualified_snapshot(standard, evaluations={key: make_evidence(standard, quality=0.85)})
    assert [
        model.reference for model, _ in rank_eligible_speech(standard, same, units=30_000, now=NOW)
    ] == [TRANSCRIBE_MODEL]


def test_disabled_model_and_absent_rollout_health_are_rejected() -> None:
    task = make_task()
    disabled = qualified_snapshot(
        task, registry=make_registry(models=(transcription_model(enabled=False), synthesis_model()))
    )
    assert rank_eligible_speech(task, disabled, units=30_000, now=NOW) == []

    unhealthy = qualified_snapshot(task, available=frozenset())
    assert rank_eligible_speech(task, unhealthy, units=30_000, now=NOW) == []

    unassigned = qualified_snapshot(
        task,
        registry=make_registry(
            profiles=(transcription_profile(assignments=()), synthesis_profile())
        ),
    )
    assert rank_eligible_speech(task, unassigned, units=30_000, now=NOW) == []


def test_sensitive_requires_model_allowance_and_every_policy_grant() -> None:
    task = make_task()
    personal_model = qualified_snapshot(
        task,
        registry=make_registry(
            models=(
                transcription_model(allowed_sensitivities=frozenset({Sensitivity.PERSONAL})),
                synthesis_model(),
            )
        ),
    )
    assert rank_eligible_speech(task, personal_model, units=30_000, now=NOW) == []

    personal_policy = make_policy(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities={Sensitivity.PERSONAL}),)
    )
    personal_grant = qualified_snapshot(task, policy=personal_policy)
    assert rank_eligible_speech(task, personal_grant, units=30_000, now=NOW) == []

    personal_grant_policy = make_policy(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities={Sensitivity.PERSONAL}),)
    )
    sensitive_task = make_task(provider_policy=personal_grant_policy)
    effective = intersect_policy(sensitive_task, default_rules())
    assert not effective.permits(Provider.OPENAI, Sensitivity.SENSITIVE)
    intersected = qualified_snapshot(
        sensitive_task,
        registry=make_registry(
            models=(
                transcription_model(
                    allowed_sensitivities=frozenset({Sensitivity.PERSONAL, Sensitivity.SENSITIVE})
                ),
                synthesis_model(),
            )
        ),
        policy=effective,
    )
    assert rank_eligible_speech(sensitive_task, intersected, units=30_000, now=NOW) == []


def test_personal_speech_task_is_denied_even_with_every_grant() -> None:
    """Voice input and spoken answers must declare SENSITIVE in this phase."""

    task = make_task(sensitivity=Sensitivity.PERSONAL)
    registry = make_registry(
        models=(
            transcription_model(
                allowed_sensitivities=frozenset({Sensitivity.PERSONAL, Sensitivity.SENSITIVE})
            ),
            synthesis_model(),
        )
    )
    snapshot = qualified_snapshot(task, registry=registry)
    assert snapshot.policy.permits(Provider.OPENAI, Sensitivity.PERSONAL)
    assert snapshot.policy.permits(Provider.OPENAI, Sensitivity.SENSITIVE)
    assert rank_eligible_speech(task, snapshot, units=30_000, now=NOW) == []


def test_mispaired_published_profile_cannot_qualify() -> None:
    both_capabilities = frozenset({Capability.TRANSCRIPTION, Capability.SPEECH_SYNTHESIS})
    dual_task_types = frozenset({TaskType.TRANSCRIBE, TaskType.SYNTHESIZE})
    dual_transcription = transcription_model(capabilities=both_capabilities)
    dual_synthesis = synthesis_model(capabilities=both_capabilities)
    key = model_key(TRANSCRIBE_MODEL)

    mispaired = make_registry(
        models=(dual_transcription,),
        profiles=(
            synthesis_profile(
                task_types=dual_task_types,
                required_capabilities=both_capabilities,
                assignments=(TRANSCRIBE_MODEL,),
            ),
        ),
    )
    mispaired_task = make_task(
        TaskType.TRANSCRIBE,
        profile=Profile.SPEECH_SYNTHESIS,
        capability_requirements=both_capabilities,
        prompt=SYNTHESIS_PROMPT,
        output_schema=SYNTHESIS_SCHEMA,
    )
    mispaired_snapshot = make_snapshot(
        mispaired_task,
        registry=mispaired,
        evaluations={key: make_evidence(mispaired_task)},
        available=frozenset({key}),
    )
    assert rank_eligible_speech(mispaired_task, mispaired_snapshot, units=30_000, now=NOW) == []

    mirror_registry = make_registry(
        models=(dual_synthesis,),
        profiles=(
            transcription_profile(
                task_types=dual_task_types,
                required_capabilities=both_capabilities,
                assignments=(SYNTHESIZE_MODEL,),
            ),
        ),
    )
    mirror_task = make_task(
        TaskType.SYNTHESIZE,
        profile=Profile.SPEECH_TRANSCRIPTION,
        capability_requirements=both_capabilities,
        prompt=TRANSCRIPTION_PROMPT,
        output_schema=TRANSCRIPTION_SCHEMA,
    )
    mirror_key = model_key(SYNTHESIZE_MODEL)
    mirror_snapshot = make_snapshot(
        mirror_task,
        registry=mirror_registry,
        evaluations={mirror_key: make_evidence(mirror_task)},
        available=frozenset({mirror_key}),
    )
    assert rank_eligible_speech(mirror_task, mirror_snapshot, units=2_000, now=NOW) == []

    paired = make_registry(
        models=(dual_transcription,),
        profiles=(
            transcription_profile(
                task_types=dual_task_types,
                required_capabilities=both_capabilities,
                assignments=(TRANSCRIBE_MODEL,),
            ),
        ),
    )
    control_task = make_task(TaskType.TRANSCRIBE, capability_requirements=both_capabilities)
    control_snapshot = make_snapshot(
        control_task,
        registry=paired,
        evaluations={key: make_evidence(control_task)},
        available=frozenset({key}),
    )
    assert [
        model.reference
        for model, _ in rank_eligible_speech(control_task, control_snapshot, units=30_000, now=NOW)
    ] == [TRANSCRIBE_MODEL]


def test_reservation_must_fit_request_and_every_policy_ceiling() -> None:
    task = make_task(max_cost=Decimal("0.006"))
    snapshot = qualified_snapshot(task)
    assert [cost for _, cost in rank_eligible_speech(task, snapshot, units=60_000, now=NOW)] == [
        Decimal("0.006")
    ]

    tight_task = make_task(max_cost=Decimal("0.005"))
    tight = qualified_snapshot(tight_task)
    assert rank_eligible_speech(tight_task, tight, units=60_000, now=NOW) == []

    operator = PolicyRules(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
        max_cost=Decimal("0.005"),
    )
    workspace = PolicyRules(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
        max_cost=Decimal("5"),
    )
    user = PolicyRules(
        grants=(ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
        max_cost=Decimal("5"),
    )
    capped = qualified_snapshot(task, rules=(operator, workspace, user))
    assert rank_eligible_speech(task, capped, units=60_000, now=NOW) == []

    equal = qualified_snapshot(task, rules=default_rules(max_cost=Decimal("0.006")))
    assert [cost for _, cost in rank_eligible_speech(task, equal, units=60_000, now=NOW)] == [
        Decimal("0.006")
    ]


def test_ordering_is_deterministic_cheapest_first_with_key_ties() -> None:
    cheap = ModelRef(provider=Provider.OPENAI, model="fixture-transcription-a-cheap")
    pricey = ModelRef(provider=Provider.OPENAI, model="fixture-transcription-z-pricey")
    models = (
        transcription_model(reference=cheap, transcription_cost_per_minute=Decimal("0.002")),
        transcription_model(reference=pricey, transcription_cost_per_minute=Decimal("0.060")),
        synthesis_model(),
    )
    task = make_task()
    evidence = make_evidence(task)
    evaluations = {model_key(cheap): evidence, model_key(pricey): evidence}
    available = frozenset(evaluations)

    def order(assignments: tuple[ModelRef, ...]) -> list[tuple[ModelRef, Decimal]]:
        registry = make_registry(
            models=models,
            profiles=(transcription_profile(assignments=assignments), synthesis_profile()),
        )
        snapshot = make_snapshot(
            task, registry=registry, evaluations=evaluations, available=available
        )
        return [
            (model.reference, cost)
            for model, cost in rank_eligible_speech(task, snapshot, units=60_000, now=NOW)
        ]

    expected = [(cheap, Decimal("0.002")), (pricey, Decimal("0.060"))]
    assert order((pricey, cheap)) == expected
    assert order((cheap, pricey)) == expected

    twin = ModelRef(provider=Provider.OPENAI, model="fixture-transcription-b-twin")
    tied = (
        transcription_model(reference=twin, transcription_cost_per_minute=Decimal("0.002")),
        *models,
    )
    tied_evaluations = {**evaluations, model_key(twin): evidence}
    registry = make_registry(
        models=tied,
        profiles=(transcription_profile(assignments=(twin, cheap)), synthesis_profile()),
    )
    snapshot = make_snapshot(
        task,
        registry=registry,
        evaluations=tied_evaluations,
        available=frozenset(tied_evaluations),
    )
    assert [
        model.reference for model, _ in rank_eligible_speech(task, snapshot, units=60_000, now=NOW)
    ] == [cheap, twin]


def test_non_audio_task_and_nonpositive_units_are_rejected() -> None:
    text_task = make_task(
        TaskType.EXTRACT,
        profile=Profile.EXTRACTION_FAST,
        capability_requirements=frozenset({Capability.TEXT}),
    )
    with pytest.raises(ValueError, match="audio task type"):
        rank_eligible_speech(text_task, make_snapshot(text_task), units=30_000, now=NOW)

    task = make_task()
    snapshot = qualified_snapshot(task)
    with pytest.raises(ValueError, match="positive integer"):
        rank_eligible_speech(task, snapshot, units=0, now=NOW)
    with pytest.raises(ValueError, match="positive integer"):
        rank_eligible_speech(task, snapshot, units=-30_000, now=NOW)
