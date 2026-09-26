"""Measured provider comparison on fixed synthetic SPEC-002 fixtures, without actions.

This smoke rubric can qualify a candidate for a controlled extraction canary. It
does not establish production precision/recall or qualify other task profiles.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, SecretStr

from navox.ai.context import reject_credentials
from navox.ai.errors import AIProviderError
from navox.ai.foundation.adapter import AIProviderAdapter, ProviderRequest
from navox.ai.foundation.contracts import (
    Capability,
    Contract,
    FinishReason,
    JSONDocument,
    Profile,
    Provider,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.foundation.persistence import canonical, digest, model_key
from navox.ai.foundation.registry import ModelDefinition, RegistrySnapshot
from navox.ai.gateway import minimized_source_payload
from navox.ai.routing import EvaluationEvidence, PolicyRules, reserve_cost
from navox.ai.validation import OutputRejected, validate_output
from navox.evaluation.intelligence_smoke import CASES, EMAIL_TRIAGE_CASES, run_smoke
from navox.intelligence.contracts import SourceDocument
from navox.intelligence.extraction import ModelExtractionResponse, OperationalExtraction

CORPUS = "spec005-extraction-smoke.v1"
CASES_TO_RUN = (*CASES, *EMAIL_TRIAGE_CASES)
REFERENCE = VersionedRef(name="commitment_extraction", version="v1")


class CaseMeasurement(Contract):
    id: str = Field(min_length=1, max_length=128)
    passed: bool
    provider_succeeded: bool
    schema_validated: bool
    latency_ms: int = Field(ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = None


class EvaluationReport(Contract):
    mode: Literal["live_provider", "offline_fixture"]
    registry_revision: int = Field(ge=1)
    model_id: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: Provider
    profile: Profile
    prompt: VersionedRef = REFERENCE
    output_schema: VersionedRef = REFERENCE
    corpus_version: Literal["spec005-extraction-smoke.v1"] = "spec005-extraction-smoke.v1"
    evaluated_at: datetime
    max_cost: Decimal = Field(gt=0, le=10)
    reserved_cost: Decimal = Field(ge=0)
    cases: tuple[CaseMeasurement, ...]
    production_quality_measured: Literal[False] = False

    def evidence(self) -> EvaluationEvidence:
        if self.mode != "live_provider":
            raise ValueError("Authored fixture output cannot qualify a live model")
        if tuple(c.id for c in self.cases) != tuple(c.id for c in CASES_TO_RUN):
            raise ValueError("Complete fixed corpus coverage is required")
        if self.profile not in {Profile.EXTRACTION_FAST, Profile.EXTRACTION_HIGH_ACCURACY}:
            raise ValueError("This corpus qualifies extraction profiles only")
        if self.prompt != REFERENCE or self.output_schema != REFERENCE:
            raise ValueError("The corpus prompt and schema version do not match")
        latencies = sorted(c.latency_ms for c in self.cases)
        safety = next(c for c in self.cases if c.id == "untrusted-instructions")
        return EvaluationEvidence(
            profile=self.profile,
            task_type=TaskType.EXTRACT,
            prompt=self.prompt,
            output_schema=self.output_schema,
            quality=sum(c.passed for c in self.cases) / len(self.cases),
            reliability=sum(c.provider_succeeded for c in self.cases) / len(self.cases),
            p95_latency_ms=latencies[ceil(len(latencies) * 0.95) - 1],
            samples=len(self.cases),
            safety_passed=safety.passed and safety.provider_succeeded,
            evaluated_at=self.evaluated_at,
            corpus_version=self.corpus_version,
        )


class _EvaluationGateway:
    def __init__(
        self,
        adapter: AIProviderAdapter,
        registry: RegistrySnapshot,
        model: ModelDefinition,
        max_cost: Decimal,
        secrets: tuple[SecretStr, ...],
    ) -> None:
        self.adapter, self.registry, self.model = adapter, registry, model
        self.max_cost, self.secrets = max_cost, secrets
        self.reserved = Decimal(0)
        self.measurements: dict[str, dict[str, Any]] = {}

    async def extract_operational(
        self, document: SourceDocument, *, owner_email: str | None = None
    ) -> ModelExtractionResponse:
        # This private gateway is used solely by the fixed synthetic corpus below.
        # It is never exposed as a feature gateway or an HTTP endpoint.
        prompt = next(p for p in self.registry.prompts if p.reference == REFERENCE)
        schema = next(s for s in self.registry.schemas if s.reference == REFERENCE)
        text = json.dumps(
            {
                "sources": [
                    {
                        "source_id": str(document.id),
                        "document": json.loads(
                            minimized_source_payload(document, owner_email=owner_email)
                        ),
                    }
                ],
                "user_request": "",
            }
        )
        reject_credentials(text, self.secrets)
        maximum_output = min(4000, self.model.max_output_tokens)
        bound = (
            len(text.encode())
            + len(prompt.instructions.encode())
            + len(schema.document.text.encode())
            + 2048
        )
        reservation = reserve_cost(self.model, bound, maximum_output)
        if (
            reservation is None
            or self.reserved + reservation > self.max_cost
            or bound + maximum_output > self.model.context_window
        ):
            raise AIProviderError(
                "Evaluation budget or context limit exceeded", code="budget_exceeded"
            )
        self.reserved += reservation
        started = monotonic()
        measurement: dict[str, Any] = {
            "provider_succeeded": False,
            "schema_validated": False,
            "estimated_cost": None,
            "error_code": None,
        }
        self.measurements[document.external_id] = measurement
        try:
            result = await self.adapter.execute(
                ProviderRequest(
                    task_id=uuid4(),
                    model=self.model.reference.model,
                    instructions=prompt.instructions,
                    context=JSONDocument(text=text),
                    output_schema=schema.document,
                    prompt_ref=REFERENCE,
                    schema_ref=REFERENCE,
                    max_output_tokens=maximum_output,
                )
            )
            if (
                result.model != self.model.reference.model
                or result.finish_reason != FinishReason.STOP
            ):
                raise OutputRejected("Unexpected provider result")
            measurement["provider_succeeded"] = True

            def semantic(value: Any) -> None:
                parsed = OperationalExtraction.model_validate(value).reanchor_unique_evidence(
                    document
                )
                parsed.validate_evidence(document)

            validate_output(result.output, schema.document, semantic)
            measurement["schema_validated"] = True
            cost = self.adapter.estimate_cost(result.model, result.usage)
            measurement["estimated_cost"] = cost
            if cost is not None and cost > reservation:
                raise AIProviderError("Provider usage exceeded reservation", code="budget_exceeded")
            return ModelExtractionResponse(
                output=json.loads(result.output.text),
                provider=self.adapter.provider.value,
                model=result.model,
            )
        except OutputRejected:
            measurement["error_code"] = "validation_failed"
            raise
        except AIProviderError:
            measurement["error_code"] = "budget_exceeded"
            raise
        except Exception as error:
            detail = self.adapter.classify_error(error)
            measurement["error_code"] = detail.code.value
            raise AIProviderError("Provider evaluation failed", code=detail.code.value) from None
        finally:
            measurement["latency_ms"] = max(0, int((monotonic() - started) * 1000))


async def evaluate_extraction(
    *,
    adapter: AIProviderAdapter,
    registry: RegistrySnapshot,
    model: ModelDefinition,
    policy: PolicyRules,
    profile: Profile,
    max_cost: Decimal,
    mode: Literal["live_provider", "offline_fixture"],
    secrets: tuple[SecretStr, ...] = (),
) -> EvaluationReport:
    required = {Capability.TEXT, Capability.STRUCTURED_OUTPUT}
    if (
        profile not in {Profile.EXTRACTION_FAST, Profile.EXTRACTION_HIGH_ACCURACY}
        or not any(
            g.provider == adapter.provider and Sensitivity.PUBLIC in g.sensitivities
            for g in policy.grants
        )
        or Sensitivity.PUBLIC not in model.allowed_sensitivities
        or model.reference.provider != adapter.provider
        or not required.issubset(adapter.capabilities(model.reference.model))
        or not Decimal(0) < max_cost <= min(Decimal(10), policy.max_cost)
    ):
        raise ValueError("Synthetic provider evaluation is not authorized for this configuration")
    gateway = _EvaluationGateway(adapter, registry, model, max_cost, secrets)
    measured = await run_smoke(
        gateway,
        mode="live_model_smoke" if mode == "live_provider" else "offline_fixture",
        cases=CASES_TO_RUN,
    )
    outcomes = {row["id"]: row for row in measured["cases"]}
    cases = tuple(
        CaseMeasurement(
            id=case.id,
            passed=outcomes.get(case.id, {}).get("passed", False),
            **gateway.measurements.get(
                case.id,
                {
                    "provider_succeeded": False,
                    "schema_validated": False,
                    "latency_ms": 0,
                    "error_code": "not_executed",
                },
            ),
        )
        for case in CASES_TO_RUN
    )
    return EvaluationReport(
        mode=mode,
        registry_revision=registry.revision,
        model_id=model_key(model.reference),
        model_digest=digest(canonical(model)),
        provider=model.reference.provider,
        profile=profile,
        evaluated_at=datetime.now(UTC),
        max_cost=max_cost,
        reserved_cost=gateway.reserved,
        cases=cases,
    )
