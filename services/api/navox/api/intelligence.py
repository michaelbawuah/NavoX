from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from navox.ai.configured import configured_adapters
from navox.ai.foundation.contracts import (
    AITask,
    Capability,
    LatencyClass,
    Profile,
    ProviderPolicy,
    QualityClass,
    Sensitivity,
    TaskType,
)
from navox.ai.foundation.persistence import RegistryStore, model_key
from navox.ai.prompts import EXTRACTION_PROMPT, EXTRACTION_SCHEMA
from navox.ai.routing import (
    EvaluationEvidence,
    PolicyRules,
    RoutingWeights,
    intersect_policy,
    rank_eligible,
)
from navox.ai.store import utc
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.ai_registry import (
    AIEvaluationRun,
    AIModel,
    AIProfileAssignment,
    AIProvider,
    AIProviderHealth,
    AIRoutingPolicy,
)
from navox.db.models import (
    Commitment,
    IntelligenceFeedback,
    IntelligencePreference,
    ObservationEvidence,
    OperationalObservation,
)
from navox.intelligence.dispatcher import dispatch_feedback
from navox.intelligence.feedback import (
    FeedbackConflict,
    FeedbackTargetNotFound,
    FeedbackType,
    bounded_weights,
    record_feedback,
)
from navox.intelligence.jobs import WorkspaceWork

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    target_id: UUID
    feedback_type: FeedbackType
    # Only commitments are accepted; source content cannot nominate another authority target.
    target_type: str = "commitment"


class FeedbackResponse(BaseModel):
    id: UUID
    applied: bool
    weights: dict[str, float]


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    payload: FeedbackRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
    settings: SettingsDependency,
) -> FeedbackResponse:
    response.headers["Cache-Control"] = "no-store"
    if payload.target_type != "commitment":
        raise HTTPException(status_code=422, detail="Feedback target must be a commitment")
    try:
        feedback, applied, weights = await record_feedback(
            database,
            workspace_id=current_account.workspace.id,
            user_id=current_account.user.id,
            target_id=payload.target_id,
            request_id=payload.request_id,
            feedback_type=payload.feedback_type,
        )
    except FeedbackTargetNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FeedbackConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await database.commit()
    if applied and settings.ai_provider != "disabled":
        try:
            await dispatch_feedback(
                WorkspaceWork(
                    str(current_account.user.id),
                    str(current_account.workspace.id),
                    str(payload.target_id),
                ),
                settings=settings,
                request_id=str(payload.request_id),
            )
        except Exception:
            # Feedback is already durable. Today reads the new preference immediately;
            # periodic reconciliation refreshes stored attention after a dispatch outage.
            pass
    return FeedbackResponse(id=feedback.id, applied=applied, weights=weights)


class IntelligenceStatusResponse(BaseModel):
    processing_ready: bool
    readiness_message: str
    observation_count: int
    evidence_count: int
    feedback_count: int
    active_commitments: int
    suppressed_commitments: int
    weights: dict[str, float]


async def extraction_readiness(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> tuple[bool, str]:
    """Inspect route configuration only; never admit work or read a source.

    A positive result means the current principal has a configured PERSONAL
    extraction route with some live traffic. Each real job still checks its
    rollout bucket, source authorization/classification, context size and budget.
    """
    if account.user.agent_paused:
        return False, "NavoX is paused. Resume it before syncing your apps."
    mode = settings.ai_provider.casefold().strip()
    # The manual Google sync endpoint currently requires this credential in both modes.
    has_key = bool(settings.openai_api_key and settings.openai_api_key.get_secret_value().strip())
    if mode not in {"openai", "automatic"} or not has_key:
        return False, "App syncing is not configured right now. You can still add a task yourself."
    if mode == "openai":
        return (
            True,
            "Syncing is configured. Grant read access, then check sync progress for the result.",
        )
    unavailable = (
        False,
        "App syncing is not available for this account right now. "
        "You can still add a task yourself.",
    )
    try:
        operator = PolicyRules.model_validate(settings.ai_provider_policy)
        rules = [operator]
        for scope in ("workspace", str(account.user.id)):
            row = await database.get(AIRoutingPolicy, (account.workspace.id, scope))
            if row is not None:
                rules.append(PolicyRules.model_validate_json(row.policy))
        registry = await RegistryStore(database).load()
        if registry is None:
            return unavailable
        task = AITask(
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            task_type=TaskType.EXTRACT,
            profile=Profile.EXTRACTION_HIGH_ACCURACY,
            capability_requirements=frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT}),
            output_schema=EXTRACTION_SCHEMA,
            prompt=EXTRACTION_PROMPT,
            sensitivity=Sensitivity.PERSONAL,
            latency_class=LatencyClass.BACKGROUND,
            quality_class=QualityClass.HIGH,
            max_cost=operator.max_cost,
            max_output_tokens=4000,
            provider_policy=ProviderPolicy(
                workspace_id=account.workspace.id,
                user_id=account.user.id,
                revision=1,
                grants=operator.grants,
            ),
        )
        binding = registry.bind_task(task)
        available = set()
        evaluations = {}
        now = datetime.now(UTC)
        for reference in binding.profile.assignments:
            key = model_key(reference)
            assignment = await database.get(AIProfileAssignment, (task.profile.value, key))
            model = await database.get(AIModel, key)
            provider = await database.get(AIProvider, reference.provider.value)
            health = await database.get(AIProviderHealth, key)
            if (
                assignment is None
                or assignment.rollout_percent <= 0
                or model is None
                or not model.enabled
                or provider is None
                or not provider.enabled
                or health is None
                or health.status == "DISABLED"
                or (utc(health.retry_after) or now) > now
                or (utc(health.probe_until) or now) > now
            ):
                continue
            evaluation = await database.scalar(
                select(AIEvaluationRun)
                .where(
                    AIEvaluationRun.model_id == key,
                    AIEvaluationRun.model_digest == model.digest,
                    AIEvaluationRun.registry_revision == registry.revision,
                    AIEvaluationRun.profile == task.profile.value,
                    AIEvaluationRun.task_type == task.task_type.value,
                    AIEvaluationRun.prompt == f"{task.prompt.name}@{task.prompt.version}",
                    AIEvaluationRun.schema
                    == f"{task.output_schema.name}@{task.output_schema.version}",
                )
                .order_by(AIEvaluationRun.evaluated_at.desc(), AIEvaluationRun.id.desc())
                .limit(1)
            )
            if evaluation is not None:
                evaluations[key] = EvaluationEvidence.model_validate_json(evaluation.evidence)
                available.add(key)
        candidates = rank_eligible(
            task,
            registry,
            policy=intersect_policy(task, tuple(rules)),
            available=frozenset(available),
            evaluations=evaluations,
            input_bound=(
                len(binding.prompt.instructions.encode())
                + len(binding.output_schema.document.text.encode())
                + 2048
            ),
            remaining_budget=min(rule.max_cost for rule in rules),
            preferred=None,
            weights=RoutingWeights(),
            now=now,
        )
        adapters = configured_adapters(settings, registry)
        ready = any(
            model.reference.provider in adapters
            and task.capability_requirements.issubset(
                adapters[model.reference.provider].capabilities(model.reference.model)
            )
            for model, _ in candidates
        )
    except (ValueError, LookupError):
        return unavailable
    if not ready:
        return unavailable
    return (
        True,
        "Syncing is configured for your account. Each sync still checks read access, "
        "source permissions and availability.",
    )


@router.get("/status", response_model=IntelligenceStatusResponse)
async def intelligence_status(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    response: Response,
    settings: SettingsDependency,
) -> IntelligenceStatusResponse:
    response.headers["Cache-Control"] = "no-store"
    user_id, workspace_id = current_account.user.id, current_account.workspace.id
    observation_scope = (
        OperationalObservation.user_id == user_id,
        OperationalObservation.workspace_id == workspace_id,
    )
    observation_count = await database.scalar(
        select(func.count()).select_from(OperationalObservation).where(*observation_scope)
    )
    evidence_count = await database.scalar(
        select(func.count())
        .select_from(ObservationEvidence)
        .join(OperationalObservation)
        .where(*observation_scope)
    )
    feedback_count = await database.scalar(
        select(func.count())
        .select_from(IntelligenceFeedback)
        .where(
            IntelligenceFeedback.workspace_id == workspace_id,
            IntelligenceFeedback.user_id == user_id,
        )
    )
    commitments = list(
        await database.scalars(
            select(Commitment).where(
                Commitment.workspace_id == workspace_id, Commitment.user_id == user_id
            )
        )
    )
    preference = await database.scalar(
        select(IntelligencePreference).where(
            IntelligencePreference.workspace_id == workspace_id,
            IntelligencePreference.user_id == user_id,
        )
    )
    ready, message = await extraction_readiness(current_account, database, settings)
    return IntelligenceStatusResponse(
        processing_ready=ready,
        readiness_message=message,
        observation_count=observation_count or 0,
        evidence_count=evidence_count or 0,
        feedback_count=feedback_count or 0,
        active_commitments=sum(
            item.status
            in {"candidate", "confirmed", "attention", "waiting", "waiting_on_external", "upcoming"}
            for item in commitments
        ),
        suppressed_commitments=sum(item.attention_band == "SUPPRESS" for item in commitments),
        weights=bounded_weights(preference.weights) if preference else {},
    )
