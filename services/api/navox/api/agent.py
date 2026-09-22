from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from navox.agent.audit import add_audit_event
from navox.agent.dispatcher import AgentDispatchError, AgentDispatcher, TemporalAgentDispatcher
from navox.agent.service import (
    AgentPausedError,
    AgentPlanConflictError,
    AgentPlanNotFoundError,
    BoundedAgentService,
)
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.models import Action, Plan, PlanStep, WorkflowRef

router = APIRouter(tags=["agent"])


def get_agent_dispatcher() -> AgentDispatcher:
    return TemporalAgentDispatcher()


AgentDispatcherDependency = Annotated[AgentDispatcher, Depends(get_agent_dispatcher)]


class HandleCommitmentRequest(BaseModel):
    request_id: UUID
    goal: str | None = Field(default=None, max_length=512)


class ActionResponse(BaseModel):
    id: UUID
    provider: str
    action_type: str
    risk_level: str
    requires_approval: bool
    status: str
    policy_reason: str | None
    payload_hash: str
    result: dict[str, object]
    created_at: datetime
    executed_at: datetime | None


class PlanStepResponse(BaseModel):
    id: UUID
    sequence_number: int
    action_type: str
    description: str
    status: str
    risk_level: str
    input: dict[str, object]
    output: dict[str, object]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    action: ActionResponse | None


class WorkflowResponse(BaseModel):
    workflow_id: str
    status: str
    run_id: str | None


class PlanSummaryResponse(BaseModel):
    id: UUID
    commitment_id: UUID | None
    goal: str
    status: str
    planner_version: str
    context_hash: str
    max_steps: int
    replan_count: int
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class PlanDetailResponse(PlanSummaryResponse):
    steps: list[PlanStepResponse]
    workflow: WorkflowResponse | None


class AgentStateResponse(BaseModel):
    paused: bool


async def current_plan(
    database: DatabaseSession,
    current_account: CurrentAccountDependency,
    plan_id: UUID,
) -> Plan:
    plan = await database.scalar(
        select(Plan).where(
            Plan.id == plan_id,
            Plan.user_id == current_account.user.id,
            Plan.workspace_id == current_account.workspace.id,
        )
    )
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return plan


def summary_response(plan: Plan) -> PlanSummaryResponse:
    return PlanSummaryResponse(
        id=plan.id,
        commitment_id=plan.commitment_id,
        goal=plan.goal,
        status=plan.status,
        planner_version=plan.planner_version,
        context_hash=plan.context_hash,
        max_steps=plan.max_steps,
        replan_count=plan.replan_count,
        error_code=plan.error_code,
        created_at=plan.created_at,
        completed_at=plan.completed_at,
    )


async def detail_response(database: DatabaseSession, plan: Plan) -> PlanDetailResponse:
    steps = list(
        await database.scalars(
            select(PlanStep)
            .where(PlanStep.plan_id == plan.id)
            .order_by(PlanStep.sequence_number)
        )
    )
    actions_by_step: dict[UUID, Action] = {}
    if steps:
        actions = list(
            await database.scalars(
                select(Action).where(Action.plan_step_id.in_([step.id for step in steps]))
            )
        )
        actions_by_step = {action.plan_step_id: action for action in actions}

    workflow = await database.scalar(
        select(WorkflowRef).where(
            WorkflowRef.entity_type == "plan",
            WorkflowRef.entity_id == plan.id,
            WorkflowRef.workflow_type == "handle_commitment",
        )
    )
    step_responses: list[PlanStepResponse] = []
    for step in steps:
        action = actions_by_step.get(step.id)
        step_responses.append(
            PlanStepResponse(
                id=step.id,
                sequence_number=step.sequence_number,
                action_type=step.action_type,
                description=step.description,
                status=step.status,
                risk_level=step.risk_level,
                input=step.input_payload,
                output=step.output_payload,
                created_at=step.created_at,
                started_at=step.started_at,
                completed_at=step.completed_at,
                action=(
                    ActionResponse(
                        id=action.id,
                        provider=action.provider,
                        action_type=action.action_type,
                        risk_level=action.risk_level,
                        requires_approval=action.requires_approval,
                        status=action.status,
                        policy_reason=action.policy_reason,
                        payload_hash=action.payload_hash,
                        result=action.result,
                        created_at=action.created_at,
                        executed_at=action.executed_at,
                    )
                    if action is not None
                    else None
                ),
            )
        )

    summary = summary_response(plan)
    return PlanDetailResponse(
        **summary.model_dump(),
        steps=step_responses,
        workflow=(
            WorkflowResponse(
                workflow_id=workflow.temporal_workflow_id,
                status=workflow.status,
                run_id=workflow.temporal_run_id,
            )
            if workflow is not None
            else None
        ),
    )


@router.post(
    "/commitments/{commitment_id}/handle",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def handle_commitment(
    commitment_id: UUID,
    payload: HandleCommitmentRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: AgentDispatcherDependency,
) -> PlanDetailResponse:
    service = BoundedAgentService()
    try:
        plan, steps, _created = await service.create_plan(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            commitment_id=commitment_id,
            request_id=payload.request_id,
            goal=payload.goal,
        )
    except AgentPlanNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (AgentPlanConflictError, AgentPausedError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    try:
        await dispatcher.dispatch(
            database,
            plan=plan,
            step_ids=[step.id for step in steps],
            settings=settings,
        )
    except AgentDispatchError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Plan was saved, but durable execution could not be dispatched",
                "plan_id": str(plan.id),
            },
        ) from error

    await database.refresh(plan)
    return await detail_response(database, plan)


@router.get("/plans", response_model=list[PlanSummaryResponse])
async def list_plans(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    commitment_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[PlanSummaryResponse]:
    statement = select(Plan).where(
        Plan.user_id == current_account.user.id,
        Plan.workspace_id == current_account.workspace.id,
    )
    if commitment_id is not None:
        statement = statement.where(Plan.commitment_id == commitment_id)
    plans = list(
        await database.scalars(statement.order_by(Plan.created_at.desc()).limit(limit))
    )
    return [summary_response(plan) for plan in plans]


@router.get("/plans/{plan_id}", response_model=PlanDetailResponse)
async def get_plan(
    plan_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> PlanDetailResponse:
    plan = await current_plan(database, current_account, plan_id)
    return await detail_response(database, plan)


@router.get("/agent/state", response_model=AgentStateResponse)
async def get_agent_state(
    current_account: CurrentAccountDependency,
) -> AgentStateResponse:
    return AgentStateResponse(paused=current_account.user.agent_paused)


async def set_agent_paused(
    paused: bool,
    *,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> AgentStateResponse:
    current_account.user.agent_paused = paused
    add_audit_event(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        event_type="agent.paused" if paused else "agent.resumed",
        entity_type="user",
        entity_id=current_account.user.id,
        actor_type="user",
        actor_id=str(current_account.user.id),
    )
    await database.commit()
    return AgentStateResponse(paused=paused)


@router.post("/agent/pause", response_model=AgentStateResponse)
async def pause_agent(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> AgentStateResponse:
    return await set_agent_paused(True, current_account=current_account, database=database)


@router.post("/agent/resume", response_model=AgentStateResponse)
async def resume_agent(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> AgentStateResponse:
    return await set_agent_paused(False, current_account=current_account, database=database)
