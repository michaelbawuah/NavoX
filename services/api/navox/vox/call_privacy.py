"""Current, call-bound text retention metadata; never a grant to record a call."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import utc
from navox.memory import history
from navox.vox import service
from navox.vox.schemas import Generation


class CallPrivacy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    workspace_id: UUID
    client_call_id: UUID
    generation: Generation
    privacy_generation: Generation
    mode: Literal["off-record", "saved-history", "history-off"]
    retention_days: Literal[30, 90] | None
    expires_at: datetime


async def read_call_privacy(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_token_hash: str,
    client_call_id: UUID,
) -> CallPrivacy:
    async def operation() -> CallPrivacy:
        boundary = await service._locked(database, user_id, workspace_id, session_token_hash)
        call = await service._call(database, user_id, workspace_id, client_call_id)
        if call is None or call.auth_session_id != boundary.session.id:
            raise service.VoxError(404, "Call reservation not found")
        if call.status != "reserved":
            raise service.VoxError(410, "This call reservation has ended")
        service._admission(boundary, call.generation)
        privacy = history.privacy_response(
            await history.privacy_row(database, user_id, workspace_id)
        )
        # Privacy changes share this owner lock and revoke the active call. This
        # descriptor therefore describes only this exact live call and session.
        mode: Literal["off-record", "saved-history", "history-off"] = "history-off"
        if privacy.off_the_record:
            mode = "off-record"
        elif privacy.history_enabled and privacy.disclosure_version == 1:
            mode = "saved-history"
        return CallPrivacy(
            user_id=user_id,
            workspace_id=workspace_id,
            client_call_id=call.client_call_id,
            generation=call.generation,
            privacy_generation=privacy.generation,
            mode=mode,
            retention_days=privacy.retention_days if mode == "saved-history" else None,
            expires_at=min(utc(call.expires_at), utc(boundary.session.expires_at)),
        )

    return await service._complete(database, operation())
