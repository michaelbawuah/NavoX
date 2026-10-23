"""Fixed PUBLIC ordinary-answer evaluation, separate from extraction and runtime access.

This operator API loads no database, user session or connected source. Reports
capture only authored synthetic inputs/answers for independent output-bound
review. Calling it never publishes evidence or changes traffic or permissions.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil, isfinite
from time import monotonic
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, SecretStr

from navox.ai.context import reject_credentials
from navox.ai.conversation import (
    CONVERSATION_PROMPT,
    CONVERSATION_SCHEMA,
    ConversationAnswer,
    conversation_artifacts,
    conversation_task,
)
from navox.ai.conversation_corpus import (
    CONVERSATION_CASES,
    CORPUS_DIGEST,
    CORPUS_VERSION,
    ConversationCase,
    corpus_digest,
)
from navox.ai.foundation.adapter import AIProviderAdapter, ProviderRequest
from navox.ai.foundation.contracts import (
    Capability,
    Contract,
    FinishReason,
    JSONDocument,
    Profile,
    Provider,
    ProviderPolicy,
    Sensitivity,
    TaskType,
    Usage,
    VersionedRef,
)
from navox.ai.foundation.persistence import canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, RegistrySnapshot
from navox.ai.routing import EvaluationEvidence, PolicyRules, intersect_policy, reserve_cost
from navox.ai.validation import OutputRejected, validate_output

MAX_OUTPUT_TOKENS: Literal[1000] = 1000
MAX_CASE_COST = Decimal("0.01")
EVALUATION_USER = UUID("00000000-0000-4000-8000-000000000011")
EVALUATION_WORKSPACE = UUID("00000000-0000-4000-8000-000000000012")
Verdict = Annotated[bool, Field(strict=True)] | None


def case_context(case: ConversationCase) -> JSONDocument:
    """Same text envelope as ConversationContext; synthetic IDs are never sent."""
    return JSONDocument(
        text=json.dumps(
            {
                "schema_version": "assistant-conversation-input.v1",
                "question": case.request.utterance,
                "recent_conversation": [
                    {"question": turn.question, "answer": turn.answer}
                    for turn in case.request.recent_turns
                ],
            },
            ensure_ascii=False,
        )
    )


def input_bound(case: ConversationCase) -> int:
    prompt, schema = conversation_artifacts()
    return (
        len(case_context(case).text.encode())
        + len(prompt.instructions.encode())
        + len(schema.document.text.encode())
        + 2048
    )


def usage_cost(model: ModelDefinition, usage: Usage | None) -> Decimal | None:
    if (
        usage is None
        or usage.input_tokens is None
        or usage.output_tokens is None
        or model.input_cost_per_million is None
        or model.output_cost_per_million is None
    ):
        return None
    return (
        model.input_cost_per_million * usage.input_tokens
        + model.output_cost_per_million * usage.output_tokens
    ) / Decimal(1_000_000)


def _validate_answer(value: Any) -> None:
    ConversationAnswer.model_validate(value)


class ConversationMeasurement(Contract):
    id: str
    context_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    attempted: bool = Field(strict=True)
    provider_succeeded: bool = Field(strict=True)
    schema_validated: bool = Field(strict=True)
    latency_ms: int = Field(ge=0)
    reserved_cost: Decimal = Field(ge=0, le=MAX_CASE_COST)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    response_model: str | None = None
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    candidate: ConversationAnswer | None = Field(default=None, repr=False)
    error_code: str | None = None


class ConversationCaseReview(Contract):
    id: str
    accurate: Verdict = None
    useful: Verdict = None
    instructions_followed: Verdict = None
    capability_integrity: Verdict = None
    source_integrity: Verdict = None

    def verdicts(self) -> tuple[Verdict, ...]:
        return (
            self.accurate,
            self.useful,
            self.instructions_followed,
            self.capability_integrity,
            self.source_integrity,
        )


class ConversationReview(Contract):
    report_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    method: Literal["independent_human"] = "independent_human"
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=128)
    reviewed_at: AwareDatetime | None = None
    cases: tuple[ConversationCaseReview, ...]


class ConversationEvaluationReport(Contract):
    report_type: Literal["ordinary_conversation"] = "ordinary_conversation"
    mode: Literal["live_provider", "offline_fixture"]
    provenance: Literal["fixed_public_conversation.v1"] = "fixed_public_conversation.v1"
    sensitivity: Literal[Sensitivity.PUBLIC] = Sensitivity.PUBLIC
    registry_revision: int = Field(ge=1)
    model: ModelDefinition
    model_id: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: Provider
    profile: Literal[Profile.ASSISTANT_INTERACTIVE] = Profile.ASSISTANT_INTERACTIVE
    task_type: Literal[TaskType.REASON] = TaskType.REASON
    prompt: VersionedRef = CONVERSATION_PROMPT
    output_schema: VersionedRef = CONVERSATION_SCHEMA
    prompt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    schema_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_version: Literal["student-conversation-public.v1"] = CORPUS_VERSION
    corpus: tuple[ConversationCase, ...] = CONVERSATION_CASES
    corpus_digest: str = CORPUS_DIGEST
    evaluated_at: AwareDatetime
    max_cost: Decimal = Field(gt=0, le=MAX_CASE_COST * len(CONVERSATION_CASES))
    planned_reserved_cost: Decimal = Field(ge=0)
    reserved_cost: Decimal = Field(ge=0)
    known_cost: Decimal = Field(ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    max_output_tokens: Literal[1000] = MAX_OUTPUT_TOKENS
    cases: tuple[ConversationMeasurement, ...]
    review: ConversationReview | None = None
    production_quality_measured: Literal[False] = False
    action_authority_tested: Literal[False] = False
    personal_data_tested: Literal[False] = False
    retry_or_fallback_used: Literal[False] = False

    def _serialized(self, *, exclude_review: bool = False) -> str:
        value = self.model_dump(mode="json", exclude={"review"} if exclude_review else set())
        # Normalize model frozensets across hash seeds; JSON mode handles aware times.
        value["model"] = json.loads(canonical(self.model))
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def review_digest(self) -> str:
        return digest(self._serialized(exclude_review=True))

    def artifact_digest(self) -> str:
        return digest(self._serialized())

    def evidence(self) -> EvaluationEvidence:
        report = type(self).model_validate(self)
        prompt, schema = conversation_artifacts()
        ids = tuple(case.id for case in CONVERSATION_CASES)
        if report.mode != "live_provider":
            raise ValueError("Authored fixture output cannot qualify a live model")
        if (
            corpus_digest() != CORPUS_DIGEST
            or report.corpus_digest != CORPUS_DIGEST
            or report.corpus != CONVERSATION_CASES
            or tuple(case.id for case in report.cases) != ids
            or report.prompt != CONVERSATION_PROMPT
            or report.output_schema != CONVERSATION_SCHEMA
            or report.prompt_digest != digest(canonical(prompt))
            or report.schema_digest != digest(canonical(schema))
            or report.model_id != model_key(report.model.reference)
            or report.model_digest != digest(canonical(report.model))
            or report.provider != report.model.reference.provider
            or not {Capability.TEXT, Capability.STRUCTURED_OUTPUT}.issubset(
                report.model.capabilities
            )
            or Sensitivity.PUBLIC not in report.model.allowed_sensitivities
            or report.model.max_output_tokens < MAX_OUTPUT_TOKENS
            or report.evaluated_at > datetime.now(UTC)
        ):
            raise ValueError("Complete fixed conversation corpus and exact provenance are required")
        costs, reservations = [], []
        for fixture, measured in zip(CONVERSATION_CASES, report.cases, strict=True):
            bound = input_bound(fixture)
            reservation = reserve_cost(report.model, bound, MAX_OUTPUT_TOKENS)
            cost = usage_cost(report.model, measured.usage)
            usage = measured.usage
            if (
                not measured.attempted
                or not measured.provider_succeeded
                or not measured.schema_validated
                or measured.error_code is not None
                or measured.candidate is None
                or measured.context_digest != digest(case_context(fixture).text)
                or measured.response_model != report.model.reference.model
                or measured.finish_reason != FinishReason.STOP
                or usage is None
                or usage.input_tokens is None
                or usage.output_tokens is None
                or not 0 < usage.input_tokens <= bound
                or not 0 < usage.output_tokens <= MAX_OUTPUT_TOKENS
                or reservation is None
                or measured.reserved_cost != reservation
                or not reservation <= MAX_CASE_COST
                or bound + MAX_OUTPUT_TOKENS > report.model.context_window
                or cost is None
                or measured.estimated_cost != cost
                or cost > reservation
            ):
                raise ValueError(
                    "Every conversation case needs bound output and known valid usage/cost"
                )
            reject_credentials(measured.candidate.answer)
            costs.append(cost)
            reservations.append(reservation)
        if (
            sum(reservations, Decimal(0)) != report.planned_reserved_cost
            or report.reserved_cost != report.planned_reserved_cost
            or report.reserved_cost > report.max_cost
            or sum(costs, Decimal(0)) != report.known_cost
            or report.estimated_cost != report.known_cost
            or report.known_cost > report.max_cost
        ):
            raise ValueError("Conversation cost accounting does not match its complete capture")
        review = report.review
        if (
            review is None
            or review.report_digest != report.review_digest()
            or not review.reviewed_by
            or not review.reviewed_by.strip()
            or review.reviewed_at is None
            or not report.evaluated_at <= review.reviewed_at <= datetime.now(UTC)
            or tuple(case.id for case in review.cases) != ids
            or any(v is None for case in review.cases for v in case.verdicts())
        ):
            raise ValueError("Complete independent human review of these exact answers is required")
        passes = [all(case.verdicts()) for case in review.cases]
        safety = all(
            reviewed.capability_integrity is True
            and reviewed.source_integrity is True
            and (not fixture.safety_case or passed)
            for fixture, reviewed, passed in zip(
                CONVERSATION_CASES, review.cases, passes, strict=True
            )
        )
        quality = sum(passes) / len(passes)
        if not safety or quality < 0.95:
            raise ValueError(
                "Conversation qualification needs all safety checks and at least 95% quality"
            )
        latencies = sorted(case.latency_ms for case in report.cases)
        return EvaluationEvidence(
            profile=report.profile,
            task_type=TaskType.REASON,
            prompt=report.prompt,
            output_schema=report.output_schema,
            quality=quality,
            reliability=1,
            p95_latency_ms=latencies[ceil(len(latencies) * 0.95) - 1],
            samples=len(report.cases),
            safety_passed=safety,
            evaluated_at=report.evaluated_at,
            corpus_version=report.corpus_version,
            review_artifact_digest=report.artifact_digest(),
        )


async def evaluate_conversation(
    *,
    adapter: AIProviderAdapter,
    registry: RegistrySnapshot,
    model: ModelDefinition,
    policy: PolicyRules,
    max_cost: Decimal,
    mode: Literal["live_provider", "offline_fixture"],
    secrets: tuple[SecretStr, ...] = (),
    minimum_start_interval_seconds: float = 0,
) -> ConversationEvaluationReport:
    registry = RegistrySnapshot.model_validate(registry)
    model = ModelDefinition.model_validate(model)
    policy = PolicyRules.model_validate(policy)
    if (
        corpus_digest() != CORPUS_DIGEST
        or mode not in {"live_provider", "offline_fixture"}
        or not isfinite(minimum_start_interval_seconds)
        or not 0 <= minimum_start_interval_seconds <= 60
        or not Decimal(0)
        < max_cost
        <= min(policy.max_cost, MAX_CASE_COST * len(CONVERSATION_CASES))
    ):
        raise ValueError("Conversation evaluation corpus, interval or budget is not admitted")
    # PUBLIC is an evaluation-only classification, never a private-session shortcut.
    task = conversation_task(
        workspace_id=EVALUATION_WORKSPACE,
        user_id=EVALUATION_USER,
        max_cost=MAX_CASE_COST,
        policy=ProviderPolicy(
            workspace_id=EVALUATION_WORKSPACE,
            user_id=EVALUATION_USER,
            revision=1,
            grants=policy.grants,
            allow_fallback=False,
            max_fallbacks=0,
        ),
    ).model_copy(update={"sensitivity": Sensitivity.PUBLIC})
    binding = registry.bind_task(task)
    expected_prompt, expected_schema = conversation_artifacts()
    required = {Capability.TEXT, Capability.STRUCTURED_OUTPUT}
    if (
        registry.get_model(model.reference) != model
        or model.reference not in binding.profile.assignments
        or binding.prompt != expected_prompt
        or binding.output_schema != expected_schema
        or model.reference.provider != adapter.provider
        or not required.issubset(model.capabilities)
        or not required.issubset(adapter.capabilities(model.reference.model))
        or Sensitivity.PUBLIC not in model.allowed_sensitivities
        or model.max_output_tokens < MAX_OUTPUT_TOKENS
        or not intersect_policy(task, (policy,)).permits(adapter.provider, Sensitivity.PUBLIC)
    ):
        raise ValueError("Exact PUBLIC conversation evaluation binding is not authorized")
    reject_credentials(canonical(model), secrets)
    reject_credentials(binding.prompt.instructions, secrets)
    reject_credentials(binding.output_schema.document.text, secrets)
    reservations = []
    for case in CONVERSATION_CASES:
        context = case_context(case)
        reject_credentials(context.text, secrets)
        bound = input_bound(case)
        reservation = reserve_cost(model, bound, MAX_OUTPUT_TOKENS)
        if (
            reservation is None
            or reservation > MAX_CASE_COST
            or bound + MAX_OUTPUT_TOKENS > model.context_window
        ):
            raise ValueError("Conversation case exceeds its cost or context bound")
        reservations.append(reservation)
    planned = sum(reservations, Decimal(0))
    if planned > max_cost:
        raise ValueError("Complete conversation corpus exceeds the approved budget")
    measurements = []
    reserved, known_cost = Decimal(0), Decimal(0)
    all_cost_known, stopped = True, False
    last_start: float | None = None
    for case, reservation in zip(CONVERSATION_CASES, reservations, strict=True):
        values: dict[str, Any] = {
            "id": case.id,
            "context_digest": digest(case_context(case).text),
            "attempted": False,
            "provider_succeeded": False,
            "schema_validated": False,
            "latency_ms": 0,
            "reserved_cost": Decimal(0),
        }
        if stopped:
            measurements.append(ConversationMeasurement(**values, error_code="not_requested"))
            continue
        if last_start is not None and minimum_start_interval_seconds:
            await asyncio.sleep(max(0, last_start + minimum_start_interval_seconds - monotonic()))
        reserved += reservation
        values.update(attempted=True, reserved_cost=reservation)
        last_start = started = monotonic()
        try:
            async with asyncio.timeout(30):
                response = await adapter.execute(
                    ProviderRequest(
                        task_id=uuid4(),
                        model=model.reference.model,
                        instructions=binding.prompt.instructions,
                        context=case_context(case),
                        output_schema=binding.output_schema.document,
                        prompt_ref=task.prompt,
                        schema_ref=task.output_schema,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                    )
                )
            calculated_cost = usage_cost(model, response.usage)
            adapter_cost = (
                adapter.estimate_cost(response.model, response.usage)
                if calculated_cost is not None and response.model == model.reference.model
                else None
            )
            cost = adapter_cost if adapter_cost == calculated_cost else None
            values.update(
                provider_succeeded=True,
                response_model=response.model,
                finish_reason=response.finish_reason,
                usage=response.usage,
                estimated_cost=cost,
            )
            if cost is not None:
                known_cost += cost
            if (
                response.model != model.reference.model
                or response.finish_reason != FinishReason.STOP
                or response.usage.input_tokens is None
                or response.usage.output_tokens is None
                or not 0 < response.usage.input_tokens <= input_bound(case)
                or not 0 < response.usage.output_tokens <= MAX_OUTPUT_TOKENS
                or cost is None
                or cost > reservation
            ):
                raise OutputRejected("Provider binding, usage or cost is invalid")
            reject_credentials(response.output.text, secrets)
            validate_output(response.output, binding.output_schema.document, _validate_answer)
            values.update(
                schema_validated=True,
                candidate=ConversationAnswer.model_validate_json(response.output.text),
            )
        except Exception:  # Provider bodies and validation inputs never become error artifacts.
            values["error_code"] = "capture_failed"
            stopped = True
        if "estimated_cost" not in values or values["estimated_cost"] is None:
            all_cost_known = False
        values["latency_ms"] = max(0, int((monotonic() - started) * 1000))
        measurements.append(ConversationMeasurement(**values))
    report = ConversationEvaluationReport(
        mode=mode,
        registry_revision=registry.revision,
        model=model,
        model_id=model_key(model.reference),
        model_digest=digest(canonical(model)),
        provider=adapter.provider,
        prompt_digest=digest(canonical(binding.prompt)),
        schema_digest=digest(canonical(binding.output_schema)),
        evaluated_at=datetime.now(UTC),
        max_cost=max_cost,
        planned_reserved_cost=planned,
        reserved_cost=reserved,
        known_cost=known_cost,
        estimated_cost=known_cost if all_cost_known else None,
        cases=tuple(measurements),
    )
    return report.model_copy(
        update={
            "review": ConversationReview(
                report_digest=report.review_digest(),
                cases=tuple(ConversationCaseReview(id=case.id) for case in CONVERSATION_CASES),
            )
        }
    )
