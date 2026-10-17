"""Owner-saved personal memory. Reads never write or contact a provider."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from navox.api.auth import CurrentAccount, CurrentAccountDependency, DatabaseSession
from navox.db.memory import OwnerMemory
from navox.memory.schemas import MAX_MEMORIES, MemoryCreate, MemoryResponse, MemoryUpdate
from navox.memory.service import MemoryError, delete_memory, save_memory, update_memory

router = APIRouter(prefix="/memory", tags=["memory"])


def require_personal(account: CurrentAccount) -> None:
    if account.workspace.workspace_type.casefold() != "personal":
        raise HTTPException(403, "Memory is available in your personal workspace")


@router.get("", response_model=list[MemoryResponse])
async def list_memories(
    current_account: CurrentAccountDependency, database: DatabaseSession
) -> list[OwnerMemory]:
    require_personal(current_account)
    return list(
        (
            await database.scalars(
                select(OwnerMemory)
                .where(
                    OwnerMemory.workspace_id == current_account.workspace.id,
                    OwnerMemory.user_id == current_account.user.id,
                )
                .order_by(OwnerMemory.created_at, OwnerMemory.id)
                .limit(MAX_MEMORIES)
            )
        ).all()
    )


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    payload: MemoryCreate,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> OwnerMemory:
    require_personal(current_account)
    try:
        return await save_memory(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            payload=payload,
        )
    except MemoryError as error:
        raise HTTPException(error.status_code, str(error)) from error
    except IntegrityError as error:
        await database.rollback()
        raise HTTPException(409, "This preference is already saved. Refresh memory.") from error


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def edit_memory(
    memory_id: UUID,
    payload: MemoryUpdate,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> OwnerMemory:
    require_personal(current_account)
    try:
        return await update_memory(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            memory_id=memory_id,
            payload=payload,
        )
    except MemoryError as error:
        raise HTTPException(error.status_code, str(error)) from error


@router.delete("/{memory_id}", status_code=204)
async def forget_memory(
    memory_id: UUID,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
) -> Response:
    require_personal(current_account)
    try:
        await delete_memory(
            database,
            user_id=current_account.user.id,
            workspace_id=current_account.workspace.id,
            memory_id=memory_id,
        )
    except MemoryError as error:
        raise HTTPException(error.status_code, str(error)) from error
    return Response(status_code=204)
