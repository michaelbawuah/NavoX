"""Private usage reads and explicitly allowlisted operator configuration."""

from collections.abc import Awaitable
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.vox import (
    PrivateVoxRoute,
    SessionHashDependency,
    bounded_json,
    expected_account,
    web_session_hash,
)
from navox.connectors.sync_state import database_now
from navox.db.models import UserSession
from navox.db.vox import VoxCall
from navox.db.vox_usage import VoxUsageStream
from navox.student.service import StudentError
from navox.vox import service, usage
from navox.vox.usage_schemas import BudgetStatus, UsagePolicy


async def live_web_session(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    session_token_hash: SessionHashDependency,
) -> None:
    """Fresh browser-only proof for every read, without locking a long report."""
    now = await database_now(database)
    session_id = await database.scalar(
        select(UserSession.id).where(
            UserSession.user_id == account.user.id,
            UserSession.token_hash == session_token_hash,
            UserSession.client_type == "web",
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )
    if session_id is None:
        raise HTTPException(401, "Authentication required")


router = APIRouter(
    prefix="/vox/usage",
    tags=["vox usage"],
    route_class=PrivateVoxRoute,
    dependencies=[Depends(web_session_hash), Depends(expected_account), Depends(live_web_session)],
)


async def _result[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except (usage.UsageError, StudentError, service.VoxError) as error:
        raise HTTPException(error.status_code, str(error)) from None


def operator(account: CurrentAccountDependency, settings: SettingsDependency) -> None:
    if str(account.user.id) not in settings.student_vox_operator_user_ids:
        raise HTTPException(403, "Operator access required")


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    policy: UsagePolicy


class PolicyView(BaseModel):
    revision: int
    policy: UsagePolicy


@router.get("/status", response_model=BudgetStatus)
async def current_usage(
    account: CurrentAccountDependency, database: DatabaseSession
) -> BudgetStatus:
    return await _result(usage.budget_status(database, account.user.id, account.workspace.id))


@router.get("/calls/{call_id}")
async def owned_call_usage(
    call_id: UUID,
    account: CurrentAccountDependency,
    database: DatabaseSession,
) -> dict[str, object]:
    call = await database.scalar(
        select(VoxCall.id).where(
            VoxCall.id == call_id,
            VoxCall.user_id == account.user.id,
            VoxCall.workspace_id == account.workspace.id,
        )
    )
    count = await database.scalar(
        select(func.count())
        .select_from(VoxUsageStream)
        .where(
            VoxUsageStream.call_id == call_id,
            VoxUsageStream.user_id == account.user.id,
            VoxUsageStream.workspace_id == account.workspace.id,
        )
    )
    if call is None and not count:
        raise HTTPException(404, "Voice call not found")
    totals = await _result(
        usage.call_totals(database, account.user.id, account.workspace.id, call_id)
    )
    if not count:
        totals.estimated_cost_usd = None
        totals.cost_basis = "unknown"
    return {
        "call_id": str(call_id),
        "metering_started": bool(count),
        "totals": totals.model_dump(mode="json"),
    }


@router.get("/policy", response_model=PolicyView, dependencies=[Depends(operator)])
async def get_usage_policy(database: DatabaseSession) -> PolicyView:
    revision, policy = await usage.get_policy(database)
    return PolicyView(revision=revision, policy=policy)


@router.put("/policy", response_model=PolicyView, dependencies=[Depends(operator)])
async def put_usage_policy(
    request: Request,
    database: DatabaseSession,
    account: CurrentAccountDependency,
    session_token_hash: SessionHashDependency,
) -> PolicyView:
    await _result(
        service._locked(
            database,
            account.user.id,
            account.workspace.id,
            session_token_hash,
        )
    )
    payload = await bounded_json(request, PolicyInput, max_bytes=32_000)
    revision, policy = await _result(
        usage.update_policy(
            database,
            payload.policy,
            expected_revision=payload.revision,
        )
    )
    await database.commit()
    return PolicyView(revision=revision, policy=policy)


@router.get("/daily", dependencies=[Depends(operator)])
async def daily_totals(
    database: DatabaseSession,
    day: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    after_user_id: UUID | None = None,
) -> dict[str, object]:
    rows = await _result(
        usage.admin_daily_totals(
            database,
            day=day,
            limit=limit,
            after_user_id=after_user_id,
        )
    )
    return {
        "students": rows,
        "next_after_user_id": rows[-1]["user_id"] if len(rows) == limit else None,
    }
