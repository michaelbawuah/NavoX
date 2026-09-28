"""Offline foundation regressions; these do not establish live provider compliance."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from navox.ai.foundation.adapter import (
    AIProviderAdapter,
    ErrorCode,
    ProviderError,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
)
from navox.ai.foundation.contracts import (
    AIResult,
    AITask,
    Capability,
    ContextReference,
    FinishReason,
    JSONDocument,
    LatencyClass,
    Profile,
    Provider,
    ProviderGrant,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
    Usage,
    VersionedRef,
    validate_result_binding,
)
from navox.ai.foundation.registry import (
    ModelDefinition,
    ModelRef,
    ProfileDefinition,
    PromptDefinition,
    RegistrySnapshot,
    SchemaDefinition,
    validate_registry_update,
)

WORKSPACE = UUID("00000000-0000-4000-8000-000000000001")
USER = UUID("00000000-0000-4000-8000-000000000002")
SCHEMA = VersionedRef(name="fixture_extraction", version="v1")
PROMPT = VersionedRef(name="fixture_extraction_prompt", version="v1")
CAPABILITIES = frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT})
MODEL = ModelRef(provider=Provider.OPENAI, model="fixture-model-not-a-live-model")


def make_policy(**changes: object) -> ProviderPolicy:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "user_id": USER,
        "revision": 1,
        "grants": (ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset(Sensitivity)),),
    }
    values.update(changes)
    return ProviderPolicy.model_validate(values)


def make_task(**changes: object) -> AITask:
    values: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "user_id": USER,
        "task_type": TaskType.EXTRACT,
        "profile": Profile.EXTRACTION_FAST,
        "capability_requirements": CAPABILITIES,
        "output_schema": SCHEMA,
        "prompt": PROMPT,
        "sensitivity": Sensitivity.PERSONAL,
        "latency_class": LatencyClass.INTERACTIVE,
        "quality_class": QualityClass.STANDARD,
        "max_cost": Decimal("0.05"),
        "max_output_tokens": 100,
        "provider_policy": make_policy(),
    }
    values.update(changes)
    return AITask.model_validate(values)


def make_result(task: AITask, **changes: object) -> AIResult:
    values: dict[str, object] = {
        "task_id": task.id,
        "workspace_id": task.workspace_id,
        "user_id": task.user_id,
        "trace_id": task.trace_id,
        "provider": Provider.OPENAI,
        "model": MODEL.model,
        "output": JSONDocument(text='{"observations":[]}'),
        "finish_reason": FinishReason.STOP,
        "latency_ms": 7,
        "output_schema": task.output_schema,
        "prompt": task.prompt,
    }
    values.update(changes)
    return AIResult.model_validate(values)


def make_registry(**changes: object) -> RegistrySnapshot:
    values: dict[str, object] = {
        "revision": 1,
        "models": (
            ModelDefinition(
                reference=MODEL,
                capabilities=CAPABILITIES,
                context_window=4000,
                max_output_tokens=1000,
            ),
        ),
        "profiles": (
            ProfileDefinition(
                profile=Profile.EXTRACTION_FAST,
                task_types=frozenset({TaskType.EXTRACT}),
                required_capabilities=CAPABILITIES,
                assignments=(MODEL,),
            ),
        ),
        "schemas": (
            SchemaDefinition(
                reference=SCHEMA,
                document=JSONDocument(text='{"type":"object"}'),
            ),
        ),
        "prompts": (
            PromptDefinition(
                reference=PROMPT,
                output_schema=SCHEMA,
                instructions="Synthetic fixture instructions, not a production prompt.",
            ),
        ),
    }
    values.update(changes)
    return RegistrySnapshot.model_validate(values)


def test_task_round_trips_json() -> None:
    task = make_task()
    assert AITask.model_validate_json(task.model_dump_json()) == task
    assert task.max_cost == Decimal("0.05")


@pytest.mark.parametrize(
    "field", ["approved", "execute", "api_key", "permissions", "model", "provider"]
)
def test_task_rejects_authority_and_provider_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        make_task(**{field: "untrusted"})


@pytest.mark.parametrize("field", ["workspace_id", "user_id"])
def test_policy_cannot_cross_scope(field: str) -> None:
    with pytest.raises(ValidationError, match="scope mismatch"):
        make_task(provider_policy=make_policy(**{field: uuid4()}))


@pytest.mark.parametrize("field", ["workspace_id", "user_id"])
def test_context_cannot_cross_scope(field: str) -> None:
    data: dict[str, object] = {
        "workspace_id": WORKSPACE,
        "user_id": USER,
        "source_id": uuid4(),
        "connection_id": uuid4(),
        "sensitivity": Sensitivity.PERSONAL,
    }
    data[field] = uuid4()
    with pytest.raises(ValidationError, match="scope mismatch"):
        make_task(context_references=(ContextReference.model_validate(data),))


@pytest.mark.parametrize("task_sensitivity", list(Sensitivity))
@pytest.mark.parametrize("source_sensitivity", list(Sensitivity))
def test_all_sensitivity_boundaries(
    task_sensitivity: Sensitivity, source_sensitivity: Sensitivity
) -> None:
    reference = ContextReference(
        workspace_id=WORKSPACE,
        user_id=USER,
        source_id=uuid4(),
        connection_id=uuid4(),
        sensitivity=source_sensitivity,
    )
    if source_sensitivity.rank > task_sensitivity.rank:
        with pytest.raises(ValidationError, match="downgrade"):
            make_task(sensitivity=task_sensitivity, context_references=(reference,))
    else:
        assert make_task(sensitivity=task_sensitivity, context_references=(reference,))


def test_duplicate_context_is_rejected() -> None:
    reference = ContextReference(
        workspace_id=WORKSPACE,
        user_id=USER,
        source_id=uuid4(),
        connection_id=uuid4(),
        sensitivity=Sensitivity.PERSONAL,
    )
    with pytest.raises(ValidationError, match="Duplicate context"):
        make_task(context_references=(reference, reference))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-0.01", "1000001"])
def test_budget_requires_nonnegative_finite_decimal(value: str) -> None:
    with pytest.raises(ValidationError):
        make_task(max_cost=Decimal(value))


@pytest.mark.parametrize("value", [-1, 0, 1_000_001, True, "100"])
def test_output_limit_is_bounded_strict_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        make_task(max_output_tokens=value)


def test_policy_defaults_deny_all_and_fallback() -> None:
    policy = make_policy(grants=())
    assert not policy.allow_fallback
    assert policy.max_fallbacks == 0
    for provider in Provider:
        for sensitivity in Sensitivity:
            assert not policy.permits(provider, sensitivity)


def test_preference_cannot_create_a_provider_grant() -> None:
    task = make_task(preferred_provider=Provider.XAI)
    assert not task.provider_policy.permits(Provider.XAI, task.sensitivity)


def test_grants_require_exact_sensitivity_not_an_implicit_range() -> None:
    policy = make_policy(
        grants=(ProviderGrant(provider=Provider.GEMINI, sensitivities={Sensitivity.PUBLIC}),)
    )
    assert policy.permits(Provider.GEMINI, Sensitivity.PUBLIC)
    assert not policy.permits(Provider.GEMINI, Sensitivity.SENSITIVE)


def test_duplicate_provider_grants_rejected() -> None:
    grant = ProviderGrant(provider=Provider.OPENAI)
    with pytest.raises(ValidationError, match="Duplicate provider"):
        make_policy(grants=(grant, grant))


@pytest.mark.parametrize(
    "changes", [{"max_fallbacks": 1}, {"allow_fallback": "yes"}, {"max_fallbacks": 4}]
)
def test_fallback_requires_explicit_bounded_permission(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_policy(**changes)


@pytest.mark.parametrize(
    "value",
    ["{", '{"x":NaN}', '{"x":Infinity}', '{"x":1e9999}', '{"x":1,"x":2}', '{"x":{"y":1,"y":2}}'],
)
def test_json_rejects_ambiguous_or_nonfinite_values(value: str) -> None:
    with pytest.raises(ValidationError):
        JSONDocument(text=value)


@pytest.mark.parametrize("text", ["null", "[]", '"hello"', "12", '{"x": 1}', "true"])
def test_json_accepts_valid_value_types(text: str) -> None:
    document = JSONDocument(text=text)
    assert json.loads(document.text) == json.loads(text)


def test_json_canonicalization_and_deep_immutability() -> None:
    document = JSONDocument(text='{"b":[2], "a":1}')
    assert document.text == '{"a":1,"b":[2]}'
    detached = json.loads(document.text)
    detached["b"].append(3)
    assert document.text == '{"a":1,"b":[2]}'
    with pytest.raises(ValidationError):
        document.text = "null"


def test_json_limits_nested_and_oversized_documents() -> None:
    for text in ['"' + "a" * 262_144 + '"', "[" * 2000 + "0" + "]" * 2000]:
        with pytest.raises(ValidationError):
            JSONDocument(text=text)


def test_private_content_not_in_reprs_or_string_errors() -> None:
    secret = "synthetic-secret-marker"
    document = JSONDocument(text=json.dumps({"body": secret}))
    assert secret not in repr(document)
    assert secret not in repr(make_result(make_task(), output=document))
    with pytest.raises(ValidationError) as error:
        JSONDocument(text=secret)
    assert secret not in str(error.value)


@pytest.mark.parametrize("value", [-1, True, 1.5, "2", 1_000_000_001])
def test_usage_rejects_invalid_counts(value: object) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate({"input_tokens": value})


def test_unknown_usage_and_price_are_not_fabricated() -> None:
    assert Usage().total_tokens is None
    assert Usage(input_tokens=1).total_tokens is None
    assert Usage(input_tokens=0, output_tokens=0).total_tokens == 0
    assert Usage(input_tokens=3, output_tokens=7).total_tokens == 10
    result = make_result(make_task())
    assert result.estimated_cost is None
    assert not result.schema_validated
    assert not result.semantic_validated
    assert not result.policy_validated


@pytest.mark.parametrize("field", ["task_id", "workspace_id", "user_id", "trace_id"])
def test_result_ids_are_bound_to_the_original_task(field: str) -> None:
    task = make_task()
    with pytest.raises(ValueError, match="attribution mismatch"):
        validate_result_binding(task, make_result(task, **{field: uuid4()}))


@pytest.mark.parametrize("field", ["output_schema", "prompt"])
def test_result_versions_cannot_drift(field: str) -> None:
    task = make_task()
    with pytest.raises(ValueError, match="attribution mismatch"):
        validate_result_binding(
            task, make_result(task, **{field: VersionedRef(name="other", version="v2")})
        )


def test_result_requires_allowed_provider_and_fallback_budget() -> None:
    task = make_task()
    validate_result_binding(task, make_result(task))
    with pytest.raises(ValueError, match="not permitted"):
        validate_result_binding(task, make_result(task, provider=Provider.XAI))
    with pytest.raises(ValueError, match="fallback limit"):
        validate_result_binding(task, make_result(task, fallback_count=1))


def test_explicitly_permitted_fallback_envelope() -> None:
    task = make_task(provider_policy=make_policy(allow_fallback=True, max_fallbacks=1))
    validate_result_binding(task, make_result(task, fallback_count=1))


def test_failed_inference_cannot_be_an_empty_success_envelope() -> None:
    values = make_result(make_task()).model_dump()
    del values["output"]
    with pytest.raises(ValidationError):
        AIResult.model_validate(values)


@pytest.mark.parametrize("value", ["", " ", "v0", "latest", "v1/private key"])
def test_versions_are_explicit_and_bounded(value: str) -> None:
    with pytest.raises(ValidationError):
        VersionedRef(name="fixture", version=value)


def test_registry_round_trip_and_exact_binding() -> None:
    registry = make_registry()
    assert RegistrySnapshot.model_validate_json(registry.model_dump_json()) == registry
    binding = registry.bind_task(make_task())
    assert binding.prompt.reference == PROMPT
    assert binding.output_schema.reference == SCHEMA
    assert binding.profile.profile == Profile.EXTRACTION_FAST


def test_models_are_disabled_and_unpriced_by_default() -> None:
    model = make_registry().get_model(MODEL)
    assert not model.enabled
    assert not model.allowed_sensitivities
    assert model.input_cost_per_million is None
    assert model.output_cost_per_million is None


@pytest.mark.parametrize("collection", ["models", "profiles", "schemas", "prompts"])
def test_duplicate_registry_keys_are_rejected(collection: str) -> None:
    values = make_registry().model_dump()
    values[collection] = values[collection] + values[collection]
    with pytest.raises(ValidationError, match="Duplicate"):
        RegistrySnapshot.model_validate(values)


def test_duplicate_profile_assignments_rejected() -> None:
    profile = make_registry().profiles[0].model_dump()
    profile["assignments"] = (MODEL, MODEL)
    with pytest.raises(ValidationError, match="Duplicate"):
        ProfileDefinition.model_validate(profile)


def test_dangling_model_or_schema_references_rejected() -> None:
    for changes in [{"models": ()}, {"schemas": ()}]:
        with pytest.raises(ValidationError, match="unregistered"):
            make_registry(**changes)


def test_assigned_models_need_profile_capabilities() -> None:
    model = make_registry().models[0].model_dump()
    model["capabilities"] = frozenset({Capability.TEXT})
    with pytest.raises(ValidationError, match="required profile capability"):
        make_registry(models=(ModelDefinition.model_validate(model),))


@pytest.mark.parametrize("field", ["prompt", "output_schema"])
def test_no_automatic_latest_version_substitution(field: str) -> None:
    with pytest.raises(LookupError):
        make_registry().bind_task(
            make_task(**{field: VersionedRef(name="not_registered", version="v2")})
        )


def test_task_cannot_drop_profile_capabilities() -> None:
    task = make_task(capability_requirements=frozenset({Capability.TEXT}))
    with pytest.raises(ValueError, match="drop required"):
        make_registry().bind_task(task)


def test_task_type_must_match_profile() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        make_registry().bind_task(make_task(task_type=TaskType.PLAN))


def test_prompt_schema_mismatch_rejected_at_binding() -> None:
    registry = make_registry()
    other = SchemaDefinition(
        reference=VersionedRef(name="other", version="v2"), document=JSONDocument(text="{}")
    )
    registry = make_registry(schemas=(*registry.schemas, other))
    with pytest.raises(ValueError, match="disagree"):
        registry.bind_task(make_task(output_schema=other.reference))


def test_missing_model_lookup_is_explicit() -> None:
    with pytest.raises(LookupError, match="not registered"):
        make_registry().get_model(ModelRef(provider=Provider.XAI, model="missing"))


@pytest.mark.parametrize("text", ["[]", "null", '"schema"'])
def test_schema_registration_requires_object(text: str) -> None:
    with pytest.raises(ValidationError):
        SchemaDefinition(reference=SCHEMA, document=JSONDocument(text=text))


def test_blank_prompt_rejected() -> None:
    with pytest.raises(ValidationError):
        PromptDefinition(reference=PROMPT, output_schema=SCHEMA, instructions=" \n ")


def test_output_limit_cannot_exceed_model_window() -> None:
    with pytest.raises(ValidationError):
        ModelDefinition(
            reference=MODEL, capabilities=CAPABILITIES, context_window=10, max_output_tokens=11
        )


def test_registry_preserves_prompt_versions_across_updates() -> None:
    old = make_registry()
    validate_registry_update(old, make_registry(revision=2))
    changed = PromptDefinition(
        reference=PROMPT, output_schema=SCHEMA, instructions="Changed content"
    )
    with pytest.raises(ValueError, match="cannot be removed or replaced"):
        validate_registry_update(old, make_registry(revision=2, prompts=(changed,)))
    with pytest.raises(ValueError, match="cannot be removed or replaced"):
        validate_registry_update(old, make_registry(revision=2, prompts=()))


def test_registry_preserves_schema_versions_across_updates() -> None:
    old = make_registry()
    changed = SchemaDefinition(reference=SCHEMA, document=JSONDocument(text='{"type":"array"}'))
    with pytest.raises(ValueError, match="schema versions"):
        validate_registry_update(old, make_registry(revision=2, schemas=(changed,)))


def test_registry_revision_must_advance() -> None:
    with pytest.raises(ValueError, match="must increase"):
        validate_registry_update(make_registry(), make_registry())


def test_new_prompt_version_can_be_added_without_replacing_old() -> None:
    old = make_registry()
    new_prompt = PromptDefinition(
        reference=VersionedRef(name=PROMPT.name, version="v2"),
        output_schema=SCHEMA,
        instructions="New version",
    )
    proposed = make_registry(revision=2, prompts=(*old.prompts, new_prompt))
    validate_registry_update(old, proposed)
    assert proposed.bind_task(make_task()).prompt.reference.version == "v1"


def test_contract_collections_are_immutable() -> None:
    task = make_task()
    registry = make_registry()
    with pytest.raises(ValidationError):
        task.provider_policy.grants = ()
    with pytest.raises(ValidationError):
        registry.revision = 2
    assert isinstance(task.capability_requirements, frozenset)
    assert isinstance(registry.models, tuple)


class FixtureAdapter:
    """Synthetic adapter; not one of the four production implementations."""

    @property
    def provider(self) -> Provider:
        return Provider.OPENAI

    async def list_models(self) -> tuple[ModelDefinition, ...]:
        return make_registry().models

    def capabilities(self, model: str) -> frozenset[Capability]:
        return make_registry().get_model(ModelRef(provider=self.provider, model=model)).capabilities

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        return self.normalize_response({"model": request.model})

    def normalize_response(self, response: Mapping[str, object]) -> ProviderResponse:
        model = response.get("model")
        if not isinstance(model, str):
            raise ValueError("Missing fixture model")
        return ProviderResponse(
            model=model, output=JSONDocument(text="{}"), finish_reason=FinishReason.STOP
        )

    def estimate_cost(self, model: str, usage: Usage) -> Decimal | None:
        return None

    def classify_error(self, error: Exception) -> ProviderError:
        return ProviderError(code=ErrorCode.UNKNOWN)

    async def health_probe(self) -> ProviderHealth:
        return ProviderHealth.DISABLED


def test_provider_protocol_has_a_usable_offline_fixture() -> None:
    adapter: AIProviderAdapter = FixtureAdapter()
    assert isinstance(adapter, AIProviderAdapter)
    request = ProviderRequest(
        task_id=uuid4(),
        model=MODEL.model,
        instructions="fixture",
        context=JSONDocument(text="{}"),
        output_schema=JSONDocument(text="{}"),
        schema_ref=SCHEMA,
        prompt_ref=PROMPT,
        max_output_tokens=100,
    )
    response = asyncio.run(adapter.execute(request))
    assert response.model == MODEL.model
    assert response.usage.total_tokens is None
    assert adapter.estimate_cost(response.model, response.usage) is None
    assert asyncio.run(adapter.health_probe()) == ProviderHealth.DISABLED


def test_normalized_error_does_not_echo_upstream_message() -> None:
    secret = "synthetic-secret-marker"
    error = FixtureAdapter().classify_error(RuntimeError(secret))
    assert secret not in repr(error)
    assert secret not in error.model_dump_json()
    with pytest.raises(ValidationError):
        ProviderError.model_validate({"code": "unknown", "message": secret})


@pytest.mark.parametrize("depth", [1, 32, 64])
def test_json_depth_limit_includes_its_boundary(depth: int) -> None:
    text = "[" * depth + "0" + "]" * depth
    assert JSONDocument(text=text).text == text


@pytest.mark.parametrize("depth", [65, 128])
def test_json_depth_limit_rejects_values_above_boundary(depth: int) -> None:
    with pytest.raises(ValidationError):
        JSONDocument(text="[" * depth + "0" + "]" * depth)


@pytest.mark.parametrize("value", ["[" * 100, '\\"[{]}', "\\\\" * 20 + "]" * 100])
def test_json_depth_scanner_does_not_count_string_content(value: str) -> None:
    document = JSONDocument(text=json.dumps({"value": value}))
    assert json.loads(document.text)["value"] == value
