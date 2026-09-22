from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from navox.api.auth import CurrentAccountDependency, DatabaseSession
from navox.api.commitments import (
    CommitmentResponse,
    current_workspace_commitment,
    response_from_commitment,
)
from navox.db.models import Commitment, CommitmentSource

router = APIRouter(prefix="/commitments", tags=["commitments"])
CommitmentType = Literal["deadline", "meeting", "follow_up", "promise", "renewal", "task"]


class ManualCommitmentRequest(BaseModel):
    request_id: UUID
    type: CommitmentType
    title: str = Field(min_length=3, max_length=256)
    description: str | None = Field(default=None, max_length=2_000)
    priority: int = Field(default=3, ge=1, le=5)
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("title must contain at least 3 non-whitespace characters")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("due_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("due_at must include a timezone")
        return value


def manual_request_key(request_id: UUID) -> str:
    return sha256(f"manual-request:{request_id}".encode()).hexdigest()


@router.post("", response_model=CommitmentResponse, status_code=status.HTTP_201_CREATED)
async def create_manual_commitment(
    payload: ManualCommitmentRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    dedupe_key = manual_request_key(payload.request_id)
    existing = await database.scalar(
        select(Commitment).where(
            Commitment.workspace_id == current_account.workspace.id,
            Commitment.user_id == current_account.user.id,
            Commitment.dedupe_key == dedupe_key,
        )
    )
    if existing is not None:
        return response_from_commitment(existing)

    commitment = Commitment(
        user_id=current_account.user.id,
        workspace_id=current_account.workspace.id,
        commitment_type=payload.type,
        title=payload.title,
        description=payload.description,
        status="confirmed",
        priority=payload.priority,
        due_at=payload.due_at,
        confidence=1.0,
        created_by="user",
        dedupe_key=dedupe_key,
        last_verified_at=datetime.now(UTC),
    )
    database.add(commitment)
    await database.flush()
    database.add(
        CommitmentSource(
            commitment_id=commitment.id,
            provider="navox",
            source_type="manual",
            external_resource_id=None,
            incoming_event_id=None,
            connection_id=None,
            source_metadata={"request_id": str(payload.request_id), "origin": "user"},
        )
    )
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        existing = await database.scalar(
            select(Commitment).where(
                Commitment.workspace_id == current_account.workspace.id,
                Commitment.user_id == current_account.user.id,
                Commitment.dedupe_key == dedupe_key,
            )
        )
        if existing is None:
            raise
        return response_from_commitment(existing)
    await database.refresh(commitment)
    return response_from_commitment(commitment)


async def transition_commitment(
    commitment_id: UUID,
    *,
    allowed_statuses: tuple[str, ...],
    target_status: str,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    completed_at: datetime | None = None,
) -> CommitmentResponse:
    result = await database.execute(
        update(Commitment)
        .where(
            Commitment.id == commitment_id,
            Commitment.workspace_id == current_account.workspace.id,
            Commitment.user_id == current_account.user.id,
            Commitment.status.in_(allowed_statuses),
        )
        .values(status=target_status, completed_at=completed_at)
        .returning(Commitment)
    )
    commitment = result.scalar_one_or_none()
    if commitment is None:
        existing = await current_workspace_commitment(
            commitment_id, current_account, database
        )
        if target_status == "completed" and existing.status == "completed":
            return response_from_commitment(existing)
        allowed = ", ".join(allowed_statuses)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Commitment must be in one of these states: {allowed}",
        )
    await database.commit()
    return response_from_commitment(commitment)


@router.post("/{commitment_id}/waiting", response_model=CommitmentResponse)
async def mark_waiting(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    return await transition_commitment(
        commitment_id,
        allowed_statuses=("confirmed", "attention"),
        target_status="waiting",
        current_account=current_account,
        database=database,
    )


@router.post("/{commitment_id}/resume", response_model=CommitmentResponse)
async def resume_commitment(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    return await transition_commitment(
        commitment_id,
        allowed_statuses=("waiting",),
        target_status="confirmed",
        current_account=current_account,
        database=database,
    )


@router.post("/{commitment_id}/complete", response_model=CommitmentResponse)
async def complete_commitment(
    commitment_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> CommitmentResponse:
    return await transition_commitment(
        commitment_id,
        allowed_statuses=("confirmed", "waiting", "attention"),
        target_status="completed",
        completed_at=datetime.now(UTC),
        current_account=current_account,
        database=database,
    )
