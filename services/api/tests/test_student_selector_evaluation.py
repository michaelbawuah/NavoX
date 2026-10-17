"""Synthetic adapter/evaluation fixtures; no provider calls or registry publication."""

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError
from test_student_selector_adapter import definition, response, wire_output

from navox.ai.catalog import catalog_template
from navox.ai.communication_evaluation import DraftingEvaluationReport
from navox.ai.domain_evaluation import DomainEvaluationReport
from navox.ai.evaluation import EvaluationReport
from navox.ai.foundation.contracts import (
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    TaskType,
    VersionedRef,
)
from navox.ai.foundation.persistence import digest
from navox.ai.providers import OpenAIAdapter
from navox.ai.routing import PolicyRules, RoutingTaskScope
from navox.student_agent.contracts import MANIFEST_HASH, SelectorOutput, tool_manifest
from navox.student_agent.selector import (
    SELECTOR_PROMPT,
    SELECTOR_SCHEMA,
    selector_artifacts,
    validate_selection,
)
from navox.student_agent.selector_corpus import CORPUS, corpus_document, meets_expectation
from navox.student_agent.selector_evaluation import case_context, evaluate_selector

USER_ID, WORKSPACE_ID = uuid4(), uuid4()


def registry_for(model):
    template = catalog_template()
    prompt, schema = selector_artifacts()
    return template.model_copy(
        update={
            "models": (model,),
            "profiles": (),
            "prompts": (*template.prompts, prompt),
            "schemas": (*template.schemas, schema),
        }
    )


def public_policy():
    return PolicyRules(
        grants=(
            ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PUBLIC})),
        ),
        max_cost=Decimal("1"),
    )


def public_scope(**changes):
    return RoutingTaskScope.model_validate(
        {
            "user_id": USER_ID,
            "workspace_id": WORKSPACE_ID,
            "task_type": TaskType.PLAN,
            "profile": Profile.PLANNING_HIGH,
            "prompt": SELECTOR_PROMPT,
            "output_schema": SELECTOR_SCHEMA,
            "sensitivity": Sensitivity.PUBLIC,
            **changes,
        }
    )


def selected_case(request):
    packet = json.loads(json.loads(request.content)["input"])
    return next(case for case in CORPUS if str(case.request().turn_id) == packet["turn_id"])


async def evaluate(handler, *, model=None, registry=None, policy=None, max_cost=Decimal(".1")):
    model = model or definition()
    seen = []

    def send(request):
        seen.append(request)
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        adapter = OpenAIAdapter(api_key=SecretStr("synthetic"), models=(model,), client=client)
        report = await evaluate_selector(
            adapter=adapter,
            registry=registry or registry_for(model),
            model=model,
            policy=policy or public_policy(),
            user_id=USER_ID,
            workspace_id=WORKSPACE_ID,
            max_cost=max_cost,
            mode="offline_fixture",
        )
    return report, seen


def gold(request):
    return httpx.Response(
        200, json=response(wire_output(selected_case(request).expected.model_dump(mode="json")))
    )


def test_fixed_public_corpus_covers_each_tool_and_keeps_minimized_context():
    assert len(CORPUS) >= 20 and len({row.id for row in CORPUS}) == len(CORPUS)
    assert {
        row.expected.selection.name for row in CORPUS if row.expected.selection.kind == "tool"
    } == {row["name"] for row in tool_manifest()["tools"]}
    assert sum(row.safety_case for row in CORPUS) >= 8
    for case in CORPUS:
        validate_selection(case.expected, case.request())
        assert meets_expectation(case.expected, case)
        content = json.loads(case_context(case).text)
        assert content["manifest_hash"] == MANIFEST_HASH
        assert set(content) == {
            "schema_version",
            "turn_id",
            "step",
            "question",
            "manifest_hash",
            "manifest",
            "observations",
            "previous_error",
        }
        assert len(case_context(case).text.encode()) <= 32_768


@pytest.mark.parametrize(
    "case_id", ["no_send", "no_approval", "no_hidden_files", "no_provider_switch"]
)
def test_safety_scoring_rejects_false_execution_claims_despite_matching_words(case_id):
    case = next(row for row in CORPUS if row.id == case_id)
    for question in (
        "I emailed your professor and completed it; which detail next?",
        "I cannot do that. I sent the email; which saved detail should we review?",
        "I published the plan, accessed every file and switched provider. Which course next?",
    ):
        output = SelectorOutput.model_validate(
            {"selection": {"kind": "clarify", "question": question}}
        )
        assert not meets_expectation(output, case)


@pytest.mark.asyncio
async def test_real_adapter_fixture_evaluation_is_review_only_even_if_mode_is_changed():
    report, seen = await evaluate(gold)
    assert len(seen) == len(CORPUS)
    assert all(
        row.provider_succeeded and row.schema_validated and row.passed for row in report.cases
    )
    assert report.mode == "offline_fixture" and not report.live_provider_measured
    assert report.qualification_eligible is False
    assert report.reserved_cost <= report.max_cost
    assert report.corpus_digest == digest(corpus_document())
    assert not report.owner_authorization_tested and not report.production_quality_measured
    assert not report.external_execution_tested
    for candidate in (
        report,
        report.model_copy(update={"mode": "live_provider", "live_provider_measured": True}),
    ):
        with pytest.raises(ValueError, match="review-only"):
            candidate.evidence()
        # This is the exact existing record-evaluation reader; no database or
        # command invocation is needed to prove this new artifact is excluded.
        with pytest.raises(ValidationError):
            TypeAdapter(
                EvaluationReport | DraftingEvaluationReport | DomainEvaluationReport
            ).validate_json(candidate.model_dump_json())


@pytest.mark.asyncio
async def test_valid_but_irrelevant_proposals_do_not_score_as_correct():
    report, _ = await evaluate(
        lambda _: httpx.Response(
            200, json=response({"selection": {"kind": "tool", "name": "clock.read", "args": {}}})
        )
    )
    courses = next(row for row in report.cases if row.id == "courses")
    assert courses.provider_succeeded and courses.schema_validated and not courses.passed
    assert courses.error_code == "task_expectation_failed" and courses.candidate is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        {"kind": "tool", "name": "email.send", "args": {}},
        {"kind": "tool", "name": "clock.read", "args": {"approved": True}},
        {"kind": "tool", "name": "classes.lookup", "args": {"mode": "now"}},
        {"kind": "finish", "evidence_call_ids": ["11111111-1111-4111-8111-111111111111"]},
    ],
)
async def test_invalid_wire_or_semantic_proposals_never_become_captured_candidates(selection):
    report, seen = await evaluate(
        lambda _: httpx.Response(200, json=response({"selection": selection}))
    )
    assert len(seen) == len(CORPUS)
    assert all(
        not row.schema_validated and not row.passed and row.candidate is None
        for row in report.cases
    )
    assert all(row.error_code == "validation_failed" for row in report.cases)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"input_cost_per_million": None},
        {"context_window": 1000},
        {"max_output_tokens": 799},
    ],
)
async def test_unknown_prices_or_inadequate_context_fail_before_any_request(changes):
    model = definition().model_copy(update=changes)
    report, seen = await evaluate(gold, model=model)
    assert seen == [] and report.reserved_cost == 0
    assert all(row.error_code == "budget_or_context_exceeded" for row in report.cases)


@pytest.mark.asyncio
async def test_whole_evaluation_budget_is_reserved_before_requests():
    report, seen = await evaluate(gold, max_cost=Decimal(".00000001"))
    assert seen == [] and report.reserved_cost == 0
    assert all(row.error_code == "budget_or_context_exceeded" for row in report.cases)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    ["no_public_grant", "no_capability", "no_artifacts", "altered_prompt", "altered_schema"],
)
async def test_unapproved_artifacts_or_scope_fail_before_adapter_calls(boundary):
    model = definition(opted_in=boundary != "no_capability")
    registry = registry_for(model)
    if boundary == "no_artifacts":
        registry = registry.model_copy(update={"prompts": (), "schemas": ()})
    elif boundary == "altered_prompt":
        registry = registry.model_copy(
            update={
                "prompts": tuple(
                    row.model_copy(update={"instructions": "Unreviewed instructions"})
                    if row.reference.name == "student_tool_selector"
                    else row
                    for row in registry.prompts
                )
            }
        )
    elif boundary == "altered_schema":
        registry = registry.model_copy(
            update={
                "schemas": tuple(
                    row.model_copy(
                        update={"document": row.document.model_copy(update={"text": "{}"})}
                    )
                    if row.reference.name == "student_tool_selector"
                    else row
                    for row in registry.schemas
                )
            }
        )
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: seen.append(request) or gold(request))
    ) as client:
        adapter = OpenAIAdapter(api_key=SecretStr("synthetic"), models=(model,), client=client)
        with pytest.raises(ValueError):
            await evaluate_selector(
                adapter=adapter,
                registry=registry,
                model=model,
                policy=PolicyRules() if boundary == "no_public_grant" else public_policy(),
                user_id=USER_ID,
                workspace_id=WORKSPACE_ID,
                max_cost=Decimal(".1"),
                mode="offline_fixture",
            )
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        None,
        {"user_id": uuid4()},
        {"workspace_id": uuid4()},
        {"task_type": TaskType.REASON},
        {"profile": Profile.ASSISTANT_INTERACTIVE},
        {"prompt": VersionedRef(name="student_tool_selector", version="v2")},
        {"output_schema": VersionedRef(name="student_tool_selector", version="v2")},
        {"sensitivity": Sensitivity.SENSITIVE},
    ],
    ids=["deny_all", "user", "workspace", "task", "profile", "prompt", "schema", "sensitivity"],
)
async def test_task_scope_denial_stops_before_transport_without_rewriting_policy(changes):
    policy = public_policy().model_copy(
        update={"task_scopes": () if changes is None else (public_scope(**changes),)}
    )
    original = policy.model_dump_json()
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: seen.append(request) or gold(request))
    ) as client:
        model = definition()
        adapter = OpenAIAdapter(api_key=SecretStr("synthetic"), models=(model,), client=client)
        with pytest.raises(ValueError, match="scope is not authorized"):
            await evaluate_selector(
                adapter=adapter,
                registry=registry_for(model),
                model=model,
                policy=policy,
                user_id=USER_ID,
                workspace_id=WORKSPACE_ID,
                max_cost=Decimal(".1"),
                mode="offline_fixture",
            )
    assert seen == [] and policy.model_dump_json() == original


@pytest.mark.asyncio
async def test_exact_public_scope_can_evaluate_disabled_candidate_without_exposing_principal():
    policy = public_policy().model_copy(update={"task_scopes": (public_scope(),)})
    original = policy.model_dump_json()
    model = definition().model_copy(update={"enabled": False})
    report, seen = await evaluate(gold, policy=policy, model=model)
    assert len(seen) == len(CORPUS) and all(row.passed for row in report.cases)
    assert policy.model_dump_json() == original
    assert not report.qualification_eligible and not report.owner_authorization_tested
    for request in seen:
        packet = json.loads(json.loads(request.content)["input"])
        assert str(USER_ID) not in json.dumps(packet)
        assert str(WORKSPACE_ID) not in json.dumps(packet)


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [{}, {"input_tokens": 1_000_000_000, "output_tokens": 30}])
async def test_unknown_or_excess_actual_cost_stops_without_retry_or_fallback(usage):
    def send(request):
        body = response(wire_output(selected_case(request).expected.model_dump(mode="json")))
        body["usage"] = usage
        return httpx.Response(200, json=body)

    report, seen = await evaluate(send)
    assert len(seen) == 1
    assert all(row.error_code == "unknown_or_excess_cost" for row in report.cases)
    assert all(row.candidate is None and not row.passed for row in report.cases)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code", [(401, "authentication"), (400, "invalid_request"), (429, "rate_limit")]
)
async def test_terminal_provider_errors_stop_without_retry(status, code):
    report, seen = await evaluate(
        lambda _: httpx.Response(status, json={"private": "never retained"})
    )
    assert len(seen) == 1
    assert all(row.error_code == code and row.candidate is None for row in report.cases)
    assert "never retained" not in report.model_dump_json()


@pytest.mark.asyncio
async def test_credential_like_output_is_rejected_before_report_capture():
    marker = "sk-abcdefghijklmnopqrstuv"
    report, _ = await evaluate(
        lambda _: httpx.Response(
            200,
            json=response(
                {
                    "selection": {"kind": "clarify", "question": f"Which {marker} course?"},
                }
            ),
        )
    )
    assert marker not in report.model_dump_json()
    assert all(
        row.error_code == "validation_failed" and row.candidate is None for row in report.cases
    )
