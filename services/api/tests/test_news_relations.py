"""Automatic relations require current operator roles and qualified-task boundaries.

Provider results below are synthetic; they do not qualify the new task for live use.
"""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_news_foundation import NOW, USER, WORKSPACE, definition
from test_news_intelligence import PipelineRuntime, setup
from test_news_intelligence import clock as clock
from test_news_stories import item, span

from navox.ai.foundation.contracts import JSONDocument
from navox.ai.validation import OutputRejected
from navox.db.news import NewsClaim, NewsClaimEvidence, NewsIntelligenceRun, NewsStoryItem
from navox.news import intelligence
from navox.news.ai_contracts import RELATIONS, NewsContextInput, validate_news_output
from navox.news.contracts import SourceDefinition, SourceEvidencePolicy
from navox.news.evidence import evaluate_claim
from navox.news.intelligence import run_story_intelligence
from navox.news.registry import activate_source
from navox.news.stories import index_item


def policy(**updates):
    return SourceEvidencePolicy.model_validate(
        dict(
            role="ORIGINAL_REPORT",
            strong_evidence_allowed=True,
            review_reference="authored-original-report-policy",
            reviewed_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(minutes=5),
            **updates,
        )
    )


def test_absent_policy_preserves_original_source_fingerprint():
    source = definition()
    old_payload = source.model_dump(mode="json")
    old_payload.pop("evidence_policy")
    # The legacy catalog predates these optional API credential bindings.
    old_payload.pop("api_connection_id")
    old_payload.pop("api_endpoint_name")
    expected = hashlib.sha256(
        json.dumps(old_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert source.fingerprint == expected


@pytest.mark.parametrize("source_type", ["COMPANY", "SOCIAL", "GOVERNMENT", "RESEARCH"])
def test_untrusted_or_interested_source_cannot_gain_original_report_authority(source_type):
    payload = definition().model_dump(mode="json") | {
        "source_type": source_type,
        "evidence_policy": policy().model_dump(mode="json"),
    }
    with pytest.raises(ValueError):
        SourceDefinition.model_validate(payload)


@pytest.mark.parametrize("role", ["INTERESTED_PARTY", "SYNDICATED", "SOCIAL", "PRIMARY_RECORD"])
def test_policy_cannot_promote_non_original_roles_to_strong(role):
    with pytest.raises(ValueError):
        SourceEvidencePolicy.model_validate(policy().model_dump(mode="json") | {"role": role})


class RelationRuntime(PipelineRuntime):
    def __init__(self):
        super().__init__()
        self.mutate_relation = None
        self.invalid_relation = False
        self.all_sources = False

    async def execute(self, task, *, context_builder, documents, semantic_validator):
        if task.prompt != RELATIONS:
            return await super().execute(
                task,
                context_builder=context_builder,
                documents=documents,
                semantic_validator=semantic_validator,
            )
        context = await context_builder.build(task, documents)
        self.calls.append((task, context))
        snapshot = NewsContextInput.model_validate(json.loads(context.content.text)["news_context"])
        record = snapshot.items[0]
        proposal = {
            "claim_id": str(snapshot.claims[0].id),
            "span": span(record).model_dump(mode="json"),
            "relationship": "SUPPORTS",
        }
        if self.invalid_relation:
            proposal["strength"] = "strong"
        value = {"relations": [proposal]}
        if self.all_sources:
            value = {
                "relations": [
                    proposal | {"span": span(source).model_dump(mode="json")}
                    for source in snapshot.items
                ]
            }
        semantic_validator(value)
        if self.mutate_relation:
            await self.mutate_relation()
        await context_builder.build(task, documents)
        return SimpleNamespace(output=JSONDocument(text=json.dumps(value)), trace_id=uuid4())


async def configured(factory, monkeypatch):
    settings, _, old, source, record, story = await setup(factory, monkeypatch)
    payload = old.model_dump(mode="json") | {
        "source_type": "PUBLISHER",
        "evidence_policy": policy().model_dump(mode="json"),
    }
    config = SourceDefinition.model_validate(payload)
    async with factory() as db:
        await activate_source(db, config, workspace_id=WORKSPACE, user_id=USER, now=NOW)
        await db.commit()
    settings.news_source_catalog = [config.model_dump(mode="json")]
    runtime = RelationRuntime()

    async def build(*args):
        return runtime

    monkeypatch.setattr(intelligence, "build_runtime", build)
    return settings, config, source, record, story, runtime


@pytest.mark.asyncio
async def test_three_phase_pipeline_bounds_spend_and_expires_automatic_authority(
    ai_database, monkeypatch
):
    settings, config, _, _, story, runtime = await configured(ai_database, monkeypatch)
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "READY"
    )
    assert len(runtime.calls) == 3
    assert sum(task.max_cost for task, _ in runtime.calls) == Decimal("0.05")
    async with ai_database() as db:
        claim = await db.scalar(select(NewsClaim))
        evidence = await db.scalar(select(NewsClaimEvidence))
        assert evidence.evidence_kind == "ORIGINAL_REPORT"
        assert evidence.review_reference.startswith("auto-rel:")
        current = await evaluate_claim(db, claim, {config.key: config}, now=NOW)
        assert current.independent_supports == 1 and current.status == "UNCONFIRMED"
        expired = await evaluate_claim(
            db, claim, {config.key: config}, now=NOW + timedelta(minutes=6)
        )
        assert expired.independent_supports == 0 and expired.status == "UNCONFIRMED"
        run = await db.scalar(select(NewsIntelligenceRun))
        assert len(run.trace_ids) == 3
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "SKIPPED"
    )
    assert len(runtime.calls) == 3


@pytest.mark.asyncio
async def test_model_cannot_add_authority_fields(ai_database, monkeypatch):
    settings, _, _, _, story, runtime = await configured(ai_database, monkeypatch)
    runtime.invalid_relation = True
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "UNAVAILABLE"
    )
    assert len(runtime.calls) == 2
    snapshot = NewsContextInput.model_validate(
        json.loads(runtime.calls[1][1].content.text)["news_context"]
    )
    proposal = {
        "claim_id": str(snapshot.claims[0].id),
        "span": span(snapshot.items[0]).model_dump(mode="json"),
        "relationship": "SUPPORTS",
    }
    for invalid in (
        {"relations": [proposal | {"claim_id": str(uuid4())}]},
        {"relations": [proposal, proposal]},
        {"relations": [proposal | {"relationship": "RETRACTS"}]},
        {"relations": [proposal | {"span": proposal["span"] | {"item_revision": 999}}]},
    ):
        with pytest.raises(OutputRejected):
            validate_news_output(RELATIONS, invalid, snapshot)
    async with ai_database() as db:
        evidence = await db.scalar(select(NewsClaimEvidence))
        assert evidence.review_reference is None and evidence.evidence_strength == "weak"


@pytest.mark.asyncio
async def test_policy_revoked_during_provider_call_cannot_publish_authority(
    ai_database, monkeypatch
):
    settings, _, _, _, story, runtime = await configured(ai_database, monkeypatch)

    async def revoke():
        settings.news_source_catalog = []

    runtime.mutate_relation = revoke
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "UNAVAILABLE"
    )
    async with ai_database() as db:
        evidence = await db.scalar(select(NewsClaimEvidence))
        assert evidence.review_reference is None


@pytest.mark.asyncio
async def test_reactivated_policy_change_mid_call_invalidates_snapshot(ai_database, monkeypatch):
    settings, config, _, _, story, runtime = await configured(ai_database, monkeypatch)

    async def change_policy():
        payload = config.model_dump(mode="json")
        payload["evidence_policy"]["strong_evidence_allowed"] = False
        changed = SourceDefinition.model_validate(payload)
        async with ai_database() as db:
            await activate_source(db, changed, workspace_id=WORKSPACE, user_id=USER, now=NOW)
            await db.commit()
        settings.news_source_catalog = [changed.model_dump(mode="json")]

    runtime.mutate_relation = change_policy
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "UNAVAILABLE"
    )
    async with ai_database() as db:
        evidence = await db.scalar(select(NewsClaimEvidence))
        assert evidence.review_reference is None


@pytest.mark.asyncio
async def test_two_reviewed_independent_sources_corroborate_in_automatic_pipeline(
    ai_database, monkeypatch
):
    settings, first, _, _, story, runtime = await configured(ai_database, monkeypatch)
    async with ai_database() as db:
        initial, _, record = await item(
            db, "two", headline="Another report confirms an observation was recorded."
        )
        second = SourceDefinition.model_validate(
            initial.model_dump(mode="json")
            | {
                "source_type": "PUBLISHER",
                "evidence_policy": policy().model_dump(mode="json"),
            }
        )
        await activate_source(db, second, workspace_id=WORKSPACE, user_id=USER, now=NOW)
        definitions = {first.key: first, second.key: second}
        separate = await index_item(db, record, definitions, now=NOW)
        membership = await db.get(NewsStoryItem, record.id)
        # Authored same-story fixture. This does not establish semantic clustering.
        membership.cluster_id = story.id
        separate.suppressed = True
        await db.commit()
    settings.news_source_catalog.append(second.model_dump(mode="json"))
    runtime.all_sources = True
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "READY"
    )
    async with ai_database() as db:
        claim = await db.scalar(select(NewsClaim))
        result = await evaluate_claim(db, claim, definitions, now=NOW)
        assert result.status == "CORROBORATED" and result.independent_supports == 2


@pytest.mark.asyncio
async def test_automatic_relation_does_not_replace_current_explicit_review(
    ai_database, monkeypatch
):
    from test_news_stories import review

    from navox.news.evidence import admit_claim, review_evidence

    settings, config, _, record, story, runtime = await configured(ai_database, monkeypatch)
    async with ai_database() as db:
        claim = await admit_claim(db, story, span(record), {config.key: config}, now=NOW)
        await review_evidence(
            db,
            claim,
            span(record),
            review("ORIGINAL_REPORT", "CONTRADICTS"),
            {config.key: config},
            now=NOW,
        )
        await db.commit()
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "READY"
    )
    async with ai_database() as db:
        evidence = await db.scalar(select(NewsClaimEvidence))
        assert evidence.review_reference == "synthetic-review-fixture"
        assert evidence.relationship == "CONTRADICTS"
