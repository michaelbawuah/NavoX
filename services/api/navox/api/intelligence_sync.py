from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.models import Connection
from navox.intelligence.dispatcher import dispatch_source
from navox.intelligence.jobs import SourceWork

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    source: Literal["gmail", "calendar"]
    request_id: UUID = Field(default_factory=uuid4)


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
