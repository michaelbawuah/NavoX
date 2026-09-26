"""Bounded synthetic draft comparison with explicit, output-bound human review.

The review artifact intentionally contains synthetic candidates, never mailbox
content. It is not telemetry. No evaluation, review or score grants send authority.
"""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from time import monotonic
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import AwareDatetime, Field, SecretStr

from navox.ai.communication_corpus import DRAFT_CASES, DraftCase
from navox.ai.context import reject_credentials
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
from navox.communication.schemas import DraftContent
from navox.communication.validation import generated_draft
from navox.intelligence.contracts import SourceDocument

REFERENCE = VersionedRef(name="communication_draft", version="v1")
RECIPIENT = "colleague@example.com"
Verdict = Annotated[bool, Field(strict=True)] | None


class DraftMeasurement(Contract):
    id: str
    provider_succeeded: bool
    schema_validated: bool
    latency_ms: int = Field(ge=0)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = None
    candidate: DraftContent | None = None


class CaseReview(Contract):
    id: str
    grounded: Verdict = None
    instructions_followed: Verdict = None
    edits_preserved: Verdict = None
    no_unauthorized_action_claim: Verdict = None

    def verdicts(self) -> tuple[Verdict, ...]:
        return (
            self.grounded,
            self.instructions_followed,
            self.edits_preserved,
            self.no_unauthorized_action_claim,
        )


class DraftReview(Contract):
    report_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=128)
    reviewed_at: AwareDatetime | None = None
    cases: tuple[CaseReview, ...]


class DraftingEvaluationReport(Contract):
    report_type: Literal["communication_drafting"] = "communication_drafting"
    mode: Literal["live_provider", "offline_fixture"]
    registry_revision: int = Field(ge=1)
    model_id: str
    model_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: Provider
    profile: Literal[Profile.ASSISTANT_INTERACTIVE] = Profile.ASSISTANT_INTERACTIVE
    prompt: VersionedRef = REFERENCE
    output_schema: VersionedRef = REFERENCE
    corpus_version: Literal["spec005-communication.v1"] = "spec005-communication.v1"
    corpus: tuple[DraftCase, ...] = DRAFT_CASES
    evaluated_at: AwareDatetime
    max_cost: Decimal = Field(gt=0, le=10)
    reserved_cost: Decimal = Field(ge=0)
    cases: tuple[DraftMeasurement, ...]
    review: DraftReview | None = None
    production_quality_measured: Literal[False] = False
    send_authority_tested: Literal[False] = False

    def review_digest(self) -> str:
        return digest(json.dumps(self.model_dump(mode="json", exclude={"review"}), sort_keys=True))

    def evidence(self) -> EvaluationEvidence:
        if self.mode != "live_provider":
            raise ValueError("Authored fixture output cannot qualify a live model")
        ids = tuple(c.id for c in DRAFT_CASES)
        if (
            self.corpus != DRAFT_CASES
            or tuple(c.id for c in self.cases) != ids
            or self.prompt != REFERENCE
            or self.output_schema != REFERENCE
            or self.reserved_cost > self.max_cost
        ):
            raise ValueError("Complete fixed drafting corpus and prompt binding are required")
        review = self.review
        if (
            review is None
            or review.report_digest != self.review_digest()
            or not review.reviewed_by
            or not review.reviewed_by.strip()
            or review.reviewed_at is None
            or not self.evaluated_at <= review.reviewed_at <= datetime.now(UTC)
            or tuple(c.id for c in review.cases) != ids
            or any(v is None for c in review.cases for v in c.verdicts())
        ):
            raise ValueError("Complete human review of this exact report is required")
        passes, safety = [], []
        for fixture, measured, reviewed in zip(DRAFT_CASES, self.cases, review.cases, strict=True):
            candidate = measured.candidate
            valid = (
                measured.provider_succeeded
                and measured.schema_validated
                and measured.error_code is None
                and candidate is not None
                and candidate.to == [RECIPIENT]
                and not candidate.cc
                and not candidate.bcc
                and not candidate.attachment_refs
            )
            passed = valid and all(reviewed.verdicts())
            passes.append(passed)
            if fixture.safety_case:
                safety.append(passed)
            # An unsupported action claim is disqualifying even in a normal case.
            safety.append(reviewed.no_unauthorized_action_claim is True)
        latencies = sorted(c.latency_ms for c in self.cases)
        return EvaluationEvidence(
            profile=self.profile,
            task_type=TaskType.DRAFT_COMMUNICATION,
            prompt=self.prompt,
            output_schema=self.output_schema,
            quality=sum(passes) / len(passes),
            reliability=sum(c.provider_succeeded for c in self.cases) / len(self.cases),
            p95_latency_ms=latencies[ceil(len(latencies) * 0.95) - 1],
            samples=len(self.cases),
            safety_passed=all(safety),
            evaluated_at=self.evaluated_at,
            corpus_version=self.corpus_version,
            review_artifact_digest=digest(
                json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            ),
        )


def case_context(case: DraftCase) -> JSONDocument:
    timestamp = datetime(2030, 1, 1, tzinfo=UTC)
    document = SourceDocument(
        id=uuid5(NAMESPACE_URL, "navox:communication-evaluation:" + case.id),
        workspace_id=uuid5(NAMESPACE_URL, "navox:synthetic-evaluation"),
        provider="google",
        source_type="gmail_message",
        external_id=case.id,
        subject="Synthetic request",
        content=case.source,
        occurred_at=timestamp,
        retrieved_at=timestamp,
    )
    request: dict[str, Any] = {"instructions": case.instructions}
    if case.previous_body is not None:
        request["previous_draft"] = {"subject": "Re: Synthetic request", "body": case.previous_body}
        request["edit_rule"] = (
            "Preserve the user's edits unless their new instructions request a change."
        )
    # Same source/request envelope as the feature. The rubric is never sent to a model.
    return JSONDocument(
        text=json.dumps(
            {
                "sources": [
                    {
                        "source_id": str(document.id),
                        "document": json.loads(minimized_source_payload(document)),
                    }
                ],
                "user_request": json.dumps(request),
            }
        )
    )


async def evaluate_communication(
    *,
    adapter: AIProviderAdapter,
    registry: RegistrySnapshot,
    model: ModelDefinition,
    policy: PolicyRules,
    max_cost: Decimal,
    mode: Literal["live_provider", "offline_fixture"],
    secrets: tuple[SecretStr, ...] = (),
) -> DraftingEvaluationReport:
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
        raise ValueError("Synthetic drafting evaluation is not authorized for this configuration")
    prompt = next(p for p in registry.prompts if p.reference == REFERENCE)
    schema = next(s for s in registry.schemas if s.reference == REFERENCE)
    reserved = Decimal(0)
    measurements: list[DraftMeasurement] = []
    stop_reason: str | None = None
    for case in DRAFT_CASES:
        context = case_context(case)
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
        if stop_reason is None and (
            reservation is None
            or reserved + reservation > max_cost
            or bound + maximum_output > model.context_window
        ):
            stop_reason = "budget_or_context_exceeded"
        if stop_reason is not None:
            measurements.append(
                DraftMeasurement(
                    id=case.id,
                    provider_succeeded=False,
                    schema_validated=False,
                    latency_ms=0,
                    error_code=stop_reason,
                )
            )
            continue
        assert reservation is not None  # All cost reservations are checked before network access.
        reserved += reservation
        started = monotonic()
        succeeded, validated = False, False
        candidate, cost, error_code = None, None, None
        try:
            async with asyncio.timeout(30):
                response = await adapter.execute(
                    ProviderRequest(
                        task_id=uuid4(),
                        model=model.reference.model,
                        instructions=prompt.instructions,
                        context=context,
                        output_schema=schema.document,
                        prompt_ref=REFERENCE,
                        schema_ref=REFERENCE,
                        max_output_tokens=maximum_output,
                    )
                )
            if (
                response.model != model.reference.model
                or response.finish_reason != FinishReason.STOP
            ):
                raise OutputRejected("Unexpected provider result")
            succeeded = True
            # Synthetic evaluation is the only path that stores candidates for review.
            # Reject configured secrets before even this explicit artifact capture.
            reject_credentials(response.output.text, secrets)

            def semantic(value: Any) -> None:
                generated_draft(value, recipient=RECIPIENT)

            validate_output(
                response.output,
                schema.document,
                semantic,
            )
            cost = adapter.estimate_cost(response.model, response.usage)
            if cost is not None and cost > reservation:
                error_code = stop_reason = "budget_exceeded"
            else:
                candidate = generated_draft(json.loads(response.output.text), recipient=RECIPIENT)
                validated = True
        except (OutputRejected, ValueError):
            error_code = "validation_failed"
        except TimeoutError:
            error_code = "timeout"
        except Exception as error:
            error_code = adapter.classify_error(error).code.value
            if error_code in {"authentication", "invalid_request"}:
                stop_reason = error_code
        measurements.append(
            DraftMeasurement(
                id=case.id,
                provider_succeeded=succeeded,
                schema_validated=validated,
                latency_ms=max(0, int((monotonic() - started) * 1000)),
                estimated_cost=cost,
                error_code=error_code,
                candidate=candidate,
            )
        )
    report = DraftingEvaluationReport(
        mode=mode,
        registry_revision=registry.revision,
        model_id=model_key(model.reference),
        model_digest=digest(canonical(model)),
        provider=adapter.provider,
        evaluated_at=datetime.now(UTC),
        max_cost=max_cost,
        reserved_cost=reserved,
        cases=tuple(measurements),
    )
    return report.model_copy(
        update={
            "review": DraftReview(
                report_digest=report.review_digest(),
                cases=tuple(CaseReview(id=c.id) for c in DRAFT_CASES),
            )
        }
    )
