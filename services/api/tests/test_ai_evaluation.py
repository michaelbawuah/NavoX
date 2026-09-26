import json
from decimal import Decimal

import pytest
from pydantic import SecretStr, ValidationError
from test_ai_gateway_foundation import CAPABILITIES

from navox.ai.catalog import catalog_template
from navox.ai.evaluation import (
    CASES_TO_RUN,
    EvaluationReport,
    diagnose_extraction,
    evaluate_extraction,
)
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


class RejectedProposalAdapter(CorpusFixtureAdapter):
    def __init__(self, mutation):
        super().__init__(Provider.OPENAI)
        self.mutation = mutation

    async def execute(self, request):
        result = await super().execute(request)
        value = json.loads(result.output.text)
        if self.mutation == "object":
            value["observations"][0]["object_text"] = "fabricated obligation"
        elif self.mutation == "schema":
            value["unsupported_field"] = "synthetic invalid output"
        elif self.mutation == "credential":
            value["observations"][0]["object_text"] = "test-protected-value"
        else:
            value["observations"][0]["evidence"][0]["text"] = "fabricated quotation"
        return result.model_copy(update={"output": JSONDocument(text=json.dumps(value))})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,code",
    [
        ("object", "object_not_grounded"),
        ("schema", "schema_invalid"),
        ("evidence", "evidence_text_mismatch"),
    ],
)
async def test_rejected_synthetic_outputs_retain_known_cost_and_fixed_validation_code(
    mutation, code
):
    adapter = RejectedProposalAdapter(mutation)
    report = await diagnose_extraction(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        case_ids=("explicit-waiting",),
        mode="offline_fixture",
    )
    case = report.cases[0]
    assert len(adapter.calls) == 1
    assert case.measurement.provider_succeeded and not case.measurement.passed
    assert not case.measurement.schema_validated
    assert case.measurement.validation_code == code
    assert case.measurement.estimated_cost == Decimal("0.00001")
    assert case.proposal is not None
    assert case.expected_types == ("waiting",)
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate_json(report.model_dump_json())


@pytest.mark.asyncio
async def test_regular_evaluation_keeps_validation_diagnostics_but_never_proposals():
    report = await evaluate_extraction(
        adapter=RejectedProposalAdapter("schema"),
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        mode="offline_fixture",
    )
    assert report.cases[0].validation_code == "schema_invalid"
    assert report.cases[0].estimated_cost == Decimal("0.00001")
    assert "synthetic invalid output" not in report.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ids", [(), ("private-mail-id",), ("explicit-waiting", "explicit-waiting")]
)
async def test_diagnostics_reject_arbitrary_or_duplicate_sources_before_provider_use(ids):
    adapter = CorpusFixtureAdapter(Provider.OPENAI)
    with pytest.raises(ValueError, match="fixed synthetic corpus"):
        await diagnose_extraction(
            adapter=adapter,
            registry=catalog_template(),
            model=model_for(Provider.OPENAI),
            policy=policy_for(Provider.OPENAI),
            profile=Profile.EXTRACTION_HIGH_ACCURACY,
            max_cost=Decimal("2"),
            case_ids=ids,
            mode="offline_fixture",
        )
    assert not adapter.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "budget", "credential"])
async def test_diagnostics_preserve_policy_budget_and_secret_boundaries(failure):
    adapter = RejectedProposalAdapter("credential")
    kwargs = dict(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=PolicyRules() if failure == "permission" else policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("0.000001") if failure == "budget" else Decimal("2"),
        case_ids=("explicit-waiting",),
        mode="offline_fixture",
        secrets=(SecretStr("test-protected-value"),),
    )
    if failure == "permission":
        with pytest.raises(ValueError, match="not authorized"):
            await diagnose_extraction(**kwargs)
        assert not adapter.calls
        return
    report = await diagnose_extraction(**kwargs)
    assert not report.cases[0].measurement.passed
    assert report.cases[0].proposal is None
    assert "test-protected-value" not in report.model_dump_json()
    if failure == "budget":
        assert not adapter.calls


@pytest.mark.asyncio
async def test_old_extraction_rubric_remains_readable_but_cannot_qualify_current_model():
    report = await evaluate_extraction(
        adapter=CorpusFixtureAdapter(Provider.OPENAI),
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        mode="live_provider",
    )
    assert report.evidence().quality == 1
    previous = report.model_dump(mode="json")
    previous["corpus_version"] = "spec005-extraction-smoke.v1"
    for case in previous["cases"]:
        case.pop("validation_code")
        case.pop("outcome_reason")
    historical = EvaluationReport.model_validate(previous)
    with pytest.raises(ValueError, match="current extraction rubric"):
        historical.evidence()


def test_diagnostic_cli_requires_file_before_loading_configuration(monkeypatch, capsys):
    from navox.ai import manage

    def forbidden():
        raise AssertionError("Configuration must not be loaded")

    monkeypatch.setattr(manage, "Settings", forbidden)
    assert (
        main(
            [
                "diagnose",
                "--live",
                "--model",
                "openai:fixture",
                "--profile",
                "EXTRACTION_HIGH_ACCURACY",
                "--case",
                "explicit-waiting",
                "--max-cost",
                "0.03",
            ]
        )
        == 2
    )
    assert "operator_command_rejected" in capsys.readouterr().out


def test_diagnostic_cli_keeps_candidates_in_explicit_file_and_only_prints_summary(
    monkeypatch, tmp_path, capsys
):
    from navox.ai import manage

    async def run(args):
        return {
            "report_type": "extraction_diagnostic",
            "model_id": "openai:fixture",
            "cases": [
                {
                    "measurement": {"id": "explicit-waiting", "passed": False},
                    "proposal": "synthetic candidate text",
                }
            ],
        }

    monkeypatch.setattr(manage, "run", run)
    path = tmp_path / "diagnostic.json"
    assert (
        main(
            [
                "diagnose",
                "--live",
                "--model",
                "openai:fixture",
                "--profile",
                "EXTRACTION_HIGH_ACCURACY",
                "--case",
                "explicit-waiting",
                "--max-cost",
                "0.03",
                "--output",
                str(path),
            ]
        )
        == 1
    )
    assert "synthetic candidate text" in path.read_text()
    output = capsys.readouterr().out
    assert "synthetic candidate text" not in output
    assert json.loads(output)["qualifies_for_promotion"] is False
