import json
from decimal import Decimal

import pytest
from test_ai_gateway_foundation import CAPABILITIES

from navox.ai.catalog import catalog_template
from navox.ai.evaluation import CASES_TO_RUN, evaluate_extraction
from navox.ai.foundation.adapter import ErrorCode, ProviderError, ProviderResponse
from navox.ai.foundation.contracts import (
    FinishReason,
    JSONDocument,
    Profile,
    Provider,
    ProviderGrant,
    Sensitivity,
    Usage,
)
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.manage import main
from navox.ai.routing import PolicyRules
from navox.evaluation.intelligence_smoke import OfflineSmokeProvider


class CorpusFixtureAdapter:
    def __init__(self, provider, fail=False, empty=False):
        self.provider, self.fail, self.empty = provider, fail, empty
        self.calls = []

    def capabilities(self, model):
        return CAPABILITIES

    def classify_error(self, error):
        return ProviderError(code=ErrorCode.UNAVAILABLE)

    def estimate_cost(self, model, usage):
        return Decimal("0.00001")

    async def execute(self, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("Untrusted upstream body must not appear in the report")
        document = json.loads(request.context.text)["sources"][0]["document"]
        result = await OfflineSmokeProvider().generate_json(
            schema_name="test",
            schema={},
            instructions="",
            input_text=json.dumps(document),
        )
        if self.empty:
            result.data["observations"] = []
        return ProviderResponse(
            model=request.model,
            output=JSONDocument(text=json.dumps(result.data)),
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=10, output_tokens=10),
        )


def model_for(provider):
    return ModelDefinition(
        reference=ModelRef(provider=provider, model="fixture-corpus-model"),
        capabilities=CAPABILITIES,
        allowed_sensitivities=frozenset({Sensitivity.PUBLIC}),
        context_window=100_000,
        max_output_tokens=4000,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    )


def policy_for(provider):
    return PolicyRules(
        grants=(ProviderGrant(provider=provider, sensitivities=frozenset({Sensitivity.PUBLIC})),),
        max_cost=Decimal("2"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", tuple(Provider))
async def test_identical_spec002_corpus_measured_without_promoting_authored_responses(provider):
    adapter = CorpusFixtureAdapter(provider)
    report = await evaluate_extraction(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(provider),
        policy=policy_for(provider),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        mode="offline_fixture",
    )
    assert len(adapter.calls) == len(CASES_TO_RUN)
    assert all(
        row.passed and row.schema_validated and row.provider_succeeded for row in report.cases
    )
    assert report.reserved_cost <= Decimal("2")
    assert not report.production_quality_measured
    assert "Please send the budget" not in report.model_dump_json()
    with pytest.raises(ValueError, match="Authored fixture"):
        report.evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "budget", "provider", "empty"])
async def test_evaluation_counts_denials_failures_and_missing_observations(failure):
    adapter = CorpusFixtureAdapter(
        Provider.OPENAI, fail=failure == "provider", empty=failure == "empty"
    )
    policy = PolicyRules() if failure == "permission" else policy_for(Provider.OPENAI)
    kwargs = dict(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=policy,
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("0.000001") if failure == "budget" else Decimal("2"),
        mode="offline_fixture",
    )
    if failure == "permission":
        with pytest.raises(ValueError, match="not authorized"):
            await evaluate_extraction(**kwargs)
        assert not adapter.calls
    else:
        report = await evaluate_extraction(**kwargs)
        assert not all(c.passed for c in report.cases)
        assert len(report.cases) == len(CASES_TO_RUN)
        if failure == "budget":
            assert not adapter.calls and report.reserved_cost == 0
        if failure == "provider":
            assert len(adapter.calls) == 1
            assert "Untrusted upstream" not in report.model_dump_json()


def test_template_is_complete_but_enables_no_model_or_traffic(capsys):
    assert main(["template"]) == 0
    template = json.loads(capsys.readouterr().out)
    assert {p["profile"] for p in template["profiles"]} == {p.value for p in Profile}
    assert not template["models"] and all(not p["assignments"] for p in template["profiles"])
    assert {p["reference"]["name"] for p in template["prompts"]} >= {
        "commitment_extraction",
        "communication_draft",
        "subscription_extraction",
        "planning",
        "meeting_preparation",
        "assistant",
    }
