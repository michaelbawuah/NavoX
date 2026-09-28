"""Versioned drafting evaluation; all model outputs and reviews here are fixtures."""

import json
from argparse import Namespace
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from test_ai_communication_evaluation import (
    DraftFixtureAdapter,
    fixture_report,
    reviewed_contract,
)
from test_ai_evaluation import model_for, policy_for

from navox.ai import communication_evaluation as drafting
from navox.ai import manage
from navox.ai.catalog import catalog_template
from navox.ai.communication_corpus import DRAFT_CASES
from navox.ai.foundation.adapter import ErrorCode, ProviderError
from navox.ai.foundation.contracts import Profile, Provider, VersionedRef
from navox.ai.foundation.persistence import RegistryConflict, RegistryStore, canonical, digest
from navox.ai.prompts import COMMUNICATION_PROMPT_V2
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment


def test_published_v1_bytes_and_approved_v2_candidate_are_exact():
    snapshot = catalog_template()
    expected = {
        "v1": "b7cc79e33870b5e48641bc06555d1b412f99d07338d8ee1b692f29446ccb1351",
        "v2": "e7e97c2ca117ec6b278fbb315bd9a34320a6bffc62d8ed58d8dd91ff33a2675d",
    }
    for version, fingerprint in expected.items():
        prompt = next(
            p
            for p in snapshot.prompts
            if p.reference == VersionedRef(name="communication_draft", version=version)
        )
        assert digest(canonical(prompt)) == fingerprint
        assert prompt.output_schema == drafting.REFERENCE
    schema = next(s for s in snapshot.schemas if s.reference == drafting.REFERENCE)
    assert (
        digest(canonical(schema))
        == "a218311abdc0e6531cd4f6d7658a656d87727da52862f82f9480f1fc6741cc83"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", drafting.SUPPORTED_PROMPTS)
async def test_explicit_version_keeps_full_corpus_and_requires_fresh_review(reference):
    adapter = DraftFixtureAdapter()
    report = await fixture_report(adapter, prompt_ref=reference)
    assert report.prompt == reference and report.output_schema == drafting.REFERENCE
    assert report.corpus == DRAFT_CASES
    assert tuple(c.id for c in report.cases) == tuple(c.id for c in DRAFT_CASES)
    assert len(adapter.calls) == 16
    assert all(
        r.prompt_ref == reference and r.schema_ref == drafting.REFERENCE for r in adapter.calls
    )
    assert report.review.reviewed_by is None and report.review.reviewed_at is None
    assert all(v is None for c in report.review.cases for v in c.verdicts())
    with pytest.raises(ValueError, match="Complete human review"):
        report.model_copy(update={"mode": "live_provider"}).evidence()
    reviewed = reviewed_contract(report)
    assert reviewed.evidence().prompt == reference
    serialized = reviewed.model_dump_json()
    restored = drafting.DraftingEvaluationReport.model_validate_json(serialized)
    assert restored.model_dump_json() == serialized
    assert restored.review_digest() == reviewed.review_digest()
    assert restored.evidence() == reviewed.evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["unpublished", "schema", "unsupported"])
async def test_invalid_version_binding_rejects_before_any_provider_call(problem):
    snapshot = catalog_template()
    reference = COMMUNICATION_PROMPT_V2
    if problem == "unpublished":
        snapshot = snapshot.model_copy(
            update={"prompts": tuple(p for p in snapshot.prompts if p.reference != reference)}
        )
    elif problem == "schema":
        snapshot = snapshot.model_copy(
            update={
                "schemas": tuple(s for s in snapshot.schemas if s.reference != drafting.REFERENCE)
            }
        )
    else:
        reference = VersionedRef(name="communication_draft", version="v3")
    adapter = DraftFixtureAdapter()
    with pytest.raises(ValueError, match="Publish|Unsupported"):
        await drafting.evaluate_communication(
            adapter=adapter,
            registry=snapshot,
            model=model_for(adapter.provider),
            policy=policy_for(adapter.provider),
            max_cost=Decimal("2"),
            mode="offline_fixture",
            prompt_ref=reference,
        )
    assert not adapter.calls


@pytest.mark.asyncio
async def test_v1_review_cannot_be_reused_by_changing_its_prompt_label():
    reviewed = reviewed_contract(await fixture_report())
    with pytest.raises(ValueError, match="Complete human review"):
        reviewed.model_copy(update={"prompt": COMMUNICATION_PROMPT_V2}).evidence()
    with pytest.raises(ValueError, match="Complete fixed drafting corpus"):
        reviewed.model_copy(update={"cases": reviewed.cases[:4]}).evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [-1, 61, float("nan"), float("inf")])
async def test_invalid_pacing_is_rejected_before_calls(interval):
    adapter = DraftFixtureAdapter()
    with pytest.raises(ValueError, match="request interval"):
        await fixture_report(adapter, minimum_start_interval_seconds=interval)
    assert not adapter.calls


@pytest.mark.asyncio
async def test_pacing_is_between_starts_and_excluded_from_latency(monkeypatch):
    clock = [0.0]
    starts = []

    async def pause(delay):
        clock[0] += delay

    class TimedAdapter(DraftFixtureAdapter):
        async def execute(self, request):
            starts.append(clock[0])
            clock[0] += 0.125
            return await super().execute(request)

    monkeypatch.setattr(drafting, "monotonic", lambda: clock[0])
    monkeypatch.setattr(drafting.asyncio, "sleep", pause)
    report = await fixture_report(
        TimedAdapter(),
        prompt_ref=COMMUNICATION_PROMPT_V2,
        minimum_start_interval_seconds=6,
    )
    assert starts == [6 * index for index in range(16)]
    assert all(c.latency_ms == 125 for c in report.cases)


@pytest.mark.asyncio
async def test_rate_limit_stops_without_retry_or_dropping_corpus_slots():
    class LimitedAdapter(DraftFixtureAdapter):
        def classify_error(self, error):
            return ProviderError(code=ErrorCode.RATE_LIMIT)

    adapter = LimitedAdapter(fail=True)
    report = await fixture_report(adapter, prompt_ref=COMMUNICATION_PROMPT_V2)
    assert len(adapter.calls) == 1 and len(report.cases) == 16
    assert all(c.error_code == "rate_limit" and c.candidate is None for c in report.cases)
    assert all(not c.provider_succeeded for c in report.cases)


def test_drafting_flags_cannot_be_silently_used_for_extraction(monkeypatch, capsys):
    def forbidden_settings():
        pytest.fail("Invalid corpus flags must fail before settings/database access")

    monkeypatch.setattr(manage, "Settings", forbidden_settings)
    assert (
        manage.main(
            [
                "evaluate",
                "--live",
                "--corpus",
                "extraction",
                "--model",
                "openai:fixture",
                "--profile",
                "EXTRACTION_HIGH_ACCURACY",
                "--max-cost",
                "1",
                "--draft-prompt-version",
                "v2",
            ]
        )
        == 2
    )
    assert "operator_command_rejected" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_publication_preserves_old_evidence_and_v2_cli_records_only_after_review(
    ai_database,
    monkeypatch,
    tmp_path,
):
    model = model_for(Provider.OPENAI)
    template = catalog_template()
    legacy = template.model_copy(
        update={
            "models": (model,),
            "prompts": tuple(p for p in template.prompts if p.reference != COMMUNICATION_PROMPT_V2),
            "profiles": tuple(
                p.model_copy(update={"assignments": (model.reference,)})
                if p.profile == Profile.ASSISTANT_INTERACTIVE
                else p
                for p in template.profiles
            ),
        }
    )
    async with ai_database() as db:
        await RegistryStore(db).publish(legacy, expected_revision=0)
        await db.commit()
    monkeypatch.setattr(
        manage,
        "Settings",
        lambda: Settings(
            _env_file=None,
            app_environment="test",
            ai_provider_policy=policy_for(model.reference.provider).model_dump(mode="json"),
        ),
    )
    monkeypatch.setattr(manage, "get_session_factory", lambda: ai_database)
    old_report = reviewed_contract(await fixture_report())
    old_path = tmp_path / "legacy-reviewed.json"
    old_path.write_text(old_report.model_dump_json())
    old_receipt = await manage.run(Namespace(command="record-evaluation", file=old_path))
    async with ai_database() as db:
        old_evidence = (await db.get(AIEvaluationRun, UUID(old_receipt["evaluation_id"]))).evidence
    # A human-reviewed report still cannot be recorded against an unpublished prompt.
    unpublished = reviewed_contract(await fixture_report(prompt_ref=COMMUNICATION_PROMPT_V2))
    path = tmp_path / "v2-review.json"
    path.write_text(unpublished.model_dump_json())
    with pytest.raises(RegistryConflict, match="prompt, schema, or task binding"):
        await manage.run(Namespace(command="record-evaluation", file=path))
    current = legacy.model_copy(update={"revision": 2, "prompts": template.prompts})
    async with ai_database() as db:
        await RegistryStore(db).publish(current, expected_revision=1)
        await db.commit()
        assert (
            await db.get(AIEvaluationRun, UUID(old_receipt["evaluation_id"]))
        ).evidence == old_evidence
    adapter = DraftFixtureAdapter()
    monkeypatch.setattr(manage, "configured_adapters", lambda *_: {Provider.OPENAI: adapter})
    raw = await manage.run(
        Namespace(
            command="evaluate",
            live=True,
            corpus="communication",
            output=path,
            model=old_report.model_id,
            profile="ASSISTANT_INTERACTIVE",
            max_cost=Decimal("2"),
            draft_prompt_version="v2",
            minimum_start_interval_seconds=0,
        )
    )
    report = drafting.DraftingEvaluationReport.model_validate(raw)
    assert report.registry_revision == 2 and report.prompt == COMMUNICATION_PROMPT_V2
    assert len(adapter.calls) == 16
    assert all(r.prompt_ref == COMMUNICATION_PROMPT_V2 for r in adapter.calls)
    path.write_text(report.model_dump_json())
    with pytest.raises(ValueError, match="Complete human review"):
        await manage.run(Namespace(command="record-evaluation", file=path))
    path.write_text(reviewed_contract(report).model_dump_json())
    receipt = await manage.run(Namespace(command="record-evaluation", file=path))
    assert not receipt["traffic_promoted"]
    async with ai_database() as db:
        rows = (await db.scalars(select(AIEvaluationRun))).all()
        assert len(rows) == 2
        old = await db.get(AIEvaluationRun, UUID(old_receipt["evaluation_id"]))
        assert old.evidence == old_evidence and old.registry_revision == 1
        new = await db.get(AIEvaluationRun, UUID(receipt["evaluation_id"]))
        assert new.registry_revision == 2 and new.prompt == "communication_draft@v2"
        assert new.schema == "communication_draft@v1"
        assert json.loads(new.evidence)["samples"] == 16
        assignments = (await db.scalars(select(AIProfileAssignment))).all()
        assert all(a.rollout_percent == 0 and not a.shadow_enabled for a in assignments)
    with pytest.raises(RegistryConflict, match="Registry changed"):
        await manage.run(Namespace(command="record-evaluation", file=old_path))
