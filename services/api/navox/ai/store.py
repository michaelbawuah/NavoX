"""Database authority, durable health, rollout state, and content-free task traces."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.foundation.adapter import ErrorCode, ProviderError
from navox.ai.foundation.contracts import AITask, ProviderPolicy
from navox.ai.foundation.persistence import RegistryStore
from navox.ai.foundation.registry import RegistrySnapshot
from navox.ai.routing import (
    EvaluationEvidence,
    PolicyRules,
    RoutingWeights,
    UserPreferences,
    intersect_policy,
    preference_scope_key,
)
from navox.db.ai_registry import (
    AIEvaluationRun,
    AIModel,
    AIProfile,
    AIProfileAssignment,
    AIProvider,
    AIProviderHealth,
    AIRoutingPolicy,
    AITaskRun,
)
from navox.db.models import User, WorkspaceMembership


def utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


@dataclass(frozen=True)
class RoutingSnapshot:
    registry: RegistrySnapshot
    policy: ProviderPolicy
    rules: tuple[PolicyRules, ...]
    available: frozenset[str]
    evaluations: dict[str, EvaluationEvidence]
    weights: RoutingWeights


class GatewayStore:
    def __init__(
        self, factory: async_sessionmaker[AsyncSession], operator_policy: PolicyRules
    ) -> None:
        self.factory, self.operator_policy = factory, operator_policy

    async def authorize(self, task: AITask) -> None:
        async with self.factory() as db:
            member = await db.get(WorkspaceMembership, (task.workspace_id, task.user_id))
            user = await db.get(User, task.user_id)
            if member is None or user is None or user.agent_paused:
                raise PermissionError("AI task access denied")

    async def snapshot(
        self, task: AITask, *, shadow: bool = False, claimed_model: str | None = None
    ) -> RoutingSnapshot:
        async with self.factory() as db:
            member = await db.get(WorkspaceMembership, (task.workspace_id, task.user_id))
            user = await db.get(User, task.user_id)
            if member is None or user is None or user.agent_paused:
                raise PermissionError("AI task access denied")
            registry = await RegistryStore(db).load()
            if registry is None:
                raise ValueError("AI catalog is not configured")
            rules = [self.operator_policy]
            for scope in ("workspace", str(task.user_id)):
                row = await db.get(AIRoutingPolicy, (task.workspace_id, scope))
                if row:
                    rules.append(PolicyRules.model_validate_json(row.policy))
            preference = await db.get(
                AIRoutingPolicy, (task.workspace_id, preference_scope_key(task.user_id))
            )
            if preference is not None:
                choice = UserPreferences.model_validate_json(preference.policy)
                # Preferences have no grant/cost/shadow authority. The earlier
                # operator, workspace and per-user policies remain hard ceilings.
                rules.append(
                    PolicyRules(
                        grants=task.provider_policy.grants,
                        preferred_provider=choice.preferred_provider,
                        allow_fallback=choice.allow_fallback,
                        max_fallbacks=3 if choice.allow_fallback else 0,
                        max_cost=Decimal("10"),
                        allow_shadow=True,
                    )
                )
            policy = intersect_policy(task, tuple(rules))
            bucket = int(hashlib.sha256(str(task.id).encode()).hexdigest()[:8], 16) % 100
            assignments = (
                await db.scalars(
                    select(AIProfileAssignment).where(
                        AIProfileAssignment.profile == task.profile.value
                    )
                )
            ).all()
            available = set()
            now = datetime.now(UTC)
            evaluations = {}
            for assignment in assignments:
                if shadow:
                    if not assignment.shadow_enabled or not all(r.allow_shadow for r in rules):
                        continue
                elif bucket >= assignment.rollout_percent:
                    continue
                model = await db.get(AIModel, assignment.model_id)
                provider = await db.get(AIProvider, model.provider) if model else None
                health = await db.get(AIProviderHealth, assignment.model_id)
                if (
                    model is None
                    or not model.enabled
                    or provider is None
                    or not provider.enabled
                    or health is None
                    or health.status == "DISABLED"
                ):
                    continue
                retry, probe = utc(health.retry_after), utc(health.probe_until)
                if (retry is not None and retry > now) or (
                    probe is not None and probe > now and model.id != claimed_model
                ):
                    continue
                evaluation = await db.scalar(
                    select(AIEvaluationRun)
                    .where(
                        AIEvaluationRun.model_id == model.id,
                        AIEvaluationRun.model_digest == model.digest,
                        AIEvaluationRun.registry_revision == registry.revision,
                        AIEvaluationRun.task_type == task.task_type.value,
                        AIEvaluationRun.prompt == f"{task.prompt.name}@{task.prompt.version}",
                        AIEvaluationRun.schema
                        == f"{task.output_schema.name}@{task.output_schema.version}",
                        AIEvaluationRun.profile == task.profile.value,
                    )
                    .order_by(AIEvaluationRun.evaluated_at.desc(), AIEvaluationRun.id.desc())
                    .limit(1)
                )
                if evaluation is not None:
                    evaluations[model.id] = EvaluationEvidence.model_validate_json(
                        evaluation.evidence
                    )
                    available.add(model.id)
            profile = await db.get(AIProfile, task.profile.value)
            return RoutingSnapshot(
                registry,
                policy,
                tuple(rules),
                frozenset(available),
                evaluations,
                RoutingWeights.model_validate(profile.weights if profile else {}),
            )

    async def claim(self, model_id: str) -> bool:
        async with self.factory() as db:
            row = await db.scalar(
                select(AIProviderHealth)
                .where(AIProviderHealth.model_id == model_id)
                .with_for_update()
            )
            now = datetime.now(UTC)
            if row is None or row.status == "DISABLED":
                return False
            retry, probe = utc(row.retry_after), utc(row.probe_until)
            if (retry is not None and retry > now) or (probe is not None and probe > now):
                return False
            if row.status == "UNAVAILABLE":
                claimed = await db.scalar(
                    update(AIProviderHealth)
                    .where(
                        AIProviderHealth.model_id == model_id,
                        AIProviderHealth.status == "UNAVAILABLE",
                        or_(
                            AIProviderHealth.probe_until.is_(None),
                            AIProviderHealth.probe_until <= now,
                        ),
                        or_(
                            AIProviderHealth.retry_after.is_(None),
                            AIProviderHealth.retry_after <= now,
                        ),
                    )
                    .values(probe_until=now + timedelta(minutes=6))
                    .returning(AIProviderHealth.model_id)
                    .execution_options(synchronize_session=False)
                )
                if claimed is None:
                    return False
            await db.commit()
            return True

    async def health(self, model_id: str, error: ProviderError | None) -> None:
        async with self.factory() as db:
            row = await db.scalar(
                select(AIProviderHealth)
                .where(AIProviderHealth.model_id == model_id)
                .with_for_update()
            )
            if row is None:
                return
            row.probe_until = None
            if error is None:
                row.status, row.failures, row.error_code, row.retry_after = "HEALTHY", 0, None, None
            else:
                row.failures += 1
                row.error_code = error.code.value
                if error.code in {ErrorCode.AUTHENTICATION, ErrorCode.INVALID_REQUEST}:
                    row.status = "DISABLED"
                elif row.failures >= 3 or error.code == ErrorCode.RATE_LIMIT:
                    row.status = "UNAVAILABLE"
                    seconds = max(
                        min(900, 30 * 2 ** min(row.failures, 5)), (error.retry_after_ms or 0) / 1000
                    )
                    row.retry_after = datetime.now(UTC) + timedelta(seconds=seconds)
                else:
                    row.status = "DEGRADED"
            await db.commit()

    async def trace(self, run: AITaskRun) -> None:
        async with self.factory() as db:
            await db.merge(run)
            await db.commit()
