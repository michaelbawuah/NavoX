"""Full, fixed public operational corpora with reproducible selection/tool scoring.

Unlike free-form drafting, these contracts contain only fact references and
bounded choices. Independent fixture expectations measure relevance, order and
tool choice; structural/domain validation alone is never a quality pass.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil, isfinite
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, SecretStr

from navox.ai.context import reject_credentials
from navox.ai.domain_corpus import CORPORA, DomainCase, meets_expectation
from navox.ai.domains import (
    DOMAIN_TASKS,
    Domain,
    domain_prompt_reference,
    domain_reference,
    domain_schema,
    validate_domain,
)
from navox.ai.foundation.adapter import AIProviderAdapter, ProviderRequest
from navox.ai.foundation.contracts import (
    Capability,
    Contract,
    FinishReason,
    JSONDocument,
    Profile,
    Sensitivity,
    VersionedRef,
)
from navox.ai.foundation.persistence import canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, RegistrySnapshot
from navox.ai.routing import EvaluationEvidence, PolicyRules, reserve_cost
from navox.ai.validation import OutputRejected, validate_output


class DomainMeasurement(Contract):
    id: str
    provider_succeeded: bool
    schema_validated: bool
    passed: bool
    latency_ms: int = Field(ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = None
    candidate: JSONDocument | None = Field(default=None, repr=False)


class DomainEvaluationReport(Contract):
    report_type: Literal["operational_domain"] = "operational_domain"
    mode: Literal["live_provider", "offline_fixture"]
    domain: Domain
    registry_revision: int = Field(ge=1)
    model_id: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: Profile
    prompt: VersionedRef
    output_schema: VersionedRef
    corpus_version: Literal["spec005-operational.v1"] = "spec005-operational.v1"
    corpus: tuple[DomainCase, ...]
    evaluated_at: AwareDatetime
    max_cost: Decimal = Field(gt=0, le=10)
    reserved_cost: Decimal = Field(ge=0)
    cases: tuple[DomainMeasurement, ...]
    production_quality_measured: Literal[False] = False
    send_authority_tested: Literal[False] = False

    def evidence(self) -> EvaluationEvidence:
        profile, task_type = DOMAIN_TASKS[self.domain]
        corpus = CORPORA[self.domain]
        if (
            self.mode != "live_provider"
            or self.corpus != corpus
            or self.profile != profile
            or self.prompt
            not in (domain_reference(self.domain), domain_prompt_reference(self.domain))
            or self.output_schema != domain_reference(self.domain)
            or tuple(c.id for c in self.cases) != tuple(c.id for c in corpus)
            or self.reserved_cost > self.max_cost
            or self.evaluated_at > datetime.now(UTC)
        ):
            raise ValueError("Complete live operational corpus and exact task binding are required")
        passes, safety = [], []
        for fixture, measured in zip(corpus, self.cases, strict=True):
            passed = False
            if measured.candidate is not None:
                if not measured.provider_succeeded or not measured.schema_validated:
                    raise ValueError("Operational candidate measurements conflict")
                proposal = validate_domain(
                    self.domain, json.loads(measured.candidate.text), fixture.context
                )
                passed = measured.error_code is None and meets_expectation(
                    proposal, fixture.expected
                )
            elif measured.schema_validated:
                raise ValueError("Validated operational candidate is missing")
            if measured.passed != passed:
                raise ValueError("Operational quality score differs from the captured candidate")
            passes.append(passed)
            if fixture.safety_case:
                safety.append(passed)
        latencies = sorted(c.latency_ms for c in self.cases)
        return EvaluationEvidence(
            profile=profile,
            task_type=task_type,
            prompt=self.prompt,
            output_schema=self.output_schema,
            quality=sum(passes) / len(passes),
            reliability=sum(c.provider_succeeded for c in self.cases) / len(self.cases),
            p95_latency_ms=latencies[ceil(len(latencies) * 0.95) - 1],
            samples=len(self.cases),
            safety_passed=all(safety),
            evaluated_at=self.evaluated_at,
            corpus_version=f"{self.corpus_version}:{self.domain.value}",
            review_artifact_digest=digest(
                json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            ),
        )


async def evaluate_domain(
    *,
    domain: Domain,
    adapter: AIProviderAdapter,
    registry: RegistrySnapshot,
    model: ModelDefinition,
    policy: PolicyRules,
    max_cost: Decimal,
    mode: Literal["live_provider", "offline_fixture"],
    secrets: tuple[SecretStr, ...] = (),
    minimum_start_interval_seconds: float = 0,
) -> DomainEvaluationReport:
    if (
        not isfinite(minimum_start_interval_seconds)
        or not 0 <= minimum_start_interval_seconds <= 60
    ):
        raise ValueError("Invalid operational request interval")
    required = {Capability.TEXT, Capability.STRUCTURED_OUTPUT}
    if (
        model.reference.provider != adapter.provider
        or not required.issubset(model.capabilities)
        or not required.issubset(adapter.capabilities(model.reference.model))
        or Sensitivity.PUBLIC not in model.allowed_sensitivities
        or not any(
            g.provider == adapter.provider and Sensitivity.PUBLIC in g.sensitivities
            for g in policy.grants
        )
        or not Decimal(0) < max_cost <= min(Decimal(10), policy.max_cost)
    ):
        raise ValueError("Synthetic operational evaluation is not authorized")
    reference = domain_reference(domain)
    prompt_ref = domain_prompt_reference(domain)
    prompt = next((p for p in registry.prompts if p.reference == prompt_ref), None)
    schema = next((s for s in registry.schemas if s.reference == reference), None)
    if (
        prompt is None
        or schema is None
        or prompt.output_schema != reference
        or schema.document != domain_schema(domain)
    ):
        raise ValueError("Publish the operational prompt and exact schema first")
    reserved = Decimal(0)
    measurements = []
    stopped: str | None = None
    last_start: float | None = None
    for fixture in CORPORA[domain]:
        context = JSONDocument(
            text=json.dumps({"operational_context": fixture.context.model_dump(mode="json")})
        )
        reject_credentials(context.text, secrets)
        reject_credentials(prompt.instructions, secrets)
        maximum_output = min(2000, model.max_output_tokens)
        bound = (
            len(context.text.encode())
            + len(prompt.instructions.encode())
            + len(schema.document.text.encode())
            + 2048
        )
        reservation = reserve_cost(model, bound, maximum_output)
        if stopped is None and (
            reservation is None
            or reserved + reservation > max_cost
            or bound + maximum_output > model.context_window
        ):
            stopped = "budget_or_context_exceeded"
        if stopped is not None:
            measurements.append(
                DomainMeasurement(
                    id=fixture.id,
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
            async with asyncio.timeout(30):
                response = await adapter.execute(
                    ProviderRequest(
                        task_id=uuid4(),
                        model=model.reference.model,
                        instructions=prompt.instructions,
                        context=context,
                        output_schema=schema.document,
                        prompt_ref=prompt_ref,
                        schema_ref=reference,
                        max_output_tokens=maximum_output,
                    )
                )
            if (
                response.model != model.reference.model
                or response.finish_reason != FinishReason.STOP
            ):
                raise OutputRejected("Unexpected provider result")
            succeeded = True
            if response.usage.input_tokens is not None and response.usage.output_tokens is not None:
                cost = reserve_cost(
                    model, response.usage.input_tokens, response.usage.output_tokens
                )
            if cost is not None and cost > reservation:
                stopped = "budget_exceeded"
                raise OutputRejected("Provider exceeded its reserved budget")
            reject_credentials(response.output.text, secrets)

            def semantic(value: Any, selected: DomainCase = fixture) -> None:
                validate_domain(domain, value, selected.context)

            validate_output(response.output, schema.document, semantic)
            candidate = response.output
            validated = True
            passed = meets_expectation(
                validate_domain(domain, json.loads(candidate.text), fixture.context),
                fixture.expected,
            )
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
            DomainMeasurement(
                id=fixture.id,
                provider_succeeded=succeeded,
                schema_validated=validated,
                passed=passed,
                candidate=candidate,
                estimated_cost=cost,
                latency_ms=max(0, int((monotonic() - started) * 1000)),
                error_code=error,
            )
        )
    return DomainEvaluationReport(
        mode=mode,
        domain=domain,
        registry_revision=registry.revision,
        model_id=model_key(model.reference),
        model_digest=digest(canonical(model)),
        profile=DOMAIN_TASKS[domain][0],
        prompt=prompt_ref,
        output_schema=reference,
        corpus=CORPORA[domain],
        evaluated_at=datetime.now(UTC),
        max_cost=max_cost,
        reserved_cost=reserved,
        cases=tuple(measurements),
    )
