import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_ai_runtime import accepts_object, setup_runtime

from navox.ai.context import ContextBuilder
from navox.ai.control import record_evaluation, record_healthy_probe, set_rollout
from navox.ai.foundation.persistence import RegistryConflict, RegistryStore
from navox.ai.routing import EvaluationEvidence, RoutingWeights, rank_eligible
from navox.ai.runtime import GatewayUnavailable
from navox.db.ai_registry import AIEvaluationRun, AIModel, AIProfileAssignment, AIProviderHealth


@pytest.mark.asyncio
async def test_operator_health_recovery_requires_current_model_and_never_restores_traffic(
    ai_database,
):
    _, task, _, _ = await setup_runtime(ai_database)
    async with ai_database() as db:
        key = "openai:fixture-first"
        model = await db.get(AIModel, key)
        model_digest = model.digest
        health = await db.get(AIProviderHealth, key)
        health.status, health.error_code = "DISABLED", "authentication"
        assignment = await db.get(AIProfileAssignment, (task.profile.value, key))
        assignment.shadow_enabled = True
        await db.commit()
        with pytest.raises(RegistryConflict):
            await record_healthy_probe(
                db, model_id=key, model_digest="stale-digest", expected_revision=1
            )
        await db.rollback()
        with pytest.raises(RegistryConflict):
            await record_healthy_probe(
                db, model_id=key, model_digest=model_digest, expected_revision=2
            )
        await db.rollback()
        await record_healthy_probe(db, model_id=key, model_digest=model_digest, expected_revision=1)
        await db.commit()
        health = await db.get(AIProviderHealth, key, populate_existing=True)
        assignment = await db.get(
            AIProfileAssignment, (task.profile.value, key), populate_existing=True
        )
        assert health.status == "HEALTHY" and health.error_code is None
        assert health.probe_until is None and health.retry_after is None
        assert assignment.rollout_percent == 0 and not assignment.shadow_enabled


@pytest.mark.asyncio
async def test_canary_requires_fresh_evidence_and_advances_one_stage(ai_database):
    runtime, task, _, _ = await setup_runtime(ai_database)
    async with ai_database() as db:
        row = await db.scalar(select(AIProfileAssignment))
        key = row.model_id
        await set_rollout(
            db,
            model_id=key,
            profile=task.profile,
            expected_revision=1,
            expected_percent=100,
            percent=0,
        )
        await db.commit()
        with pytest.raises(RegistryConflict, match="one stage"):
            await set_rollout(
                db,
                model_id=key,
                profile=task.profile,
                expected_revision=1,
                expected_percent=0,
                percent=100,
            )
        await db.rollback()
        for before, after in ((0, 5), (5, 25), (25, 50), (50, 100)):
            await set_rollout(
                db,
                model_id=key,
                profile=task.profile,
                expected_revision=1,
                expected_percent=before,
                percent=after,
            )
            await db.commit()
        with pytest.raises(RegistryConflict, match="Rollout changed"):
            await set_rollout(
                db,
                model_id=key,
                profile=task.profile,
                expected_revision=1,
                expected_percent=50,
                percent=100,
            )


@pytest.mark.asyncio
async def test_regression_rolls_back_traffic_success_does_not_reenable(ai_database):
    _, task, _, _ = await setup_runtime(ai_database)
    async with ai_database() as db:
        previous = await db.scalar(select(AIEvaluationRun))
        key = previous.model_id
        evidence = EvaluationEvidence.model_validate_json(previous.evidence)
        failed = evidence.model_copy(update={"quality": 0.5, "evaluated_at": datetime.now(UTC)})
        await record_evaluation(
            db,
            model_id=key,
            model_digest=previous.model_digest,
            registry_revision=1,
            evidence=failed,
        )
        await db.commit()
        assignment = await db.get(AIProfileAssignment, (task.profile.value, key))
        assert assignment.rollout_percent == 0 and not assignment.shadow_enabled
        with pytest.raises(RegistryConflict, match="passing evaluation"):
            await set_rollout(
                db,
                model_id=key,
                profile=task.profile,
                expected_revision=1,
                expected_percent=0,
                percent=5,
            )
        await db.rollback()
        model = await db.get(AIModel, key)
        await record_evaluation(
            db,
            model_id=key,
            model_digest=model.digest,
            registry_revision=1,
            evidence=evidence.model_copy(update={"evaluated_at": datetime.now(UTC)}),
        )
        await db.commit()
        assert (await db.get(AIProfileAssignment, (task.profile.value, key))).rollout_percent == 0


@pytest.mark.asyncio
async def test_new_catalog_invalidates_old_evaluation_for_serving_and_promotion(ai_database):
    runtime, task, a, b = await setup_runtime(ai_database)
    async with ai_database() as db:
        snapshot = await RegistryStore(db).load()
        await RegistryStore(db).publish(
            snapshot.model_copy(update={"revision": 2}), expected_revision=1
        )
        await db.commit()
        with pytest.raises(GatewayUnavailable):
            await runtime.execute(
                task,
                context_builder=ContextBuilder(db),
                documents={},
                semantic_validator=accepts_object,
            )
        assert not a.calls and not b.calls
        assignment = await db.scalar(select(AIProfileAssignment))
        with pytest.raises(RegistryConflict, match="passing evaluation"):
            await set_rollout(
                db,
                model_id=assignment.model_id,
                profile=task.profile,
                expected_revision=2,
                expected_percent=100,
                percent=100,
            )


@pytest.mark.asyncio
async def test_only_one_half_open_probe_can_claim_after_cooldown(ai_database):
    runtime, _, _, _ = await setup_runtime(ai_database)
    async with ai_database() as db:
        row = await db.scalar(select(AIProviderHealth))
        key = row.model_id
        row.status = "UNAVAILABLE"
        row.retry_after = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    claims = await asyncio.gather(*(runtime.store.claim(key) for _ in range(5)))
    assert claims.count(True) == 1


@pytest.mark.asyncio
async def test_latency_class_changes_ranking_without_relaxing_quality(ai_database):
    runtime, task, _, _ = await setup_runtime(ai_database)
    snapshot = await runtime.store.snapshot(task)
    evaluations = dict(snapshot.evaluations)
    keys = sorted(evaluations)
    evaluations[keys[0]] = evaluations[keys[0]].model_copy(
        update={"quality": 0.90, "p95_latency_ms": 50}
    )
    evaluations[keys[1]] = evaluations[keys[1]].model_copy(
        update={"quality": 0.99, "p95_latency_ms": 4000}
    )

    def select_for(latency):
        return rank_eligible(
            task.model_copy(update={"latency_class": latency}),
            snapshot.registry,
            policy=snapshot.policy,
            available=snapshot.available,
            evaluations=evaluations,
            input_bound=1000,
            remaining_budget=task.max_cost,
            preferred=None,
            weights=RoutingWeights(),
        )[0][0].reference

    from navox.ai.foundation.contracts import LatencyClass

    assert select_for(LatencyClass.INTERACTIVE) != select_for(LatencyClass.BACKGROUND)
