from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
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
    configured = bool(
        settings.ai_provider == "openai"
        and settings.openai_api_key
        and settings.openai_api_key.get_secret_value().strip()
    )
    ready = configured and not current_account.user.agent_paused
    message = (
        "Intelligence is paused in your workspace."
        if current_account.user.agent_paused
        else "Extraction is configured. Processing also requires the workflow worker."
        if configured
        else "Extraction needs a configured AI provider and API key."
    )
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
