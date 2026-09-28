"""Immutable prompt bindings and fail-closed use of freshly qualified contracts."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from test_ai_gateway_foundation import USER, WORKSPACE
from test_ai_operational_domains import CorpusAdapter, operational_runtime, run_fixture

from navox.ai.catalog import catalog_template
from navox.ai.domain_corpus import A
from navox.ai.domains import Domain, domain_prompt_reference, domain_reference
from navox.ai.foundation.persistence import canonical, digest
from navox.ai.operational_service import OperationalRequest, advise
from navox.ai.prompts import builtin_prompts
from navox.ai.readiness import propose_catalog
from navox.ai.runtime import GatewayUnavailable
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun


def test_all_r5_published_definitions_remain_byte_equivalent():
    expected = json.loads(
        (Path(__file__).parent / "fixtures/spec005_r5_definition_digests.json").read_text()
    )
    prompts, schemas = builtin_prompts()
    for kind, definitions in (("prompts", prompts), ("schemas", schemas)):
        current = {
            f"{p.reference.name}@{p.reference.version}": digest(canonical(p)) for p in definitions
        }
        assert all(current[key] == value for key, value in expected[kind].items())


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", tuple(Domain))
async def test_unpublished_prompt_stops_before_first_provider_request(domain):
    latest = catalog_template()
    old = latest.model_copy(
        update={"prompts": tuple(p for p in latest.prompts if p.reference.version != "v3")}
    )
    adapter = CorpusAdapter()
    with pytest.raises(ValueError, match="Publish"):
        await run_fixture(domain, adapter, registry=old)
    assert not adapter.calls
    proposed = propose_catalog(old)
    assert set(old.prompts) <= set(proposed.prompts) and proposed.schemas == old.schemas
    assert proposed.models == old.models and proposed.revision == old.revision + 1
    report = await run_fixture(domain, adapter, registry=proposed)
    assert report.prompt == domain_prompt_reference(domain)
    assert report.output_schema == domain_reference(domain)
    assert all(
        r.prompt_ref == report.prompt and r.schema_ref == report.output_schema
        for r in adapter.calls
    )


@pytest.mark.parametrize("declined", [True, False])
@pytest.mark.asyncio
async def test_original_r5_action_request_outputs_still_fail_safety(declined):
    def original_failed_output(fixture, output):
        if fixture.id == "status-change-is-not-answer":
            return {
                "facts": [{"item_id": str(A), "field": field} for field in ("title", "status")],
                "unknowns": [],
                "insufficient_context": declined,
            }
        return output

    report = await run_fixture(Domain.ASSISTANT, CorpusAdapter(alter=original_failed_output))
    measured = report.model_copy(update={"mode": "live_provider"}).evidence()
    assert measured.quality == 11 / 12 and not measured.safety_passed
    # Historical v2 reports remain inspectable, never relabeled as new evaluation.
    historical = report.model_copy(
        update={"mode": "live_provider", "prompt": domain_reference(Domain.ASSISTANT)}
    )
    assert historical.evidence().safety_passed is False


@pytest.mark.asyncio
async def test_old_prompt_evidence_cannot_serve_new_prompt(ai_database):
    runtime, adapters = await operational_runtime(ai_database)
    async with ai_database() as db:
        rows = list(
            await db.scalars(
                select(AIEvaluationRun).where(AIEvaluationRun.prompt == "assistant@v3")
            )
        )
        assert rows
        for row in rows:
            data = json.loads(row.evidence)
            data["prompt"]["version"] = "v2"
            row.evidence = json.dumps(data)
            row.prompt = "assistant@v2"
        await db.commit()
    with pytest.raises(GatewayUnavailable):
        await advise(
            runtime,
            Settings(_env_file=None),
            workspace_id=WORKSPACE,
            user_id=USER,
            domain=Domain.ASSISTANT,
            request=OperationalRequest(item_ids=(A,), instructions="Show saved status."),
        )
    assert all(not adapter.calls for adapter in adapters.values())
