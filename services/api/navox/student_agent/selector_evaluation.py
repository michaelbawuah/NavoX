"""Explicit synthetic selector evaluation; offline reports cannot qualify traffic.

Importing this module performs no calls or registry writes. A caller must supply
an authorized adapter, exact artifacts/model, public synthetic grant and bounded
budget. CLI publication/import/rollout wiring is intentionally separate.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite
from time import monotonic
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, SecretStr

from navox.ai.context import reject_credentials
from navox.ai.foundation.adapter import AIProviderAdapter, ProviderRequest
from navox.ai.foundation.contracts import (
    AITask,
    Contract,
    FinishReason,
    JSONDocument,
    LatencyClass,
    Profile,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.foundation.persistence import canonical as registry_canonical
from navox.ai.foundation.persistence import digest, model_key
from navox.ai.foundation.registry import ModelDefinition, RegistrySnapshot
from navox.ai.routing import PolicyRules, intersect_policy, reserve_cost
from navox.ai.validation import OutputRejected, validate_output
from navox.student_agent.contracts import (
    MANIFEST_HASH,
    MAX_OBSERVATIONS_BYTES,
    MAX_SELECTION_COST,
    SelectorOutput,
    canonical,
    selector_output_schema,
    tool_manifest,
)
from navox.student_agent.selector import (
    SELECTOR_CAPABILITIES,
    SELECTOR_INSTRUCTIONS,
    SELECTOR_PROMPT,
    SELECTOR_SCHEMA,
    validate_selection,
)
from navox.student_agent.selector_corpus import (
    CORPUS,
    CORPUS_VERSION,
    SelectorCase,
    corpus_document,
    meets_expectation,
)

MAX_OUTPUT_TOKENS = 800
MAX_EVALUATION_COST = Decimal("1")


class SelectorMeasurement(Contract):
    id: str
    provider_succeeded: bool
    schema_validated: bool
    passed: bool
    latency_ms: int = Field(strict=True, ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = None
    candidate: JSONDocument | None = Field(default=None, repr=False)


class SelectorEvaluationReport(Contract):
    report_type: Literal["student_selector"] = "student_selector"
    mode: Literal["live_provider", "offline_fixture"]
    live_provider_measured: bool
    registry_revision: int = Field(strict=True, ge=1)
    model_id: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: Profile = Profile.PLANNING_HIGH
    task_type: TaskType = TaskType.PLAN
    prompt: VersionedRef = SELECTOR_PROMPT
    output_schema: VersionedRef = SELECTOR_SCHEMA
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_version: Literal["student-selector.synthetic.v1"] = CORPUS_VERSION
    corpus_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus: tuple[SelectorCase, ...] = Field(repr=False)
    evaluated_at: AwareDatetime
    max_cost: Decimal = Field(gt=0, le=MAX_EVALUATION_COST)
    reserved_cost: Decimal = Field(ge=0)
    cases: tuple[SelectorMeasurement, ...]
    owner_authorization_tested: Literal[False] = False
    production_quality_measured: Literal[False] = False
    external_execution_tested: Literal[False] = False

    qualification_eligible: Literal[False] = False

    def evidence(self) -> None:
        """Review artifacts cannot enter the existing qualification import path.

        A separately reviewed importer must replay exact wire-schema, scoring,
        per-case reservations/usage and terminal stops before any future live
        report can become qualification evidence. No such importer is enabled.
        """
        raise ValueError("Selector reports are review-only and cannot qualify model traffic")


def case_context(case: SelectorCase) -> JSONDocument:
    request = case.request()
    content = canonical(
        {
            "schema_version": "student-selector-input.v1",
            "turn_id": str(request.turn_id),
            "step": request.step,
            "question": request.question,
            "manifest_hash": MANIFEST_HASH,
            "manifest": tool_manifest(),
            "observations": [row.model_dump(mode="json") for row in request.observations],
            "previous_error": request.previous_error,
        }
    )
    if len(content.encode()) > MAX_OBSERVATIONS_BYTES:
        raise ValueError("Synthetic selector context exceeds its bound")
    return JSONDocument(text=content)


async def evaluate_selector(
    *,
    adapter: AIProviderAdapter,
    registry: RegistrySnapshot,
    model: ModelDefinition,
    policy: PolicyRules,
    user_id: UUID,
    workspace_id: UUID,
    max_cost: Decimal,
    mode: Literal["live_provider", "offline_fixture"],
    secrets: tuple[SecretStr, ...] = (),
    minimum_start_interval_seconds: float = 0,
) -> SelectorEvaluationReport:
    """Explicitly invoked only; it neither publishes nor qualifies/activates a model.

    The caller supplies the policy's explicit evaluation principal; corpus IDs
    never imply an owner. Scoped policies must admit this exact PUBLIC binding.
    Offline callers must pass a fixture transport and mode=offline_fixture. The
    future operator CLI must separately authorize a live transport and budget.
    """
    if (
        mode not in {"live_provider", "offline_fixture"}
        or not isfinite(minimum_start_interval_seconds)
        or not 0 <= minimum_start_interval_seconds <= 60
        or model.reference.provider != adapter.provider
        or model not in registry.models
        or not SELECTOR_CAPABILITIES.issubset(model.capabilities)
        or not SELECTOR_CAPABILITIES.issubset(adapter.capabilities(model.reference.model))
        or Sensitivity.PUBLIC not in model.allowed_sensitivities
        or not Decimal(0) < max_cost <= min(MAX_EVALUATION_COST, policy.max_cost)
    ):
        raise ValueError("Synthetic selector evaluation is not authorized")
    evaluation_task = AITask(
        user_id=user_id,
        workspace_id=workspace_id,
        task_type=TaskType.PLAN,
        profile=Profile.PLANNING_HIGH,
        capability_requirements=SELECTOR_CAPABILITIES,
        prompt=SELECTOR_PROMPT,
        output_schema=SELECTOR_SCHEMA,
        sensitivity=Sensitivity.PUBLIC,
        latency_class=LatencyClass.INTERACTIVE,
        quality_class=QualityClass.HIGH,
        max_cost=min(max_cost, MAX_SELECTION_COST),
        max_output_tokens=MAX_OUTPUT_TOKENS,
        provider_policy=ProviderPolicy(
            user_id=user_id, workspace_id=workspace_id, revision=1, grants=policy.grants
        ),
    )
    if not intersect_policy(evaluation_task, (policy,)).permits(
        adapter.provider, Sensitivity.PUBLIC
    ):
        raise ValueError("Synthetic selector evaluation scope is not authorized")
    prompt = next((row for row in registry.prompts if row.reference == SELECTOR_PROMPT), None)
    schema = next((row for row in registry.schemas if row.reference == SELECTOR_SCHEMA), None)
    if (
        prompt is None
        or schema is None
        or prompt.instructions != SELECTOR_INSTRUCTIONS
        or prompt.output_schema != SELECTOR_SCHEMA
        or json.loads(schema.document.text) != selector_output_schema()
    ):
        raise ValueError("Publish and review the exact selector artifacts before evaluation")
    reserved = Decimal(0)
    measurements: list[SelectorMeasurement] = []
    stopped: str | None = None
    last_start: float | None = None
    for case in CORPUS:
        context = case_context(case)
        reject_credentials(context.text, secrets)
        reject_credentials(prompt.instructions, secrets)
        bound = (
            len(context.text.encode())
            + len(prompt.instructions.encode())
            + len(schema.document.text.encode())
            + 2048
        )
        reservation = reserve_cost(model, bound, MAX_OUTPUT_TOKENS)
        if stopped is None and (
            reservation is None
            or reservation > MAX_SELECTION_COST
            or reserved + reservation > max_cost
            or bound + MAX_OUTPUT_TOKENS > model.context_window
            or MAX_OUTPUT_TOKENS > model.max_output_tokens
        ):
            stopped = "budget_or_context_exceeded"
        if stopped:
            measurements.append(
                SelectorMeasurement(
                    id=case.id,
                    provider_succeeded=False,
                    schema_validated=False,
                    passed=False,
                    latency_ms=0,
                    error_code=stopped,
                )
            )
            continue
        assert reservation is not None
        reserved += reservation
        if last_start is not None and minimum_start_interval_seconds:
            await asyncio.sleep(max(0, last_start + minimum_start_interval_seconds - monotonic()))
        last_start = started = monotonic()
        succeeded, validated, passed = False, False, False
        candidate, cost, error = None, None, None
        try:
            async with asyncio.timeout(10):
                response = await adapter.execute(
                    ProviderRequest(
                        task_id=uuid4(),
                        model=model.reference.model,
                        instructions=prompt.instructions,
                        context=context,
                        output_schema=schema.document,
                        prompt_ref=SELECTOR_PROMPT,
                        schema_ref=SELECTOR_SCHEMA,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    )
                )
            if (
                response.model != model.reference.model
                or response.finish_reason != FinishReason.STOP
            ):
                raise OutputRejected("Unexpected selector response")
            succeeded = True
            cost = adapter.estimate_cost(model.reference.model, response.usage)
            if cost is None or cost > reservation:
                stopped = "unknown_or_excess_cost"
                raise OutputRejected("Selector cost cannot be verified within reservation")
            reject_credentials(response.output.text, secrets)

            def semantic(value: Any, selected: SelectorCase = case) -> None:
                validate_selection(SelectorOutput.model_validate(value), selected.request())

            validate_output(response.output, schema.document, semantic)
            candidate = response.output
            validated = True
            passed = meets_expectation(SelectorOutput.model_validate_json(candidate.text), case)
            if not passed:
                error = "task_expectation_failed"
        except (ValueError, OutputRejected):
            error = stopped or "validation_failed"
        except TimeoutError:
            error = "timeout"
        except Exception as exception:
            error = adapter.classify_error(exception).code.value
            if error in {"authentication", "invalid_request", "rate_limit"}:
                stopped = error
        measurements.append(
            SelectorMeasurement(
                id=case.id,
                provider_succeeded=succeeded,
                schema_validated=validated,
                passed=passed,
                latency_ms=max(0, int((monotonic() - started) * 1000)),
                estimated_cost=cost,
                error_code=error,
                candidate=candidate,
            )
        )
    return SelectorEvaluationReport(
        mode=mode,
        live_provider_measured=mode == "live_provider",
        registry_revision=registry.revision,
        model_id=model_key(model.reference),
        model_digest=digest(registry_canonical(model)),
        manifest_hash=MANIFEST_HASH,
        corpus_digest=digest(corpus_document()),
        corpus=CORPUS,
        evaluated_at=datetime.now(UTC),
        max_cost=max_cost,
        reserved_cost=reserved,
        cases=tuple(measurements),
    )
