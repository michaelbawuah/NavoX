import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_ai_evaluation import model_for
from test_ai_gateway_foundation import USER, WORKSPACE
from test_ai_operational_domains import operational_runtime

from navox.ai import readiness
from navox.ai.catalog import catalog_template
from navox.ai.domains import Domain, domain_prompt_reference, domain_reference
from navox.ai.foundation.contracts import Provider, Sensitivity
from navox.ai.foundation.persistence import RegistryStore, canonical, digest
from navox.ai.readiness import TraceManifest, inventory, propose_catalog, trace_coverage
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment, AITaskRun


def prior_catalog():
    template = catalog_template()
    new_refs = {r for d in Domain for r in (domain_reference(d), domain_prompt_reference(d))}
    return template.model_copy(
        update={
            "revision": 4,
            "models": tuple(
                model_for(p).model_copy(update={"enabled": True})
                for p in (Provider.OPENAI, Provider.GEMINI, Provider.ANTHROPIC)
            ),
            "prompts": tuple(p for p in template.prompts if p.reference not in new_refs),
            "schemas": tuple(s for s in template.schemas if s.reference not in new_refs),
        }
    )


def test_proposals_preserve_history_and_never_guess_a_provider_or_expand_grants_implicitly():
    current = prior_catalog()
    proposal = propose_catalog(current)
    assert proposal.revision == 5 and proposal.models == current.models
    assert set(current.prompts) <= set(proposal.prompts)
    assert set(current.schemas) <= set(proposal.schemas)
    assert all(m.reference.provider != Provider.XAI for m in proposal.models)
    personal = propose_catalog(current, personal_providers=frozenset({Provider.OPENAI}))
    assert personal.models[0].allowed_sensitivities == {Sensitivity.PUBLIC, Sensitivity.PERSONAL}
    assert personal.models[1:] == current.models[1:]
    with pytest.raises(ValueError, match="unregistered"):
        propose_catalog(current, personal_providers=frozenset({Provider.XAI}))
    with pytest.raises(ValueError, match="already contains"):
        propose_catalog(proposal)
    changed = proposal.model_copy(
        update={
            "prompts": tuple(
                p.model_copy(update={"instructions": "Changed published instruction"})
                if p.reference == domain_reference(Domain.ASSISTANT)
                else p
                for p in proposal.prompts
            )
        }
    )
    with pytest.raises(ValueError, match="differs"):
        propose_catalog(changed)


@pytest.mark.asyncio
async def test_inventory_matches_current_task_binding_and_does_not_count_old_evidence(ai_database):
    runtime, _ = await operational_runtime(ai_database)
    settings = Settings(
        _env_file=None, ai_provider_policy=runtime.store.operator_policy.model_dump(mode="json")
    )
    async with ai_database() as db:
        current = await inventory(db, settings)
        assert len(current["bindings"]) == 14
        assert sum(b["qualification_passes"] for b in current["bindings"]) == 8
        assert all(
            not b["qualification_passes"]
            for b in current["bindings"]
            if b["task"] == "communication"
        )
        registry = await RegistryStore(db).load()
        await RegistryStore(db).publish(
            registry.model_copy(update={"revision": 2}), expected_revision=1
        )
        await db.commit()
        newer = await inventory(db, settings)
        assert all(not b["qualification_passes"] for b in newer["bindings"])
        assert len(list(await db.scalars(select(AIEvaluationRun)))) == 8
        assert newer["live_acceptance_complete"] is False


@pytest.mark.asyncio
async def test_prepare_exports_review_files_without_database_or_traffic_changes(
    ai_database, tmp_path, monkeypatch
):
    runtime, _ = await operational_runtime(ai_database)
    async with ai_database() as db:
        registry = await RegistryStore(db).load()
        # No new contracts are needed here, but an explicitly requested model
        # grant proposal differs. It must remain a file, not a publication.
        for assignment in await db.scalars(select(AIProfileAssignment)):
            assignment.rollout_percent = 0
        await db.commit()
    monkeypatch.setattr(readiness, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(readiness, "Settings", lambda: Settings(_env_file=None))
    args = Namespace(
        command="prepare",
        expected_revision=1,
        expected_digest=digest(canonical(registry)),
        personal_provider=[],
        directory=tmp_path / "proposal",
    )
    # Add a new permission only to the draft so there is a concrete difference.
    async with ai_database() as db:
        old = registry.model_copy(
            update={
                "revision": 2,
                "models": tuple(
                    m.model_copy(update={"allowed_sensitivities": frozenset({Sensitivity.PUBLIC})})
                    for m in registry.models
                ),
            }
        )
        await RegistryStore(db).publish(old, expected_revision=1)
        await db.commit()
    args.expected_revision = 2
    args.expected_digest = digest(canonical(old))
    args.personal_provider = [Provider.OPENAI]
    result = await readiness.run(args)
    assert result["published"] is False and result["traffic_changed"] is False
    assert len(list(args.directory.iterdir())) == 4
    proposal = json.loads((args.directory / "proposed-catalog.json").read_text())
    assert proposal["revision"] == 3
    async with ai_database() as db:
        assert await RegistryStore(db).load() == old
        assert all(
            a.rollout_percent == 0 and not a.shadow_enabled
            for a in await db.scalars(select(AIProfileAssignment))
        )
    with pytest.raises(FileExistsError):
        await readiness.run(args)
    args.expected_digest = "0" * 64
    with pytest.raises(ValueError, match="checkpoint"):
        await readiness.run(args)


@pytest.mark.asyncio
async def test_trace_coverage_uses_supplied_denominator_scope_and_terminal_attempts(ai_database):
    await operational_runtime(ai_database)
    ids = tuple(uuid4() for _ in range(3))
    now = datetime.now(UTC)
    async with ai_database() as db:
        for task_id, shadow, finished in ((ids[0], False, True), (ids[1], True, True)):
            db.add(
                AITaskRun(
                    task_id=task_id,
                    workspace_id=WORKSPACE,
                    user_id=USER,
                    trace_id=uuid4(),
                    profile="ASSISTANT_INTERACTIVE",
                    task_type="reason",
                    prompt="assistant@v2",
                    schema="assistant@v2",
                    status="FAILED",
                    shadow=shadow,
                    created_at=now,
                    finished_at=now if finished else None,
                )
            )
        await db.commit()
        manifest = TraceManifest(
            workspace_id=WORKSPACE,
            user_id=USER,
            started_at=now - timedelta(seconds=1),
            ended_at=datetime.now(UTC),
            task_ids=ids,
        )
        report = await trace_coverage(db, manifest)
        assert report["expected_tasks"] == 3 and report["traced_tasks"] == 1
        assert report["trace_coverage"] == 1 / 3 and not report["meets_99_percent_target"]
        assert not report["safety_or_quality_measured"]
        complete = await trace_coverage(db, manifest.model_copy(update={"task_ids": (ids[0],)}))
        assert complete["meets_99_percent_target"] and complete["without_terminal_attempt"] == []
        foreign = await trace_coverage(db, manifest.model_copy(update={"workspace_id": uuid4()}))
        assert foreign["traced_tasks"] == 0
        with pytest.raises(ValueError):
            await trace_coverage(db, manifest.model_copy(update={"task_ids": (ids[0], ids[0])}))
