import asyncio
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from temporalio.api.enums.v1 import PendingActivityState
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import ApplicationError

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.models import Connection
from navox.intelligence.dispatcher import dispatch_source
from navox.intelligence.jobs import SourceWork
from navox.intelligence.source_cooldown import source_cooldown
from navox.intelligence.sync_errors import sanitize_diagnostic

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    source: Literal["gmail", "calendar"]
    request_id: UUID = Field(default_factory=uuid4)


class SyncStatus(BaseModel):
    workflow_id: str
    status: Literal[
        "queued",
        "running",
        "retrying",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "unavailable",
    ]
    commitment_count: int | None = None
    error: dict[str, str | int] | None = None


def _workflow_connection(workflow_id: str) -> UUID:
    """Accept only IDs issued by this endpoint, never arbitrary Temporal handles."""
    parts = workflow_id.split(":")
    try:
        if len(parts) != 4 or parts[0] != "intelligence" or parts[2] not in {"gmail", "calendar"}:
            raise ValueError
        connection_id, request_id = UUID(parts[1]), UUID(parts[3])
        if str(connection_id) != parts[1] or str(request_id) != parts[3]:
            raise ValueError
        return connection_id
    except ValueError:
        raise HTTPException(404, "Sync not found") from None


def _failure_diagnostic(error: BaseException) -> dict[str, str | int]:
    # Only our typed, allowlisted activity details cross the API boundary. In particular,
    # never return exception messages, stacks, heartbeat details, or provider responses.
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        if (
            isinstance(current, ApplicationError)
            and current.type == "IntelligenceProcessingFailure"
        ):
            return sanitize_diagnostic(current.details[0] if current.details else None)
        current = current.__cause__
    return sanitize_diagnostic(None)


async def _describe_sync(workflow_id: str, temporal_target: str) -> SyncStatus:
    unavailable = SyncStatus(workflow_id=workflow_id, status="unavailable")
    try:
        # Bound both connection setup and RPCs. A status read must not wait for the job
        # itself or start any Google/model work.
        async with asyncio.timeout(8):
            client = await Client.connect(temporal_target)
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe(rpc_timeout=timedelta(seconds=3))
            if description.workflow_type != "ProcessSourceEventWorkflow":
                return unavailable
            # Bind subsequent reads to the exact run we described.
            handle = client.get_workflow_handle(workflow_id, run_id=description.run_id)
            state = description.status
            if state == WorkflowExecutionStatus.COMPLETED:
                count = await handle.result(follow_runs=False, rpc_timeout=timedelta(seconds=3))
                return SyncStatus(
                    workflow_id=workflow_id,
                    status="completed",
                    commitment_count=count if type(count) is int and count >= 0 else None,
                )
            if state == WorkflowExecutionStatus.FAILED:
                try:
                    await handle.result(follow_runs=False, rpc_timeout=timedelta(seconds=3))
                except WorkflowFailureError as error:
                    return SyncStatus(
                        workflow_id=workflow_id, status="failed", error=_failure_diagnostic(error)
                    )
                return SyncStatus(
                    workflow_id=workflow_id, status="failed", error=sanitize_diagnostic(None)
                )
            if state in {WorkflowExecutionStatus.CANCELED, WorkflowExecutionStatus.TERMINATED}:
                return SyncStatus(workflow_id=workflow_id, status="cancelled")
            if state == WorkflowExecutionStatus.TIMED_OUT:
                return SyncStatus(workflow_id=workflow_id, status="timed_out")
            if state != WorkflowExecutionStatus.RUNNING:
                return unavailable
            pending = description.raw_description.pending_activities
            for activity_info in pending:
                if activity_info.HasField("last_failure"):
                    failure = await description.data_converter.decode_failure(
                        activity_info.last_failure
                    )
                    return SyncStatus(
                        workflow_id=workflow_id,
                        status="retrying",
                        error=_failure_diagnostic(failure),
                    )
            queued = description.history_length <= 2 or (
                bool(pending)
                and all(
                    item.state == PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED
                    and item.attempt <= 1
                    for item in pending
                )
            )
            return SyncStatus(workflow_id=workflow_id, status="queued" if queued else "running")
    except Exception:
        # Missing/expired Temporal history and infrastructure outages are not job success.
        return unavailable


@router.get("/sync/status")
async def sync_status(
    workflow_id: Annotated[str, Query(min_length=1, max_length=160)],
    response: Response,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SyncStatus:
    response.headers["Cache-Control"] = "no-store"
    connection_id = _workflow_connection(workflow_id)
    connection = await database.scalar(
        select(Connection.id).where(
            Connection.id == connection_id,
            Connection.workspace_id == current_account.workspace.id,
            Connection.user_id == current_account.user.id,
            Connection.provider == "google",
        )
    )
    if connection is None:
        raise HTTPException(404, "Sync not found")
    return await _describe_sync(workflow_id, settings.temporal_target)


@router.post("/sync", status_code=202)
async def sync_source(
    payload: SyncRequest,
    current_account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, str]:
    connection = await database.scalar(
        select(Connection).where(
            Connection.id == payload.connection_id,
            Connection.workspace_id == current_account.workspace.id,
            Connection.user_id == current_account.user.id,
            Connection.provider == "google",
            Connection.status == "active",
        )
    )
    if connection is None:
        raise HTTPException(404, "Connection not found")
    if current_account.user.agent_paused:
        raise HTTPException(409, "Resume NavoX before syncing")
    required = "https://www.googleapis.com/auth/" + (
        "gmail.readonly" if payload.source == "gmail" else "calendar.events.readonly"
    )
    if required not in connection.granted_scopes:
        raise HTTPException(403, "Authorize read access before syncing")
    if settings.ai_provider == "disabled" or (
        settings.openai_api_key is None or not settings.openai_api_key.get_secret_value()
    ):
        raise HTTPException(503, "Intelligence provider is not configured")
    cooldown = await source_cooldown(database, connection.id, payload.source)
    if cooldown is not None:
        seconds = int(cooldown["retry_after_seconds"])
        detail = f"Google requests are paused for this source. Try again in {seconds} seconds."
        if cooldown["code"] in {"google_daily_limit_exceeded", "google_quota_exceeded"}:
            detail += " Check this API's quotas in the Google Cloud project used by NavoX."
        raise HTTPException(429, detail, headers={"Retry-After": str(seconds)})
    try:
        workflow_id = await dispatch_source(
            SourceWork(
                str(connection.id),
                str(connection.user_id),
                str(connection.workspace_id),
                payload.source,
            ),
            settings=settings,
            request_id=str(payload.request_id),
        )
    except Exception:
        raise HTTPException(503, "Intelligence worker is temporarily unavailable") from None
    return {"workflow_id": workflow_id, "status": "queued"}
