"""Operator-only registry/evaluation rollout controls. Caller owns the transaction."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.ai.foundation.contracts import Profile
from navox.ai.foundation.persistence import RegistryConflict, RegistryStore
from navox.ai.routing import EvaluationEvidence, RoutingWeights
from navox.ai.store import utc
from navox.db.ai_registry import (
    AIEvaluationRun,
    AIModel,
    AIProfile,
    AIProfileAssignment,
    AIProviderHealth,
    AIRegistryState,
)

STAGES = (0, 5, 25, 50, 100)


def evaluation_passes(evidence: EvaluationEvidence, *, now: datetime | None = None) -> bool:
    age = (now or datetime.now(UTC)) - evidence.evaluated_at
    return (
        evidence.samples >= 10
        and evidence.quality >= 0.90
        and evidence.reliability >= 0.95
        and evidence.safety_passed
        and timedelta(0) <= age <= timedelta(days=30)
    )


async def locked_revision(database: AsyncSession, expected: int) -> None:
    current = await database.scalar(
        select(AIRegistryState.revision).where(AIRegistryState.id == 1).with_for_update()
    )
    if current != expected:
        raise RegistryConflict("Registry changed; reload the catalog and evaluate again")
    # Also establishes a writer transaction on SQLite; on PostgreSQL the row
    # lock serializes catalog changes with rollout and evaluation recording.
    locked = await database.scalar(
        update(AIRegistryState)
        .where(AIRegistryState.id == 1, AIRegistryState.revision == expected)
        .values(revision=current)
        .returning(AIRegistryState.revision)
    )
    if locked != expected:
        raise RegistryConflict("Registry changed; reload before changing traffic")


async def record_evaluation(
    database: AsyncSession,
    *,
    model_id: str,
    model_digest: str,
    registry_revision: int,
    evidence: EvaluationEvidence,
) -> AIEvaluationRun:
    evidence = EvaluationEvidence.model_validate(evidence)
    await locked_revision(database, registry_revision)
    model = await database.get(AIModel, model_id)
    assignment = await database.get(AIProfileAssignment, (evidence.profile.value, model_id))
    snapshot = await RegistryStore(database).load(registry_revision)
    if model is None or model.digest != model_digest or assignment is None or snapshot is None:
        raise RegistryConflict("Evaluation does not match the configured model and profile")
    prompt = next((p for p in snapshot.prompts if p.reference == evidence.prompt), None)
    profile = next((p for p in snapshot.profiles if p.profile == evidence.profile), None)
    if (
        prompt is None
        or prompt.output_schema != evidence.output_schema
        or profile is None
        or evidence.task_type not in profile.task_types
    ):
        raise RegistryConflict("Evaluation prompt, schema, or task binding failed")
    row = AIEvaluationRun(
        model_id=model_id,
        model_digest=model_digest,
        registry_revision=registry_revision,
        profile=evidence.profile.value,
        task_type=evidence.task_type.value,
        prompt=f"{evidence.prompt.name}@{evidence.prompt.version}",
        schema=f"{evidence.output_schema.name}@{evidence.output_schema.version}",
        evidence=evidence.model_dump_json(),
        evaluated_at=evidence.evaluated_at,
    )
    database.add(row)
    # A new failing evaluation removes this candidate from serving and shadow
    # traffic atomically. A later success never restores traffic automatically.
    if not evaluation_passes(evidence):
        assignment.rollout_percent, assignment.shadow_enabled = 0, False
    await database.flush()
    return row


async def set_rollout(
    database: AsyncSession,
    *,
    model_id: str,
    profile: Profile,
    expected_revision: int,
    expected_percent: int,
    percent: int,
    shadow: bool = False,
) -> None:
    await locked_revision(database, expected_revision)
    assignment = await database.get(AIProfileAssignment, (profile.value, model_id))
    if assignment is None or assignment.rollout_percent != expected_percent:
        raise RegistryConflict("Rollout changed; reload before changing traffic")
    if percent not in STAGES or expected_percent not in STAGES:
        raise RegistryConflict("Rollout must use a supported canary stage")
    if percent > expected_percent and STAGES.index(percent) != STAGES.index(expected_percent) + 1:
        raise RegistryConflict("Canary promotion must advance one stage at a time")
    if percent or shadow:
        model = await database.get(AIModel, model_id)
        health = await database.get(AIProviderHealth, model_id)
        if model is None or not model.enabled or health is None or health.status != "HEALTHY":
            raise RegistryConflict("Only an enabled healthy model can receive traffic")
        evaluations = (
            await database.scalars(
                select(AIEvaluationRun)
                .where(
                    AIEvaluationRun.model_id == model_id,
                    AIEvaluationRun.model_digest == model.digest,
                    AIEvaluationRun.registry_revision == expected_revision,
                    AIEvaluationRun.profile == profile.value,
                )
                .order_by(AIEvaluationRun.evaluated_at.desc(), AIEvaluationRun.id.desc())
            )
        ).all()
        latest: dict[tuple[str, str, str], AIEvaluationRun] = {}
        for row in evaluations:
            latest.setdefault((row.task_type, row.prompt, row.schema), row)
        if not latest or any(
            not evaluation_passes(EvaluationEvidence.model_validate_json(row.evidence))
            or utc(row.evaluated_at)
            != EvaluationEvidence.model_validate_json(row.evidence).evaluated_at
            for row in latest.values()
        ):
            raise RegistryConflict("Fresh passing evaluation is required before promotion")
    assignment.rollout_percent, assignment.shadow_enabled = percent, shadow
    await database.flush()


async def set_weights(
    database: AsyncSession, profile: Profile, weights: RoutingWeights, *, expected_revision: int
) -> None:
    weights = RoutingWeights.model_validate(weights)
    await locked_revision(database, expected_revision)
    row = await database.get(AIProfile, profile.value)
    if row is None:
        raise RegistryConflict("Profile is not registered")
    row.weights = weights.model_dump()
    await database.flush()


async def record_healthy_probe(
    database: AsyncSession, *, model_id: str, model_digest: str, expected_revision: int
) -> None:
    """Operator-only recovery after a real model-list probe, never a promotion."""
    await locked_revision(database, expected_revision)
    model = await database.get(AIModel, model_id)
    health = await database.scalar(
        select(AIProviderHealth).where(AIProviderHealth.model_id == model_id).with_for_update()
    )
    if model is None or not model.enabled or model.digest != model_digest or health is None:
        raise RegistryConflict("Probe does not match the current enabled model")
    # Recovery never silently restores serving or shadow traffic. A reviewed
    # evaluation and explicit canary promotion are still required afterwards.
    await database.execute(
        update(AIProfileAssignment)
        .where(AIProfileAssignment.model_id == model_id)
        .values(rollout_percent=0, shadow_enabled=False)
    )
    health.status, health.failures = "HEALTHY", 0
    health.error_code = health.retry_after = health.probe_until = None
    await database.flush()
