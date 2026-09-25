from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from navox.agent.audit import add_audit_event
from navox.api.auth import CurrentAccountDependency, DatabaseSession
from navox.db.models import AuditEvent, Commitment, CommitmentRelation, CommitmentSource, Workspace
from navox.intelligence.attention import ACTIVE

router = APIRouter(prefix="/commitments", tags=["commitments"])


class CommitmentResponse(BaseModel):
    id: UUID
    type: str
    title: str
    description: str | None
    status: str
    priority: int
    due_at: datetime | None
    confidence: float
    created_by: str
    created_at: datetime


class CommitmentSourceResponse(BaseModel):
    provider: str
    source_type: str
    external_resource_id: str | None
    incoming_event_id: UUID | None
    extracted_at: datetime
    metadata: dict[str, str]


class CommitmentRelationResponse(BaseModel):
    from_commitment_id: UUID
    to_commitment_id: UUID
    relation_type: str


class CommitmentDetailResponse(CommitmentResponse):
    sources: list[CommitmentSourceResponse]
    outgoing_relations: list[CommitmentRelationResponse]


def response_from_commitment(commitment: Commitment) -> CommitmentResponse:
    return CommitmentResponse(
        id=commitment.id,
        type=commitment.commitment_type,
        title=commitment.title,
        description=commitment.description,
        status=commitment.status,
        priority=commitment.priority,
        due_at=commitment.due_at,
        confidence=commitment.confidence,
        created_by=commitment.created_by,
        created_at=commitment.created_at,
    )


async def current_workspace_commitment(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    *,
    lock: bool = False,
) -> Commitment:
    statement = (
        select(Commitment)
        .where(
            Commitment.id == commitment_id,
            Commitment.workspace_id == current_account.workspace.id,
            Commitment.user_id == current_account.user.id,
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        await database.scalar(
            select(Workspace)
            .where(Workspace.id == current_account.workspace.id)
            .with_for_update(key_share=True)
        )
        statement = statement.with_for_update(key_share=True)
    commitment = await database.scalar(statement)
    if commitment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commitment not found")
    return commitment


@router.get("", response_model=list[CommitmentResponse])
async def list_commitments(
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    commitment_status: Annotated[str | None, Query(alias="status")] = None,
) -> list[CommitmentResponse]:
    statement = select(Commitment).where(
        Commitment.workspace_id == current_account.workspace.id,
        Commitment.user_id == current_account.user.id,
    )
    if commitment_status is not None:
        statement = statement.where(Commitment.status == commitment_status)
    commitments = await database.scalars(
        statement.order_by(Commitment.due_at, Commitment.created_at)
    )
    return [response_from_commitment(commitment) for commitment in commitments]


@router.get("/{commitment_id}", response_model=CommitmentDetailResponse)
async def get_commitment(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentDetailResponse:
    commitment = await current_workspace_commitment(commitment_id, current_account, database)
    sources = await database.scalars(
        select(CommitmentSource)
        .where(CommitmentSource.commitment_id == commitment.id)
        .order_by(CommitmentSource.extracted_at)
    )
    relations = await database.scalars(
        select(CommitmentRelation)
        .where(CommitmentRelation.from_commitment_id == commitment.id)
        .order_by(CommitmentRelation.created_at)
    )
    return CommitmentDetailResponse(
        **response_from_commitment(commitment).model_dump(),
        sources=[
            CommitmentSourceResponse(
                provider=source.provider,
                source_type=source.source_type,
                external_resource_id=source.external_resource_id,
                incoming_event_id=source.incoming_event_id,
                extracted_at=source.extracted_at,
                metadata=source.source_metadata,
            )
            for source in sources
        ],
        outgoing_relations=[
            CommitmentRelationResponse(
                from_commitment_id=relation.from_commitment_id,
                to_commitment_id=relation.to_commitment_id,
                relation_type=relation.relation_type,
            )
            for relation in relations
        ],
    )


@router.post("/{commitment_id}/confirm", response_model=CommitmentResponse)
async def confirm_commitment(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    commitment = await current_workspace_commitment(
        commitment_id, current_account, database, lock=True
    )
    if commitment.status != "candidate":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only candidate commitments can be confirmed",
        )
    commitment.status = "confirmed"
    add_audit_event(
        database,
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        event_type="commitment.confirmed",
        entity_type="commitment",
        entity_id=commitment.id,
        actor_type="user",
        actor_id=str(current_account.user.id),
    )
    await database.commit()
    return response_from_commitment(commitment)


@router.post("/{commitment_id}/reject", response_model=CommitmentResponse)
async def reject_commitment(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    commitment = await current_workspace_commitment(
        commitment_id, current_account, database, lock=True
    )
    if commitment.status != "candidate":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only candidate commitments can be rejected",
        )
    commitment.status = "rejected"
    await database.commit()
    return response_from_commitment(commitment)


@router.post("/{commitment_id}/keep", response_model=CommitmentResponse)
async def keep_email_suggestion(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    """Explicitly choose to track a saved suggestion without rereading the mailbox."""
    commitment = await current_workspace_commitment(
        commitment_id, current_account, database, lock=True
    )
    if commitment.created_by != "ai" or commitment.status not in ACTIVE:
        raise HTTPException(409, "Only active suggestions can be kept")
    existing = await database.scalar(
        select(AuditEvent.id).where(
            AuditEvent.user_id == current_account.user.id,
            AuditEvent.workspace_id == current_account.workspace.id,
            AuditEvent.entity_id == commitment.id,
            AuditEvent.entity_type == "commitment",
            AuditEvent.actor_type == "user",
            AuditEvent.event_type == "commitment.kept",
        )
    )
    if commitment.status == "candidate":
        commitment.status = "confirmed"
    if existing is None:
        add_audit_event(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            event_type="commitment.kept",
            entity_type="commitment",
            entity_id=commitment.id,
            actor_type="user",
            actor_id=str(current_account.user.id),
        )
    await database.commit()
    return response_from_commitment(commitment)
