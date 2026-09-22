from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from navox.approvals.dispatcher import (
    ApprovalDispatcher,
    ApprovalDispatchError,
    TemporalApprovalDispatcher,
)
from navox.approvals.schemas import (
    ApprovalDecisionRequest,
    EditGmailSendRequest,
    PrepareGmailSendRequest,
)
from navox.approvals.service import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalPausedError,
    ApprovalPermissionError,
    ApprovalService,
    latest_approval,
)
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.models import Action, Approval, PlanStep, WorkflowRef

router = APIRouter(tags=["actions"])


def get_approval_dispatcher() -> ApprovalDispatcher:
    return TemporalApprovalDispatcher()


ApprovalDispatcherDependency = Annotated[ApprovalDispatcher, Depends(get_approval_dispatcher)]


class ApprovalResponse(BaseModel):
    id: UUID
    version: int
    status: str
    action_payload_hash: str
    expires_at: datetime
    approved_at: datetime | None
    rejected_at: datetime | None
    consumed_at: datetime | None
    superseded_at: datetime | None


class ActionDetailResponse(BaseModel):
    id: UUID
    commitment_id: UUID | None
    plan_id: UUID
    provider: str
    action_type: str
    risk_level: str
    requires_approval: bool
    status: str
    payload: dict[str, object]
    payload_hash: str
    result: dict[str, object]
    policy_reason: str | None
    created_at: datetime
    started_at: datetime | None
    executed_at: datetime | None
    verified_at: datetime | None
    approval: ApprovalResponse | None
    workflow_status: str | None


def approval_response(approval: Approval | None) -> ApprovalResponse | None:
    if approval is None:
        return None
    return ApprovalResponse(
        id=approval.id,
        version=approval.version,
        status=approval.status,
        action_payload_hash=approval.action_payload_hash,
        expires_at=approval.expires_at,
        approved_at=approval.approved_at,
        rejected_at=approval.rejected_at,
        consumed_at=approval.consumed_at,
        superseded_at=approval.superseded_at,
    )


async def action_response(database: DatabaseSession, action: Action) -> ActionDetailResponse:
    step = await database.scalar(select(PlanStep).where(PlanStep.id == action.plan_step_id))
    if step is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Action plan step is unavailable",
        )
    approval = await latest_approval(database, action.id)
    workflow_ref = await database.scalar(
        select(WorkflowRef).where(
            WorkflowRef.entity_type == "action",
            WorkflowRef.entity_id == action.id,
            WorkflowRef.workflow_type == "approved_action",
        )
    )
    return ActionDetailResponse(
        id=action.id,
        commitment_id=action.commitment_id,
        plan_id=step.plan_id,
        provider=action.provider,
        action_type=action.action_type,
        risk_level=action.risk_level,
        requires_approval=action.requires_approval,
        status=action.status,
        payload=action.payload,
        payload_hash=action.payload_hash,
        result=action.result,
        policy_reason=action.policy_reason,
        created_at=action.created_at,
        started_at=action.started_at,
        executed_at=action.executed_at,
        verified_at=action.verified_at,
        approval=approval_response(approval),
        workflow_status=workflow_ref.status if workflow_ref is not None else None,
    )


async def current_action(
    database: DatabaseSession,
    current_account: CurrentAccountDependency,
    action_id: UUID,
) -> Action:
    action = await database.scalar(
        select(Action).where(
            Action.id == action_id,
            Action.user_id == current_account.user.id,
            Action.workspace_id == current_account.workspace.id,
        )
    )
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
    return action


def approval_error(error: Exception) -> HTTPException:
    if isinstance(error, ApprovalNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ApprovalPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))
    if isinstance(error, (ApprovalConflictError, ApprovalPausedError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Approval request could not be completed",
    )


async def ensure_workflow(
    dispatcher: ApprovalDispatcher,
    database: DatabaseSession,
    action: Action,
    settings: SettingsDependency,
) -> None:
    try:
        await dispatcher.ensure_started(database, action=action, settings=settings)
    except ApprovalDispatchError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Action was saved, but approval workflow dispatch failed",
                "action_id": str(action.id),
            },
        ) from error


async def best_effort_signal(
    dispatcher: ApprovalDispatcher,
    *,
    action_id: UUID,
    decision: str,
    settings: SettingsDependency,
) -> None:
    try:
        await dispatcher.signal(
            action_id=action_id,
            decision=decision,
            settings=settings,
        )
    except ApprovalDispatchError:
        # PostgreSQL is the authorization source of truth. The Temporal workflow
        # polls that state, so a lost acceleration signal cannot bypass or undo
        # the user's persisted decision.
        return


@router.post(
    "/commitments/{commitment_id}/actions/gmail-send/prepare",
    response_model=ActionDetailResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_gmail_send(
    commitment_id: UUID,
    payload: PrepareGmailSendRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: ApprovalDispatcherDependency,
) -> ActionDetailResponse:
    try:
        action, _approval, _created = await ApprovalService().prepare_gmail_send(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            commitment_id=commitment_id,
            request=payload,
        )
    except (
        ApprovalNotFoundError,
        ApprovalPermissionError,
        ApprovalConflictError,
        ApprovalPausedError,
    ) as error:
        raise approval_error(error) from error

    await ensure_workflow(dispatcher, database, action, settings)
    await database.refresh(action)
    return await action_response(database, action)


@router.get("/actions", response_model=list[ActionDetailResponse])
async def list_actions(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    action_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ActionDetailResponse]:
    statement = select(Action).where(
        Action.user_id == current_account.user.id,
        Action.workspace_id == current_account.workspace.id,
    )
    if action_status is not None:
        statement = statement.where(Action.status == action_status)
    actions = list(
        await database.scalars(statement.order_by(Action.created_at.desc()).limit(limit))
    )
    return [await action_response(database, action) for action in actions]


@router.get("/actions/{action_id}", response_model=ActionDetailResponse)
async def get_action(
    action_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> ActionDetailResponse:
    action = await current_action(database, current_account, action_id)
    return await action_response(database, action)


@router.post("/actions/{action_id}/approve", response_model=ActionDetailResponse)
async def approve_action(
    action_id: UUID,
    payload: ApprovalDecisionRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: ApprovalDispatcherDependency,
) -> ActionDetailResponse:
    try:
        action, _approval = await ApprovalService().approve(
            database,
            action_id=action_id,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            request_id=payload.request_id,
        )
    except (ApprovalNotFoundError, ApprovalConflictError, ApprovalPausedError) as error:
        raise approval_error(error) from error

    await ensure_workflow(dispatcher, database, action, settings)
    await best_effort_signal(
        dispatcher,
        action_id=action.id,
        decision="approved",
        settings=settings,
    )
    await database.refresh(action)
    return await action_response(database, action)


@router.post("/actions/{action_id}/reject", response_model=ActionDetailResponse)
async def reject_action(
    action_id: UUID,
    payload: ApprovalDecisionRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: ApprovalDispatcherDependency,
) -> ActionDetailResponse:
    try:
        action, _approval = await ApprovalService().reject(
            database,
            action_id=action_id,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            request_id=payload.request_id,
        )
    except (ApprovalNotFoundError, ApprovalConflictError) as error:
        raise approval_error(error) from error

    await ensure_workflow(dispatcher, database, action, settings)
    await best_effort_signal(
        dispatcher,
        action_id=action.id,
        decision="rejected",
        settings=settings,
    )
    await database.refresh(action)
    return await action_response(database, action)


@router.post("/actions/{action_id}/edit", response_model=ActionDetailResponse)
async def edit_action(
    action_id: UUID,
    payload: EditGmailSendRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    dispatcher: ApprovalDispatcherDependency,
) -> ActionDetailResponse:
    try:
        action, _approval = await ApprovalService().edit_gmail_send(
            database,
            action_id=action_id,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            request=payload,
        )
    except (ApprovalNotFoundError, ApprovalConflictError) as error:
        raise approval_error(error) from error

    await ensure_workflow(dispatcher, database, action, settings)
    await best_effort_signal(
        dispatcher,
        action_id=action.id,
        decision="modified",
        settings=settings,
    )
    await database.refresh(action)
    return await action_response(database, action)
