import json
from argparse import Namespace
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from test_ai_gateway_foundation import CAPABILITIES

from navox.ai.catalog import catalog_template
from navox.ai.evaluation import (
    CASES_TO_RUN,
    EvaluationReport,
    ExtractionDiagnosticReport,
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
from navox.ai.foundation.persistence import canonical, digest
from navox.ai.foundation.registry import ModelDefinition, ModelRef
from navox.ai.manage import main
from navox.ai.prompts import EXTRACTION_PROMPT, EXTRACTION_SCHEMA
from navox.ai.routing import PolicyRules
from navox.evaluation.intelligence_smoke import OfflineSmokeProvider

# Audio vocabulary is published only by an explicit operator opt-in (M11A), so the
# default catalog template must not advertise these profiles yet.
SPEECH_PROFILES = (Profile.SPEECH_TRANSCRIPTION, Profile.SPEECH_SYNTHESIS)


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
    assert report.prompt == EXTRACTION_PROMPT and report.output_schema == EXTRACTION_SCHEMA
    assert all(
        request.prompt_ref == EXTRACTION_PROMPT and request.schema_ref == EXTRACTION_SCHEMA
        for request in adapter.calls
    )
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
    assert {p["profile"] for p in template["profiles"]} == {p.value for p in Profile} - {
        p.value for p in SPEECH_PROFILES
    }
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
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_old_extraction_rubric_remains_readable_but_cannot_qualify_current_model(version):
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
    previous["corpus_version"] = f"spec005-extraction-smoke.{version}"
    previous["prompt"] = {"name": "commitment_extraction", "version": "v1"}
    for case in previous["cases"]:
        case.pop("validation_code")
        case.pop("outcome_reason")
    historical = EvaluationReport.model_validate(previous)
    with pytest.raises(ValueError, match="current extraction rubric"):
        historical.evidence()
    historical_payload = historical.model_dump(mode="json")
    historical_payload["corpus_version"] = report.corpus_version
    with pytest.raises(ValueError, match="prompt and schema version"):
        EvaluationReport.model_validate(historical_payload).evidence()


def test_new_extraction_prompt_preserves_published_v1_prompt_and_schema():
    catalog = catalog_template()
    previous = next(p for p in catalog.prompts if p.reference == EXTRACTION_SCHEMA)
    schema = next(s for s in catalog.schemas if s.reference == EXTRACTION_SCHEMA)
    assert (
        digest(canonical(previous))
        == "7194d527891262a021b985597806bac16a6a6576e6a530afa7618130a0befd42"
    )
    assert (
        digest(canonical(schema))
        == "3b9ac726a3cb65dcfe630e11d51d6a4359ea2ed1732ba6c8358c73ed6489f066"
    )
    current = next(p for p in catalog.prompts if p.reference == EXTRACTION_PROMPT)
    assert current.output_schema == schema.reference
    assert current.instructions != previous.instructions


@pytest.mark.asyncio
async def test_legacy_catalog_requires_explicit_prompt_publication_before_provider_calls():
    catalog = catalog_template()
    historical = catalog.model_copy(
        update={"prompts": tuple(p for p in catalog.prompts if p.reference != EXTRACTION_PROMPT)}
    )
    adapter = CorpusFixtureAdapter(Provider.OPENAI)
    with pytest.raises(ValueError, match="Publish the current extraction prompt"):
        await evaluate_extraction(
            adapter=adapter,
            registry=historical,
            model=model_for(Provider.OPENAI),
            policy=policy_for(Provider.OPENAI),
            profile=Profile.EXTRACTION_HIGH_ACCURACY,
            max_cost=Decimal("2"),
            mode="offline_fixture",
        )
    assert not adapter.calls


REJECTED_CAPTURES = json.loads(
    (Path(__file__).parent / "fixtures/spec005_r2_rejected_extractions.json").read_text()
)["cases"]


@pytest.mark.asyncio
@pytest.mark.parametrize("capture", REJECTED_CAPTURES, ids=lambda c: c["id"])
async def test_captured_ungrounded_outputs_still_fail_under_new_prompt(capture):
    class CapturedAdapter(CorpusFixtureAdapter):
        async def execute(self, request):
            response = await super().execute(request)
            return response.model_copy(
                update={"output": JSONDocument(text=json.dumps(capture["proposal"]))}
            )

    adapter = CapturedAdapter(Provider(capture["provider"]))
    report = await diagnose_extraction(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(adapter.provider),
        policy=policy_for(adapter.provider),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        case_ids=(capture["case_id"],),
        mode="offline_fixture",
    )
    measured = report.cases[0].measurement
    assert measured.provider_succeeded and not measured.schema_validated and not measured.passed
    assert measured.validation_code == capture["validation_code"]


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


class WaitingFailureAdapter(CorpusFixtureAdapter):
    def __init__(self, *, credential=False):
        super().__init__(Provider.OPENAI)
        self.credential = credential
        self.rejected_output = None

    async def execute(self, request):
        result = await super().execute(request)
        document = json.loads(request.context.text)["sources"][0]["document"]
        if document["external_id"] != "explicit-waiting":
            return result
        value = json.loads(result.output.text)
        value["unsupported_field"] = (
            "test-protected-value" if self.credential else "captured synthetic rejected proposal"
        )
        self.rejected_output = JSONDocument(text=json.dumps(value))
        return result.model_copy(update={"output": self.rejected_output})


@pytest.mark.asyncio
async def test_full_evaluation_captures_the_same_failed_response_without_retrying_or_rescoring():
    adapter = WaitingFailureAdapter()
    captures = []
    report = await evaluate_extraction(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        mode="offline_fixture",
        failure_capture=captures.append,
    )
    assert len(adapter.calls) == len(CASES_TO_RUN) == len(report.cases)
    assert sum(case.passed for case in report.cases) == len(CASES_TO_RUN) - 1
    assert len(captures) == 1
    capture = captures[0]
    assert len(capture.cases) == 1
    case = capture.cases[0]
    measurement = next(row for row in report.cases if row.id == "explicit-waiting")
    assert case.measurement == measurement
    assert not measurement.passed and measurement.validation_code == "schema_invalid"
    assert case.proposal == adapter.rejected_output
    request = next(
        call
        for call in adapter.calls
        if json.loads(call.context.text)["sources"][0]["document"]["external_id"]
        == "explicit-waiting"
    )
    assert (
        json.loads(case.source.text) == json.loads(request.context.text)["sources"][0]["document"]
    )
    assert capture.evaluated_at == report.evaluated_at
    assert capture.model_digest == report.model_digest
    assert capture.registry_revision == report.registry_revision
    assert capture.reserved_cost == report.reserved_cost <= report.max_cost
    assert not capture.qualifies_for_promotion
    assert "captured synthetic rejected proposal" not in report.model_dump_json()
    assert "captured synthetic rejected proposal" in capture.model_dump_json()
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate_json(capture.model_dump_json())


@pytest.mark.asyncio
async def test_successful_full_evaluation_emits_an_empty_failure_capture():
    adapter = CorpusFixtureAdapter(Provider.OPENAI)
    captures = []
    report = await evaluate_extraction(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("2"),
        mode="offline_fixture",
        failure_capture=captures.append,
    )
    assert all(case.passed for case in report.cases)
    assert len(adapter.calls) == len(CASES_TO_RUN)
    assert len(captures) == 1 and captures[0].cases == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "budget", "credential"])
async def test_full_evaluation_capture_preserves_policy_budget_and_credential_boundaries(failure):
    adapter = WaitingFailureAdapter(credential=failure == "credential")
    captures = []
    kwargs = dict(
        adapter=adapter,
        registry=catalog_template(),
        model=model_for(Provider.OPENAI),
        policy=PolicyRules() if failure == "permission" else policy_for(Provider.OPENAI),
        profile=Profile.EXTRACTION_HIGH_ACCURACY,
        max_cost=Decimal("0.000001") if failure == "budget" else Decimal("2"),
        mode="offline_fixture",
        secrets=(SecretStr("test-protected-value"),),
        failure_capture=captures.append,
    )
    if failure == "permission":
        with pytest.raises(ValueError, match="not authorized"):
            await evaluate_extraction(**kwargs)
        assert not adapter.calls and not captures
        return
    report = await evaluate_extraction(**kwargs)
    assert len(captures) == 1
    assert "test-protected-value" not in report.model_dump_json()
    assert "test-protected-value" not in captures[0].model_dump_json()
    assert all(case.proposal is None for case in captures[0].cases)
    assert not all(case.passed for case in report.cases)
    if failure == "budget":
        assert not adapter.calls and report.reserved_cost == 0


@pytest.mark.parametrize("invalid", ["missing", "same", "symlink", "hardlink", "communication"])
def test_failure_capture_cli_rejects_invalid_paths_or_corpus_before_configuration(
    invalid, monkeypatch, tmp_path, capsys
):
    from navox.ai import manage

    called = []

    def forbidden():
        called.append(True)
        raise AssertionError("Configuration must not be loaded")

    monkeypatch.setattr(manage, "Settings", forbidden)
    output = tmp_path / "evaluation.json"
    diagnostic = tmp_path / "failures.json"
    output.write_text("existing evaluation")
    if invalid == "same":
        diagnostic = output
    elif invalid == "symlink":
        diagnostic.symlink_to(output)
    elif invalid == "hardlink":
        diagnostic.hardlink_to(output)
    arguments = [
        "evaluate",
        "--live",
        "--model",
        "openai:fixture",
        "--profile",
        "EXTRACTION_HIGH_ACCURACY",
        "--max-cost",
        "0.25",
        "--diagnostic-output",
        str(diagnostic),
    ]
    if invalid != "missing":
        arguments.extend(["--output", str(output)])
    if invalid == "communication":
        arguments.extend(["--corpus", "communication", "--profile", "ASSISTANT_INTERACTIVE"])
    assert main(arguments) == 2
    assert not called
    assert output.read_text() == "existing evaluation"
    assert "operator_command_rejected" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_failure_capture_writes_only_the_separate_file_and_keeps_traffic_disabled(
    ai_database, monkeypatch, tmp_path, capsys
):
    from sqlalchemy import func, select

    from navox.ai import manage
    from navox.ai.foundation.persistence import RegistryStore
    from navox.core.settings import Settings
    from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment

    model = model_for(Provider.OPENAI)
    template = catalog_template()
    snapshot = template.model_copy(
        update={
            "models": (model,),
            "profiles": tuple(
                profile.model_copy(update={"assignments": (model.reference,)})
                if profile.profile == Profile.EXTRACTION_HIGH_ACCURACY
                else profile
                for profile in template.profiles
            ),
        }
    )
    async with ai_database() as db:
        await RegistryStore(db).publish(snapshot, expected_revision=0)
        await db.commit()
    settings = Settings(
        _env_file=None,
        app_environment="test",
        ai_provider_policy=policy_for(Provider.OPENAI).model_dump(mode="json"),
    )
    adapter = WaitingFailureAdapter()
    monkeypatch.setattr(manage, "Settings", lambda: settings)
    monkeypatch.setattr(manage, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(manage, "configured_adapters", lambda *_: {Provider.OPENAI: adapter})
    diagnostic = tmp_path / "failures.json"
    result = await manage.run(
        Namespace(
            command="evaluate",
            corpus="extraction",
            live=True,
            model="openai:fixture-corpus-model",
            profile="EXTRACTION_HIGH_ACCURACY",
            max_cost=Decimal("2"),
            output=tmp_path / "evaluation.json",
            diagnostic_output=diagnostic,
        )
    )
    capture = ExtractionDiagnosticReport.model_validate_json(diagnostic.read_text())
    assert len(capture.cases) == 1 and not capture.cases[0].measurement.passed
    assert capture.cases[0].proposal == adapter.rejected_output
    assert len(result["cases"]) == len(adapter.calls) == len(CASES_TO_RUN)
    assert "captured synthetic rejected proposal" not in json.dumps(result)
    terminal = capsys.readouterr()
    assert "captured synthetic rejected proposal" not in terminal.out + terminal.err
    async with ai_database() as db:
        assert await db.scalar(select(func.count()).select_from(AIEvaluationRun)) == 0
        assignment = await db.get(
            AIProfileAssignment, ("EXTRACTION_HIGH_ACCURACY", "openai:fixture-corpus-model")
        )
        assert assignment.rollout_percent == 0 and not assignment.shadow_enabled
